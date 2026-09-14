"""Causal prior-day-range failed-breakout events for R036-Q001."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r025 import previous_tse_open_date


def _normal_rows(
    classifier: CalendarClassifier, trade_day: date, bars: list[Bar] | None
) -> list[Bar] | None:
    if bars is None:
        return None
    start, end = classifier.session_open(trade_day, Session.DAY), normal_session_end(
        classifier, trade_day, Session.DAY
    )
    by_time = {bar.ts_jst: bar for bar in bars}
    rows = [by_time.get(start + timedelta(minutes=index)) for index in range((end - start).seconds // 60)]
    if any(row is None for row in rows):
        return None
    result = [row for row in rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_day or row.session is not Session.DAY for row in result):
        return None
    return result


def prior_day_failed_breakout_event(
    classifier: CalendarClassifier,
    cash_calendar: TSECashMarketCalendar,
    trade_day: date,
    day_bars: list[Bar] | None,
    prior_day_bars: list[Bar] | None,
    *,
    day_quarantined: bool = False,
    prior_day_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Use immediate prior TSE day H/L and classify its first 60-minute break at b+5."""
    event: dict[str, object] = {"trade_date": trade_day.isoformat(), "session": "day", "status": "skipped"}
    if not development_start <= trade_day <= development_end:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    if not cash_calendar.is_open(trade_day):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    prior = previous_tse_open_date(cash_calendar, trade_day)
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
    reference = _normal_rows(classifier, prior, prior_day_bars)
    if reference is None:
        event["reason"] = "REFERENCE_FULL_NORMAL_DAY_MISSING_OR_INELIGIBLE"
        return event
    pdh, pdl = max(row.high for row in reference), min(row.low for row in reference)
    event.update({"PDH_points": pdh, "PDL_points": pdl, "p_normal_bar_count": len(reference)})
    start = classifier.session_open(trade_day, Session.DAY)
    event.update({"S_scheduled_open_jst": start.isoformat(), "search_end_jst": (start + timedelta(minutes=59)).isoformat(), "confirmation_delay_bars": 5})
    by_time = {bar.ts_jst: bar for bar in day_bars or []}
    for offset in range(60):
        breakout = by_time.get(start + timedelta(minutes=offset))
        if breakout is None:
            event["reason"] = "BREAKOUT_SEARCH_MISSING"
            return event
        if not breakout.is_eligible or breakout.trade_date != trade_day or breakout.session is not Session.DAY:
            event["reason"] = "BREAKOUT_SEARCH_INELIGIBLE_OR_SESSION_MISMATCH"
            return event
        if not (breakout.close > pdh or breakout.close < pdl):
            continue
        direction = "long" if breakout.close > pdh else "short"
        confirmation = [by_time.get(breakout.ts_jst + timedelta(minutes=index)) for index in range(1, 6)]
        if any(row is None for row in confirmation):
            event.update({"reason": "CONFIRMATION_WINDOW_MISSING", "breakout_ts_jst": breakout.ts_jst.isoformat()})
            return event
        rows = [row for row in confirmation if row is not None]
        if any(not row.is_eligible or row.trade_date != trade_day or row.session is not Session.DAY for row in rows):
            event.update({"reason": "CONFIRMATION_WINDOW_INELIGIBLE_OR_SESSION_MISMATCH", "breakout_ts_jst": breakout.ts_jst.isoformat()})
            return event
        k = rows[-1]
        persistent = k.close > pdh if direction == "long" else k.close < pdl
        event.update({
            "status": "persistent" if persistent else "rejected", "reason": "FIRST_STRICT_CLOSE_BREAK_FIXED_5M_CLASSIFICATION",
            "breakout_direction": direction, "breakout_ts_jst": breakout.ts_jst.isoformat(), "breakout_close": breakout.close,
            "k_ts_jst": k.ts_jst.isoformat(), "k_close": k.close,
            "E_planned_entry_jst": (k.ts_jst + timedelta(minutes=1)).isoformat(),
            "EXIT_signal_bar_start_jst": (k.ts_jst + timedelta(minutes=60)).isoformat(),
            "X_planned_exit_jst": (k.ts_jst + timedelta(minutes=61)).isoformat(),
            "breakout_30m_bucket": breakout.ts_jst.replace(minute=(breakout.ts_jst.minute // 30) * 30, second=0, microsecond=0).strftime("%H:%M"),
        })
        return event
    event["reason"] = "NO_STRICT_CLOSE_BREAK_IN_FIRST_60_BARS"
    return event
