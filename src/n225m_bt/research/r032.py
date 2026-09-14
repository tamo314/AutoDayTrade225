"""Causal prior-day / night-open five-minute confirmation events for R032-Q001."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r022 import normal_session_end


def prior_day_night_gap_confirmation_event(
    classifier: CalendarClassifier,
    night_trade_date: date,
    night_bars: list[Bar] | None,
    reference: tuple[date, Session] | None,
    day_bars: list[Bar] | None,
    *,
    night_quarantined: bool = False,
    day_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Construct one R032 event using exactly the preceding scheduled day.

    The fifth night bar is the final input.  No observed-session ordering or
    fallback reference is used: ``reference`` comes from the versioned plan.
    """
    try:
        night_open = classifier.session_open(night_trade_date, Session.NIGHT)
    except ValueError:
        return {"trade_date": night_trade_date.isoformat(), "session": "night", "status": "skipped", "reason": "NO_SCHEDULED_TARGET_NIGHT"}
    signal, entry = night_open + timedelta(minutes=4), night_open + timedelta(minutes=5)
    exit_signal, exit_time = night_open + timedelta(minutes=64), night_open + timedelta(minutes=65)
    event: dict[str, object] = {
        "trade_date": night_trade_date.isoformat(), "session": "night",
        "N_scheduled_open_jst": night_open.isoformat(),
        "Q_window_start_jst": night_open.isoformat(), "Q_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(), "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": exit_signal.isoformat(), "X_planned_exit_jst": exit_time.isoformat(),
        "required_Q_window_bars": 5, "status": "skipped",
    }
    if not development_start <= night_trade_date <= development_end:
        event["reason"] = "TARGET_NIGHT_OUTSIDE_DEVELOPMENT"
        return event
    if night_quarantined:
        event["reason"] = "TARGET_NIGHT_QUARANTINED"
        return event
    if reference is None:
        event["reason"] = "NO_SCHEDULED_PREVIOUS_SESSION"
        return event
    reference_date, reference_session = reference
    event.update({"D_trade_date": reference_date.isoformat(), "D_session": reference_session.value})
    if reference_session is not Session.DAY:
        event["reason"] = "PREVIOUS_SCHEDULED_SESSION_NOT_DAY"
        return event
    if not development_start <= reference_date <= development_end:
        event["reason"] = "REFERENCE_DAY_OUTSIDE_DEVELOPMENT"
        return event
    if day_quarantined:
        event["reason"] = "REFERENCE_DAY_QUARANTINED"
        return event
    if night_bars is None:
        event["reason"] = "TARGET_NIGHT_MISSING"
        return event
    if day_bars is None:
        event["reason"] = "REFERENCE_DAY_MISSING"
        return event
    try:
        day_end = normal_session_end(classifier, reference_date, Session.DAY)
    except ValueError:
        event["reason"] = "NO_SCHEDULED_REFERENCE_DAY"
        return event
    day_final_start = day_end - timedelta(minutes=1)
    event.update({"D_normal_end_jst": day_end.isoformat(), "D_final_normal_bar_start_jst": day_final_start.isoformat()})
    day_final = {bar.ts_jst: bar for bar in day_bars}.get(day_final_start)
    if day_final is None:
        event["reason"] = "REFERENCE_DAY_FINAL_NORMAL_BAR_MISSING"
        return event
    if not day_final.is_eligible or day_final.trade_date != reference_date or day_final.session is not Session.DAY:
        event["reason"] = "REFERENCE_DAY_FINAL_NORMAL_BAR_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    night_by_time = {bar.ts_jst: bar for bar in night_bars}
    rows = [night_by_time.get(night_open + timedelta(minutes=index)) for index in range(5)]
    if any(row is None for row in rows):
        event["reason"] = "TARGET_NIGHT_FIRST_FIVE_BARS_MISSING"
        return event
    first_five = [row for row in rows if row is not None]
    if any(not row.is_eligible or row.trade_date != night_trade_date or row.session is not Session.NIGHT for row in first_five):
        event["reason"] = "TARGET_NIGHT_FIRST_FIVE_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    gap, q = first_five[0].open - day_final.close, first_five[-1].close - first_five[0].open
    event.update({
        "C_D_final_normal_day_points": day_final.close,
        "O_N_first_night_points": first_five[0].open,
        "C_5_fifth_night_points": first_five[-1].close,
        "G_points": gap, "Q_points": q,
    })
    if gap == 0:
        event["reason"] = "ZERO_G"
        return event
    if q == 0:
        event["reason"] = "ZERO_Q"
        return event
    g_direction, q_direction = ("long" if gap > 0 else "short"), ("long" if q > 0 else "short")
    event.update({"G_sign": 1 if gap > 0 else -1, "Q_sign": 1 if q > 0 else -1, "G_direction": g_direction, "Q_direction": q_direction})
    if (gap > 0) == (q > 0):
        event.update({"status": "confirmed", "reason": "G_Q_SAME_SIGN", "A_direction": g_direction, "F_reverse_direction": "short" if g_direction == "long" else "long"})
    else:
        event.update({"status": "nonconfirmed", "reason": "G_Q_OPPOSITE_SIGN"})
    return event
