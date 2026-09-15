"""Causal false-break/re-entry measurements for TASK-R102-Q001."""

# ruff: noqa: E701, E702

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r088_cash_open_path_efficiency import _valid

DEVELOPMENT_START, DEVELOPMENT_END = date(2021, 1, 1), date(2025, 6, 30)
LOOKBACK, MIN_VALID = 120, 50
MBB_BLOCK_LENGTH, MBB_REPETITIONS, MBB_SEED = 20, 10_000, 20260916


@dataclass(frozen=True)
class FalseBreakMeasure:
    direction: int
    classification: str
    width: float
    excess_up: float
    excess_down: float

    @property
    def x(self) -> float:
        return max(self.excess_up, self.excess_down) / self.width


def nearest_rank(values: Sequence[float], percentile: int = 50) -> float:
    if percentile != 50 or not values:
        raise ValueError("unregistered R102 quantile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def false_break_measure(
    classifier: CalendarClassifier, target: date, bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]], *, opening_end: time = time(9, 14),
    confirmation_minutes: int = 15, threshold_ticks: int = 1,
    relative_threshold: bool = False, tick_size: float = 5.0,
) -> tuple[FalseBreakMeasure | None, dict[str, object]]:
    """Classify exclusively from bars available at the confirmation-window close."""
    row: dict[str, object] = {"trade_date": target.isoformat(), "observation_valid": False}
    schedule = classifier.exchange_calendar.get(target)
    if schedule is None:
        row["reason"] = "NO_REGISTERED_SCHEDULE"; return None, row
    zone = classifier.session_open(target, Session.DAY).tzinfo
    assert zone is not None
    start = datetime.combine(target, time(9), zone)
    opening_count = int((datetime.combine(target, opening_end, zone) - start).total_seconds() // 60) + 1
    confirm_start = start + timedelta(minutes=opening_count)
    confirm_end = confirm_start + timedelta(minutes=confirmation_minutes - 1)
    row.update(schedule_version=schedule.schedule_version,
               opening_start_jst=start.isoformat(), opening_end_jst=(start + timedelta(minutes=opening_count - 1)).isoformat(),
               confirmation_start_jst=confirm_start.isoformat(), confirmation_end_jst=confirm_end.isoformat(),
               opening_scheduled_bar_count=opening_count, confirmation_scheduled_bar_count=confirmation_minutes,
               threshold_ticks=threshold_ticks, relative_threshold=relative_threshold, tick_size_points=tick_size)
    if (target, Session.DAY) in isolated:
        row["reason"] = "R004_DAY_SESSION_QUARANTINED"; return None, row
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    opening = [lookup.get(start + timedelta(minutes=index)) for index in range(opening_count)]
    confirmation = [lookup.get(confirm_start + timedelta(minutes=index)) for index in range(confirmation_minutes)]
    if not all(_valid(bar, target) and _valid(bar, target, close=True) for bar in opening + confirmation):
        row["reason"] = "WINDOW_INCOMPLETE_OR_INELIGIBLE"; return None, row
    first, second = cast(list[Bar], opening), cast(list[Bar], confirmation)
    high, low = float(max(bar.high for bar in first)), float(min(bar.low for bar in first))
    width = high - low
    if width <= 0:
        row["reason"] = "NONPOSITIVE_OPENING_RANGE"; return None, row
    excess_up, excess_down = float(max(bar.high for bar in second) - high), float(low - min(bar.low for bar in second))
    threshold = max(threshold_ticks * tick_size, 0.10 * width) if relative_threshold else threshold_ticks * tick_size
    up, down = excess_up >= threshold, excess_down >= threshold
    row.update(h0_points=high, l0_points=low, w_points=width, eu_points=excess_up, ed_points=excess_down,
               threshold_points=threshold, x=max(excess_up, excess_down) / width)
    if up == down:
        row.update(reason="NO_UNIQUE_DIRECTIONAL_BREAK" if not up else "BOTH_SIDES_BROKEN", event="NONE")
        return None, row
    direction = 1 if up else -1
    close = float(second[-1].close)
    classification = "Q" if low <= close <= high else ("T" if (close > high if direction > 0 else close < low) else "Z")
    measure = FalseBreakMeasure(direction, classification, width, excess_up, excess_down)
    row.update(observation_valid=True, event="U", b=direction, b_direction="up" if direction > 0 else "down",
               classification=classification, confirmation_close_points=close, reason="VALID_UNIDIRECTIONAL_BREAK")
    return measure, row


def state_ledger(
    classifier: CalendarClassifier, axis: Iterable[date], bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]], *, opening_end: time = time(9, 14), confirmation_minutes: int = 15,
    threshold_ticks: int = 1, relative_threshold: bool = False, tick_size: float = 5.0,
) -> list[dict[str, object]]:
    """Build classification and current-excluded x bands before any execution lookup."""
    if (opening_end, confirmation_minutes, threshold_ticks, relative_threshold) not in {
        (time(9, 14), 15, 1, False), (time(9, 14), 15, 2, False), (time(9, 14), 15, 1, True),
        (time(9, 9), 15, 1, False), (time(9, 19), 15, 1, False),
        (time(9, 14), 10, 1, False), (time(9, 14), 20, 1, False),
    }:
        raise ValueError("unregistered R102 measurement profile")
    ordered = list(axis)
    observed = [false_break_measure(classifier, day, bars, isolated, opening_end=opening_end,
                                    confirmation_minutes=confirmation_minutes, threshold_ticks=threshold_ticks,
                                    relative_threshold=relative_threshold, tick_size=tick_size) for day in ordered]
    measures, rows = [item[0] for item in observed], [item[1] for item in observed]
    for index, (measure, row) in enumerate(zip(measures, rows, strict=True)):
        refs = tuple(range(max(0, index - LOOKBACK), index))
        events = tuple(item for item in refs if measures[item] is not None)
        row.update(index=index, reference_scheduled_trade_dates=[ordered[item].isoformat() for item in refs],
                   reference_unidirectional_trade_dates=[ordered[item].isoformat() for item in events],
                   reference_unidirectional_count=len(events), q_ready=False, x_band=None)
        if measure is None or len(events) < MIN_VALID:
            if measure is not None: row["reason"] = "INSUFFICIENT_PRIOR_UNIDIRECTIONAL_EVENTS"
            continue
        q50 = nearest_rank([cast(FalseBreakMeasure, measures[item]).x for item in events])
        row.update(q_ready=True, prior_x_q50=q50, x_band="LOW" if measure.x < q50 else "HIGH")
    return rows


def build_events(ledger: Iterable[dict[str, object]], bars: dict[tuple[date, Session], list[Bar]], *,
                 entry_delay_bars: int = 0, exit_time: time = time(10, 30)) -> list[dict[str, object]]:
    """Append availability outcomes; event identity and class are already immutable."""
    if entry_delay_bars not in {0, 1} or exit_time not in {time(10), time(10, 30), time(11)}:
        raise ValueError("unregistered R102 execution profile")
    events: list[dict[str, object]] = []
    for source in ledger:
        event = dict(source); event["status"] = "SKIPPED"
        if event.get("event") != "U": events.append(event); continue
        target = date.fromisoformat(cast(str, event["trade_date"])); end = datetime.fromisoformat(cast(str, event["confirmation_end_jst"]))
        zone = end.tzinfo; assert zone is not None
        entry_signal = end + timedelta(minutes=entry_delay_bars)
        exit_signal = datetime.combine(target, exit_time, zone) - timedelta(minutes=1)
        candidates = [bar for bar in bars.get((target, Session.DAY), []) if bar.ts_jst >= entry_signal + timedelta(minutes=1) and _valid(bar, target)]
        exits = [bar for bar in bars.get((target, Session.DAY), []) if bar.ts_jst >= exit_signal + timedelta(minutes=1) and _valid(bar, target)]
        event.update(selection_fixed_at_jst=end.isoformat(), entry_signal_jst=entry_signal.isoformat(), exit_signal_jst=exit_signal.isoformat())
        if not candidates: event.update(status="ENTRY_CANCELLED", reason="NO_ELIGIBLE_NORMAL_BAR_AFTER_FIXED_SIGNAL")
        else:
            event["entry_open_jst"] = candidates[0].ts_jst.isoformat()
            if not exits: event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
            else: event.update(status="EXECUTABLE", exit_open_jst=exits[0].ts_jst.isoformat(), tse_regime="old" if target <= date(2024, 11, 1) else "new", reason="STATE_FIXED_FIRST_ELIGIBLE_ENTRY_AND_EXIT")
        events.append(event)
    return events


def route(classification: str | None, name: str) -> str | None:
    routes: dict[str, dict[str, str | None]] = {"S": {"Q": "reverse", "T": None, "Z": None}, "B": {"Q": "break", "T": None, "Z": None}, "U": {"Q": "reverse", "T": "reverse", "Z": "reverse"}, "T": {"Q": None, "T": "reverse", "Z": None}, "N": {"Q": None, "T": None, "Z": None}}
    if name not in routes: raise ValueError("unknown R102 route")
    if classification is None: return None
    return routes[name].get(classification)


def mbb_indices(length: int) -> NDArray[np.int64]:
    rng = np.random.default_rng(MBB_SEED); blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def percentile_ci(values: NDArray[np.float64]) -> list[float]:
    return [float(item) for item in np.quantile(values, (0.025, 0.975), method="linear")]
