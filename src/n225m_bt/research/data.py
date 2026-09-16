"""Select only allowed calendar partitions, then filter by OSE trade date."""

from collections import Counter
from dataclasses import dataclass, fields
from datetime import date
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal

import polars as pl

from n225m_bt.config import DateRange, ResearchConfig
from n225m_bt.domain import Bar, Session

Split = Literal["development", "out_of_sample"]
SPLITS = {
    "development": DateRange(start=date(2021, 1, 1), end=date(2025, 6, 30)),
    "out_of_sample": DateRange(start=date(2025, 7, 1), end=date(2025, 12, 31)),
}


def validate_splits(config: ResearchConfig) -> None:
    for name, expected in SPLITS.items():
        if config.splits.get(name) != expected:
            raise ValueError(f"research split {name} must remain {expected}")
    holdout = config.splits.get("final_holdout")
    if holdout is None or holdout.start != date(2026, 1, 1):
        raise ValueError("Final Holdout must start 2026-01-01")
    if set(config.splits) != {*SPLITS, "final_holdout"}:
        raise ValueError("unexpected research split")


def partition_paths(root: Path, split: Split) -> list[Path]:
    if split not in SPLITS:
        raise ValueError("Final Holdout is locked; only development/out_of_sample are allowed")
    from n225m_bt.research.execution import require_market_read

    require_market_read(root, split)
    period = SPLITS[split]
    # Partitioning uses calendar year/month. Prior December can contain January's night.
    paths: list[Path] = []
    for year in range(period.start.year - 1, period.end.year + 1):
        for month in range(1, 13):
            if (year, month) < (period.start.year - 1, 12):
                continue
            if (year, month) > (period.end.year, period.end.month):
                continue
            paths.extend(sorted((root / f"year={year}" / f"month={month:02d}").glob("*.parquet")))
    if not paths:
        raise ValueError(f"no canonical center Parquet partitions for {split} under {root}")
    return paths


@dataclass(frozen=True)
class ResearchData:
    bars: list[Bar]
    data_version: str
    quality: dict[str, object]


def load_split(root: Path, split: Split) -> ResearchData:
    paths = partition_paths(root, split)
    period = SPLITS[split]
    frame = (
        pl.scan_parquet(paths, hive_partitioning=False)
        .filter(pl.col("trade_date").is_between(period.start, period.end))
        .sort("ts_jst")
        .collect()
    )
    if frame.is_empty():
        raise ValueError(f"no bars in {split}")
    if frame["series_type"].unique().to_list() != ["center_continuous"]:
        raise ValueError("research requires isolated center_continuous data")
    if frame["instrument"].unique().to_list() != ["N225M"]:
        raise ValueError("research requires N225M")
    if frame["ts_jst"].dtype != pl.Datetime("us", "Asia/Tokyo"):
        raise ValueError("research requires canonical timezone-aware Asia/Tokyo timestamps")
    columns = [field.name for field in fields(Bar)]
    duplicate_rows = frame.filter(pl.col("ts_jst").is_duplicated())
    duplicate_audit = duplicate_rows.group_by("source_file").len().sort("source_file").to_dicts()
    if not duplicate_rows.is_empty():
        conflicts = (
            duplicate_rows.group_by("ts_jst")
            .agg(pl.struct(columns).n_unique().alias("versions"))
            .filter(pl.col("versions") > 1)
        )
        if not conflicts.is_empty():
            raise ValueError(
                "conflicting canonical bars at duplicate timestamps; check Gold quality"
            )
    input_rows = frame.height
    # Annual files overlap. Collapse only fully identical Bar records, including QC flags.
    # This explicit policy is recorded in the quality report and never mutates Gold/Raw.
    frame = frame.unique(subset=["ts_jst"], keep="first", maintain_order=True)
    canonical = frame.select(columns)
    buffer = BytesIO()
    canonical.write_ipc(buffer, compression="uncompressed")
    data_version = sha256(buffer.getvalue()).hexdigest()
    bars = []
    flags: Counter[str] = Counter()
    for row in canonical.iter_rows(named=True):
        row["session"] = Session(row["session"])
        row["quality_flags"] = tuple(row["quality_flags"])
        flags.update(row["quality_flags"])
        bars.append(Bar(**row))
    severe = {"OHLC_INVARIANT", "NON_POSITIVE_PRICE", "INVALID_VOLUME", "CALENDAR_UNCERTAIN"}
    if any(flags[flag] for flag in severe):
        raise ValueError(f"severe Gold quality flags: {dict(flags)}")
    return ResearchData(
        bars,
        data_version,
        {
            "split": split,
            "rows": len(bars),
            "input_rows": input_rows,
            "identical_duplicate_rows_collapsed": input_rows - len(bars),
            "duplicate_source_counts": duplicate_audit,
            "duplicate_policy": "collapse_fully_identical_Bar_records_else_fail",
            "trade_date_start": str(min(bar.trade_date for bar in bars)),
            "trade_date_end": str(max(bar.trade_date for bar in bars)),
            "flags": dict(sorted(flags.items())),
            "ineligible_bars": sum(not bar.is_eligible for bar in bars),
            "partitions": [str(path) for path in paths],
            "forward_namespace_read": False,
            "holdout_read": False,
        },
    )
