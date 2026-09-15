"""PnL-free K-order audit and sole gate revision for TASK-R094-Q002."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import cast

_CANCEL_REASONS = {
    "FIRST_NORMAL_NIGHT_ENTRY_OPEN_MISSING_OR_INELIGIBLE": 10,
    "R004_FOLLOWING_NIGHT_SESSION_QUARANTINED": 1,
}
_BANDS = ("0.50_to_0.75", "0.75_to_1.00")


def _regime(row: dict[str, object]) -> str:
    return (
        "new"
        if "from_20241105" in str(row.get("night_schedule_version", ""))
        else "old"
    )


def _band(row: dict[str, object]) -> str | None:
    percentile = float(cast(float, row.get("current_x_rolling_percentile", -1.0)))
    if 0.50 <= percentile < 0.75:
        return _BANDS[0]
    if 0.75 <= percentile <= 1.00:
        return _BANDS[1]
    return None


def k_order_audit(events: list[dict[str, object]]) -> dict[str, object]:
    """Summarize only K availability/order labels; no price or performance fields."""
    k_rows = [row for row in events if row.get("state") == "K"]
    orders = [row for row in k_rows if bool(row.get("entry_order_submitted"))]
    complete = [row for row in orders if row.get("status") == "EXECUTABLE"]
    cancelled = [row for row in orders if row.get("status") == "ENTRY_CANCELLED"]
    unresolved = [row for row in orders if row.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"]

    def counts(items: list[dict[str, object]], key: str) -> dict[str, int]:
        return dict(sorted(Counter(str(row[key]) for row in items).items()))

    cash_direction = Counter(
        "positive"
        if cast(int, cast(dict[str, object], row["observation"])["r_sign"]) > 0
        else "negative"
        for row in complete
    )
    bands = {band: 0 for band in _BANDS}
    for row in complete:
        if (label := _band(row)) is not None:
            bands[label] += 1
    cancel_reasons = counts(cancelled, "reason")
    checks = {
        "all_K_states_submit_before_following_night_resolution": len(orders) == len(k_rows),
        "K_order_accounting_complete_cancelled_unresolved": len(orders)
        == len(complete) + len(cancelled) + len(unresolved),
        "K_submitted_orders_exactly_254": len(orders) == 254,
        "K_completed_exactly_243": len(complete) == 243,
        "K_entry_cancellations_exactly_11": len(cancelled) == 11,
        "K_cancellation_reasons_exactly_explainable": cancel_reasons == _CANCEL_REASONS,
        "K_filled_unresolved_exits_equal_zero": not unresolved,
    }
    return {
        "scope": "PnL-free K order/completion availability audit",
        "K_state_days": len(k_rows),
        "K_submitted_orders": len(orders),
        "K_completed": len(complete),
        "K_entry_cancelled": len(cancelled),
        "K_filled_exit_unknown": len(unresolved),
        "K_entry_cancellations_by_reason": cancel_reasons,
        "K_completed_by_cash_year": counts(complete, "trade_date_year")
        if complete and "trade_date_year" in complete[0]
        else dict(
            sorted(
                Counter(str(date.fromisoformat(cast(str, row["trade_date"])).year) for row in complete).items()
            )
        ),
        "K_completed_by_cash_direction": dict(sorted(cash_direction.items())),
        "K_completed_by_ose_regime": dict(sorted(Counter(_regime(row) for row in complete).items())),
        "K_completed_by_rolling_x_band": bands,
        "checks": checks,
        "passed": all(checks.values()),
    }


def revised_gate(q001_feasibility: dict[str, object]) -> dict[str, object]:
    """Replace only R094-Q001's K >=250 availability condition by K >=240."""
    original = cast(dict[str, bool], q001_feasibility["gate"])
    gate = {key: value for key, value in original.items() if key not in {"passed", "K_completed_at_least_250"}}
    gate["K_completed_at_least_240"] = int(cast(int, q001_feasibility["K_completed"])) >= 240
    return {
        **q001_feasibility,
        "scope": "PnL-free Q002 gate; only K completion lower bound revised from 250 to 240",
        "q002_gate_revision": {"q001_minimum": 250, "q002_minimum": 240},
        "gate": gate | {"passed": all(gate.values())},
    }
