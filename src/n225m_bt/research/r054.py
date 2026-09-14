"""Causal TSE-opening-range failed-breakout events for R054-Q001."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


@dataclass(frozen=True)
class R054Specification:
    """All relative planned-bar positions for one independently rebuilt specification."""

    opening_minutes: int = 30
    confirmation_minutes: int = 5
    holding_minutes: int = 30
    search_end_minutes: int = 90
    required_exit_minutes: tuple[int, ...] = (15, 30, 45)


BASE = R054Specification()


def r054_event(
    classifier: CalendarClassifier,
    target: date,
    target_bars: list[Bar] | None,
    *,
    specification: R054Specification = BASE,
    target_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Classify the first close breakout using only information through confirmation."""
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "session": "day",
        "status": "skipped",
        "opening_minutes": specification.opening_minutes,
        "confirmation_minutes": specification.confirmation_minutes,
        "holding_minutes": specification.holding_minutes,
    }
    if not development_start <= target <= development_end:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    if target_quarantined:
        event["reason"] = "TARGET_QUARANTINED"
        return event
    try:
        start = classifier.session_open(target, Session.DAY)
    except ValueError:
        event["reason"] = "NO_SCHEDULED_TARGET_DAY"
        return event
    end = start + timedelta(minutes=specification.search_end_minutes - 1)
    event.update(
        S_scheduled_open_jst=start.isoformat(),
        opening_end_jst=(start + timedelta(minutes=specification.opening_minutes - 1)).isoformat(),
        search_start_jst=(start + timedelta(minutes=specification.opening_minutes)).isoformat(),
        search_end_jst=end.isoformat(),
    )
    if target_bars is None:
        event["reason"] = "TARGET_MISSING"
        return event
    by_time = {bar.ts_jst: bar for bar in target_bars}

    def valid(row: Bar | None) -> bool:
        return bool(
            row is not None
            and row.is_eligible
            and row.trade_date == target
            and row.session is Session.DAY
        )

    common_last_index = (
        specification.search_end_minutes
        - 1
        + specification.confirmation_minutes
        + 1
        + max(specification.required_exit_minutes)
    )
    common_path = [by_time.get(start + timedelta(minutes=index)) for index in range(common_last_index + 1)]
    if not all(valid(row) for row in common_path):
        event["reason"] = "COMMON_E_PATH_MISSING_OR_INELIGIBLE"
        return event

    opening = [by_time.get(start + timedelta(minutes=index)) for index in range(specification.opening_minutes)]
    if not all(valid(row) for row in opening):
        event["reason"] = "TARGET_OPENING_MISSING_OR_INELIGIBLE"
        return event
    rows = [row for row in opening if row is not None]
    high, low = max(row.high for row in rows), min(row.low for row in rows)
    event.update(H_points=high, L_points=low, R_points=high - low)
    if high <= low:
        event["reason"] = "ZERO_OPENING_RANGE"
        return event
    for index in range(specification.opening_minutes, specification.search_end_minutes):
        breakout = by_time.get(start + timedelta(minutes=index))
        if not valid(breakout):
            event["reason"] = "BREAKOUT_SEARCH_MISSING_OR_INELIGIBLE"
            return event
        assert breakout is not None
        if not (breakout.close > high or breakout.close < low):
            continue
        direction = "long" if breakout.close > high else "short"
        confirmation_time = breakout.ts_jst + timedelta(minutes=specification.confirmation_minutes)
        confirmation = by_time.get(confirmation_time)
        if not valid(confirmation):
            event.update(reason="CONFIRMATION_MISSING_OR_INELIGIBLE", breakout_ts_jst=breakout.ts_jst.isoformat())
            return event
        assert confirmation is not None
        accepted = confirmation.close > high if direction == "long" else confirmation.close < low
        entry = confirmation_time + timedelta(minutes=1)
        exit_signal = confirmation_time + timedelta(minutes=specification.holding_minutes)
        event.update(
            status="accepted" if accepted else "failed",
            reason="FIXED_CONFIRMATION_CLASSIFICATION",
            breakout_ts_jst=breakout.ts_jst.isoformat(),
            breakout_direction=direction,
            breakout_close=breakout.close,
            confirmation_ts_jst=confirmation_time.isoformat(),
            confirmation_close=confirmation.close,
            E_planned_entry_jst=entry.isoformat(),
            EXIT_signal_bar_start_jst=exit_signal.isoformat(),
            X_planned_exit_jst=(exit_signal + timedelta(minutes=1)).isoformat(),
            breakout_elapsed_scheduled_minutes=index,
            breakout_excess_bps=(
                (breakout.close - high) / high * 10_000
                if direction == "long"
                else (low - breakout.close) / low * 10_000
            ),
            opening_range_bps=(high - low) / rows[0].open * 10_000,
        )
        return event
    event["reason"] = "NO_CLOSE_BREAKOUT"
    return event
