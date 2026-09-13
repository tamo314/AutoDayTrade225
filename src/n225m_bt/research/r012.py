"""Causal event selection for R012-Q001 night-direction follow-through."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r006 import night_reference_time


def night_direction_event(
    classifier: CalendarClassifier,
    trade_date: date,
    day_bars: list[Bar],
    night_bars: list[Bar] | None,
    *,
    reference_night_quarantined: bool = False,
) -> dict[str, object]:
    """Select R012's event using only scheduled reference bars and the S bar.

    ``R`` is the scheduled linked night open-to-final-normal-close return and
    ``G`` is the linked-night close-to-day-open gap.  The selection never
    substitutes observed boundary rows or searches an earlier night.
    """
    S = classifier.session_open(trade_date, Session.DAY)
    day_close = classifier.session_close(trade_date, Session.DAY)
    entry_time = S + timedelta(minutes=1)
    exit_time = S + timedelta(minutes=61)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "S_day_open_bar_start_jst": S.isoformat(),
        "E_planned_entry_jst": entry_time.isoformat(),
        "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(),
        "new_entry_cutoff_jst": (day_close - timedelta(minutes=15)).isoformat(),
        "F_force_flat_jst": (day_close - timedelta(minutes=5)).isoformat(),
        "status": "skipped",
    }
    if entry_time > day_close - timedelta(minutes=15) or exit_time > day_close - timedelta(
        minutes=5
    ):
        event["reason"] = "SCHEDULED_ENTRY_OR_EXIT_OUTSIDE_EXECUTION_WINDOW"
        return event
    try:
        C_start, C_known, schedule_version, C_basis = night_reference_time(classifier, trade_date)
        N_start = classifier.session_open(trade_date, Session.NIGHT)
    except ValueError as exc:
        event["reason"] = str(exc)
        return event
    event.update(
        {
            "N_night_open_bar_start_jst": N_start.isoformat(),
            "C_normal_bar_start_jst": C_start.isoformat(),
            "C_close_known_jst": C_known.isoformat(),
            "night_schedule_version": schedule_version,
            "C_selection_basis": C_basis,
        }
    )
    day_by_time = {bar.ts_jst: bar for bar in day_bars}
    day_open = day_by_time.get(S)
    if day_open is None:
        event["reason"] = "DAY_OPEN_BAR_MISSING"
        return event
    if not day_open.is_eligible:
        event["reason"] = "DAY_OPEN_BAR_INELIGIBLE"
        return event
    if reference_night_quarantined:
        event["reason"] = "REFERENCE_NIGHT_QUARANTINED"
        return event
    if night_bars is None:
        event["reason"] = "REFERENCE_NIGHT_MISSING"
        return event
    night_by_time = {bar.ts_jst: bar for bar in night_bars}
    night_open = night_by_time.get(N_start)
    if night_open is None:
        event["reason"] = "NIGHT_OPEN_BAR_MISSING"
        return event
    if not night_open.is_eligible:
        event["reason"] = "NIGHT_OPEN_BAR_INELIGIBLE"
        return event
    night_close = night_by_time.get(C_start)
    if night_close is None:
        event["reason"] = "REFERENCE_NORMAL_BAR_MISSING"
        return event
    if not night_close.is_eligible:
        event["reason"] = "REFERENCE_NORMAL_BAR_INELIGIBLE"
        return event
    if (
        night_open.trade_date != trade_date
        or night_close.trade_date != trade_date
        or night_open.session is not Session.NIGHT
        or night_close.session is not Session.NIGHT
    ):
        event["reason"] = "REFERENCE_NIGHT_TRADE_DATE_OR_SESSION_MISMATCH"
        return event
    R = night_close.close - night_open.open
    G = day_open.open - night_close.close
    event.update(
        {
            "O_night_open": night_open.open,
            "C_night_normal_close": night_close.close,
            "O_day_open": day_open.open,
            "R_points": R,
            "G_points": G,
        }
    )
    if R == 0:
        event["reason"] = "ZERO_R"
        return event
    if G == 0:
        event["reason"] = "ZERO_G"
        return event
    event.update(
        {
            "status": "eligible",
            "reason": "R_AND_G_NONZERO",
            "A_direction": "long" if R > 0 else "short",
            "D_direction": "short" if G > 0 else "long",
        }
    )
    return event
