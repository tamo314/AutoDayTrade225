"""Causal fixed-time directional-consistency event selection for R009-Q001."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def session_groups(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    """Group an already isolated view without changing its membership."""
    grouped: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.trade_date, bar.session)].append(bar)
    return {key: sorted(value, key=lambda item: item.ts_jst) for key, value in grouped.items()}


def directional_consistency_event(
    classifier: CalendarClassifier,
    trade_date: date,
    session: Session,
    bars: list[Bar],
    *,
    require_consistency: bool,
) -> dict[str, object]:
    """Evaluate the one preregistered S+119 signal without later substitution."""
    session_open = classifier.session_open(trade_date, session)
    session_close = classifier.session_close(trade_date, session)
    entry_cutoff = session_close - timedelta(minutes=15)
    force_flat = session_close - timedelta(minutes=5)
    signal_time = session_open + timedelta(minutes=119)
    entry_time = session_open + timedelta(minutes=120)
    exit_time = session_open + timedelta(minutes=180)
    expected = [signal_time - timedelta(minutes=step) for step in range(60, -1, -1)]
    by_timestamp = {bar.ts_jst: bar for bar in bars}
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": session.value,
        "rule": "A_directional_consistency" if require_consistency else "D_direction_only",
        "S_session_open_jst": session_open.isoformat(),
        "new_entry_cutoff_jst": entry_cutoff.isoformat(),
        "F_force_flat_jst": force_flat.isoformat(),
        "t_signal_jst": signal_time.isoformat(),
        "E_planned_entry_jst": entry_time.isoformat(),
        "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(),
        "required_window_bar_count": 61,
        "required_change_count": 60,
        "status": "no_event",
    }
    window = [by_timestamp.get(stamp) for stamp in expected]
    if any(
        bar is None
        or not bar.is_eligible
        or bar.trade_date != trade_date
        or bar.session is not session
        for bar in window
    ):
        event["reason"] = "WINDOW_MISSING_OR_INELIGIBLE"
        return event
    if entry_time > entry_cutoff or exit_time > force_flat:
        event["reason"] = "SCHEDULED_ENTRY_OR_EXIT_OUTSIDE_EXECUTION_WINDOW"
        return event
    concrete = [cast(Bar, bar) for bar in window]
    changes = [concrete[index].close - concrete[index - 1].close for index in range(1, 61)]
    delta = concrete[60].close - concrete[0].close
    variation = sum(abs(change) for change in changes)
    event.update(
        {
            "window_start_jst": expected[0].isoformat(),
            "window_end_jst": expected[-1].isoformat(),
            "delta_points": delta,
            "variation_points": variation,
            "sum_changes_points": sum(changes),
        }
    )
    if variation == 0:
        event["reason"] = "ZERO_VARIATION"
        return event
    if delta == 0:
        event["reason"] = "ZERO_DELTA"
        return event
    event["directional_ratio"] = abs(delta) / variation
    event["direction"] = "long" if delta > 0 else "short"
    event["consistency_lhs_2absdelta"] = 2 * abs(delta)
    event["consistency_rhs_variation"] = variation
    event["consistency_qualifies"] = 2 * abs(delta) >= variation
    if require_consistency and not cast(bool, event["consistency_qualifies"]):
        event["reason"] = "DIRECTIONAL_CONSISTENCY_BELOW_HALF"
        return event
    event.update({"status": "event", "reason": "DIRECTIONAL_CONSISTENCY" if require_consistency else "DIRECTION_NONZERO"})
    return event
