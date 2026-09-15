"""Causal two-window measurements for TASK-R101-Q001."""

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
class TwoStageMeasure:
    p_points: float
    c_points: float


def nearest_rank(values: Sequence[float], percentile: int) -> float:
    if percentile not in {40, 50, 60} or not values:
        raise ValueError("unregistered R101 quantile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def two_stage_measure(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    second_end: time = time(9, 14),
) -> tuple[TwoStageMeasure | None, dict[str, object]]:
    """Measure only complete normal bars through the fixed second-window close."""
    row: dict[str, object] = {"trade_date": target.isoformat(), "observation_valid": False}
    schedule = classifier.exchange_calendar.get(target)
    if schedule is None:
        row["reason"] = "NO_REGISTERED_SCHEDULE"
        return None, row
    zone = classifier.session_open(target, Session.DAY).tzinfo
    assert zone is not None
    first_start = datetime.combine(target, time(8, 45), zone)
    second_start = datetime.combine(target, time(9), zone)
    second_end_dt = datetime.combine(target, second_end, zone)
    second_count = int((second_end_dt - second_start).total_seconds() // 60) + 1
    row.update(
        schedule_version=schedule.schedule_version,
        first_window_start_jst=first_start.isoformat(),
        first_window_end_jst=(first_start + timedelta(minutes=14)).isoformat(),
        second_window_start_jst=second_start.isoformat(),
        second_window_end_jst=second_end_dt.isoformat(),
        first_scheduled_bar_count=15,
        second_scheduled_bar_count=second_count,
    )
    if (target, Session.DAY) in isolated:
        row["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return None, row
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    first = [lookup.get(first_start + timedelta(minutes=index)) for index in range(15)]
    second = [lookup.get(second_start + timedelta(minutes=index)) for index in range(second_count)]
    if not all(_valid(bar, target) and _valid(bar, target, close=True) for bar in first + second):
        row["reason"] = "WINDOW_INCOMPLETE_OR_INELIGIBLE"
        return None, row
    valid_first, valid_second = cast(list[Bar], first), cast(list[Bar], second)
    p, c = float(valid_first[-1].close - valid_first[0].open), float(
        valid_second[-1].close - valid_second[0].open
    )
    if p == 0 or c == 0:
        row["reason"] = "ZERO_FIRST_OR_SECOND_DISPLACEMENT"
        return None, row
    row.update(
        p_open_points=float(valid_first[0].open), p_close_points=float(valid_first[-1].close),
        c_open_points=float(valid_second[0].open), c_close_points=float(valid_second[-1].close),
        p_points=p, c_points=c, p_direction="up" if p > 0 else "down",
        c_direction="up" if c > 0 else "down", observation_valid=True, reason="VALID_TWO_STAGE_WINDOWS",
    )
    return TwoStageMeasure(p, c), row


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = LOOKBACK,
    min_valid: int = MIN_VALID,
    percentile: int = 50,
    second_end: time = time(9, 14),
) -> list[dict[str, object]]:
    """Create current-excluded state rows; no execution availability is read here."""
    if (lookback, min_valid) not in {(120, 100), (60, 50), (240, 200)}:
        raise ValueError("unregistered R101 reference profile")
    ordered = list(axis)
    observed = [two_stage_measure(classifier, day, bars, isolated, second_end=second_end) for day in ordered]
    measures, rows = [item[0] for item in observed], [item[1] for item in observed]
    for index, (current, row) in enumerate(zip(measures, rows, strict=True)):
        ref_indices = tuple(range(max(0, index - lookback), index))
        valid_indices = tuple(item for item in ref_indices if measures[item] is not None)
        row.update(
            index=index,
            reference_scheduled_trade_dates=[ordered[item].isoformat() for item in ref_indices],
            reference_valid_trade_dates=[ordered[item].isoformat() for item in valid_indices],
            reference_valid_count=len(valid_indices), q_ready=False, event="NONE", w_band=None,
        )
        if current is None:
            continue
        if len(valid_indices) < min_valid:
            row["reason"] = "INSUFFICIENT_VALID_PRIOR_OBSERVATIONS"
            continue
        refs = [cast(TwoStageMeasure, measures[item]) for item in valid_indices]
        qp = nearest_rank([abs(item.p_points) for item in refs], percentile)
        qc = nearest_rank([abs(item.c_points) for item in refs], percentile)
        if qp <= 0 or qc <= 0:
            row["reason"] = "DEGENERATE_PRIOR_QUANTILE"
            continue
        prior_e_w = [
            min(abs(item.p_points) / qp, abs(item.c_points) / qc)
            for item in refs
            if abs(item.p_points) >= qp and abs(item.c_points) >= qc
        ]
        if not prior_e_w:
            row["reason"] = "EMPTY_STRICT_PRIOR_E_DISTRIBUTION"
            continue
        w = min(abs(current.p_points) / qp, abs(current.c_points) / qc)
        event = "E" if w >= 1 else "NONE"
        if event == "E":
            event = "K" if current.p_points * current.c_points > 0 else "D"
        row.update(qp_abs_points=qp, qc_abs_points=qc, w=w, strict_prior_e_count=len(prior_e_w),
                   strict_prior_e_w_median=nearest_rank(prior_e_w, 50),
                   w_band="LOW" if w < nearest_rank(prior_e_w, 50) else "HIGH",
                   q_ready=True, event=event, reason="STATE_AVAILABLE")
    return rows


def build_events(
    ledger: Iterable[dict[str, object]],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    exit_time: time = time(10, 30),
    entry_delay_bars: int = 0,
) -> list[dict[str, object]]:
    """Append fixed signals and availability outcomes without changing selection."""
    events: list[dict[str, object]] = []
    for source in ledger:
        event = dict(source)
        event["status"] = "SKIPPED"
        if event.get("event") not in {"K", "D"}:
            events.append(event)
            continue
        target = date.fromisoformat(cast(str, event["trade_date"]))
        second_end = datetime.fromisoformat(cast(str, event["second_window_end_jst"]))
        zone = second_end.tzinfo
        assert zone is not None
        if entry_delay_bars not in {0, 1}:
            raise ValueError("unregistered R101 entry delay")
        entry_signal = second_end + timedelta(minutes=entry_delay_bars)
        exit_signal = datetime.combine(target, exit_time, zone) - timedelta(minutes=1)
        candidates = [bar for bar in bars.get((target, Session.DAY), []) if bar.ts_jst >= entry_signal + timedelta(minutes=1) and _valid(bar, target)]
        exits = [bar for bar in bars.get((target, Session.DAY), []) if bar.ts_jst >= exit_signal + timedelta(minutes=1) and _valid(bar, target)]
        event.update(selection_fixed_at_jst=second_end.isoformat(), entry_signal_jst=entry_signal.isoformat(), exit_signal_jst=exit_signal.isoformat())
        if not candidates:
            event.update(status="ENTRY_CANCELLED", reason="NO_ELIGIBLE_NORMAL_BAR_AFTER_FIXED_SIGNAL")
        else:
            event["entry_open_jst"] = candidates[0].ts_jst.isoformat()
            if not exits:
                event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
            else:
                event.update(status="EXECUTABLE", exit_open_jst=exits[0].ts_jst.isoformat(),
                             trade_direction="long" if event["c_direction"] == "up" else "short",
                             tse_regime="old" if target <= date(2024, 11, 1) else "new",
                             reason="STATE_FIXED_FIRST_ELIGIBLE_ENTRY_AND_EXIT")
        events.append(event)
    return events


def route(event: str, name: str) -> str | None:
    routes: dict[str, dict[str, str | None]] = {
        "A": {"K": "c", "D": None}, "U": {"K": "c", "D": "c"},
        "R": {"K": "reverse", "D": None}, "D_control": {"K": None, "D": "c"},
        "N": {"K": None, "D": None},
    }
    if name not in routes:
        raise ValueError("unknown R101 route")
    return routes[name].get(event)


def mbb_indices(length: int) -> NDArray[np.int64]:
    rng = np.random.default_rng(MBB_SEED)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def percentile_ci(values: NDArray[np.float64]) -> list[float]:
    return [float(item) for item in np.quantile(values, (0.025, 0.975), method="linear")]
