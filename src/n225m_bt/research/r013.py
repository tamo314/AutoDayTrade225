"""Causal previous-scheduled-same-session direction event selection for R013."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def previous_same_session_event(
    classifier: CalendarClassifier,
    trade_date: date,
    session: Session,
    current_bars: list[Bar],
    reference_bars: list[Bar] | None,
    *,
    reference_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
) -> dict[str, object]:
    """Select one R013 event using only the calendar-designated prior session.

    The reference is never inferred from observed rows.  A missing or
    quarantined immediate predecessor is a skip, rather than permission to
    search farther into history.
    """
    S = classifier.session_open(trade_date, session)
    close = classifier.session_close(trade_date, session)
    E, X = S + timedelta(minutes=1), S + timedelta(minutes=61)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": session.value,
        "S_current_open_bar_start_jst": S.isoformat(),
        "E_planned_entry_jst": E.isoformat(),
        "EXIT_signal_bar_start_jst": (X - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": X.isoformat(),
        "new_entry_cutoff_jst": (close - timedelta(minutes=15)).isoformat(),
        "F_force_flat_jst": (close - timedelta(minutes=5)).isoformat(),
        "status": "skipped",
    }
    if close - timedelta(minutes=15) < E or close - timedelta(minutes=5) < X:
        event["reason"] = "SCHEDULED_ENTRY_OR_EXIT_OUTSIDE_EXECUTION_WINDOW"
        return event
    calendar_day = classifier.exchange_calendar.get(trade_date)
    previous = calendar_day.previous_trade_date if calendar_day else None
    if previous is None:
        event["reason"] = "NO_SCHEDULED_PREVIOUS_TRADE_DATE"
        return event
    event["previous_same_session_trade_date"] = previous.isoformat()
    if previous < development_start:
        event["reason"] = "REFERENCE_OUTSIDE_DEVELOPMENT"
        return event
    try:
        P = classifier.session_open(previous, session)
    except ValueError as exc:
        event["reason"] = f"REFERENCE_SCHEDULE_ERROR:{exc}"
        return event
    event.update(
        {
            "P_reference_open_bar_start_jst": P.isoformat(),
            "reference_window_start_jst": (P + timedelta(minutes=1)).isoformat(),
            "reference_window_end_jst": (P + timedelta(minutes=121)).isoformat(),
        }
    )
    if P + timedelta(minutes=121) >= S:
        event["reason"] = "REFERENCE_NOT_KNOWN_BEFORE_CURRENT_S"
        return event
    current = {bar.ts_jst: bar for bar in current_bars}
    current_s = current.get(S)
    if current_s is None:
        event["reason"] = "CURRENT_S_BAR_MISSING"
        return event
    if not current_s.is_eligible:
        event["reason"] = "CURRENT_S_BAR_INELIGIBLE"
        return event
    if reference_quarantined:
        event["reason"] = "REFERENCE_SESSION_QUARANTINED"
        return event
    if reference_bars is None:
        event["reason"] = "REFERENCE_SESSION_MISSING"
        return event
    by_time = {bar.ts_jst: bar for bar in reference_bars}
    window = [by_time.get(P + timedelta(minutes=index)) for index in range(1, 122)]
    if any(bar is None for bar in window):
        event["reason"] = "REFERENCE_WINDOW_MISSING"
        return event
    rows = [bar for bar in window if bar is not None]
    if any(
        not bar.is_eligible or bar.trade_date != previous or bar.session is not session for bar in rows
    ):
        event["reason"] = "REFERENCE_WINDOW_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    U = rows[60].open - rows[0].open
    V = rows[120].open - rows[60].open
    event.update(
        {
            "open_p_1": rows[0].open,
            "open_p_61": rows[60].open,
            "open_p_121": rows[120].open,
            "U_points": U,
            "V_points": V,
        }
    )
    if U == 0:
        event["reason"] = "ZERO_U"
        return event
    if V == 0:
        event["reason"] = "ZERO_V"
        return event
    event.update(
        {
            "status": "eligible",
            "reason": "CONTIGUOUS_REFERENCE_U_V_NONZERO",
            "A_direction": "long" if U > 0 else "short",
            "D_direction": "long" if V > 0 else "short",
        }
    )
    return event
