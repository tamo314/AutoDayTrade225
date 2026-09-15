"""PnL-free causal event construction for TASK-R092-Q001."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import cast

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r057 import LOOKBACK, MIN_REFERENCES, nearest_rank, tse_normal_close
from n225m_bt.research.r091_fixed_tse_short import tse_cash_close


def _valid(row: Bar | None, target: date, *, close: bool = False) -> bool:
    price = row.close if close and row is not None else row.open if row is not None else 0
    return bool(
        row is not None
        and row.is_eligible
        and row.trade_date == target
        and row.session is Session.DAY
        and price > 0
    )


def _observation(
    target: date,
    previous: date | None,
    target_rows: list[Bar] | None,
    previous_rows: list[Bar] | None,
) -> dict[str, object]:
    """Return R057's unchanged p/o/g/x observation without a confirmation path."""
    if previous is None:
        return {"status": "invalid", "reason": "PREVIOUS_TSE_SESSION_NOT_UNIQUE"}
    target_by_time = {row.ts_jst: row for row in target_rows or []}
    previous_by_time = {row.ts_jst: row for row in previous_rows or []}
    opening = datetime.combine(target, time(9), JST)
    prior_last = datetime.combine(previous, tse_normal_close(previous), JST) - timedelta(minutes=1)
    p_row, o_row = previous_by_time.get(prior_last), target_by_time.get(opening)
    if not _valid(p_row, previous, close=True):
        return {"status": "invalid", "reason": "PREVIOUS_TSE_FINAL_BAR_INVALID"}
    if not _valid(o_row, target):
        return {"status": "invalid", "reason": "OPENING_BAR_INVALID"}
    p, o = cast(Bar, p_row).close, cast(Bar, o_row).open
    g = (o - p) / p
    return {
        "status": "valid",
        "p_points": p,
        "o_points": o,
        "g": g,
        "x": abs(g),
        "gap_sign": 1 if g > 0 else -1 if g < 0 else 0,
    }


def r092_event(
    target: date,
    target_rows: list[Bar] | None,
    previous_day: date | None,
    previous_rows: list[Bar] | None,
    history: Iterable[tuple[date, date | None, list[Bar] | None, list[Bar] | None, bool]],
    cash_calendar: TSECashMarketCalendar,
    *,
    quarantined: bool = False,
    entry_time: time = time(9, 1),
    exit_minus_minutes: int = 0,
) -> dict[str, object]:
    """Build E_exec before inspecting its future exit and then settle its path."""
    entry_open = datetime.combine(target, entry_time, JST)
    exit_open = tse_cash_close(target) - timedelta(minutes=exit_minus_minutes)
    row: dict[str, object] = {
        "trade_date": target.isoformat(),
        "schedule_id": "R092-R057-PREVIOUS-CLOSE-OPEN-GAP-1",
        "lookback_scheduled_tse_days": LOOKBACK,
        "minimum_valid_x": MIN_REFERENCES,
        "submit_jst": (entry_open - timedelta(minutes=1)).isoformat(),
        "entry_decision_jst": (entry_open - timedelta(minutes=1)).isoformat(),
        "entry_fill_planned_jst": entry_open.isoformat(),
        "exit_decision_jst": (exit_open - timedelta(minutes=1)).isoformat(),
        "exit_fill_planned_jst": exit_open.isoformat(),
        "tse_cash_close_jst": tse_cash_close(target).isoformat(),
        "entry_eligible": False,
        "entry_capable": False,
        "order_submitted": False,
        "completion_status": "NOT_EVALUATED",
        "status": "skipped",
    }
    if not cash_calendar.is_open(target):
        row.update(
            reason="TSE_CASH_MARKET_CLOSED", completion_status="NO_ORDER_TSE_CASH_MARKET_CLOSED"
        )
        return row
    if quarantined:
        row.update(
            reason="R004_DAY_SESSION_QUARANTINED",
            completion_status="NO_ORDER_R004_DAY_SESSION_QUARANTINED",
        )
        return row
    references = list(history)
    if len(references) != LOOKBACK:
        row.update(
            reason="HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS",
            completion_status="NO_ORDER_HISTORY_INSUFFICIENT",
        )
        return row
    observation = _observation(target, previous_day, target_rows, previous_rows)
    if observation["status"] != "valid":
        row.update(
            observation=observation,
            reason="TARGET_GAP_INVALID",
            completion_status="NO_ORDER_TARGET_GAP_INVALID",
        )
        return row
    values: list[float] = []
    for day, prior, rows, prior_rows, isolated in references:
        if isolated:
            continue
        item = _observation(day, prior, rows, prior_rows)
        if item["status"] == "valid":
            values.append(float(cast(float, item["x"])))
    row.update(
        observation=observation,
        reference_trade_dates=[day.isoformat() for day, *_ in references],
        reference_valid_x_count=len(values),
    )
    if len(values) < MIN_REFERENCES:
        row.update(
            reason="INSUFFICIENT_VALID_X_REFERENCES",
            completion_status="NO_ORDER_INSUFFICIENT_VALID_X",
        )
        return row
    row.update(
        q70=nearest_rank(values, 70), q75=nearest_rank(values, 75), q80=nearest_rank(values, 80)
    )
    obs = observation
    if int(cast(int, obs["gap_sign"])) == 0:
        row.update(reason="ZERO_GAP", completion_status="NO_ORDER_ZERO_GAP")
        return row
    lookup = {bar.ts_jst: bar for bar in target_rows or []}
    # This pair is all data available at entry submit/fill; no exit lookup above this line.
    if not _valid(lookup.get(entry_open - timedelta(minutes=1)), target, close=True):
        row.update(
            reason="ENTRY_DECISION_UNAVAILABLE",
            completion_status="NO_ORDER_ENTRY_DECISION_UNAVAILABLE",
        )
        return row
    if not _valid(lookup.get(entry_open), target):
        row.update(
            reason="ENTRY_FILL_UNAVAILABLE", completion_status="NO_ORDER_ENTRY_FILL_UNAVAILABLE"
        )
        return row
    row["entry_capable"] = True
    if float(cast(float, obs["x"])) >= float(cast(float, row["q75"])):
        row.update(
            status="E_exec",
            reason="EXECUTABLE_ORDER_SUBMITTED",
            entry_eligible=True,
            order_submitted=True,
        )
    else:
        row.update(reason="X_BELOW_Q75", completion_status="NO_ORDER_X_BELOW_Q75")
    if not _valid(lookup.get(exit_open - timedelta(minutes=1)), target, close=True):
        row.update(completion_status="UNRESOLVED_EXIT_DECISION")
        return row
    if not _valid(lookup.get(exit_open), target):
        row.update(completion_status="UNRESOLVED_EXIT_FILL")
        return row
    row["completion_status"] = "COMPLETE"
    if bool(row["entry_eligible"]):
        row["reason"] = "SCHEDULED_PATH_COMPLETE"
    return row


def selected(event: dict[str, object], quantile: int = 75) -> bool:
    """A/B/C/D share precisely the same, decision-time E predicate."""
    if not bool(event.get("entry_capable")) or event.get("completion_status") != "COMPLETE":
        return False
    observation = cast(dict[str, object], event["observation"])
    return float(cast(float, observation["x"])) >= float(cast(float, event[f"q{quantile}"]))
