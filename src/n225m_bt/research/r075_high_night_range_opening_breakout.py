"""Causal state selection for frozen R075-Q001 high-night-range breakout."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r074_low_night_range_opening_breakout import (
    DEVELOPMENT_START,
    HISTORY_COUNT,
    MAX_CALENDAR_LOOKBACK_DAYS,
    _next_eligible,
    _night_range,
    _stamp,
    _valid,
    nearest_rank,
    previous_calendar_eligible_nights,
)


def select_state(
    current_range: int,
    *,
    q25: int,
    q75: int,
    high_percentile_value: int,
) -> tuple[str | None, str | None]:
    """Return exclusive R075 state; no state may rescue a degenerate quantile day."""
    if q25 >= q75:
        return None, "STATE_DEGENERATE_Q25_GTE_Q75"
    if current_range >= high_percentile_value:
        return "high", None
    if current_range <= q25:
        return "low", None
    if q25 < current_range < q75:
        return "middle", None
    return None, "OUTSIDE_SELECTED_NIGHT_RANGE_STATE"


def r075_event(
    classifier: CalendarClassifier,
    target: date,
    bars_by_day: dict[date, list[Bar]],
    *,
    isolated: set[tuple[date, Session]],
    state_group: str = "high",
    high_percentile: int = 75,
    opening_minutes: int = 30,
    entry_delay_minutes: int = 0,
    exit_time: time = time(14, 30),
) -> dict[str, object]:
    """Select the frozen state-conditioned strict-close breakout from causal prefixes."""
    if state_group not in {"high", "middle", "low"}:
        raise ValueError("unregistered R075 state group")
    if high_percentile not in {67, 75, 80}:
        raise ValueError("unregistered R075 high percentile")
    if opening_minutes not in {20, 30, 45} or entry_delay_minutes not in {0, 1}:
        raise ValueError("unregistered R075 timing profile")
    if exit_time not in {time(14, 15), time(14, 30), time(14, 45)}:
        raise ValueError("unregistered R075 exit")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "SKIPPED",
        "state_group": state_group,
        "high_percentile": high_percentile,
        "opening_minutes": opening_minutes,
        "entry_delay_minutes": entry_delay_minutes,
        "exit_open_jst_planned_clock": exit_time.isoformat(timespec="minutes"),
        "history_count_required": HISTORY_COUNT,
        "max_calendar_lookback_days": MAX_CALENDAR_LOOKBACK_DAYS,
        "quantile_method": "nearest_rank ceil(n*p/100)-1",
    }
    references = previous_calendar_eligible_nights(classifier, target)
    if references is None:
        event["reason"] = "INSUFFICIENT_20_CALENDAR_ELIGIBLE_NIGHT_HISTORY_WITHIN_60_DAYS"
        return event
    event["reference_trade_dates_p1_to_p20"] = [item.isoformat() for item in references]
    if any(reference < DEVELOPMENT_START for reference in references):
        event["reason"] = "REFERENCE_OUTSIDE_DEVELOPMENT"
        return event
    reference_ranges: list[int] = []
    for index, reference in enumerate(references, start=1):
        value, reason, _ = _night_range(
            classifier, reference, bars_by_day.get(reference, []), isolated=isolated
        )
        if value is None:
            event.update(reason=f"REFERENCE_{reason}", reference_index=index)
            return event
        reference_ranges.append(value)
    current_range, current_reason, _ = _night_range(
        classifier, target, bars_by_day.get(target, []), isolated=isolated
    )
    if current_range is None:
        event["reason"] = f"CURRENT_{current_reason}"
        return event
    q25 = nearest_rank(reference_ranges, 25)
    q67 = nearest_rank(reference_ranges, 67)
    q75 = nearest_rank(reference_ranges, 75)
    q80 = nearest_rank(reference_ranges, 80)
    threshold = {67: q67, 75: q75, 80: q80}[high_percentile]
    event.update(
        reference_ranges_points_p1_to_p20=reference_ranges,
        current_night_range_points=current_range,
        q25_points=q25,
        q67_points=q67,
        q75_points=q75,
        q80_points=q80,
        high_threshold_points=threshold,
    )
    assigned, reason = select_state(
        current_range, q25=q25, q75=q75, high_percentile_value=threshold
    )
    if reason is not None or assigned != state_group:
        event["reason"] = reason or "OUTSIDE_SELECTED_NIGHT_RANGE_STATE"
        return event
    if (target, Session.DAY) in isolated:
        event["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return event
    bars = bars_by_day.get(target, [])
    lookup = {bar.ts_jst: bar for bar in bars}
    tz = classifier.session_open(target, Session.DAY).tzinfo
    opening_start = _stamp(target, time(9), tz)
    opening_rows = [lookup.get(opening_start + timedelta(minutes=index)) for index in range(opening_minutes)]
    if any(not _valid(row, target, Session.DAY) for row in opening_rows):
        event["reason"] = "OPENING_RANGE_MISSING_OR_INELIGIBLE"
        return event
    opening = [row for row in opening_rows if row is not None]
    high, low = max(row.high for row in opening), min(row.low for row in opening)
    event.update(opening_high_points=high, opening_low_points=low)
    search_start = opening_start + timedelta(minutes=opening_minutes)
    search_end = _stamp(target, time(11), tz)
    signal_bar: Bar | None = None
    direction: str | None = None
    current = search_start
    while current <= search_end:
        row = lookup.get(current)
        if not _valid(row, target, Session.DAY):
            event["reason"] = "BREAKOUT_SEARCH_MISSING_OR_INELIGIBLE"
            return event
        assert row is not None
        if row.close > high or row.close < low:
            signal_bar = row
            direction = "long" if row.close > high else "short"
            break
        current += timedelta(minutes=1)
    if signal_bar is None or direction is None:
        event["reason"] = "NO_STRICT_CLOSE_BREAKOUT_BY_1100"
        return event
    delayed_signal = signal_bar.ts_jst + timedelta(minutes=entry_delay_minutes)
    if not _valid(lookup.get(delayed_signal), target, Session.DAY):
        event.update(status="SIGNALLED", reason="DELAY_SIGNAL_MISSING_OR_INELIGIBLE")
        return event
    entry = _next_eligible(bars, delayed_signal)
    if entry is None:
        event.update(
            status="ENTRY_CANCELLED",
            reason="NO_NEXT_ELIGIBLE_ENTRY_WITHIN_10_MINUTES",
            breakout_direction=direction,
            breakout_bar_jst=signal_bar.ts_jst.isoformat(),
            signal_bar_jst=delayed_signal.isoformat(),
            entry_open_jst=None,
            entry_observable=False,
        )
        return event
    event.update(
        breakout_direction=direction,
        breakout_bar_jst=signal_bar.ts_jst.isoformat(),
        signal_bar_jst=delayed_signal.isoformat(),
        entry_open_jst=entry.ts_jst.isoformat(),
        entry_observable=True,
    )
    exit_signal = _stamp(target, (datetime.combine(target, exit_time) - timedelta(minutes=1)).time(), tz)
    exit_bar = lookup.get(_stamp(target, exit_time, tz))
    event.update(
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=(exit_bar.ts_jst.isoformat() if exit_bar is not None and _valid(exit_bar, target, Session.DAY) else None),
        exit_observable=_valid(lookup.get(exit_signal), target, Session.DAY) and _valid(exit_bar, target, Session.DAY),
    )
    if not bool(event["exit_observable"]):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    event.update(status="EXECUTABLE", reason="SELECTED_STATE_FIRST_STRICT_CLOSE_BREAKOUT")
    return event
