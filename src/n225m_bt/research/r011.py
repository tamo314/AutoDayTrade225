"""Causal fixed-time close-mean-reversion event selection for R011-Q001."""

from __future__ import annotations

from datetime import date, timedelta
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def close_mean_reversion_event(
    classifier: CalendarClassifier, trade_date: date, session: Session, bars: list[Bar]
) -> dict[str, object]:
    """Evaluate the preregistered S+119 event without rounding the mean."""
    session_open = classifier.session_open(trade_date, session)
    session_close = classifier.session_close(trade_date, session)
    signal_time = session_open + timedelta(minutes=119)
    entry_time = session_open + timedelta(minutes=120)
    exit_time = session_open + timedelta(minutes=180)
    expected = [signal_time - timedelta(minutes=step) for step in range(60, -1, -1)]
    by_timestamp = {bar.ts_jst: bar for bar in bars}
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(), "session": session.value,
        "S_session_open_jst": session_open.isoformat(),
        "new_entry_cutoff_jst": (session_close - timedelta(minutes=15)).isoformat(),
        "F_force_flat_jst": (session_close - timedelta(minutes=5)).isoformat(),
        "t_signal_jst": signal_time.isoformat(), "E_planned_entry_jst": entry_time.isoformat(),
        "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(), "required_window_bar_count": 61,
        "mean_reference_close_count": 60, "status": "no_event",
    }
    window = [by_timestamp.get(stamp) for stamp in expected]
    if any(bar is None or not bar.is_eligible or bar.trade_date != trade_date or bar.session is not session for bar in window):
        event["reason"] = "WINDOW_MISSING_OR_INELIGIBLE"
        return event
    if entry_time > session_close - timedelta(minutes=15) or exit_time > session_close - timedelta(minutes=5):
        event["reason"] = "SCHEDULED_ENTRY_OR_EXIT_OUTSIDE_EXECUTION_WINDOW"
        return event
    concrete = [cast(Bar, bar) for bar in window]
    prior_sum = sum(bar.close for bar in concrete[:60])
    current_close = concrete[60].close
    z = 60 * current_close - prior_sum
    event.update({"window_start_jst": expected[0].isoformat(), "window_end_jst": expected[-1].isoformat(), "prior_60_close_sum_points": prior_sum, "current_close_points": current_close, "Z_points_times_60": z})
    if z == 0:
        event["reason"] = "ZERO_Z"
        return event
    event.update({"status": "event", "reason": "CLOSE_MEAN_DEVIATION", "direction": "short" if z > 0 else "long"})
    return event
