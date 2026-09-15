"""Read-only reconciliation of the frozen R032 result ledger.

The audit accepts only the R032 ``-02`` result directory and a small whitelist
of saved ledger artifacts.  It never opens market bars, features, raw inputs,
or OOS/Final Holdout paths.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from statistics import fmean
from typing import Any

import polars as pl

RUN_NAME = "r032-q001-20260914-prior-day-night-gap-confirmation-02"
EVENT_COLUMNS = ("trade_date", "base_event_status", "status", "gross_pnl_jpy")


class R032LedgerAuditError(ValueError):
    """The requested audit input is not the immutable R032 ledger scope."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise R032LedgerAuditError(f"{path.name} must be a JSON object")
    return value


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    point = (len(ordered) - 1) * quantile
    low, high = int(point), min(int(point) + 1, len(ordered) - 1)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def _bootstrap_difference(
    confirmed: list[int], nonconfirmed: list[int], repetitions: int, seed: int, block: int
) -> dict[str, object]:
    if len(confirmed) != len(nonconfirmed) or not confirmed or block > len(confirmed):
        raise R032LedgerAuditError("R032 aligned daily zero-tick ledger is invalid")
    difference = [left - right for left, right in zip(confirmed, nonconfirmed, strict=True)]
    rng = Random(seed)
    samples: list[float] = []
    count = len(difference)
    for _ in range(repetitions):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        samples.append(fmean(difference[index] for index in indices[:count]))
    return {
        "estimate": fmean(difference),
        "ci95_percentile_linear": [_percentile(samples, 0.025), _percentile(samples, 0.975)],
    }


def _assert_close(actual: float, expected: float, label: str) -> None:
    if abs(actual - expected) > 1e-9:
        raise R032LedgerAuditError(f"{label} differs: {actual!r} != {expected!r}")


def audit_r032_ledger(run_dir: Path, audit_id: str, commit: str) -> dict[str, object]:
    """Reconcile R032 display and bootstrap estimands from saved non-price ledgers."""
    module_path = Path(__file__)
    cli_path = module_path.parents[1] / "cli.py"
    if run_dir.name != RUN_NAME:
        raise R032LedgerAuditError(f"only immutable R032 ledger {RUN_NAME} is accepted")
    paths = {
        "campaign_manifest": run_dir / "campaign_manifest.json",
        "preflight": run_dir / "preflight.json",
        "diagnostic": run_dir / "gap_direction_0tick_diagnostic.json",
        "bootstrap": run_dir / "bootstrap.json",
        "b0_events": run_dir / "diagnostics" / "B_all_gap_follow_0tick" / "events.parquet",
    }
    absent = [name for name, path in paths.items() if not path.is_file()]
    if absent:
        raise R032LedgerAuditError(f"missing required R032 artifacts: {', '.join(absent)}")
    campaign, preflight, diagnostic, bootstrap = (
        _read_json(paths["campaign_manifest"]),
        _read_json(paths["preflight"]),
        _read_json(paths["diagnostic"]),
        _read_json(paths["bootstrap"]),
    )
    if campaign.get("oos") != "NOT_EVALUATED" or campaign.get("final_holdout") != "NOT_ACCESSED":
        raise R032LedgerAuditError("R032 input claims an impermissible OOS or Final Holdout state")
    targets = preflight.get("fixed_target_night_trade_dates")
    if not isinstance(targets, list) or not targets or not all(isinstance(day, str) for day in targets):
        raise R032LedgerAuditError("R032 fixed target-night axis is absent")
    target_days = tuple(targets)
    if len(set(target_days)) != len(target_days):
        raise R032LedgerAuditError("R032 target-night axis has duplicate trade dates")
    schema = pl.read_parquet_schema(paths["b0_events"])
    absent_columns = [column for column in EVENT_COLUMNS if column not in schema]
    if absent_columns:
        raise R032LedgerAuditError(f"R032 B0 events lack columns: {', '.join(absent_columns)}")
    rows = pl.read_parquet(paths["b0_events"], columns=list(EVENT_COLUMNS)).to_dicts()
    daily = {status: dict.fromkeys(target_days, 0) for status in ("confirmed", "nonconfirmed")}
    totals: dict[str, dict[str, int | float | None]] = {
        status: {"count": 0, "gross_pnl_jpy": 0} for status in daily
    }
    for row in rows:
        status, fill_status, day, gross = (
            row["base_event_status"],
            row["status"],
            row["trade_date"],
            row["gross_pnl_jpy"],
        )
        if status not in daily or fill_status != "filled":
            continue
        if not isinstance(day, str) or day not in daily[status] or not isinstance(gross, int):
            raise R032LedgerAuditError("R032 B0 filled event is outside the fixed axis or lacks gross PnL")
        daily[status][day] += gross
        count = totals[status]["count"]
        total = totals[status]["gross_pnl_jpy"]
        if not isinstance(count, int) or not isinstance(total, int):
            raise R032LedgerAuditError("R032 aggregate ledger types are invalid")
        totals[status]["count"] = count + 1
        totals[status]["gross_pnl_jpy"] = total + gross
    for status, values in totals.items():
        count = values["count"]
        gross_total = values["gross_pnl_jpy"]
        if not isinstance(count, int) or not isinstance(gross_total, int):
            raise R032LedgerAuditError("R032 aggregate ledger types are invalid")
        values["gross_expectancy_jpy"] = gross_total / count if count else None
        saved = diagnostic.get(status)
        if not isinstance(saved, dict):
            raise R032LedgerAuditError(f"R032 diagnostic lacks {status}")
        if saved.get("trade_count") != count or saved.get("gross_pnl_jpy") != values["gross_pnl_jpy"]:
            raise R032LedgerAuditError(f"R032 diagnostic does not match B0 {status} ledger")
        saved_expectancy = saved.get("gross_expectancy_jpy")
        if not isinstance(saved_expectancy, (int, float)) or values["gross_expectancy_jpy"] is None:
            raise R032LedgerAuditError(f"R032 diagnostic lacks {status} gross expectancy")
        _assert_close(float(values["gross_expectancy_jpy"]), float(saved_expectancy), status)
    confirmed_expectancy = totals["confirmed"]["gross_expectancy_jpy"]
    nonconfirmed_expectancy = totals["nonconfirmed"]["gross_expectancy_jpy"]
    if not isinstance(confirmed_expectancy, (int, float)) or not isinstance(
        nonconfirmed_expectancy, (int, float)
    ):
        raise R032LedgerAuditError("R032 diagnostic has no filled confirmation groups")
    display_difference = float(confirmed_expectancy) - float(nonconfirmed_expectancy)
    raw_confirmed = [daily["confirmed"][day] for day in target_days]
    raw_nonconfirmed = [daily["nonconfirmed"][day] for day in target_days]
    repetitions = bootstrap.get("repetitions")
    seed = bootstrap.get("seed")
    block = bootstrap.get("block_length_trade_dates")
    if not isinstance(repetitions, int) or not isinstance(seed, int) or not isinstance(block, int):
        raise R032LedgerAuditError("R032 bootstrap parameters are not integer values")
    actual_bootstrap = _bootstrap_difference(raw_confirmed, raw_nonconfirmed, repetitions, seed, block)
    saved_bootstrap = bootstrap.get("confirmed_minus_nonconfirmed_gap_direction_0tick_gross_daily_mean_jpy")
    if not isinstance(saved_bootstrap, dict):
        raise R032LedgerAuditError("R032 bootstrap lacks the confirmation difference")
    saved_ci = saved_bootstrap.get("ci95_percentile_linear")
    actual_estimate = actual_bootstrap["estimate"]
    actual_ci = actual_bootstrap["ci95_percentile_linear"]
    if not isinstance(actual_estimate, (int, float)) or not (
        isinstance(actual_ci, list) and len(actual_ci) == 2 and all(isinstance(value, (int, float)) for value in actual_ci)
    ):
        raise R032LedgerAuditError("R032 reconstructed bootstrap output is invalid")
    if not isinstance(saved_bootstrap.get("estimate"), (int, float)) or not (
        isinstance(saved_ci, list) and len(saved_ci) == 2 and all(isinstance(value, (int, float)) for value in saved_ci)
    ):
        raise R032LedgerAuditError("R032 bootstrap difference has invalid output fields")
    _assert_close(float(actual_estimate), float(saved_bootstrap["estimate"]), "bootstrap estimate")
    for index, actual in enumerate(actual_ci):
        _assert_close(float(actual), float(saved_ci[index]), f"bootstrap CI[{index}]")
    return {
        "audit_manifest": {
            "audit_id": audit_id,
            "scope": "R3-A R032 saved-ledger reconciliation only",
            "commit": commit,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "environment": {"python": sys.version, "platform": platform.platform()},
            "input_sha256": {str(path): _sha256(path) for path in paths.values()},
            "code_sha256": {
                "src/n225m_bt/research/r032_ledger_audit.py": _sha256(module_path),
                "src/n225m_bt/cli.py": _sha256(cli_path),
            },
        },
        "access_ledger": {
            "status": "PASS",
            "allowed_reads": [str(path) for path in paths.values()],
            "parquet_columns_read": {str(paths["b0_events"]): list(EVENT_COLUMNS)},
            "not_read": ["raw/Silver/Gold bars", "features", "OOS", "Final Holdout", "price columns"],
        },
        "ledger_integrity": {
            "status": "PASS",
            "fixed_target_night_count": len(target_days),
            "B0_filled_ledger": totals,
            "diagnostic_matches_saved_B0_events": True,
            "bootstrap_matches_saved_fixed_axis_and_seed": True,
        },
        "metric_reconciliation": {
            "status": "RECONCILED_AS_DIFFERENT_ESTIMANDS",
            "displayed_metric": {
                "unit": "JPY_PER_TRADE",
                "confirmed_expectancy": totals["confirmed"]["gross_expectancy_jpy"],
                "nonconfirmed_expectancy": totals["nonconfirmed"]["gross_expectancy_jpy"],
                "confirmed_minus_nonconfirmed": display_difference,
            },
            "bootstrap_metric": {
                "unit": "JPY_PER_TARGET_NIGHT",
                "estimate": actual_bootstrap["estimate"],
                "ci95_percentile_linear": actual_ci,
                "target_nights_including_no_trade_zero": len(target_days),
                "method": bootstrap.get("method"),
                "seed": seed,
                "block_length_trade_dates": block,
                "repetitions": repetitions,
            },
            "conclusion": (
                "The two displayed differences are reproduced from their named ledgers, but "
                "they are not interchangeable because their denominators and weighting differ."
            ),
        },
        "scope_and_dependencies": {
            "R3-A": {"status": "COMPLETE", "scope": "R032 ledger reconciliation"},
            "R2": {"status": "PASS_LIMITED", "scope": "limited Development diagnostics only"},
            "R065": {
                "status": "BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED",
                "blocked_dependency": "separate_r065_executor",
            },
        },
    }


def write_r032_ledger_audit(run_dir: Path, output: Path, audit_id: str, commit: str) -> Path:
    """Create one exclusive R3-A audit directory from the immutable saved ledger."""
    records = audit_r032_ledger(run_dir, audit_id, commit)
    output.mkdir(parents=True, exist_ok=False)
    for name, value in records.items():
        (output / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (output / "summary.md").write_text(
        "# R3-A R032 ledger reconciliation\n\n"
        "Status: COMPLETE. Per-trade and per-target-night differences were both reproduced "
        "from their saved ledgers and are different estimands. No price data was read.\n",
        encoding="utf-8",
    )
    return output
