"""Causal 08:59-close / 09:00-open discontinuity events for R028-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar


def cash_open_discontinuity_event(
    trade_date: date,
    day_bars: list[Bar] | None,
    cash_calendar: TSECashMarketCalendar,
    *,
    day_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Select an event using the 09:00 bar *open* and no later price fields."""
    start = datetime(trade_date.year, trade_date.month, trade_date.day, 8, 45, tzinfo=JST)
    signal = start + timedelta(minutes=15)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "O_bar_start_jst": start.isoformat(),
        "C_bar_start_jst": (signal - timedelta(minutes=1)).isoformat(),
        "K_bar_start_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": (signal + timedelta(minutes=1)).isoformat(),
        "EXIT_signal_bar_start_jst": (signal + timedelta(minutes=15)).isoformat(),
        "X_planned_exit_jst": (signal + timedelta(minutes=16)).isoformat(),
        "required_window_bars": 16,
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
    rows = [by_time.get(start + timedelta(minutes=offset)) for offset in range(16)]
    if any(row is None for row in rows):
        event["reason"] = "WINDOW_0845_TO_0900_MISSING"
        return event
    window = [row for row in rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY for row in window):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    opening, close_0859, open_0900 = window[0].open, window[14].close, window[15].open
    p, jump = close_0859 - opening, open_0900 - close_0859
    event.update({
        "O_open_0845_points": opening,
        "C_close_0859_points": close_0859,
        "K_open_0900_points": open_0900,
        "P_points": p,
        "J_points": jump,
    })
    if p == 0:
        event["reason"] = "ZERO_P"
        return event
    if jump == 0:
        event["reason"] = "ZERO_J"
        return event
    jump_follow = "long" if jump > 0 else "short"
    preopen_follow = "long" if p > 0 else "short"
    event.update({
        "status": "eligible",
        "reason": "TSE_OPEN_P_AND_J_NONZERO",
        "P_sign": 1 if p > 0 else -1,
        "J_sign": 1 if jump > 0 else -1,
        "A_direction": "short" if jump_follow == "long" else "long",
        "D_follow_direction": jump_follow,
        "F_preopen_direction": "short" if preopen_follow == "long" else "long",
    })
    return event
