"""Audit EXIT-callback reachability in existing R003-Q001 Development ledgers.

This is deliberately ledger-only: it neither reads normalized prices nor invokes a
backtest.  Parquet reads project timestamp/state columns only.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "results/research/r003-q001-20260913-development-campaign-01"
OUT = ROOT / "results/research/r003-q001-20260913-exit-impact-audit-01"

TRADE_COLUMNS = [
    "trade_id",
    "trade_date",
    "entry_signal_ts",
    "entry_ts",
    "exit_signal_ts",
    "exit_ts",
    "holding_minutes",
    "entry_reason",
    "exit_reason",
    "strategy_id",
    "strategy_version",
    "parameter_hash",
]
ORDER_COLUMNS = ["trade_id", "order_kind", "signal_ts", "fill_ts", "status"]
FILL_COLUMNS = ["trade_id", "fill_kind", "ts_jst"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path, columns: list[str]) -> list[dict[str, Any]]:
    """Read only projected non-price/non-PnL ledger columns."""
    return pq.read_table(path, columns=columns).to_pylist()


def _as_datetime(value: Any) -> datetime:
    assert isinstance(value, datetime)
    return value


def _as_date(value: Any) -> date:
    assert isinstance(value, date)
    return value


def _condition_audit(folder: Path, classifier: CalendarClassifier) -> dict[str, Any]:
    manifest = _json(folder / "run_manifest.json")
    execution = _json(folder / "execution_audit.json")
    trades = _rows(folder / "trades.parquet", TRADE_COLUMNS)
    orders = _rows(folder / "orders.parquet", ORDER_COLUMNS)
    fills = _rows(folder / "fills.parquet", FILL_COLUMNS)
    holding = int(manifest["holding_minutes"])
    reach_count = 0
    forced_or_eod = Counter()
    scheduled_mismatches = 0
    scheduled_after_cutoff_examples: list[dict[str, str]] = []
    closest_callback_margin_minutes: int | None = None
    for trade in trades:
        entry_ts = _as_datetime(trade["entry_ts"])
        trade_date = _as_date(trade["trade_date"])
        classified = classifier.classify_timestamp(entry_ts, trade_date)
        close = classifier.session_close(trade_date, classified.session)
        cutoff = close - timedelta(minutes=15)
        force_flat = close - timedelta(minutes=5)
        scheduled = entry_ts + timedelta(minutes=holding - 1)
        margin = int((cutoff - scheduled).total_seconds() // 60)
        closest_callback_margin_minutes = (
            margin
            if closest_callback_margin_minutes is None
            else min(closest_callback_margin_minutes, margin)
        )
        if scheduled >= cutoff:
            reach_count += 1
            scheduled_after_cutoff_examples.append(
                {
                    "trade_id": str(trade["trade_id"]),
                    "trade_date": trade_date.isoformat(),
                    "session": classified.session.value,
                    "scheduled_callback_jst": scheduled.isoformat(),
                    "cutoff_jst": cutoff.isoformat(),
                    "force_flat_jst": force_flat.isoformat(),
                }
            )
        exit_reason = str(trade["exit_reason"])
        if exit_reason in {"force_flat", "end_of_data"}:
            forced_or_eod[exit_reason] += 1
        exit_signal = trade["exit_signal_ts"]
        if exit_reason == "signal" and exit_signal != scheduled:
            scheduled_mismatches += 1
    order_kinds = Counter(str(row["order_kind"]) for row in orders)
    statuses = Counter(str(row["status"]) for row in orders)
    fill_kinds = Counter(str(row["fill_kind"]) for row in fills)
    expected_orders = len(trades) * 2
    expected_fills = len(trades) * 2
    return {
        "condition": folder.name,
        "experiment_id": manifest["experiment_id"],
        "strategy_id": manifest.get("strategy_id", None),
        "parameter_hash": trades[0]["parameter_hash"] if trades else None,
        "holding_minutes": holding,
        "trade_count_from_state_ledger": len(trades),
        "entry_search_rule": "strategy code: opening offset 30 <= breakout offset < 120; one entry attempt",
        "scheduled_exit_callback": "actual_entry_fill + (H - 1) minutes; generated at bar close and first eligible fill is next bar open",
        "entry_cutoff": "scheduled_close - 15 minutes",
        "force_flat": "scheduled_close - 5 minutes",
        "max_fill_delay_minutes": 10,
        "scheduled_callback_at_or_after_cutoff_count": reach_count,
        "closest_saved_callback_margin_before_cutoff_minutes": closest_callback_margin_minutes,
        "signal_exit_timestamp_mismatches_from_scheduled_callback": scheduled_mismatches,
        "force_flat_or_end_of_data_exit_reasons": dict(sorted(forced_or_eod.items())),
        "orders": {
            "rows": len(orders),
            "expected_rows": expected_orders,
            "order_kinds": dict(sorted(order_kinds.items())),
            "statuses": dict(sorted(statuses.items())),
            "all_rows_expected_and_filled": len(orders) == expected_orders and statuses == {"filled": expected_orders},
        },
        "fills": {
            "rows": len(fills),
            "expected_rows": expected_fills,
            "fill_kinds": dict(sorted(fill_kinds.items())),
            "all_rows_expected": len(fills) == expected_fills
            and fill_kinds == {"entry": len(trades), "exit": len(trades)},
        },
        "canceled_orders": int(execution["canceled_orders"]),
        "scheduled_after_cutoff_examples": scheduled_after_cutoff_examples,
        "verdict": "UNAFFECTED_PROVEN"
        if reach_count == 0
        and scheduled_mismatches == 0
        and not forced_or_eod
        and int(execution["canceled_orders"]) == 0
        and len(orders) == expected_orders
        and len(fills) == expected_fills
        else "UNRESOLVED",
    }


def main() -> None:
    if not (OUT / "audit_plan.json").exists():
        raise ValueError("audit plan must be frozen before ledger access")
    if (OUT / "ledger_reachability.json").exists():
        raise ValueError("audit output already exists; never overwrite an audit")
    _, sessions, _, _backtest = load_project_config(ROOT / "config")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    )
    rows = [
        _condition_audit(folder, classifier)
        for folder in sorted(path for path in CAMPAIGN.iterdir() if path.is_dir())
    ]
    if len(rows) != 12:
        raise ValueError(f"expected 12 conditions, found {len(rows)}")
    summary = {
        "audit_id": OUT.name,
        "source_campaign": CAMPAIGN.name,
        "ledger_access": {
            "format": "Parquet",
            "schema_access": "footer metadata was inspected first",
            "projected_columns": {
                "trades": TRADE_COLUMNS,
                "orders": ORDER_COLUMNS,
                "fills": FILL_COLUMNS,
            },
            "excluded_columns": "all price, PnL, fee, slippage, equity, and metric columns",
            "physical_access_limitation": "Parquet column projection restricts decoded logical columns, but opening each immutable container can access file metadata and storage blocks; no row-level physical access guarantee is claimed.",
            "market_data_access": "none",
            "OOS_access": "none",
            "Final_Holdout_access": "none",
        },
        "code_identity": {
            "legacy_engine_sha256_from_campaign_manifest": _json(CAMPAIGN / "campaign_manifest.json")["source"]["file_hashes"]["src/n225m_bt/backtest/engine.py"],
            "legacy_compression_sha256_from_campaign_manifest": _json(CAMPAIGN / "campaign_manifest.json")["source"]["file_hashes"]["src/n225m_bt/strategies/compression.py"],
            "corrected_engine_sha256_current": _sha256(ROOT / "src/n225m_bt/backtest/engine.py"),
            "current_compression_sha256": _sha256(ROOT / "src/n225m_bt/strategies/compression.py"),
            "corrected_engine_sha256_from_exit_diagnostic": _json(ROOT / "results/research/r005-q001-20260913-exit-path-diagnostic-01/post_fix_results.json")["code_identity_after_execution"]["src/n225m_bt/backtest/engine.py"],
        },
        "engine_difference": "legacy skips all strategy callbacks at/after new-entry cutoff; corrected code permits callbacks only while a position is held. Pending order fill, delay cancellation, protective execution, force-flat, fill price, slippage, fee, and position accounting are unchanged.",
        "condition_results": rows,
        "all_conditions_unaffected_proven": all(row["verdict"] == "UNAFFECTED_PROVEN" for row in rows),
        "backtest_executed": False,
        "pnl_reaggregated": False,
    }
    (OUT / "ledger_reachability.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
