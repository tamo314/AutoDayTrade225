"""Causal morning efficiency state and fixed routes for TASK-R100-Q001."""

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
LOOKBACK, MIN_VALID = 120, 100
MBB_BLOCK_LENGTH, MBB_REPETITIONS, MBB_SEED = 20, 10_000, 20260916


@dataclass(frozen=True)
class MorningMeasure:
    """PnL-free measurement observed at the 11:29 close."""

    r_points: float
    e_efficiency: float
    direction: int
    v_points: float | None = None


@dataclass(frozen=True)
class StateRow:
    index: int
    state: str | None
    reason: str
    references: tuple[int, ...]
    r_band: str | None


def nearest_rank(values: Sequence[float], percentile: int) -> float:
    if percentile not in {25, 33, 40, 50, 60, 67, 75} or not values:
        raise ValueError("unregistered R100 quantile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def state_rows(
    measures: Sequence[MorningMeasure | None],
    *,
    lookback: int = LOOKBACK,
    min_valid: int = MIN_VALID,
    e_low: int = 33,
    e_high: int = 67,
    r_cutoff: int = 50,
    event_metric: str = "r",
) -> list[StateRow]:
    """Use only the exact preceding scheduled observations, without backfill."""
    if (lookback, min_valid) not in {(120, 100), (60, 50), (240, 200)}:
        raise ValueError("unregistered R100 reference profile")
    if event_metric not in {"r", "v"}:
        raise ValueError("unregistered R100 event metric")
    result: list[StateRow] = []
    for index, current in enumerate(measures):
        reference_indices = tuple(range(max(0, index - lookback), index))
        valid_indices = tuple(item for item in reference_indices if measures[item] is not None)
        if current is None:
            result.append(StateRow(index, None, "CURRENT_MORNING_UNAVAILABLE", valid_indices, None))
            continue
        if len(valid_indices) < min_valid:
            result.append(
                StateRow(index, None, "INSUFFICIENT_VALID_PRIOR_MORNINGS", valid_indices, None)
            )
            continue
        metric_values = [
            cast(MorningMeasure, measures[item]).r_points
            if event_metric == "r"
            else cast(MorningMeasure, measures[item]).v_points
            for item in valid_indices
        ]
        e_values = [cast(MorningMeasure, measures[item]).e_efficiency for item in valid_indices]
        if any(value is None for value in metric_values):
            result.append(StateRow(index, None, "EVENT_METRIC_UNAVAILABLE", valid_indices, None))
            continue
        q_elow, q_ehigh = nearest_rank(e_values, e_low), nearest_rank(e_values, e_high)
        if q_elow >= q_ehigh:
            result.append(StateRow(index, None, "EFFICIENCY_QUANTILES_DEGENERATE", valid_indices, None))
            continue
        metric = current.r_points if event_metric == "r" else current.v_points
        assert metric is not None
        q_metric, q_metric75 = nearest_rank(cast(list[float], metric_values), r_cutoff), nearest_rank(
            cast(list[float], metric_values), 75
        )
        band = "R50_75" if metric >= q_metric and metric < q_metric75 else (
            "R75_100" if metric >= q_metric75 else None
        )
        event = metric >= q_metric and current.direction != 0
        state = "HE" if event and current.e_efficiency >= q_ehigh else (
            "LE" if event and current.e_efficiency <= q_elow else "NONE"
        )
        result.append(StateRow(index, state, "STATE_AVAILABLE", valid_indices, band))
    return result


def morning_measure(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
) -> tuple[MorningMeasure | None, dict[str, object]]:
    row: dict[str, object] = {"trade_date": target.isoformat(), "observation_valid": False}
    schedule = classifier.exchange_calendar.get(target)
    if schedule is None:
        row["reason"] = "NO_REGISTERED_SCHEDULE"
        return None, row
    zone = classifier.session_open(target, Session.DAY).tzinfo
    assert zone is not None
    start, end = datetime.combine(target, time(9), zone), datetime.combine(target, time(11, 29), zone)
    row.update(schedule_version=schedule.schedule_version, window_start_jst=start.isoformat(), window_end_jst=end.isoformat(), scheduled_bar_count=150)
    if (target, Session.DAY) in isolated:
        row["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return None, row
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    window = [lookup.get(start + timedelta(minutes=item)) for item in range(150)]
    if not window or not _valid(window[0], target):
        row["reason"] = "P0_0900_OPEN_MISSING_OR_INELIGIBLE"
        return None, row
    if not all(
        _valid(bar, target) and _valid(bar, target, close=True) and bar is not None
        and bar.high > 0 and bar.low > 0 and bar.high >= bar.low
        for bar in window
    ):
        row["reason"] = "MORNING_WINDOW_INCOMPLETE_OR_INELIGIBLE"
        return None, row
    concrete = cast(list[Bar], window)
    opening, closing = float(concrete[0].open), float(concrete[-1].close)
    high, low = float(max(bar.high for bar in concrete)), float(min(bar.low for bar in concrete))
    if high <= low:
        row["reason"] = "NONPOSITIVE_MORNING_RANGE"
        return None, row
    displacement = closing - opening
    measure = MorningMeasure(
        abs(displacement),
        abs(displacement) / (high - low),
        int(displacement > 0) - int(displacement < 0),
        high - low,
    )
    row.update(o_open_points=opening, c_close_points=closing, h_points=high, l_points=low, v_range_points=measure.v_points, displacement_points=displacement, r_abs_points=measure.r_points, e_efficiency=measure.e_efficiency, direction="up" if measure.direction > 0 else ("down" if measure.direction < 0 else "zero"), observation_valid=True, reason="VALID_MORNING_OHLC")
    return measure, row


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = LOOKBACK,
    min_valid: int = MIN_VALID,
    e_low: int = 33,
    e_high: int = 67,
    r_cutoff: int = 50,
    event_metric: str = "r",
) -> list[dict[str, object]]:
    ordered = list(axis)
    observations = [morning_measure(classifier, target, bars, isolated) for target in ordered]
    measures, rows = [item[0] for item in observations], [item[1] for item in observations]
    states = state_rows(
        measures,
        lookback=lookback,
        min_valid=min_valid,
        e_low=e_low,
        e_high=e_high,
        r_cutoff=r_cutoff,
        event_metric=event_metric,
    )
    for item, state in zip(rows, states, strict=True):
        item.update(index=state.index, reference_scheduled_trade_dates=[ordered[i].isoformat() for i in range(max(0, state.index - lookback), state.index)], reference_valid_trade_dates=[ordered[i].isoformat() for i in state.references], reference_valid_count=len(state.references), state=state.state, state_reason=state.reason, r_band=state.r_band, selection_metric=event_metric, q_ready=state.reason == "STATE_AVAILABLE")
    return rows


def build_events(
    ledger: Iterable[dict[str, object]],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_extra_bars: int = 0,
    exit_time: time = time(14, 55),
) -> list[dict[str, object]]:
    if entry_extra_bars not in {0, 1} or exit_time not in {time(14, 30), time(14, 55)}:
        raise ValueError("unregistered R100 execution profile")
    events: list[dict[str, object]] = []
    for source in ledger:
        event = dict(source)
        event.update(status="SKIPPED", entry_extra_bars=entry_extra_bars, exit_time_jst=exit_time.isoformat())
        if event.get("state") not in {"HE", "LE"}:
            events.append(event)
            continue
        target = date.fromisoformat(cast(str, event["trade_date"]))
        endpoint = datetime.fromisoformat(cast(str, event["window_end_jst"]))
        zone = endpoint.tzinfo
        assert zone is not None
        entry_not_before = datetime.combine(target, time(12, 30), zone) + timedelta(minutes=entry_extra_bars)
        exit_open = datetime.combine(target, exit_time, zone)
        exit_signal = exit_open - timedelta(minutes=1)
        lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
        event.update(selection_fixed_at_jst=endpoint.isoformat(), entry_not_before_jst=entry_not_before.isoformat(), exit_signal_jst=exit_signal.isoformat(), exit_open_jst=exit_open.isoformat())
        choices = [bar for bar in bars.get((target, Session.DAY), []) if bar.ts_jst >= entry_not_before and _valid(bar, target)]
        if not choices:
            event.update(status="ENTRY_CANCELLED", reason="NO_ELIGIBLE_NORMAL_BAR_AT_OR_AFTER_1230")
            events.append(event)
            continue
        entry = choices[0]
        event.update(
            entry_signal_jst=(entry.ts_jst - timedelta(minutes=1)).isoformat(),
            entry_open_jst=entry.ts_jst.isoformat(),
        )
        if entry.ts_jst >= exit_open or not _valid(lookup.get(exit_signal), target, close=True) or not _valid(lookup.get(exit_open), target):
            event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
            events.append(event)
            continue
        direction = "long" if cast(float, event["displacement_points"]) > 0 else "short"
        event.update(status="EXECUTABLE", reason="STATE_FIXED_1129_FIRST_ELIGIBLE_AFTER_1230_FIXED_EXIT_EXECUTABLE", continuation_direction=direction, reversal_direction="short" if direction == "long" else "long", tse_regime="old" if target <= date(2024, 11, 1) else "new")
        events.append(event)
    return events


def route(state: str | None, name: str) -> str | None:
    routes: dict[str, dict[str, str | None]] = {
        "A": {"HE": "continuation", "LE": "reversal"},
        "C": {"HE": "continuation", "LE": "continuation"},
        "F": {"HE": "reversal", "LE": "reversal"},
        "I": {"HE": "reversal", "LE": "continuation"},
        "N": {"HE": None, "LE": None},
    }
    if name not in routes:
        raise ValueError("unknown R100 route")
    if state is None:
        return None
    return routes[name].get(state)


def mbb_indices(length: int) -> NDArray[np.int64]:
    rng = np.random.default_rng(MBB_SEED)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def percentile_ci(values: NDArray[np.float64]) -> list[float]:
    return [float(item) for item in np.quantile(values, (0.025, 0.975), method="linear")]
