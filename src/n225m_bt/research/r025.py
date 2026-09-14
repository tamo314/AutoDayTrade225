"""Causal prior-TSE-day-range acceptance events for R025-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end


def previous_tse_open_date(calendar: TSECashMarketCalendar, current: date) -> date | None:
    """Return the immediate preceding TSE business date, never from bar observations."""
    candidate = current - timedelta(days=1)
    while candidate >= calendar.source_start:
        if calendar.is_open(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return None


def prior_tse_day_range_acceptance_event(
    classifier: CalendarClassifier,
    cash_calendar: TSECashMarketCalendar,
    trade_date: date,
    day_bars: list[Bar] | None,
    prior_day_bars: list[Bar] | None,
    *,
    day_quarantined: bool = False,
    prior_day_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
) -> dict[str, object]:
    """Select a fixed event using p's scheduled full day range and the 08:59 close."""
    opening = datetime(trade_date.year, trade_date.month, trade_date.day, 8, 45, tzinfo=JST)
    signal, entry, exit_signal, exit_time = (
        opening + timedelta(minutes=14),
        opening + timedelta(minutes=15),
        opening + timedelta(minutes=74),
        opening + timedelta(minutes=75),
    )
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "O_bar_start_jst": opening.isoformat(),
        "K_bar_start_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": exit_signal.isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(),
        "required_current_window_bars": 15,
        "status": "skipped",
    }
    if not cash_calendar.is_open(trade_date):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    prior = previous_tse_open_date(cash_calendar, trade_date)
    if prior is None or prior < development_start:
        event["reason"] = "REFERENCE_OUTSIDE_DEVELOPMENT"
        return event
    event["p_trade_date"] = prior.isoformat()
    if day_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    if prior_day_quarantined:
        event["reason"] = "REFERENCE_DAY_SESSION_QUARANTINED"
        return event
    if day_bars is None:
        event["reason"] = "DAY_SESSION_MISSING"
        return event
    if prior_day_bars is None:
        event["reason"] = "REFERENCE_DAY_SESSION_MISSING"
        return event
    prior_start = classifier.session_open(prior, Session.DAY)
    prior_end = normal_session_end(classifier, prior, Session.DAY)
    stamps = [prior_start + timedelta(minutes=index) for index in range(int((prior_end - prior_start).total_seconds() // 60))]
    event.update({
        "p_scheduled_open_jst": prior_start.isoformat(),
        "p_normal_end_jst": prior_end.isoformat(),
        "p_final_normal_bar_start_jst": (prior_end - timedelta(minutes=1)).isoformat(),
        "p_expected_bar_count": len(stamps),
    })
    previous_by_time = {bar.ts_jst: bar for bar in prior_day_bars}
    previous_rows = [previous_by_time.get(stamp) for stamp in stamps]
    if any(row is None for row in previous_rows):
        event["reason"] = "REFERENCE_FULL_NORMAL_DAY_MISSING"
        return event
    reference = [row for row in previous_rows if row is not None]
    if any(not row.is_eligible or row.trade_date != prior or row.session is not Session.DAY for row in reference):
        event["reason"] = "REFERENCE_FULL_NORMAL_DAY_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    high, low = max(row.high for row in reference), min(row.low for row in reference)
    event.update({"H_points": high, "L_points": low})
    if high == low:
        event["reason"] = "ZERO_RANGE_H_EQUALS_L"
        return event
    current_by_time = {bar.ts_jst: bar for bar in day_bars}
    current_rows = [current_by_time.get(opening + timedelta(minutes=index)) for index in range(15)]
    if any(row is None for row in current_rows):
        event["reason"] = "WINDOW_0845_TO_0859_MISSING"
        return event
    current = [row for row in current_rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY for row in current):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    opening_price, closing_price = current[0].open, current[-1].close
    event.update({"O_open_0845_points": opening_price, "K_close_0859_points": closing_price})
    if opening_price > high:
        direction = "long"
        side = "upper"
    elif opening_price < low:
        direction = "short"
        side = "lower"
    else:
        event["reason"] = "OPENING_INSIDE_OR_BOUNDARY_OF_PRIOR_RANGE"
        return event
    event.update({"base_direction": direction, "range_side": side})
    accepted = closing_price > high if side == "upper" else closing_price < low
    if accepted:
        event.update({
            "status": "confirmed", "reason": "OPENING_OUTSIDE_AND_0859_REMAINS_SAME_SIDE",
            "A_direction": direction, "F_reverse_direction": "short" if direction == "long" else "long",
        })
    else:
        event.update({"status": "nonconfirmed", "reason": "OPENING_OUTSIDE_BUT_0859_NOT_SAME_SIDE"})
    return event
