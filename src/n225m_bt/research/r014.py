"""Causal fixed-time range-midpoint event selection for R014-Q001."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def range_midpoint_event(
    classifier: CalendarClassifier, trade_date: date, session: Session, bars: list[Bar]
) -> dict[str, object]:
    """Select R014's one scheduled event without inferring timings from rows.

    The 61-bar eligibility check is deliberately larger than the high/low range:
    close(t-60) participates only in Delta, while H/L use t-59 through t.
    """
    S = classifier.session_open(trade_date, session)
    close = classifier.session_close(trade_date, session)
    t, E, X = S + timedelta(minutes=119), S + timedelta(minutes=120), S + timedelta(minutes=180)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": session.value,
        "S_session_start_jst": S.isoformat(),
        "t_signal_jst": t.isoformat(),
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
    by_time = {bar.ts_jst: bar for bar in bars}
    expected = [t - timedelta(minutes=step) for step in range(60, -1, -1)]
    window = [by_time.get(stamp) for stamp in expected]
    if any(bar is None for bar in window):
        event["reason"] = "WINDOW_MISSING"
        return event
    rows = [bar for bar in window if bar is not None]
    if any(
        not bar.is_eligible or bar.trade_date != trade_date or bar.session is not session
        for bar in rows
    ):
        event["reason"] = "WINDOW_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    range_rows = rows[1:]
    H, L = max(bar.high for bar in range_rows), min(bar.low for bar in range_rows)
    current, start = rows[-1], rows[0]
    K, delta = 2 * current.close - H - L, current.close - start.close
    event.update(
        {
            "window_start_jst": expected[0].isoformat(),
            "window_end_jst": expected[-1].isoformat(),
            "required_window_bar_count": 61,
            "range_bar_count": 60,
            "range_start_jst": expected[1].isoformat(),
            "range_end_jst": expected[-1].isoformat(),
            "H_points": H,
            "L_points": L,
            "close_t_points": current.close,
            "close_t_minus_60_points": start.close,
            "K_points_times_1": K,
            "Delta_points": delta,
        }
    )
    if H <= L:
        event["reason"] = "NONPOSITIVE_RANGE"
        return event
    if K == 0:
        event["reason"] = "ZERO_K"
        return event
    if delta == 0:
        event["reason"] = "ZERO_DELTA"
        return event
    event.update(
        {
            "status": "eligible",
            "reason": "CONTIGUOUS_61_ELIGIBLE_H_GT_L_K_DELTA_NONZERO",
            "A_direction": "long" if K > 0 else "short",
            "D_direction": "long" if delta > 0 else "short",
        }
    )
    return event
