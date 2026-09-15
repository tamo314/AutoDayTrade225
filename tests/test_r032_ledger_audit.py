"""Synthetic tests for the saved-ledger-only R3-A audit."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from n225m_bt.research.r032_ledger_audit import (
    RUN_NAME,
    R032LedgerAuditError,
    _bootstrap_difference,
    audit_r032_ledger,
)


def _write_fixture(root: Path) -> Path:
    run_dir = root / RUN_NAME
    events_dir = run_dir / "diagnostics" / "B_all_gap_follow_0tick"
    events_dir.mkdir(parents=True, exist_ok=True)
    days = [f"2021-01-{day:02d}" for day in range(1, 21)]
    confirmed = {days[0]: 100, days[3]: -20}
    nonconfirmed = {days[1]: 40, days[3]: 10}
    rows = [
        {"trade_date": day, "base_event_status": status, "status": "filled", "gross_pnl_jpy": pnl}
        for status, values in (("confirmed", confirmed), ("nonconfirmed", nonconfirmed))
        for day, pnl in values.items()
    ]
    pl.DataFrame(rows).write_parquet(events_dir / "events.parquet")
    result = _bootstrap_difference(
        [confirmed.get(day, 0) for day in days], [nonconfirmed.get(day, 0) for day in days], 7, 17, 4
    )
    (run_dir / "campaign_manifest.json").write_text(
        json.dumps({"oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}), encoding="utf-8"
    )
    (run_dir / "preflight.json").write_text(
        json.dumps({"fixed_target_night_trade_dates": days}), encoding="utf-8"
    )
    (run_dir / "gap_direction_0tick_diagnostic.json").write_text(
        json.dumps(
            {
                "confirmed": {"trade_count": 2, "gross_pnl_jpy": 80, "gross_expectancy_jpy": 40.0},
                "nonconfirmed": {"trade_count": 2, "gross_pnl_jpy": 50, "gross_expectancy_jpy": 25.0},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "bootstrap.json").write_text(
        json.dumps(
            {
                "repetitions": 7,
                "seed": 17,
                "block_length_trade_dates": 4,
                "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
                "confirmed_minus_nonconfirmed_gap_direction_0tick_gross_daily_mean_jpy": result,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_r032_ledger_audit_reconciles_different_denominators(workspace_tmp: Path) -> None:
    report = audit_r032_ledger(_write_fixture(workspace_tmp), "R3A-SYNTH", "commit")
    reconciliation = report["metric_reconciliation"]
    assert reconciliation["status"] == "RECONCILED_AS_DIFFERENT_ESTIMANDS"
    assert reconciliation["displayed_metric"]["confirmed_minus_nonconfirmed"] == 15.0
    assert reconciliation["bootstrap_metric"]["unit"] == "JPY_PER_TARGET_NIGHT"


def test_r032_ledger_audit_rejects_mismatched_saved_diagnostic(workspace_tmp: Path) -> None:
    run_dir = _write_fixture(workspace_tmp)
    diagnostic = json.loads((run_dir / "gap_direction_0tick_diagnostic.json").read_text(encoding="utf-8"))
    diagnostic["confirmed"]["gross_pnl_jpy"] = 999
    (run_dir / "gap_direction_0tick_diagnostic.json").write_text(json.dumps(diagnostic), encoding="utf-8")
    with pytest.raises(R032LedgerAuditError, match="does not match"):
        audit_r032_ledger(run_dir, "R3A-SYNTH", "commit")
