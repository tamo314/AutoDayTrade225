"""Causal pre-cash/cash-open conflict events for R043-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar


def precash_cash_conflict_event(
    trade_date: date,
    day_bars: list[Bar] | None,
    cash_calendar: TSECashMarketCalendar,
    *,
    day_quarantined: bool = False,
) -> dict[str, object]:
    """Classify the exact two R043 windows using information through 09:04 only."""
    precash = datetime(trade_date.year, trade_date.month, trade_date.day, 8, 45, tzinfo=JST)
    cash = precash + timedelta(minutes=15)
    signal = cash + timedelta(minutes=4)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "precash_window_start_jst": precash.isoformat(),
        "precash_window_end_jst": (precash + timedelta(minutes=14)).isoformat(),
        "cash_window_start_jst": cash.isoformat(),
        "cash_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": (signal + timedelta(minutes=1)).isoformat(),
        "EXIT_signal_bar_start_jst": (signal + timedelta(minutes=60)).isoformat(),
        "X_planned_exit_jst": (signal + timedelta(minutes=61)).isoformat(),
        "required_precash_window_bars": 15,
        "required_cash_window_bars": 5,
        "status": "skipped",
    }
    if not cash_calendar.is_open(trade_date):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if day_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    if day_bars is None:
        event["reason"] = "DAY_SESSION_MISSING"
        return event
    by_time = {bar.ts_jst: bar for bar in day_bars}
    precash_rows = [by_time.get(precash + timedelta(minutes=offset)) for offset in range(15)]
    cash_rows = [by_time.get(cash + timedelta(minutes=offset)) for offset in range(5)]
    if any(row is None for row in precash_rows):
        event["reason"] = "WINDOW_0845_TO_0859_MISSING"
        return event
    if any(row is None for row in cash_rows):
        event["reason"] = "WINDOW_0900_TO_0904_MISSING"
        return event
    rows = [row for row in [*precash_rows, *cash_rows] if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY
        for row in rows
    ):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    p_rows = [row for row in precash_rows if row is not None]
    c_rows = [row for row in cash_rows if row is not None]
    r_p = p_rows[-1].close - p_rows[0].open
    r_c = c_rows[-1].close - c_rows[0].open
    event.update(
        {
            "open_0845_points": p_rows[0].open,
            "close_0859_points": p_rows[-1].close,
            "open_0900_points": c_rows[0].open,
            "close_0904_points": c_rows[-1].close,
            "rP_points": r_p,
            "rC_points": r_c,
        }
    )
    if r_p == 0:
        event["reason"] = "ZERO_rP"
        return event
    if r_c == 0:
        event["reason"] = "ZERO_rC"
        return event
    p_direction = "long" if r_p > 0 else "short"
    c_direction = "long" if r_c > 0 else "short"
    event.update(
        {
            "rP_sign": 1 if r_p > 0 else -1,
            "rC_sign": 1 if r_c > 0 else -1,
            "rP_direction": p_direction,
            "rC_direction": c_direction,
        }
    )
    if r_p * r_c < 0:
        event.update(
            {
                "status": "conflict",
                "reason": "rP_rC_OPPOSITE_SIGN",
                "A_direction": c_direction,
                "F_precash_direction": p_direction,
            }
        )
    else:
        event.update({"status": "agreement", "reason": "rP_rC_SAME_SIGN"})
    return event
