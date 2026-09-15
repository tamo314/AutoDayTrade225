"""Causal event construction for TASK-R103-Q001.

The state ledger deliberately ends at the 12:44 confirmation close.  Execution
availability is appended later and cannot alter K/D classification.
"""

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
class LunchConfirmationMeasure:
    p_points: float
    c_points: float


def nearest_rank(values: Sequence[float], percentile: int) -> float:
    if percentile not in {40, 50, 60} or not values:
        raise ValueError("unregistered R103 quantile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def lunch_confirmation_measure(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    confirmation_end: time = time(12, 44),
) -> tuple[LunchConfirmationMeasure | None, dict[str, object]]:
    """Measure complete 11:30--12:29 and 12:30--confirmation-end windows."""
    row: dict[str, object] = {"trade_date": target.isoformat(), "observation_valid": False}
    schedule = classifier.exchange_calendar.get(target)
    if schedule is None:
        row["reason"] = "NO_REGISTERED_SCHEDULE"
        return None, row
    zone = classifier.session_open(target, Session.DAY).tzinfo
    assert zone is not None
    first_start, second_start = datetime.combine(target, time(11, 30), zone), datetime.combine(target, time(12, 30), zone)
    second_end = datetime.combine(target, confirmation_end, zone)
    second_count = int((second_end - second_start).total_seconds() // 60) + 1
    row.update(
        schedule_version=schedule.schedule_version,
        first_window_start_jst=first_start.isoformat(), first_window_end_jst=(first_start + timedelta(minutes=59)).isoformat(),
        second_window_start_jst=second_start.isoformat(), second_window_end_jst=second_end.isoformat(),
        first_scheduled_bar_count=60, second_scheduled_bar_count=second_count,
    )
    if (target, Session.DAY) in isolated:
        row["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return None, row
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    first = [lookup.get(first_start + timedelta(minutes=index)) for index in range(60)]
    second = [lookup.get(second_start + timedelta(minutes=index)) for index in range(second_count)]
    if not all(_valid(bar, target) and _valid(bar, target, close=True) for bar in first + second):
        row["reason"] = "WINDOW_INCOMPLETE_OR_INELIGIBLE"
        return None, row
    valid_first, valid_second = cast(list[Bar], first), cast(list[Bar], second)
    p, c = float(valid_first[-1].close - valid_first[0].open), float(valid_second[-1].close - valid_second[0].open)
    if p == 0 or c == 0:
        row["reason"] = "ZERO_LUNCH_OR_CONFIRMATION_DISPLACEMENT"
        return None, row
    row.update(
        p_open_points=float(valid_first[0].open), p_close_points=float(valid_first[-1].close),
        c_open_points=float(valid_second[0].open), c_close_points=float(valid_second[-1].close),
        p_points=p, c_points=c, p_direction="up" if p > 0 else "down", c_direction="up" if c > 0 else "down",
        observation_valid=True, reason="VALID_LUNCH_AND_CASH_CONFIRMATION_WINDOWS",
    )
    return LunchConfirmationMeasure(p, c), row


def state_ledger(
    classifier: CalendarClassifier, axis: Iterable[date], bars: dict[tuple[date, Session], list[Bar]], isolated: set[tuple[date, Session]],
    *, lookback: int = LOOKBACK, min_valid: int = MIN_VALID, percentile: int = 50, confirmation_end: time = time(12, 44),
) -> list[dict[str, object]]:
    if (lookback, min_valid) not in {(120, 100), (60, 50), (240, 200)}:
        raise ValueError("unregistered R103 reference profile")
    ordered = list(axis)
    observed = [lunch_confirmation_measure(classifier, day, bars, isolated, confirmation_end=confirmation_end) for day in ordered]
    measures, rows = [item[0] for item in observed], [item[1] for item in observed]
    for index, (current, row) in enumerate(zip(measures, rows, strict=True)):
        ref_indices = tuple(range(max(0, index - lookback), index))
        valid_indices = tuple(item for item in ref_indices if measures[item] is not None)
        row.update(index=index, reference_scheduled_trade_dates=[ordered[item].isoformat() for item in ref_indices], reference_valid_trade_dates=[ordered[item].isoformat() for item in valid_indices], reference_valid_count=len(valid_indices), q_ready=False, event="NONE", w_band=None)
        if current is None:
            continue
        if len(valid_indices) < min_valid:
            row["reason"] = "INSUFFICIENT_VALID_PRIOR_OBSERVATIONS"
            continue
        refs = [cast(LunchConfirmationMeasure, measures[item]) for item in valid_indices]
        qp, qc = nearest_rank([abs(item.p_points) for item in refs], percentile), nearest_rank([abs(item.c_points) for item in refs], percentile)
        if qp <= 0 or qc <= 0:
            row["reason"] = "DEGENERATE_PRIOR_QUANTILE"
            continue
        w = min(abs(current.p_points) / qp, abs(current.c_points) / qc)
        event = "E" if w >= 1 else "NONE"
        if event == "E":
            event = "K" if current.p_points * current.c_points > 0 else "D"
        row.update(qp_abs_points=qp, qc_abs_points=qc, w=w, w_band="LOW" if w < 1.5 else "HIGH", q_ready=True, event=event, reason="STATE_AVAILABLE")
    return rows


def build_events(
    ledger: Iterable[dict[str, object]], bars: dict[tuple[date, Session], list[Bar]], *, exit_time: time = time(14, 30), entry_delay_bars: int = 0,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    if entry_delay_bars not in {0, 1}:
        raise ValueError("unregistered R103 entry delay")
    for source in ledger:
        event = dict(source)
        event["status"] = "SKIPPED"
        if event.get("event") not in {"K", "D"}:
            events.append(event)
            continue
        target = date.fromisoformat(cast(str, event["trade_date"]))
        end = datetime.fromisoformat(cast(str, event["second_window_end_jst"]))
        zone = end.tzinfo
        assert zone is not None
        entry_signal, exit_signal = end + timedelta(minutes=entry_delay_bars), datetime.combine(target, exit_time, zone) - timedelta(minutes=1)
        candidates = [bar for bar in bars.get((target, Session.DAY), []) if bar.ts_jst >= entry_signal + timedelta(minutes=1) and _valid(bar, target)]
        exits = [bar for bar in bars.get((target, Session.DAY), []) if bar.ts_jst >= exit_signal + timedelta(minutes=1) and _valid(bar, target)]
        event.update(selection_fixed_at_jst=end.isoformat(), entry_signal_jst=entry_signal.isoformat(), exit_signal_jst=exit_signal.isoformat())
        if not candidates:
            event.update(status="ENTRY_CANCELLED", reason="NO_ELIGIBLE_NORMAL_BAR_AFTER_FIXED_SIGNAL")
        else:
            event["entry_open_jst"] = candidates[0].ts_jst.isoformat()
            if not exits:
                event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
            else:
                event.update(status="EXECUTABLE", exit_open_jst=exits[0].ts_jst.isoformat(), trade_direction="long" if event["c_direction"] == "up" else "short", tse_regime="old" if target <= date(2024, 11, 1) else "new", reason="STATE_FIXED_FIRST_ELIGIBLE_ENTRY_AND_EXIT")
        events.append(event)
    return events


def route(event: str, name: str) -> str | None:
    routes: dict[str, dict[str, str | None]] = {"A": {"K": "c", "D": None}, "U": {"K": "c", "D": "c"}, "R": {"K": "reverse", "D": None}, "D_control": {"K": None, "D": "c"}, "N": {"K": None, "D": None}}
    if name not in routes:
        raise ValueError("unknown R103 route")
    return routes[name].get(event)


def mbb_indices(length: int) -> NDArray[np.int64]:
    rng = np.random.default_rng(MBB_SEED)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def percentile_ci(values: NDArray[np.float64]) -> list[float]:
    return [float(item) for item in np.quantile(values, (0.025, 0.975), method="linear")]
