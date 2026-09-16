"""Read-only R003 Development price-lineage audit; intentionally imports no backtest code."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import openpyxl
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
AUDIT_ID = "r003-20260913-dev-price-audit-01"
OUT = ROOT / "results" / "research" / AUDIT_ID
START = date(2021, 1, 1)
END = date(2025, 6, 30)
TICK = 5
PRICE_COLUMNS = ["open", "high", "low", "close"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_path(value: str) -> Path:
    return ROOT / Path(value.replace("\\", "/"))


def raw_file_metadata(path: Path, permit_content_hash: bool) -> dict[str, object]:
    stat = path.stat()
    result: dict[str, object] = {
        "path": str(path.relative_to(ROOT)),
        "bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }
    if permit_content_hash:
        result["sha256"] = sha256_file(path)
    else:
        result["sha256"] = None
        result["sha256_reason"] = "not_computed: 2025 workbook contains OOS rows; only selected Development cells were read"
    return result


def raw_cells(source_file: str, row_number: int) -> dict[str, object]:
    """Return only a specified Excel row; never enumerate raw rows after that row."""
    path = project_path(source_file)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["1min"]
        headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
        index = {str(value).strip(): position for position, value in enumerate(headers)}
        values = next(
            sheet.iter_rows(min_row=row_number, max_row=row_number, values_only=True), None
        )
        if values is None:
            raise ValueError(f"missing selected raw row: {source_file}:{row_number}")
        row = {name: values[position] for name, position in index.items()}
    finally:
        workbook.close()
    raw_date = row["日付"]
    parsed_date = raw_date.date() if isinstance(raw_date, datetime) else raw_date
    if not isinstance(parsed_date, date) or not (START <= parsed_date <= END):
        raise ValueError(f"selected raw row outside Development: {source_file}:{row_number}={raw_date!r}")
    parsed = {name: int(float(str(row[japanese]).replace(",", "").strip())) for name, japanese in {
        "open": "始値", "high": "高値", "low": "安値", "close": "終値"
    }.items()}
    return {
        "raw_trade_date": parsed_date.isoformat(),
        "raw_time": str(row["時間"]),
        "raw_ohlc": parsed,
        "raw_remainders": {name: value % TICK for name, value in parsed.items()},
    }


def select_samples(violations: pl.DataFrame) -> pl.DataFrame:
    keyed = violations.with_columns(
        pl.concat_str(
            [pl.lit(AUDIT_ID), pl.col("ts_jst").cast(pl.String), pl.col("source_file"), pl.col("source_row_number").cast(pl.String)],
            separator="|",
        ).map_elements(lambda value: hashlib.sha256(value.encode()).hexdigest(), return_dtype=pl.String).alias("sample_hash")
    )
    year_session = keyed.with_columns(pl.col("trade_date").dt.year().alias("year")).sort("sample_hash").group_by(["year", "session"], maintain_order=True).first()
    source = keyed.sort("sample_hash").group_by("source_file", maintain_order=True).first()
    return pl.concat([year_session, source], how="diagonal_relaxed").unique(
        subset=["ts_jst", "source_file", "source_row_number"], maintain_order=True
    ).sort(["trade_date", "session", "ts_jst"])


def main() -> None:
    # The directory is created by the preregistration patch.  Refuse a rerun if
    # any result artifact already exists, preserving the one-ID/one-result rule.
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r003_development_price_audit.py')

    if not OUT.exists():
        raise ValueError("missing preregistered audit directory")
    if any((OUT / name).exists() for name in [
        "audit_results.json", "tick_grid_by_stratum.parquet", "tick_grid_lineage_samples.parquet",
        "continuous_runs.parquet", "raw_lineage_comparison.parquet", "provenance.json", "audit_report.md",
    ]):
        raise ValueError("audit results already exist; use a new audit ID")
    plan = json.loads((OUT / "audit_plan.json").read_text(encoding="utf-8"))
    if plan["audit_id"] != AUDIT_ID or plan["preregistration_status"] != "frozen_before_data_results":
        raise ValueError("audit plan was not frozen before execution")

    paths = [ROOT / "data" / "gold" / "year=2020" / "month=12" / "bars.parquet"]
    paths.extend(
        ROOT / "data" / "gold" / f"year={year}" / f"month={month:02d}" / "bars.parquet"
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    )
    if not all(path.exists() for path in paths):
        raise ValueError("an allowed Development partition is missing")
    columns = [
        "ts_jst", "trade_date", "calendar_date", "session", "schedule_version", "instrument",
        "series_type", "contract_month", "open", "high", "low", "close", "volume",
        "is_session_open", "is_session_close", "is_missing_prev_expected", "roll_risk",
        "is_eligible", "quality_flags", "source", "source_file", "source_row_number",
    ]
    # Predicate controls returned values.  The December 2020 partition is needed for
    # trade-date 2021 night bars; physical Parquet row-group I/O is logged separately.
    frame = (
        pl.scan_parquet(paths, hive_partitioning=False)
        .filter(pl.col("trade_date").is_between(START, END))
        .select(columns)
        .sort("ts_jst")
        .collect()
    )
    if frame.filter(~pl.col("trade_date").is_between(START, END)).height:
        raise AssertionError("period filter returned a non-Development price")
    if frame.select(pl.col("instrument").n_unique(), pl.col("series_type").n_unique()).row(0) != (1, 1):
        raise AssertionError("mixed instrument or series_type")
    if frame["instrument"].unique().to_list() != ["N225M"] or frame["series_type"].unique().to_list() != ["center_continuous"]:
        raise AssertionError("instrument/series contract differs from R003 input")
    duplicate = frame.filter(pl.col("ts_jst").is_duplicated())
    # Exact-duplicate policy is defined on Bar fields, not source lineage: annual
    # file boundaries legitimately have distinct source_file/source_row_number.
    bar_columns = [
        "ts_jst", "trade_date", "calendar_date", "session", "schedule_version", "open", "high",
        "low", "close", "volume", "is_session_open", "is_session_close", "is_missing_prev_expected",
        "roll_risk", "is_eligible", "quality_flags",
    ]
    conflicts = duplicate.group_by("ts_jst").agg(pl.struct(bar_columns).n_unique().alias("versions")).filter(pl.col("versions") > 1)
    if conflicts.height:
        raise AssertionError("conflicting duplicate canonical bars")
    input_rows = frame.height
    canonical = frame.unique(subset=["ts_jst"], keep="first", maintain_order=True)

    with_remainders = canonical.with_columns(
        *[(pl.col(name) % TICK).alias(f"{name}_remainder") for name in PRICE_COLUMNS]
    ).with_columns(
        pl.any_horizontal([pl.col(f"{name}_remainder") != 0 for name in PRICE_COLUMNS]).alias("bar_violation"),
        pl.sum_horizontal([(pl.col(f"{name}_remainder") != 0).cast(pl.Int8) for name in PRICE_COLUMNS]).alias("field_violation_count"),
    )
    violations = with_remainders.filter(pl.col("bar_violation"))
    fields = violations.select(pl.sum("field_violation_count")).item()
    flagged = violations.filter(pl.col("quality_flags").list.contains("TICK_GRID_VIOLATION")).height
    if violations.height != flagged:
        raise AssertionError("computed tick violations disagree with Gold flag")

    by_stratum = pl.concat([
        with_remainders.with_columns(pl.col("trade_date").dt.year().alias("year")).group_by(["year", "session"]).agg(
            pl.len().alias("bars"), pl.col("bar_violation").sum().alias("violation_bars"), pl.col("field_violation_count").sum().alias("violation_fields")
        ).with_columns(pl.lit("year_session").alias("stratum")),
        with_remainders.with_columns(pl.col("trade_date").dt.strftime("%Y-%m").alias("month")).group_by(["month", "session"]).agg(
            pl.len().alias("bars"), pl.col("bar_violation").sum().alias("violation_bars"), pl.col("field_violation_count").sum().alias("violation_fields")
        ).with_columns(pl.lit("month_session").alias("stratum")),
        with_remainders.group_by("source_file").agg(
            pl.len().alias("bars"), pl.col("bar_violation").sum().alias("violation_bars"), pl.col("field_violation_count").sum().alias("violation_fields")
        ).with_columns(pl.lit("source_file").alias("stratum")),
    ], how="diagonal_relaxed").sort(["stratum", "year", "month", "session", "source_file"])
    by_stratum.write_parquet(OUT / "tick_grid_by_stratum.parquet")

    offset_columns = [f"{name}_remainder" for name in PRICE_COLUMNS]
    offset_summary = violations.group_by(offset_columns).agg(pl.len().alias("bars"), pl.col("field_violation_count").sum().alias("fields")).sort("bars", descending=True)
    shared_offsets = violations.filter(
        pl.all_horizontal([pl.col(name) == pl.col("open_remainder") for name in offset_columns[1:]]) & (pl.col("open_remainder") != 0)
    ).height

    ordered = violations.sort("ts_jst").with_columns(
        (pl.col("ts_jst") - pl.col("ts_jst").shift(1)).dt.total_minutes().alias("minute_gap"),
        (pl.col("trade_date") == pl.col("trade_date").shift(1)).alias("same_trade_date"),
        (pl.col("session") == pl.col("session").shift(1)).alias("same_session"),
        (pl.col("source_file") == pl.col("source_file").shift(1)).alias("same_source"),
    ).with_columns(
        (~((pl.col("minute_gap") == 1) & pl.col("same_trade_date") & pl.col("same_session") & pl.col("same_source"))).cum_sum().alias("run_id")
    )
    runs = ordered.group_by("run_id").agg(
        pl.len().alias("bars"), pl.first("ts_jst").alias("start_ts_jst"), pl.last("ts_jst").alias("end_ts_jst"),
        pl.first("trade_date").alias("trade_date"), pl.first("session").alias("session"), pl.first("source_file").alias("source_file"),
        pl.col("field_violation_count").sum().alias("violation_fields")
    ).sort(["bars", "start_ts_jst"], descending=[True, False])
    runs.write_parquet(OUT / "continuous_runs.parquet")

    samples = select_samples(violations)
    samples.write_parquet(OUT / "tick_grid_lineage_samples.parquet")
    comparisons: list[dict[str, Any]] = []
    for row in samples.iter_rows(named=True):
        raw = raw_cells(str(row["source_file"]), int(row["source_row_number"]))
        gold = {name: int(row[name]) for name in PRICE_COLUMNS}
        comparisons.append({
            "ts_jst": row["ts_jst"], "trade_date": row["trade_date"], "session": row["session"],
            "source_file": row["source_file"], "source_row_number": row["source_row_number"],
            "raw_trade_date": raw["raw_trade_date"], "raw_time": raw["raw_time"],
            "gold_ohlc": gold, "raw_ohlc": raw["raw_ohlc"],
            "gold_remainders": {name: value % TICK for name, value in gold.items()},
            "raw_remainders": raw["raw_remainders"], "all_ohlc_equal": gold == raw["raw_ohlc"],
        })
    comparison_frame = pl.DataFrame(comparisons)
    comparison_frame.write_parquet(OUT / "raw_lineage_comparison.parquet")
    if not comparison_frame["all_ohlc_equal"].all():
        raise AssertionError("selected Gold/raw OHLC lineage mismatch")

    source_files = sorted(set(canonical["source_file"].to_list()))
    raw_metadata = [raw_file_metadata(project_path(source), "N225minif_2025.xlsx" not in source) for source in source_files]
    provenance = {
        "audit_id": AUDIT_ID,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_script": str(Path(__file__).relative_to(ROOT)),
        "audit_script_sha256": sha256_file(Path(__file__)),
        "git_commit": __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "code_hashes": {str(path.relative_to(ROOT)): sha256_file(path) for path in [
            ROOT / "src/n225m_bt/research/data.py", ROOT / "src/n225m_bt/research/r003.py",
            ROOT / "src/n225m_bt/ingest/labo225.py", ROOT / "src/n225m_bt/ingest/pipeline.py",
            ROOT / "src/n225m_bt/normalize/bars.py", ROOT / "src/n225m_bt/normalize/gold.py",
            ROOT / "src/n225m_bt/quality/checks.py",
        ]},
        "config_hashes": {str(path.relative_to(ROOT)): sha256_file(path) for path in [
            ROOT / "config/data.yaml", ROOT / "config/instrument.yaml", ROOT / "config/backtest.yaml", ROOT / "config/strategy_compression.yaml",
        ]},
        "gold_partitions": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)} for path in paths],
        "raw_files": raw_metadata,
        "logical_price_access": {"trade_date_min": str(canonical["trade_date"].min()), "trade_date_max": str(canonical["trade_date"].max()), "oos_prices_returned": 0, "final_holdout_prices_returned": 0},
        "physical_io_note": "Parquet predicate scans can read compressed row groups. The December-2020 calendar partition was required for Development trade-date night bars; no non-Development rows were returned. For selected 2025 raw rows, openpyxl opened the workbook and read only specified Development row values; no 2025-H2 raw row values were returned.",
        "existing_preflight_difference": "Existing run_preflight reproduces violation bars and writes one row per violating bar, but does not count OHLC fields, aggregate fixed strata, examine continuous runs/shared offsets, compare raw lineage samples, or classify continuous-series evidence. This audit adds only read-only artifacts.",
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    results = {
        "audit_id": AUDIT_ID,
        "quality_status": "BLOCKED",
        "decision": "INVESTIGATE",
        "development_pnl": "NOT_RUN", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
        "canonical_development_bars": canonical.height, "input_development_rows": input_rows,
        "identical_duplicate_rows_collapsed": input_rows - canonical.height,
        "tick_grid_violation_bars": violations.height, "tick_grid_violation_fields": fields,
        "gold_flagged_tick_grid_bars": flagged, "shared_nonzero_ohlc_offset_bars": shared_offsets,
        "continuous_runs": runs.height, "runs_with_multiple_bars": runs.filter(pl.col("bars") > 1).height,
        "largest_continuous_run_bars": int(runs["bars"].max()),
        "selected_lineage_samples": samples.height, "selected_raw_gold_ohlc_matches": int(comparison_frame["all_ohlc_equal"].sum()),
        "source_series": {"instrument": canonical["instrument"].unique().to_list(), "series_type": canonical["series_type"].unique().to_list(), "contract_month_nonnull": int(canonical.filter(pl.col("contract_month").is_not_null()).height)},
        "offset_patterns": offset_summary.to_dicts(),
        "unresolved": [
            "No supplier specification or contract-change record establishes whether center_continuous prices are unadjusted, additive-adjusted, or ratio-adjusted.",
            "contract_month is null for all audited bars and roll_risk lacks observed contract evidence; price jumps were not used to infer a roll.",
            "Raw/Gold equality proves non-5 offsets predate this normalization path for selected strata, but does not establish their market type or tradability."
        ],
    }
    (OUT / "audit_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report = f"""# R003 Development price-lineage audit

Audit ID: `{AUDIT_ID}`  
Gate: **BLOCKED**  
Development PnL: **NOT_RUN**  
OOS: **NOT_EVALUATED**  
Final Holdout: **NOT_ACCESSED**

## Confirmed facts

- Fixed 5-point integer-grid rule reproduces **{violations.height:,} violating bars** and **{fields:,} violating OHLC fields** after the existing exact-duplicate policy ({input_rows - canonical.height:,} duplicate rows collapsed).
- All {violations.height:,} computed violation bars carry `TICK_GRID_VIOLATION` in Gold. Integer remainders are used; Gold contains integer price columns, so this observation is not a floating-point residual.
- {shared_offsets:,} violating bars have the same nonzero remainder in all OHLC fields. Violations form {runs.height:,} minute-contiguous runs; {runs.filter(pl.col('bars') > 1).height:,} runs contain multiple bars and the largest has {int(runs['bars'].max()):,} bars.
- The fixed sample rule selected {samples.height} lineage rows. Raw and Gold OHLC match for all {int(comparison_frame['all_ohlc_equal'].sum())} selected rows, so the selected offsets existed in the raw rows before the documented normalization path.
- Gold contains only `N225M` / `center_continuous`; `contract_month` is null for all {canonical.height:,} audited canonical bars.

## Inferences, not established facts

- The pattern is compatible with a source-level non-5 grid or an upstream continuous-series transformation. It is not evidence that these are exchange-tradable prices.
- The selected raw/Gold equality makes a Gold-only integer conversion defect less likely, but does not rule out provider-side transformation, market-type mixing, or a raw-source anomaly.

## Unresolved gate conditions

- No provider provenance states whether `center_continuous` is unadjusted, additive-adjusted, or ratio-adjusted, nor its roll-selection method/timestamps.
- No observed contract ID/change record is supplied. `roll_risk=false` therefore cannot be interpreted as evidence of no roll.
- As the existing quality gate permits PASS only with explanation and observed roll evidence, its required conditions are not met. No tolerance was relaxed and no price was rounded or excluded.

## Next minimum work

Obtain the 225Labo center-series construction specification and a Development contract/roll-change table (contract ID, selection rule, effective timestamp, adjustment method). Join it read-only to this audit's `source_file/source_row_number` lineage, then determine whether each offset class is a quoted tradable price, an explicitly adjusted research price, or a source anomaly.
"""
    (OUT / "audit_report.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
