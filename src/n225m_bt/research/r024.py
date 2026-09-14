"""Causal 08:45/09:00 directional-confirmation events for R024-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar


def cash_open_confirmation_event(
    trade_date: date,
    day_bars: list[Bar] | None,
    cash_calendar: TSECashMarketCalendar,
    *,
    day_quarantined: bool = False,
) -> dict[str, object]:
    """Select one event using only the close of the 09:04 JST bar.

    P is the futures-only 08:45--08:59 change and Q is the futures-only
    09:00--09:04 change.  No cash price is read or inferred.
    """
    first_start = datetime(trade_date.year, trade_date.month, trade_date.day, 8, 45, tzinfo=JST)
    second_start = first_start + timedelta(minutes=15)
    signal = second_start + timedelta(minutes=4)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "P_window_start_jst": first_start.isoformat(),
        "P_window_end_jst": (first_start + timedelta(minutes=14)).isoformat(),
        "Q_window_start_jst": second_start.isoformat(),
        "Q_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": (signal + timedelta(minutes=1)).isoformat(),
        "EXIT_signal_bar_start_jst": (signal + timedelta(minutes=25)).isoformat(),
        "X_planned_exit_jst": (signal + timedelta(minutes=26)).isoformat(),
        "required_P_window_bars": 15,
        "required_Q_window_bars": 5,
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
    p_rows = [by_time.get(first_start + timedelta(minutes=offset)) for offset in range(15)]
    q_rows = [by_time.get(second_start + timedelta(minutes=offset)) for offset in range(5)]
    if any(row is None for row in p_rows):
        event["reason"] = "WINDOW_0845_TO_0859_MISSING"
        return event
    if any(row is None for row in q_rows):
        event["reason"] = "WINDOW_0900_TO_0904_MISSING"
        return event
    rows = [row for row in [*p_rows, *q_rows] if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY for row in rows):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    p_concrete, q_concrete = [row for row in p_rows if row is not None], [row for row in q_rows if row is not None]
    p = p_concrete[-1].close - p_concrete[0].open
    q = q_concrete[-1].close - q_concrete[0].open
    event.update({
        "P_open_0845_points": p_concrete[0].open,
        "P_close_0859_points": p_concrete[-1].close,
        "Q_open_0900_points": q_concrete[0].open,
        "Q_close_0904_points": q_concrete[-1].close,
        "P_points": p,
        "Q_points": q,
    })
    if p == 0:
        event["reason"] = "ZERO_P"
        return event
    if q == 0:
        event["reason"] = "ZERO_Q"
        return event
    q_direction = "long" if q > 0 else "short"
    event.update({"P_sign": 1 if p > 0 else -1, "Q_sign": 1 if q > 0 else -1, "Q_direction": q_direction})
    if (p > 0) == (q > 0):
        event.update({"status": "confirmed", "reason": "P_Q_SAME_SIGN", "A_direction": q_direction, "F_reverse_direction": "short" if q_direction == "long" else "long"})
    else:
        event.update({"status": "nonconfirmed", "reason": "P_Q_OPPOSITE_SIGN"})
    return event
