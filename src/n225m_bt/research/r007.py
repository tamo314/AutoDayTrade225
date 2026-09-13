"""Causal local-shock event selection for R007-Q001."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def session_groups(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    """Group the already-isolated view without changing its bars."""
    grouped: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.trade_date, bar.session)].append(bar)
    return {key: sorted(value, key=lambda item: item.ts_jst) for key, value in grouped.items()}


def local_shock_event(
    classifier: CalendarClassifier,
    trade_date: date,
    session: Session,
    bars: list[Bar],
) -> dict[str, object]:
    """Return the first fixed-window local shock, or an auditable no-event.

    Expected timestamps, rather than row count, define the 62-bar window.
    Thus an ineligible or missing bar makes that candidate unevaluable but does
    not prevent a later candidate whose own full window has recovered.
    """
    session_open = classifier.session_open(trade_date, session)
    session_close = classifier.session_close(trade_date, session)
    # The runner rejects any active contract other than these frozen settings.
    entry_cutoff = session_close - timedelta(minutes=15)
    force_flat = session_close - timedelta(minutes=5)
    by_timestamp = {bar.ts_jst: bar for bar in bars}
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": session.value,
        "S_session_open_jst": session_open.isoformat(),
        "new_entry_cutoff_jst": entry_cutoff.isoformat(),
        "F_force_flat_jst": force_flat.isoformat(),
        "candidate_start_jst": (session_open + timedelta(minutes=61)).isoformat(),
        "candidate_end_jst": (session_open + timedelta(minutes=180)).isoformat(),
        "candidate_total": 120,
        "candidate_unevaluable": 0,
        "candidate_scheduled_outside_execution_window": 0,
        "candidate_evaluable_no_shock": 0,
        "status": "no_event",
    }
    for offset in range(61, 181):
        candidate = session_open + timedelta(minutes=offset)
        expected = [candidate - timedelta(minutes=step) for step in range(61, -1, -1)]
        window = [by_timestamp.get(stamp) for stamp in expected]
        if any(
            bar is None
            or not bar.is_eligible
            or bar.trade_date != trade_date
            or bar.session is not session
            for bar in window
        ):
            event["candidate_unevaluable"] = cast(int, event["candidate_unevaluable"]) + 1
            continue
        concrete = [bar for bar in window if bar is not None]
        entry = candidate + timedelta(minutes=1)
        exit_time = entry + timedelta(minutes=15)
        # The engine's no-new-entry callback is evaluated on the signal bar;
        # entry is additionally required to be within its documented window.
        if candidate >= entry_cutoff or entry >= entry_cutoff or exit_time > force_flat:
            event["candidate_scheduled_outside_execution_window"] = (
                cast(int, event["candidate_scheduled_outside_execution_window"]) + 1
            )
            continue
        q = concrete[61].close - concrete[60].close
        scale_changes = [
            abs(concrete[index].close - concrete[index - 1].close) for index in range(1, 61)
        ]
        scale = median(scale_changes)
        threshold = max(4 * scale, 20)
        if abs(q) < threshold:
            event["candidate_evaluable_no_shock"] = cast(int, event["candidate_evaluable_no_shock"]) + 1
            continue
        event.update(
            {
                "status": "event",
                "reason": "FIRST_ABS_Q_AT_LEAST_MAX_4_MEDIAN_ABS_Q_4_TICKS",
                "t_signal_jst": candidate.isoformat(),
                "E_planned_entry_jst": entry.isoformat(),
                "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
                "X_planned_exit_jst": exit_time.isoformat(),
                "q_t_points": q,
                "m_t_points": scale,
                "threshold_T_points": threshold,
                "scale_change_count": len(scale_changes),
                "window_bar_count": len(concrete),
                "candidate_offset_minutes": offset,
            }
        )
        return event
    event["reason"] = "NO_QUALIFYING_EVALUABLE_CANDIDATE"
    return event
