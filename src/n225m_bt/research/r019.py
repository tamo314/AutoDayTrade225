"""Causal prior-same-session range-regime event selection for R019-Q001."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START = date(2021, 1, 1)
HISTORY_COUNT = 20
RANGE_WINDOW_MINUTES = 60
CURRENT_WINDOW_MINUTES = 30


def prior_scheduled_same_session_dates(
    classifier: CalendarClassifier, current: date, count: int = HISTORY_COUNT
) -> list[date] | None:
    """Return p1..pN from the calendar, never from observed bar availability."""
    dates: list[date] = []
    cursor = current
    for _ in range(count):
        calendar_day = classifier.exchange_calendar.get(cursor)
        previous = calendar_day.previous_trade_date if calendar_day else None
        if previous is None:
            return None
        dates.append(previous)
        cursor = previous
    return dates


def prior_range_regime_event(
    classifier: CalendarClassifier,
    trade_date: date,
    session: Session,
    current_bars: list[Bar],
    history_by_date: dict[date, list[Bar]],
    quarantined: set[tuple[date, Session]],
    *,
    development_start: date = DEVELOPMENT_START,
) -> dict[str, object]:
    """Create R019's single scheduled event using only earlier same-type sessions.

    Every p1..p20 is calendar-designated.  A missing, isolated, ineligible, or
    Development-external member invalidates the current event; the selector
    explicitly never searches farther back to replace it.
    """
    start = classifier.session_open(trade_date, session)
    close = classifier.session_close(trade_date, session)
    signal = start + timedelta(minutes=CURRENT_WINDOW_MINUTES - 1)
    entry = signal + timedelta(minutes=1)
    exit_time = start + timedelta(minutes=90)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": session.value,
        "S_session_open_bar_start_jst": start.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(),
        "new_entry_cutoff_jst": (close - timedelta(minutes=15)).isoformat(),
        "F_force_flat_jst": (close - timedelta(minutes=5)).isoformat(),
        "history_count_required": HISTORY_COUNT,
        "history_range_window_bars": RANGE_WINDOW_MINUTES,
        "current_initial_window_bars": CURRENT_WINDOW_MINUTES,
        "status": "skipped",
    }
    if entry > close - timedelta(minutes=15) or exit_time > close - timedelta(minutes=5):
        event["reason"] = "SCHEDULED_ENTRY_OR_EXIT_OUTSIDE_EXECUTION_WINDOW"
        return event
    scheduled = prior_scheduled_same_session_dates(classifier, trade_date)
    if scheduled is None:
        event["reason"] = "INSUFFICIENT_SCHEDULED_HISTORY"
        return event
    event["scheduled_prior_trade_dates_p1_to_p20"] = [item.isoformat() for item in scheduled]
    ranges: list[int] = []
    for index, prior in enumerate(scheduled, start=1):
        if prior < development_start:
            event.update({"reason": "REFERENCE_OUTSIDE_DEVELOPMENT", "reference_index": index})
            return event
        if (prior, session) in quarantined:
            event.update({"reason": "REFERENCE_SESSION_QUARANTINED", "reference_index": index})
            return event
        prior_start = classifier.session_open(prior, session)
        if prior_start + timedelta(minutes=RANGE_WINDOW_MINUTES - 1) >= start:
            event.update({"reason": "REFERENCE_NOT_KNOWN_BEFORE_CURRENT_S", "reference_index": index})
            return event
        rows_by_time = {bar.ts_jst: bar for bar in history_by_date.get(prior, [])}
        expected = [prior_start + timedelta(minutes=offset) for offset in range(RANGE_WINDOW_MINUTES)]
        rows = [rows_by_time.get(timestamp) for timestamp in expected]
        if any(row is None for row in rows):
            event.update({"reason": "REFERENCE_WINDOW_MISSING", "reference_index": index})
            return event
        concrete = [row for row in rows if row is not None]
        if any(
            not row.is_eligible or row.trade_date != prior or row.session is not session
            for row in concrete
        ):
            event.update(
                {"reason": "REFERENCE_WINDOW_INELIGIBLE_OR_SESSION_MISMATCH", "reference_index": index}
            )
            return event
        ranges.append(max(row.high for row in concrete) - min(row.low for row in concrete))
    # R1..R5 and R6..R20 are intentionally disjoint.  Zero is a valid observed range.
    recent_sum, prior_sum = sum(ranges[:5]), sum(ranges[5:])
    regime = 3 * recent_sum - prior_sum
    event.update(
        {
            "R_points_p1_to_p20": ranges,
            "recent_R1_to_R5_sum_points": recent_sum,
            "baseline_R6_to_R20_sum_points": prior_sum,
            "V_points": regime,
            "recent_average_times_15_points": 3 * recent_sum,
            "baseline_average_times_15_points": prior_sum,
        }
    )
    current_by_time = {bar.ts_jst: bar for bar in current_bars}
    current_times = [start + timedelta(minutes=offset) for offset in range(CURRENT_WINDOW_MINUTES)]
    current_rows = [current_by_time.get(timestamp) for timestamp in current_times]
    if any(row is None for row in current_rows):
        event["reason"] = "CURRENT_30BAR_WINDOW_MISSING"
        return event
    current_concrete = [row for row in current_rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_date or row.session is not session
        for row in current_concrete
    ):
        event["reason"] = "CURRENT_30BAR_WINDOW_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    movement = current_concrete[-1].close - current_concrete[0].open
    event.update(
        {
            "open_S_points": current_concrete[0].open,
            "close_t_points": current_concrete[-1].close,
            "M_points": movement,
        }
    )
    if regime == 0:
        event["reason"] = "ZERO_V"
        return event
    if movement == 0:
        event["reason"] = "ZERO_M"
        return event
    follow = "long" if movement > 0 else "short"
    reverse = "short" if follow == "long" else "long"
    event.update(
        {
            "status": "eligible",
            "reason": "TWENTY_SCHEDULED_RANGES_AND_CURRENT_INITIAL_MOVE_NONZERO",
            "range_regime": "expanded" if regime > 0 else "contracted",
            "M_direction": follow,
            "A_direction": follow if regime > 0 else reverse,
            "D_direction": follow,
            "F_direction": reverse,
        }
    )
    return event
