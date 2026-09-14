"""Causal day-close extreme selection for R039-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import ceil

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.calendar.session_rules import regime_for_trade_date
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
LOOKBACK_DAYS = 60
MIN_VALID_RETURNS = 50


def _regular_day_end(classifier: CalendarClassifier, trade_day: date) -> datetime:
    """Return the versioned continuous-trading endpoint, never an observed edge."""
    regime = regime_for_trade_date(classifier.sessions, trade_day)
    endpoint = regime.day.regular_end or regime.day.session_close
    assert endpoint is not None
    return datetime.combine(trade_day, endpoint, JST)


def _window_return(
    classifier: CalendarClassifier, trade_day: date, bars: list[Bar] | None, offset: int
) -> tuple[int | None, datetime, datetime]:
    """Return a strict planned 30-bar day window ending ``offset`` minutes before end."""
    end = _regular_day_end(classifier, trade_day) - timedelta(minutes=offset)
    start = end - timedelta(minutes=30)
    by_time = {bar.ts_jst: bar for bar in bars or []}
    rows = [by_time.get(start + timedelta(minutes=index)) for index in range(30)]
    if any(row is None for row in rows):
        return None, start, end - timedelta(minutes=1)
    concrete = [row for row in rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_day or row.session is not Session.DAY
        for row in concrete
    ):
        return None, start, end - timedelta(minutes=1)
    return concrete[-1].close - concrete[0].open, start, end - timedelta(minutes=1)


def _prior_scheduled_days(
    calendar: ExchangeCalendar, reference_day: date
) -> list[date] | None:
    """Follow exactly 60 linked schedule entries; never skip or backfill a date."""
    output: list[date] = []
    current = reference_day
    for _ in range(LOOKBACK_DAYS):
        record = calendar.get(current)
        if record is None or record.previous_trade_date is None:
            return None
        current = record.previous_trade_date
        output.append(current)
    return output


def _window_audit(
    classifier: CalendarClassifier,
    calendar: ExchangeCalendar,
    reference_day: date,
    bars_by_session: dict[tuple[date, Session], list[Bar]],
    isolated_sessions: set[tuple[date, Session]],
    *,
    offset: int,
    label: str,
) -> dict[str, object]:
    current, start, last = _window_return(
        classifier, reference_day, bars_by_session.get((reference_day, Session.DAY)), offset
    )
    audit: dict[str, object] = {
        f"{label}_window_start_jst": start.isoformat(),
        f"{label}_window_last_bar_start_jst": last.isoformat(),
        f"r{label}_points": current,
        f"{label}_reference_scheduled_day_count": 0,
        f"{label}_reference_valid_abs_return_count": 0,
    }
    references = _prior_scheduled_days(calendar, reference_day)
    if references is None:
        audit[f"{label}_status"] = "history_short_or_unlinked"
        return audit
    audit[f"{label}_reference_trade_dates"] = [item.isoformat() for item in references]
    audit[f"{label}_reference_scheduled_day_count"] = len(references)
    if any(not DEVELOPMENT_START <= item <= DEVELOPMENT_END for item in references):
        audit[f"{label}_status"] = "history_outside_development"
        return audit
    values: list[int] = []
    for previous in references:
        if (previous, Session.DAY) in isolated_sessions:
            continue
        value, _, _ = _window_return(
            classifier, previous, bars_by_session.get((previous, Session.DAY)), offset
        )
        if value is not None and value != 0:
            values.append(abs(value))
    audit[f"{label}_reference_valid_abs_return_count"] = len(values)
    if len(values) < MIN_VALID_RETURNS:
        audit[f"{label}_status"] = "insufficient_valid_reference_returns"
        return audit
    threshold = sorted(values)[ceil(0.75 * len(values)) - 1]
    audit[f"Q{label}_points"] = threshold
    if current is None:
        audit[f"{label}_status"] = "target_window_missing_or_ineligible"
    elif current == 0:
        audit[f"{label}_status"] = "target_return_zero"
    elif abs(current) > threshold:
        audit[f"{label}_status"] = "extreme"
    else:
        audit[f"{label}_status"] = "nonextreme"
    return audit


def day_close_night_event(
    classifier: CalendarClassifier,
    calendar: ExchangeCalendar,
    target_night_trade_date: date,
    bars_by_session: dict[tuple[date, Session], list[Bar]],
    isolated_sessions: set[tuple[date, Session]],
) -> dict[str, object]:
    """Select C/P using the immediately preceding scheduled day for one target night."""
    event: dict[str, object] = {
        "trade_date": target_night_trade_date.isoformat(),
        "session": "night",
        "status": "skipped",
        "lookback_scheduled_days": LOOKBACK_DAYS,
        "minimum_valid_abs_returns": MIN_VALID_RETURNS,
        "quantile": "ascending ceil(0.75*n), strict |r|>Q; reference day excluded",
    }
    if not DEVELOPMENT_START <= target_night_trade_date <= DEVELOPMENT_END:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    target_schedule = calendar.get(target_night_trade_date)
    if target_schedule is None or target_schedule.night_calendar_start_date is None:
        event["reason"] = "TARGET_NIGHT_NOT_SCHEDULED"
        return event
    if (target_night_trade_date, Session.NIGHT) in isolated_sessions:
        event["reason"] = "TARGET_NIGHT_QUARANTINED"
        return event
    reference_day = target_schedule.previous_trade_date
    if reference_day is None or not DEVELOPMENT_START <= reference_day <= DEVELOPMENT_END:
        event["reason"] = "REFERENCE_DAY_OUTSIDE_DEVELOPMENT_OR_MISSING"
        return event
    if (reference_day, Session.DAY) in isolated_sessions:
        event["reason"] = "REFERENCE_DAY_QUARANTINED"
        return event
    event["reference_day_trade_date"] = reference_day.isoformat()
    event.update(
        _window_audit(
            classifier,
            calendar,
            reference_day,
            bars_by_session,
            isolated_sessions,
            offset=0,
            label="C",
        )
    )
    event.update(
        _window_audit(
            classifier,
            calendar,
            reference_day,
            bars_by_session,
            isolated_sessions,
            offset=30,
            label="P",
        )
    )
    night_open = classifier.session_open(target_night_trade_date, Session.NIGHT)
    event.update(
        {
            "night_scheduled_open_jst": night_open.isoformat(),
            "night_E_planned_entry_jst": night_open.isoformat(),
            "night_X_planned_exit_jst": (night_open + timedelta(minutes=30)).isoformat(),
            "night_exit_signal_bar_start_jst": (night_open + timedelta(minutes=29)).isoformat(),
            "status": "evaluated",
            "reason": "INDEPENDENT_C_AND_P_FIXED_SELECTION",
        }
    )
    return event
