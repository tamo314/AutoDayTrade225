"""Causal prior-scheduled-session range-end event selection for R022-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.session_rules import regime_for_trade_date
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session


def normal_session_end(classifier: CalendarClassifier, day: date, session: Session) -> datetime:
    """Scheduled normal-trading endpoint, never inferred from observed bars."""
    regime_day = day
    if session is Session.NIGHT:
        mapping = classifier.exchange_calendar.get(day)
        if mapping is None or mapping.night_calendar_start_date is None:
            raise ValueError("NO_SCHEDULED_NIGHT_MAPPING")
        regime_day = mapping.night_calendar_start_date
    regime = regime_for_trade_date(classifier.sessions, regime_day)
    fields = regime.day if session is Session.DAY else regime.night
    normal = fields.regular_end if session is Session.DAY else fields.regular_end_next_day
    if normal is None:
        return classifier.session_close(day, session)
    return datetime.combine(day, normal, JST)


def scheduled_sessions(
    classifier: CalendarClassifier, days: list[date]
) -> list[tuple[date, Session, datetime]]:
    """All calendar-designated sessions ordered by scheduled real time."""
    schedule: list[tuple[date, Session, datetime]] = []
    for day in sorted(days):
        for session in (Session.DAY, Session.NIGHT):
            try:
                schedule.append((day, session, classifier.session_open(day, session)))
            except ValueError:
                # An absent night mapping is not reconstructed from observed prices.
                continue
    return sorted(schedule, key=lambda item: item[2])


def previous_scheduled_session(
    classifier: CalendarClassifier, days: list[date], current: tuple[date, Session]
) -> tuple[date, Session] | None:
    schedule = scheduled_sessions(classifier, days)
    for index, (day, session, _) in enumerate(schedule):
        if (day, session) == current:
            return None if index == 0 else schedule[index - 1][:2]
    return None


def prior_range_end_event(
    classifier: CalendarClassifier,
    current_day: date,
    current_session: Session,
    current_bars: list[Bar] | None,
    reference: tuple[date, Session] | None,
    reference_bars: list[Bar] | None,
    *,
    current_quarantined: bool = False,
    reference_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
) -> dict[str, object]:
    """Return one fixed R022 event; p is supplied only by the planned calendar."""
    S = classifier.session_open(current_day, current_session)
    E, X = S + timedelta(minutes=1), S + timedelta(minutes=61)
    close = classifier.session_close(current_day, current_session)
    event: dict[str, object] = {
        "trade_date": current_day.isoformat(), "session": current_session.value,
        "S_current_open_bar_start_jst": S.isoformat(),
        "E_planned_entry_jst": E.isoformat(),
        "EXIT_signal_bar_start_jst": (X - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": X.isoformat(), "status": "skipped",
    }
    if current_quarantined:
        event["reason"] = "CURRENT_SESSION_QUARANTINED"
        return event
    if close - timedelta(minutes=15) < E or close - timedelta(minutes=5) < X:
        event["reason"] = "SCHEDULED_ENTRY_OR_EXIT_OUTSIDE_EXECUTION_WINDOW"
        return event
    if current_bars is None:
        event["reason"] = "CURRENT_SESSION_MISSING"
        return event
    current_s = {bar.ts_jst: bar for bar in current_bars}.get(S)
    if current_s is None:
        event["reason"] = "CURRENT_S_BAR_MISSING"
        return event
    if not current_s.is_eligible or current_s.trade_date != current_day or current_s.session is not current_session:
        event["reason"] = "CURRENT_S_BAR_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    if reference is None:
        event["reason"] = "NO_SCHEDULED_PREVIOUS_SESSION"
        return event
    p_day, p_session = reference
    event.update({"p_trade_date": p_day.isoformat(), "p_session": p_session.value})
    if p_day < development_start:
        event["reason"] = "REFERENCE_OUTSIDE_DEVELOPMENT"
        return event
    if reference_quarantined:
        event["reason"] = "REFERENCE_SESSION_QUARANTINED"
        return event
    if reference_bars is None:
        event["reason"] = "REFERENCE_SESSION_MISSING"
        return event
    P, normal_end = classifier.session_open(p_day, p_session), normal_session_end(classifier, p_day, p_session)
    last = normal_end - timedelta(minutes=1)
    if normal_end > S:
        event["reason"] = "REFERENCE_NOT_KNOWN_BEFORE_CURRENT_S"
        return event
    event.update({
        "P_reference_scheduled_open_jst": P.isoformat(),
        "p_normal_end_jst": normal_end.isoformat(), "p_final_normal_bar_start_jst": last.isoformat(),
        "p_expected_bar_count": int((normal_end - P).total_seconds() // 60),
    })
    by_time = {bar.ts_jst: bar for bar in reference_bars}
    stamps = [P + timedelta(minutes=index) for index in range(int((normal_end - P).total_seconds() // 60))]
    rows = [by_time.get(stamp) for stamp in stamps]
    if any(row is None for row in rows):
        event["reason"] = "REFERENCE_FULL_NORMAL_SESSION_MISSING"
        return event
    normal_rows = [row for row in rows if row is not None]
    if any(not row.is_eligible or row.trade_date != p_day or row.session is not p_session for row in normal_rows):
        event["reason"] = "REFERENCE_FULL_NORMAL_SESSION_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    high, low = max(row.high for row in normal_rows), min(row.low for row in normal_rows)
    opening, closing = normal_rows[0].open, normal_rows[-1].close
    late_start = last - timedelta(minutes=59)
    late_open = by_time[late_start].open
    late_change = closing - late_open
    event.update({"H_points": high, "L_points": low, "O_points": opening, "C_points": closing,
                  "late60_start_jst": late_start.isoformat(), "late60_open_points": late_open,
                  "J_points": late_change, "quartile_expression": 4 * closing - 3 * high - low})
    if high == low:
        event["reason"] = "ZERO_RANGE_H_EQUALS_L"
        return event
    if closing == opening:
        event["reason"] = "ZERO_C_MINUS_O"
        return event
    if late_change == 0:
        event["reason"] = "ZERO_J"
        return event
    if 4 * closing == 3 * high + low:
        event["reason"] = "UPPER_QUARTILE_BOUNDARY_EQUAL"
        return event
    if 4 * closing == high + 3 * low:
        event["reason"] = "LOWER_QUARTILE_BOUNDARY_EQUAL"
        return event
    if not (4 * closing > 3 * high + low or 4 * closing < high + 3 * low):
        event["reason"] = "MIDDLE_FIFTY_PERCENT"
        return event
    a = "long" if 4 * closing > 3 * high + low else "short"
    event.update({"status": "eligible", "reason": "FULL_P_NORMAL_RANGE_ENDPOINT_C_O_J_NONZERO",
                  "range_quartile": "upper" if a == "long" else "lower", "A_direction": a,
                  "D_direction": "long" if closing > opening else "short", "F_late_direction": "long" if late_change > 0 else "short"})
    return event
