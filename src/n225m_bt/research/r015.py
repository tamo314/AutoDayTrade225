"""Causal event selection for R015-Q001 total night-close/day-opening change."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r006 import night_reference_time


def total_change_event(
    classifier: CalendarClassifier,
    trade_date: date,
    day_bars: list[Bar],
    night_bars: list[Bar] | None,
    *,
    reference_night_quarantined: bool = False,
) -> dict[str, object]:
    """Construct the fixed R015 event without substituting any reference bar.

    The event uses 30 consecutive scheduled day bars from ``S`` through
    ``t=S+29``.  The linked night close is the versioned R006 final normal
    bar, never an observed session boundary or auction row.
    """
    start = classifier.session_open(trade_date, Session.DAY)
    close = classifier.session_close(trade_date, Session.DAY)
    signal, entry, exit_time = (
        start + timedelta(minutes=29),
        start + timedelta(minutes=30),
        start + timedelta(minutes=90),
    )
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "S_day_open_bar_start_jst": start.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(),
        "new_entry_cutoff_jst": (close - timedelta(minutes=15)).isoformat(),
        "F_force_flat_jst": (close - timedelta(minutes=5)).isoformat(),
        "required_day_window_bar_count": 30,
        "status": "skipped",
    }
    if entry > close - timedelta(minutes=15) or exit_time > close - timedelta(minutes=5):
        event["reason"] = "SCHEDULED_ENTRY_OR_EXIT_OUTSIDE_EXECUTION_WINDOW"
        return event
    try:
        night_close_start, known_time, schedule_version, basis = night_reference_time(
            classifier, trade_date
        )
    except ValueError as exc:
        event["reason"] = str(exc)
        return event
    event.update(
        {
            "C_normal_bar_start_jst": night_close_start.isoformat(),
            "C_close_known_jst": known_time.isoformat(),
            "night_schedule_version": schedule_version,
            "C_selection_basis": basis,
        }
    )
    day_by_time = {bar.ts_jst: bar for bar in day_bars}
    window_times = [start + timedelta(minutes=index) for index in range(30)]
    window = [day_by_time.get(stamp) for stamp in window_times]
    if any(bar is None for bar in window):
        event["reason"] = "DAY_30BAR_WINDOW_MISSING"
        return event
    rows = [bar for bar in window if bar is not None]
    if any(
        not bar.is_eligible or bar.trade_date != trade_date or bar.session is not Session.DAY
        for bar in rows
    ):
        event["reason"] = "DAY_30BAR_WINDOW_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    if reference_night_quarantined:
        event["reason"] = "REFERENCE_NIGHT_QUARANTINED"
        return event
    if night_bars is None:
        event["reason"] = "REFERENCE_NIGHT_MISSING"
        return event
    reference = {bar.ts_jst: bar for bar in night_bars}.get(night_close_start)
    if reference is None:
        event["reason"] = "REFERENCE_NORMAL_BAR_MISSING"
        return event
    if not reference.is_eligible:
        event["reason"] = "REFERENCE_NORMAL_BAR_INELIGIBLE"
        return event
    if reference.trade_date != trade_date or reference.session is not Session.NIGHT:
        event["reason"] = "REFERENCE_NIGHT_TRADE_DATE_OR_SESSION_MISMATCH"
        return event
    if reference.calendar_date != night_close_start.date():
        event["reason"] = "REFERENCE_NORMAL_BAR_CALENDAR_DATE_MISMATCH"
        return event
    day_open, current = rows[0], rows[-1]
    gap = day_open.open - reference.close
    momentum = current.close - day_open.open
    total = current.close - reference.close
    if total != gap + momentum:
        raise AssertionError("integer total must equal gap plus day movement")
    event.update(
        {
            "O_day_open": day_open.open,
            "C_t": current.close,
            "C_night_normal_close": reference.close,
            "G_points": gap,
            "M_points": momentum,
            "Q_points": total,
        }
    )
    if gap == 0:
        event["reason"] = "ZERO_G"
        return event
    if momentum == 0:
        event["reason"] = "ZERO_M"
        return event
    if total == 0:
        event["reason"] = "ZERO_Q"
        return event
    event.update(
        {
            "status": "eligible",
            "reason": "G_M_Q_NONZERO",
            "A_direction": "long" if total > 0 else "short",
            "D_direction": "long" if momentum > 0 else "short",
            "F_direction": "long" if gap > 0 else "short",
        }
    )
    return event
