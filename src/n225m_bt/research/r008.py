"""Causal session-local compression breakout event selection for R008-Q001."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def session_groups(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    """Group an already-isolated view without changing its bar membership."""
    grouped: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.trade_date, bar.session)].append(bar)
    return {key: sorted(value, key=lambda item: item.ts_jst) for key, value in grouped.items()}


def compression_breakout_event(
    classifier: CalendarClassifier,
    trade_date: date,
    session: Session,
    bars: list[Bar],
    *,
    require_compression: bool,
) -> dict[str, object]:
    """Select the first valid R008 event, keeping later candidates recoverable.

    The three 30-bar windows precede t; the required 91 bars include t itself.
    Expected timestamps, eligibility, trade_date, and session define quality.
    """
    session_open = classifier.session_open(trade_date, session)
    session_close = classifier.session_close(trade_date, session)
    entry_cutoff = session_close - timedelta(minutes=15)
    force_flat = session_close - timedelta(minutes=5)
    by_timestamp = {bar.ts_jst: bar for bar in bars}
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": session.value,
        "rule": "A_compression" if require_compression else "D_unfiltered_breakout",
        "S_session_open_jst": session_open.isoformat(),
        "new_entry_cutoff_jst": entry_cutoff.isoformat(),
        "F_force_flat_jst": force_flat.isoformat(),
        "candidate_start_jst": (session_open + timedelta(minutes=90)).isoformat(),
        "candidate_end_jst": (session_open + timedelta(minutes=180)).isoformat(),
        "candidate_total": 91,
        "candidate_unevaluable": 0,
        "candidate_scheduled_outside_execution_window": 0,
        "candidate_zero_range": 0,
        "candidate_not_compressed": 0,
        "candidate_no_breakout": 0,
        "status": "no_event",
    }
    for offset in range(90, 181):
        candidate = session_open + timedelta(minutes=offset)
        expected = [candidate - timedelta(minutes=step) for step in range(90, -1, -1)]
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
        entry, exit_time = candidate + timedelta(minutes=1), candidate + timedelta(minutes=31)
        if entry > entry_cutoff or exit_time > force_flat:
            event["candidate_scheduled_outside_execution_window"] = (
                cast(int, event["candidate_scheduled_outside_execution_window"]) + 1
            )
            continue
        concrete = [bar for bar in window if bar is not None]
        w2, w1, w0 = concrete[0:30], concrete[30:60], concrete[60:90]
        r2 = max(bar.high for bar in w2) - min(bar.low for bar in w2)
        r1 = max(bar.high for bar in w1) - min(bar.low for bar in w1)
        r0 = max(bar.high for bar in w0) - min(bar.low for bar in w0)
        if r0 <= 0 or r1 + r2 <= 0:
            event["candidate_zero_range"] = cast(int, event["candidate_zero_range"]) + 1
            continue
        compressed = 4 * r0 <= r1 + r2
        if require_compression and not compressed:
            event["candidate_not_compressed"] = cast(int, event["candidate_not_compressed"]) + 1
            continue
        upper, lower, close = (
            max(bar.high for bar in w0),
            min(bar.low for bar in w0),
            concrete[90].close,
        )
        direction = "long" if close >= upper + 5 else "short" if close <= lower - 5 else None
        if direction is None:
            event["candidate_no_breakout"] = cast(int, event["candidate_no_breakout"]) + 1
            continue
        event.update(
            {
                "status": "event",
                "reason": "FIRST_COMPRESSION_BREAKOUT"
                if require_compression
                else "FIRST_UNFILTERED_BREAKOUT",
                "candidate_offset_minutes": offset,
                "t_signal_jst": candidate.isoformat(),
                "E_planned_entry_jst": entry.isoformat(),
                "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
                "X_planned_exit_jst": exit_time.isoformat(),
                "window_bar_count": 91,
                "W0_bar_count": len(w0),
                "W1_bar_count": len(w1),
                "W2_bar_count": len(w2),
                "R0_points": r0,
                "R1_points": r1,
                "R2_points": r2,
                "upper_U_points": upper,
                "lower_L_points": lower,
                "close_t_points": close,
                "compression_lhs_4R0": 4 * r0,
                "compression_rhs_R1_plus_R2": r1 + r2,
                "compression_qualifies": compressed,
                "breakout_buffer_ticks": 1,
                "direction": direction,
            }
        )
        return event
    return event
