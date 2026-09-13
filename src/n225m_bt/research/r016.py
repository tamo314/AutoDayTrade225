"""Saved-ledger-only diagnostic for R016-D001.

This module deliberately has no market-data or backtest-engine dependency.  It
may read only completed campaign artefacts listed in ``SPECS``.
"""

from __future__ import annotations

import hashlib
import json
import random
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from math import ceil, floor
from pathlib import Path
from statistics import fmean
from typing import Any, cast

import polars as pl

DIAGNOSTIC_ID = "r016-d001-20260914-development-directional-evidence-03"
SEED = 20260913
ITERATIONS = 10_000
PRIMARY_BLOCK_LENGTH = 20
BLOCK_LENGTHS = (10, 20, 40)
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
EXPECTED_QUARANTINE = {
    "quarantined_sessions": 45,
    "quarantined_bars": 27_345,
    "included_sessions": 2_216,
    "included_bars": 1_326_086,
}


@dataclass(frozen=True)
class CampaignSpec:
    short_name: str
    campaign_id: str
    a: str


SPECS = (
    CampaignSpec("R011", "r011-q001-20260914-close-mean-reversion-02", "A_close_mean_reversion"),
    CampaignSpec("R012", "r012-q001-20260914-night-direction-followthrough-01", "A_night_follow"),
    CampaignSpec(
        "R013", "r013-q001-20260914-previous-same-session-direction-01", "A_previous_follow"
    ),
    CampaignSpec("R014", "r014-q001-20260914-range-midpoint-position-01", "A_range_midpoint"),
    CampaignSpec(
        "R015", "r015-q001-20260914-night-close-to-day-opening-total-change-01", "A_total_follow"
    ),
)


class GateError(ValueError):
    """A saved-input prerequisite was not met; no diagnostic inference is valid."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateError(f"expected a JSON object: {path}")
    return cast(dict[str, Any], value)


def _linear_quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a quantile of no values")
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    low, high = floor(index), ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def moving_block_indices(n: int, block_length: int, iterations: int, seed: int) -> list[list[int]]:
    """Generate non-circular, tail-truncated moving-block samples.

    A fresh pseudo-random generator for every block length makes the sensitivity
    runs independently reproducible.  Every returned row has exactly ``n``
    indexes and is intended to be shared by all compared series.
    """
    if n <= 0 or block_length <= 0 or iterations <= 0:
        raise ValueError("n, block_length, and iterations must be positive")
    rng = random.Random(seed)
    samples: list[list[int]] = []
    for _ in range(iterations):
        sample: list[int] = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(range(start, min(start + block_length, n)))
        samples.append(sample[:n])
    return samples


def simultaneous_intervals(
    series: dict[str, list[int]], indices: list[list[int]]
) -> dict[str, object]:
    """Max-deviation simultaneous bootstrap intervals for aligned daily series."""
    if not series or not indices:
        raise ValueError("series and bootstrap indices are required")
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1 or next(iter(lengths)) == 0:
        raise ValueError("all daily series must have one positive common length")
    n = next(iter(lengths))
    if any(len(row) != n or any(index < 0 or index >= n for index in row) for row in indices):
        raise ValueError("bootstrap indices do not match the common daily axis")
    names = list(series)
    means = {name: fmean(values) for name, values in series.items()}
    t_values: list[float] = []
    replicated: dict[str, list[float]] = {name: [] for name in names}
    for row in indices:
        deviations: list[float] = []
        for name in names:
            result = fmean(series[name][index] for index in row)
            replicated[name].append(result)
            deviations.append(abs(result - means[name]))
        t_values.append(max(deviations))
    q = _linear_quantile(t_values, 0.95)
    return {
        "means": means,
        "q": q,
        "intervals": {name: {"lower": means[name] - q, "upper": means[name] + q} for name in names},
        "t_values": t_values,
        "replicated_means": replicated,
    }


def _conditions(spec: CampaignSpec) -> dict[str, str]:
    return {"A": spec.a, "B": "B_always_long", "C": "C_always_short"}


def _required_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for spec in SPECS:
        campaign = root / spec.campaign_id
        files.extend(
            campaign / name
            for name in (
                "COMPLETED.json",
                "campaign_manifest.json",
                "preregistration.json",
                "preflight.json",
                "effective_config.json",
            )
        )
        for condition in _conditions(spec).values():
            files.extend(
                campaign / condition / name
                for name in (
                    "events.parquet",
                    "orders.parquet",
                    "fills.parquet",
                    "trades.parquet",
                    "daily_net_pnl.parquet",
                    "metrics.json",
                    "metrics_research.json",
                    "execution_audit.json",
                    "run_manifest.json",
                )
            )
    return files


def _preregistration(repo: Path) -> dict[str, object]:
    return {
        "diagnostic_id": DIAGNOSTIC_ID,
        "status": "frozen_before_saved_ledger_statistics",
        "proposition": "At least one frozen primary condition A from R011--R015 has positive pre-cost expectancy and exceeds both same-event always-long and always-short controls.",
        "scope": {
            "campaigns": {spec.short_name: spec.campaign_id for spec in SPECS},
            "conditions": "A/B/C only; one-tick one-way slippage and JPY 30 fee per side only",
            "excluded": [
                "R011 ...-01 interrupted execution",
                "new direction reversals",
                "parameter changes",
                "conditional subgroups",
                "strategy combinations",
                "market-bar/raw/external-price access",
                "backtest reruns",
                "OOS and Final Holdout",
            ],
        },
        "input_gate": {
            "completed_development_only": True,
            "required_quality": "PASS_LIMITED or better only",
            "development_trade_date_range": "2021-01-01 through 2025-06-30",
            "fixed_r004_quarantine": {
                **EXPECTED_QUARANTINE,
                "quarantined_session_list_sha256": QUARANTINE_HASH,
            },
            "missing_ledger_behavior": "BLOCKED; do not infer missing values from results",
        },
        "economics": {
            "P0": "net_pnl_jpy + fees_jpy + slippage_cost_jpy = side.sign * (exit_reference_price - entry_reference_price) * contract_multiplier * qty",
            "P1": "P0 - slippage_cost_jpy - fees_jpy = saved net_pnl_jpy",
            "interpretation": "P0 removes saved costs on the fixed saved path only; it is not a zero-tick replay, realizable profit, or justification to lower costs.",
        },
        "daily_series": ["P0_A", "P1_A", "P0_A_minus_B", "P0_A_minus_C"],
        "daily_axis": {
            "base": "R011/R013/R014 identical saved 1,120 trade_date axis",
            "r012_r015_extension": "Only the nine dates absent from a 1,111-day saved axis and proven by the saved quarantine list to have an excluded day session are zero-filled. No other missing date is filled.",
            "zeroes": "Saved no-trade/skipped days remain zero; no date is selected after observing results.",
        },
        "inference": {
            "series_count": 20,
            "bootstrap": "non-circular moving block, tail-truncated, common indexes across all 20 series",
            "iterations": ITERATIONS,
            "seed": SEED,
            "primary_block_length_trade_dates": PRIMARY_BLOCK_LENGTH,
            "sensitivity_block_lengths_trade_dates": list(BLOCK_LENGTHS),
            "interval": "T_b=max_j(abs(m*_bj-m_j)); q=linear 95th percentile(T); simultaneous interval=[m_j-q,m_j+q]",
        },
        "decision_rule": {
            "support": "For one A, lower bounds of P0_A, P0_A_minus_B, and P0_A_minus_C are all strictly greater than zero; the conclusion must not change across block 10/20/40 to be called robust.",
            "cost_shortfall": "If support holds and P1_A upper bound is less than zero, report prediction evidence but insufficient at specified costs.",
            "otherwise": "No pre-cost directional-prediction evidence for these five rules; a failed lower bound is not proof of zero predictive power.",
            "boundaries": "No CANDIDATE promotion, REJECT alteration, cost change, reversed trading, OOS access, or next experiment.",
        },
        "code_paths": [
            str(repo / "src/n225m_bt/research/r016.py"),
            str(repo / "scripts/run_r016_d001.py"),
            str(repo / "tests/test_r016_d001.py"),
        ],
    }


def _snapshot(repo: Path, output: Path) -> dict[str, str]:
    paths = [
        repo / "src/n225m_bt/research/r016.py",
        repo / "scripts/run_r016_d001.py",
        repo / "tests/test_r016_d001.py",
    ]
    archive = output / "source_snapshot.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zipped:
        for path in paths:
            zipped.write(path, path.relative_to(repo))
    return {str(path.relative_to(repo)): _sha256(path) for path in paths} | {
        "source_snapshot.zip": _sha256(archive)
    }


def _canonical(frame: pl.DataFrame, columns: list[str]) -> list[dict[str, object]]:
    return frame.select(columns).sort(columns).to_dicts()


def _pre_event_columns(frame: pl.DataFrame) -> list[str]:
    excluded = {
        "condition",
        "side",
        "entry_ts_jst",
        "exit_ts_jst",
        "entry_delay_minutes",
        "exit_delay_minutes",
        "exit_reason",
        "gross_pnl_jpy",
        "fees_jpy",
        "slippage_cost_jpy",
        "net_pnl_jpy",
    }
    return [column for column in frame.columns if column not in excluded]


def _assert_identical(label: str, frames: dict[str, pl.DataFrame], columns: list[str]) -> None:
    baseline = _canonical(frames["A"], columns)
    for condition in ("B", "C"):
        if _canonical(frames[condition], columns) != baseline:
            raise GateError(f"{label} differs between A and {condition}")


def _daily_map(frame: pl.DataFrame) -> dict[date, int]:
    if frame.columns != ["trade_date", "net_pnl_jpy"]:
        raise GateError(f"unexpected daily ledger schema: {frame.columns}")
    values = {
        date.fromisoformat(row["trade_date"]): int(row["net_pnl_jpy"]) for row in frame.to_dicts()
    }
    if len(values) != frame.height:
        raise GateError("saved daily ledger has duplicate trade_date")
    return values


def _trade_daily(frame: pl.DataFrame, value_column: str) -> dict[date, int]:
    grouped = frame.group_by("trade_date").agg(pl.col(value_column).sum().alias("value"))
    return {row["trade_date"]: int(row["value"]) for row in grouped.to_dicts()}


def _p0(frame: pl.DataFrame, multiplier: int) -> pl.DataFrame:
    signed = (
        pl.when(pl.col("side") == "long")
        .then(1)
        .when(pl.col("side") == "short")
        .then(-1)
        .otherwise(None)
    )
    return frame.with_columns(
        (pl.col("net_pnl_jpy") + pl.col("fees_jpy") + pl.col("slippage_cost_jpy")).alias(
            "P0_saved"
        ),
        (
            signed
            * (pl.col("exit_reference_price") - pl.col("entry_reference_price"))
            * multiplier
            * pl.col("qty")
        ).alias("P0_reference"),
    )


def _validate_campaign(
    spec: CampaignSpec, root: Path
) -> tuple[dict[str, dict[date, int]], dict[str, Any], list[date]]:
    campaign = root / spec.campaign_id
    completed = _read_json(campaign / "COMPLETED.json")
    if completed.get("status") != "development_complete" or completed.get("decision") != "REJECT":
        raise GateError(f"{spec.short_name} is not a completed rejected Development campaign")
    if completed.get("quality_status") != "PASS_LIMITED":
        raise GateError(f"{spec.short_name} quality is not PASS_LIMITED")
    if completed.get("oos") != "NOT_EVALUATED" or completed.get("final_holdout") != "NOT_ACCESSED":
        raise GateError(f"{spec.short_name} has an impermissible OOS/holdout state")
    preflight = _read_json(campaign / "preflight.json")
    input_range = preflight.get("development_input", {})
    if (
        input_range.get("trade_date_end") != "2025-06-30"
        or input_range.get("trade_date_start", "") < "2021-01-01"
    ):
        raise GateError(f"{spec.short_name} Development trade-date range differs")
    quarantine = preflight.get("quarantine", {})
    if (
        not quarantine.get("expected_match")
        or quarantine.get("quarantined_session_list_hash") != QUARANTINE_HASH
    ):
        raise GateError(f"{spec.short_name} R004 quarantine hash does not match")
    if any(quarantine.get(key) != value for key, value in EXPECTED_QUARANTINE.items()):
        raise GateError(f"{spec.short_name} R004 quarantine counts do not match")
    conditions = _conditions(spec)
    events = {
        key: pl.read_parquet(campaign / name / "events.parquet") for key, name in conditions.items()
    }
    orders = {
        key: pl.read_parquet(campaign / name / "orders.parquet") for key, name in conditions.items()
    }
    fills = {
        key: pl.read_parquet(campaign / name / "fills.parquet") for key, name in conditions.items()
    }
    trades = {
        key: pl.read_parquet(campaign / name / "trades.parquet") for key, name in conditions.items()
    }
    dailies = {
        key: _daily_map(pl.read_parquet(campaign / name / "daily_net_pnl.parquet"))
        for key, name in conditions.items()
    }
    _assert_identical(
        f"{spec.short_name} pre-event ledger", events, _pre_event_columns(events["A"])
    )
    _assert_identical(f"{spec.short_name} orders", orders, orders["A"].columns)
    _assert_identical(
        f"{spec.short_name} fills reference path",
        fills,
        ["trade_id", "fill_kind", "ts_jst", "reference_price"],
    )
    path_columns = [
        "trade_id",
        "trade_date",
        "qty",
        "entry_signal_ts",
        "entry_ts",
        "entry_reference_price",
        "exit_signal_ts",
        "exit_ts",
        "exit_reference_price",
        "holding_minutes",
        "entry_reason",
        "exit_reason",
    ]
    _assert_identical(f"{spec.short_name} saved execution path", trades, path_columns)
    effective = _read_json(campaign / "effective_config.json")
    multiplier = effective.get("instrument", {}).get("instrument", {}).get("contract_multiplier")
    if not isinstance(multiplier, int) or multiplier <= 0:
        raise GateError(f"{spec.short_name} has no valid saved contract multiplier")
    p0_dailies: dict[str, dict[date, int]] = {}
    condition_audit: dict[str, object] = {}
    for key, frame in trades.items():
        run_manifest = _read_json(campaign / conditions[key] / "run_manifest.json")
        costs = run_manifest.get("costs", {})
        if costs != {"fee_jpy_per_side": 30, "slippage_ticks_per_side": 1}:
            raise GateError(f"{spec.short_name}/{key} does not use the frozen 1-tick/JPY30 costs")
        reconciled = _p0(frame, multiplier)
        if reconciled.filter(pl.col("P0_saved") != pl.col("P0_reference")).height:
            raise GateError(f"{spec.short_name}/{key} P0 reference-price reconciliation failed")
        if reconciled.filter(
            pl.col("gross_pnl_jpy") != pl.col("P0_saved") - pl.col("slippage_cost_jpy")
        ).height:
            raise GateError(f"{spec.short_name}/{key} gross/slippage contract differs")
        if reconciled.filter(
            pl.col("net_pnl_jpy")
            != pl.col("P0_saved") - pl.col("slippage_cost_jpy") - pl.col("fees_jpy")
        ).height:
            raise GateError(f"{spec.short_name}/{key} P1/net reconciliation failed")
        p0_dailies[key] = _trade_daily(reconciled, "P0_saved")
        net_from_trades = _trade_daily(frame, "net_pnl_jpy")
        if any(dailies[key][day] != net_from_trades.get(day, 0) for day in dailies[key]) or set(
            net_from_trades
        ) - set(dailies[key]):
            raise GateError(f"{spec.short_name}/{key} trade-to-daily net reconciliation failed")
        overall = _read_json(campaign / conditions[key] / "metrics.json").get("overall")
        if not isinstance(overall, dict):
            raise GateError(f"{spec.short_name}/{key} has no saved overall metrics")
        expected_metrics = {
            "trade_count": frame.height,
            "gross_pnl_jpy": int(frame.get_column("gross_pnl_jpy").sum()),
            "fees_jpy": int(frame.get_column("fees_jpy").sum()),
            "slippage_cost_jpy": int(frame.get_column("slippage_cost_jpy").sum()),
            "net_pnl_jpy": int(frame.get_column("net_pnl_jpy").sum()),
        }
        if any(overall.get(field) != value for field, value in expected_metrics.items()):
            raise GateError(f"{spec.short_name}/{key} trade totals do not match saved metrics")
        if sum(dailies[key].values()) != expected_metrics["net_pnl_jpy"]:
            raise GateError(f"{spec.short_name}/{key} daily total does not match saved metrics")
        condition_audit[key] = {
            "pre_events": events[key].height,
            "event_status_counts": dict(Counter(events[key].get_column("status").to_list())),
            "orders": orders[key].height,
            "order_status_counts": dict(Counter(orders[key].get_column("status").to_list())),
            "fills": fills[key].height,
            "trades": trades[key].height,
            "reconciled_metrics": expected_metrics,
            "exit_reason_counts": dict(Counter(trades[key].get_column("exit_reason").to_list())),
            "execution_audit_sha256": _sha256(campaign / conditions[key] / "execution_audit.json"),
        }
    if not (set(dailies["A"]) == set(dailies["B"]) == set(dailies["C"])):
        raise GateError(f"{spec.short_name} A/B/C daily axes differ")
    return (
        p0_dailies,
        {"quarantine": quarantine, "conditions": condition_audit},
        sorted(dailies["A"]),
    )


def _extension_dates(
    primary: list[date], reduced: list[date], quarantine: dict[str, object]
) -> list[date]:
    missing = sorted(set(primary) - set(reduced))
    if (
        len(primary) != 1120
        or len(reduced) != 1111
        or len(missing) != 9
        or set(reduced) - set(primary)
    ):
        raise GateError("saved daily axes are not the fixed 1,120/1,111 configuration")
    quarantined = quarantine.get("quarantined_session_list")
    if not isinstance(quarantined, list) or not all(isinstance(item, dict) for item in quarantined):
        raise GateError("saved quarantine session list is unavailable")
    quarantined_day_dates = {
        date.fromisoformat(item["trade_date"])
        for item in quarantined
        if item.get("session") == "day"
    }
    if not set(missing) <= quarantined_day_dates:
        raise GateError(
            "a reduced-axis date is not proven day-session-excluded by saved isolation evidence"
        )
    return missing


def _yearly(series: dict[str, list[int]], axis: list[date]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for name, values in series.items():
        for year in sorted({day.year for day in axis}):
            subset = [value for day, value in zip(axis, values, strict=True) if day.year == year]
            output.append(
                {
                    "series": name,
                    "year": year,
                    "period": "2025-01-01 through 2025-06-30" if year == 2025 else str(year),
                    "trade_date_count": len(subset),
                    "total_jpy": sum(subset),
                    "mean_jpy_per_trade_date": fmean(subset),
                }
            )
    return output


def _diagnostic_conclusion(intervals: dict[str, dict[str, float]]) -> dict[str, bool]:
    support = all(intervals[name]["lower"] > 0 for name in ("P0_A", "P0_A_minus_B", "P0_A_minus_C"))
    cost_shortfall = support and intervals["P1_A"]["upper"] < 0
    return {
        "pre_cost_directional_support": support,
        "prediction_but_cost_shortfall": cost_shortfall,
    }


def run_r016(repo: Path) -> Path:
    """Create the append-only R016-D001 result from already saved Development ledgers."""
    research_root = repo / "results" / "research"
    output = research_root / DIAGNOSTIC_ID
    if output.exists():
        raise FileExistsError(f"diagnostic output already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / "preregistration.json", _preregistration(repo))
    duplicates = []
    for preregistration in research_root.glob("*/preregistration.json"):
        if preregistration.parent == output:
            continue
        try:
            if _read_json(preregistration).get("diagnostic_id") == DIAGNOSTIC_ID:
                duplicates.append(str(preregistration))
        except json.JSONDecodeError:
            continue
    _write_json(
        output / "novelty_check.json",
        {
            "equivalent_existing_diagnostic": duplicates,
            "status": "CLEAR" if not duplicates else "DUPLICATE",
        },
    )
    if duplicates:
        _write_json(
            output / "BLOCKED.json", {"status": "DUPLICATE_NOT_EXECUTED", "paths": duplicates}
        )
        return output
    _write_json(output / "source_manifest.json", _snapshot(repo, output))
    required = _required_files(research_root)
    absent = [str(path) for path in required if not path.is_file()]
    _write_json(
        output / "input_manifest.json",
        {
            "files": {
                str(path.relative_to(repo)): _sha256(path) for path in required if path.is_file()
            },
            "absent": absent,
        },
    )
    if absent:
        _write_json(
            output / "BLOCKED.json", {"status": "MISSING_REQUIRED_LEDGER", "absent": absent}
        )
        return output
    try:
        campaign_data: dict[str, tuple[dict[str, dict[date, int]], dict[str, Any], list[date]]] = {
            spec.short_name: _validate_campaign(spec, research_root) for spec in SPECS
        }
        common_axis = campaign_data["R011"][2]
        if common_axis != campaign_data["R013"][2] or common_axis != campaign_data["R014"][2]:
            raise GateError("R011/R013/R014 saved daily axes are not identical")
        extensions = {
            name: _extension_dates(
                common_axis, campaign_data[name][2], campaign_data[name][1]["quarantine"]
            )
            for name in ("R012", "R015")
        }
        p0_by_campaign: dict[str, dict[str, dict[date, int]]] = {}
        p1_by_campaign: dict[str, dict[str, dict[date, int]]] = {}
        for spec in SPECS:
            campaign = research_root / spec.campaign_id
            p0_by_campaign[spec.short_name] = campaign_data[spec.short_name][0]
            p1_by_campaign[spec.short_name] = {
                key: _daily_map(pl.read_parquet(campaign / condition / "daily_net_pnl.parquet"))
                for key, condition in _conditions(spec).items()
            }
        daily_series: dict[str, list[int]] = {}
        rows: list[dict[str, object]] = []
        for spec in SPECS:
            name = spec.short_name
            p0 = p0_by_campaign[name]
            p1 = p1_by_campaign[name]
            series = {
                "P0_A": [p0["A"].get(day, 0) for day in common_axis],
                "P1_A": [p1["A"].get(day, 0) for day in common_axis],
                "P0_A_minus_B": [p0["A"].get(day, 0) - p0["B"].get(day, 0) for day in common_axis],
                "P0_A_minus_C": [p0["A"].get(day, 0) - p0["C"].get(day, 0) for day in common_axis],
            }
            for series_name, values in series.items():
                full_name = f"{name}_{series_name}"
                daily_series[full_name] = values
                rows.extend(
                    {"trade_date": day, "series": full_name, "pnl_jpy": value}
                    for day, value in zip(common_axis, values, strict=True)
                )
        pl.DataFrame(rows).write_parquet(output / "daily_series.parquet")
        by_block_length: dict[str, Any] = {}
        bootstrap_summary: dict[str, Any] = {
            "settings": {
                "seed": SEED,
                "iterations": ITERATIONS,
                "common_daily_axis_count": len(common_axis),
                "method": "non-circular moving block, tail-truncated; each block length resets the seeded generator; each index row is shared across all 20 series",
            },
            "by_block_length": by_block_length,
        }
        replicate_rows: list[dict[str, object]] = []
        conclusions: dict[int, dict[str, dict[str, bool]]] = {}
        for block_length in BLOCK_LENGTHS:
            indices = moving_block_indices(len(common_axis), block_length, ITERATIONS, SEED)
            result = simultaneous_intervals(daily_series, indices)
            intervals = result["intervals"]
            assert isinstance(intervals, dict)
            index_hash = hashlib.sha256(
                json.dumps(indices, separators=(",", ":")).encode()
            ).hexdigest()
            by_campaign = {
                spec.short_name: _diagnostic_conclusion(
                    {
                        key.removeprefix(f"{spec.short_name}_"): value
                        for key, value in intervals.items()
                        if key.startswith(f"{spec.short_name}_")
                    }
                )
                for spec in SPECS
            }
            conclusions[block_length] = by_campaign
            by_block_length[str(block_length)] = {
                "q": result["q"],
                "intervals": intervals,
                "index_sha256": index_hash,
                "first_index_rows": indices[:3],
                "conclusion": {"by_campaign": conclusions[block_length]},
            }
            replicated = result["replicated_means"]
            t_values = result["t_values"]
            assert isinstance(replicated, dict) and isinstance(t_values, list)
            for iteration, t_value in enumerate(t_values):
                replicate_rows.append(
                    {"block_length": block_length, "iteration": iteration, "T": t_value}
                    | {name: replicated[name][iteration] for name in daily_series}
                )
        _write_json(output / "bootstrap.json", bootstrap_summary)
        pl.DataFrame(replicate_rows).write_parquet(output / "bootstrap_replicates.parquet")
        yearly_rows: list[dict[str, object]] = []
        for spec in SPECS:
            prefix = f"{spec.short_name}_"
            subset = {
                name.removeprefix(prefix): values
                for name, values in daily_series.items()
                if name.startswith(prefix)
            }
            for row in _yearly(subset, common_axis):
                row["campaign"] = spec.short_name
                yearly_rows.append(row)
        _write_json(output / "yearly_diagnostics.json", yearly_rows)
        primary = cast(dict[str, Any], by_block_length[str(PRIMARY_BLOCK_LENGTH)])
        primary_conclusion = cast(dict[str, dict[str, bool]], primary["conclusion"]["by_campaign"])
        robust = {
            spec.short_name: all(
                conclusions[length][spec.short_name]["pre_cost_directional_support"]
                == primary_conclusion[spec.short_name]["pre_cost_directional_support"]
                for length in BLOCK_LENGTHS
            )
            for spec in SPECS
        }
        supported = [
            name
            for name, value in primary_conclusion.items()
            if value["pre_cost_directional_support"] and robust[name]
        ]
        final = {
            "status": "COMPLETE",
            "decision": "NO_PRE_COST_DIRECTIONAL_PREDICTION_EVIDENCE"
            if not supported
            else "DIAGNOSTIC_SUPPORT_ONLY",
            "supported_campaigns": supported,
            "primary_block_length": PRIMARY_BLOCK_LENGTH,
            "block_sensitivity_robust": robust,
            "interpretation": "No CANDIDATE promotion or change to the five original REJECT decisions follows from this post-hoc Development-only diagnostic.",
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
            "quality_status": "PASS_LIMITED_INHERITED",
            "axis_extension_dates": {
                name: [str(day) for day in dates] for name, dates in extensions.items()
            },
            "limitations": [
                "The simultaneous interval controls only these fixed 20 saved-ledger series, not adaptive research-wide exploration.",
                "Moving-block bootstrap relies on stationarity-style approximations and is not an independent replication or walk-forward test.",
                "R004 post-hoc whole-session isolation, continuous-series real-contract/roll/provider limitations remain inherited.",
                "B/C are fixed-direction bias controls, not causal controls for order flow.",
            ],
        }
        _write_json(
            output / "validation.json",
            {
                "campaigns": {name: data[1] for name, data in campaign_data.items()},
                "axis": [str(day) for day in common_axis],
                "extensions": final["axis_extension_dates"],
                "access_scope": "saved Development campaign artefacts only; no bars/raw/external prices/backtests/OOS/Final Holdout",
            },
        )
        _write_json(output / "decision.json", final)
        _write_json(output / "COMPLETED.json", final)
    except GateError as error:
        _write_json(
            output / "BLOCKED.json",
            {
                "status": "INPUT_OR_EXECUTION_GATE_FAILED",
                "reason": str(error),
                "oos": "NOT_EVALUATED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
    return output
