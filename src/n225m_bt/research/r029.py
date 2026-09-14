"""Causal TSE-lunch directional-confirmation events for R029-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar


def tse_lunch_confirmation_event(
    trade_date: date,
    day_bars: list[Bar] | None,
    cash_calendar: TSECashMarketCalendar,
    *,
    day_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Select an event using no price later than the 12:34 JST bar close."""
    start = datetime(trade_date.year, trade_date.month, trade_date.day, 11, 30, tzinfo=JST)
    confirmation_start = start + timedelta(minutes=60)
    signal = confirmation_start + timedelta(minutes=4)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "P_window_start_jst": start.isoformat(),
        "P_window_end_jst": (confirmation_start - timedelta(minutes=1)).isoformat(),
        "Q_window_start_jst": confirmation_start.isoformat(),
        "Q_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": (signal + timedelta(minutes=1)).isoformat(),
        "EXIT_signal_bar_start_jst": (signal + timedelta(minutes=25)).isoformat(),
        "X_planned_exit_jst": (signal + timedelta(minutes=26)).isoformat(),
        "required_P_window_bars": 60,
        "required_Q_window_bars": 5,
        "status": "skipped",
    }
    if not development_start <= trade_date <= development_end:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
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
    p_rows = [by_time.get(start + timedelta(minutes=index)) for index in range(60)]
    q_rows = [by_time.get(confirmation_start + timedelta(minutes=index)) for index in range(5)]
    if any(row is None for row in [*p_rows, *q_rows]):
        event["reason"] = "WINDOW_1130_TO_1234_MISSING"
        return event
    rows = [row for row in [*p_rows, *q_rows] if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY for row in rows):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    p_window, q_window = [row for row in p_rows if row is not None], [row for row in q_rows if row is not None]
    p, q = p_window[-1].close - p_window[0].open, q_window[-1].close - q_window[0].open
    event.update({
        "P_open_1130_points": p_window[0].open,
        "P_close_1229_points": p_window[-1].close,
        "Q_open_1230_points": q_window[0].open,
        "Q_close_1234_points": q_window[-1].close,
        "P_points": p,
        "Q_points": q,
    })
    if p == 0:
        event["reason"] = "ZERO_P"
        return event
    if q == 0:
        event["reason"] = "ZERO_Q"
        return event
    p_direction = "long" if p > 0 else "short"
    event.update({"P_sign": 1 if p > 0 else -1, "Q_sign": 1 if q > 0 else -1, "P_direction": p_direction})
    if (p > 0) == (q > 0):
        event.update({"status": "confirmed", "reason": "P_Q_SAME_SIGN", "A_direction": p_direction, "F_reverse_direction": "short" if p_direction == "long" else "long"})
    else:
        event.update({"status": "nonconfirmed", "reason": "P_Q_OPPOSITE_SIGN"})
    return event
