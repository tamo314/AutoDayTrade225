"""Causal cash-close-window event selection for R021-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar


def tse_cash_close(trade_date: date) -> datetime:
    """Official TSE cash close used by R021, never inferred from futures bars."""
    clock = (15, 0) if trade_date <= date(2024, 11, 1) else (15, 30)
    return datetime(trade_date.year, trade_date.month, trade_date.day, *clock, tzinfo=JST)


def opening_to_cash_close_event(
    trade_date: date, bars: list[Bar], cash_calendar: TSECashMarketCalendar
) -> dict[str, object]:
    """Select R021's one pre-scheduled day event using only O/M windows."""
    close = tse_cash_close(trade_date)
    entry = close - timedelta(minutes=30)
    signal = entry - timedelta(minutes=1)
    momentum_start = entry - timedelta(minutes=30)
    opening_start = datetime(trade_date.year, trade_date.month, trade_date.day, 9, tzinfo=JST)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "C_cash_close_jst": close.isoformat(),
        "O_window_start_jst": opening_start.isoformat(),
        "O_window_end_jst": (opening_start + timedelta(minutes=29)).isoformat(),
        "M_window_start_jst": momentum_start.isoformat(),
        "M_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": (close - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": close.isoformat(),
        "required_window_bars": 30,
        "status": "skipped",
    }
    if not cash_calendar.is_open(trade_date):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    by_time = {bar.ts_jst: bar for bar in bars}
    opening_times = [opening_start + timedelta(minutes=i) for i in range(30)]
    momentum_times = [momentum_start + timedelta(minutes=i) for i in range(30)]
    opening_rows = [by_time.get(stamp) for stamp in opening_times]
    momentum_rows = [by_time.get(stamp) for stamp in momentum_times]
    if any(row is None for row in opening_rows):
        event["reason"] = "O_WINDOW_0900_TO_0929_MISSING"
        return event
    if any(row is None for row in momentum_rows):
        event["reason"] = "M_WINDOW_E_MINUS_30_TO_E_MINUS_1_MISSING"
        return event
    rows = [row for row in [*opening_rows, *momentum_rows] if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY
        for row in rows
    ):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    o_rows = [row for row in opening_rows if row is not None]
    m_rows = [row for row in momentum_rows if row is not None]
    opening = o_rows[-1].close - o_rows[0].open
    momentum = m_rows[-1].close - m_rows[0].open
    event.update(
        {
            "open_0900_points": o_rows[0].open,
            "close_0929_points": o_rows[-1].close,
            "open_M_points": m_rows[0].open,
            "close_E_minus_1_points": m_rows[-1].close,
            "O_points": opening,
            "M_points": momentum,
        }
    )
    if opening == 0:
        event["reason"] = "ZERO_O"
        return event
    if momentum == 0:
        event["reason"] = "ZERO_M"
        return event
    o_follow = "long" if opening > 0 else "short"
    m_follow = "long" if momentum > 0 else "short"
    event.update(
        {
            "status": "eligible",
            "reason": "TSE_OPEN_AND_O_M_NONZERO",
            "A_direction": o_follow,
            "D_direction": m_follow,
            "F_reverse_direction": "short" if o_follow == "long" else "long",
        }
    )
    return event
