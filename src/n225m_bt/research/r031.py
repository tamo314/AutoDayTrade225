"""Causal full-prior-night and 09:00 confirmation events for R031-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end


def night_cash_open_confirmation_event(
    classifier: CalendarClassifier,
    cash_calendar: TSECashMarketCalendar,
    trade_date: date,
    day_bars: list[Bar] | None,
    night_bars: list[Bar] | None,
    *,
    day_quarantined: bool = False,
    night_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
) -> dict[str, object]:
    """Use exactly the scheduled night carrying ``trade_date``; never look back.

    ``S`` is the first-open to final-normal-close price change of that complete
    night.  ``Q`` is the 09:00--09:04 day-session price change.  The latter is
    the last observed input and all event fields are immutable at 09:04 close.
    """
    opening = datetime(trade_date.year, trade_date.month, trade_date.day, 9, tzinfo=JST)
    signal, entry = opening + timedelta(minutes=4), opening + timedelta(minutes=5)
    exit_signal, exit_time = opening + timedelta(minutes=64), opening + timedelta(minutes=65)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(), "session": "day", "N_trade_date": trade_date.isoformat(),
        "Q_window_start_jst": opening.isoformat(), "Q_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(), "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": exit_signal.isoformat(), "X_planned_exit_jst": exit_time.isoformat(),
        "required_Q_window_bars": 5, "status": "skipped",
    }
    if not cash_calendar.is_open(trade_date):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if trade_date < development_start:
        event["reason"] = "REFERENCE_OUTSIDE_DEVELOPMENT"
        return event
    if day_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    if night_quarantined:
        event["reason"] = "REFERENCE_NIGHT_SESSION_QUARANTINED"
        return event
    if day_bars is None or night_bars is None:
        event["reason"] = "DAY_OR_REFERENCE_NIGHT_MISSING"
        return event
    try:
        night_open = classifier.session_open(trade_date, Session.NIGHT)
        night_end = normal_session_end(classifier, trade_date, Session.NIGHT)
    except ValueError:
        event["reason"] = "NO_SCHEDULED_NIGHT_MAPPING"
        return event
    if night_end > opening:
        event["reason"] = "REFERENCE_NIGHT_NOT_COMPLETE_BY_0900"
        return event
    stamps = [night_open + timedelta(minutes=index) for index in range(int((night_end - night_open).total_seconds() // 60))]
    event.update({
        "N_scheduled_open_jst": night_open.isoformat(), "N_normal_end_jst": night_end.isoformat(),
        "N_final_normal_bar_start_jst": (night_end - timedelta(minutes=1)).isoformat(),
        "N_expected_bar_count": len(stamps),
    })
    night_by_time = {bar.ts_jst: bar for bar in night_bars}
    reference_rows = [night_by_time.get(stamp) for stamp in stamps]
    if any(row is None for row in reference_rows):
        event["reason"] = "REFERENCE_FULL_NORMAL_NIGHT_MISSING"
        return event
    night = [row for row in reference_rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.NIGHT for row in night):
        event["reason"] = "REFERENCE_NIGHT_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    day_by_time = {bar.ts_jst: bar for bar in day_bars}
    q_rows = [day_by_time.get(opening + timedelta(minutes=index)) for index in range(5)]
    if any(row is None for row in q_rows):
        event["reason"] = "WINDOW_0900_TO_0904_MISSING"
        return event
    current = [row for row in q_rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY for row in current):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    s, q = night[-1].close - night[0].open, current[-1].close - current[0].open
    event.update({
        "S_open_night_points": night[0].open, "S_close_final_normal_night_points": night[-1].close,
        "Q_open_0900_points": current[0].open, "Q_close_0904_points": current[-1].close,
        "S_points": s, "Q_points": q,
    })
    if s == 0:
        event["reason"] = "ZERO_S"
        return event
    if q == 0:
        event["reason"] = "ZERO_Q"
        return event
    s_direction, q_direction = ("long" if s > 0 else "short"), ("long" if q > 0 else "short")
    event.update({"S_sign": 1 if s > 0 else -1, "Q_sign": 1 if q > 0 else -1, "S_direction": s_direction, "Q_direction": q_direction})
    if (s > 0) == (q > 0):
        event.update({"status": "confirmed", "reason": "S_Q_SAME_SIGN", "A_direction": s_direction, "F_reverse_direction": "short" if s_direction == "long" else "long"})
    else:
        event.update({"status": "nonconfirmed", "reason": "S_Q_OPPOSITE_SIGN"})
    return event
