"""Causal all-magnitude first-hour continuation events for TASK-R080-Q001."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session

MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    value = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and value > 0
    )


def _execution_event(base: dict[str, object], bars: dict[tuple[date, Session], list[Bar]], *, entry_delay_minutes: int = 0, exit_time: time = time(14, 30)) -> dict[str, object]:
    """Turn an already-observed nonzero R into an event without magnitude selection."""
    if entry_delay_minutes not in {0, 1} or exit_time not in {time(14, 15), time(14, 30), time(14, 45)}:
        raise ValueError("unregistered R080 execution profile")
    event = dict(base)
    event.update(status="SKIPPED", entry_delay_minutes=entry_delay_minutes, exit_open_jst_planned_clock=exit_time.isoformat(timespec="minutes"))
    if not bool(event.get("r_valid")):
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    endpoint = datetime.fromisoformat(cast(str, event["signal_endpoint_jst"]))
    signal = endpoint + timedelta(minutes=entry_delay_minutes)
    entry = signal + timedelta(minutes=1)
    exit_open = datetime.combine(target, exit_time, signal.tzinfo)
    exit_signal = exit_open - timedelta(minutes=1)
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    event.update(
        selection_fixed_at_jst=endpoint.isoformat(),
        entry_signal_jst=signal.isoformat(),
        entry_open_jst=entry.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
    )
    if not _valid(lookup.get(signal), target, close=True) or not _valid(lookup.get(entry), target):
        event.update(status="ENTRY_CANCELLED", reason="ENTRY_SIGNAL_OR_NEXT_OPEN_MISSING_OR_INELIGIBLE")
        return event
    if not _valid(lookup.get(exit_signal), target, close=True) or not _valid(lookup.get(exit_open), target):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    movement = cast(int, event["r_points"])
    event.update(
        status="EXECUTABLE",
        reason="NONZERO_R_EXECUTABLE_NO_MAGNITUDE_SELECTION",
        continuation_direction="long" if movement > 0 else "short",
        fade_direction="short" if movement > 0 else "long",
    )
    return event


def primary_events(source_events: Iterable[dict[str, object]], bars: dict[tuple[date, Session], list[Bar]]) -> list[dict[str, object]]:
    """Reuse R078's observations, but select every valid nonzero R rather than E only."""
    return [_execution_event(dict(event), bars) for event in source_events]


def endpoint_events(classifier: CalendarClassifier, axis: Iterable[date], bars: dict[tuple[date, Session], list[Bar]], isolated: set[tuple[date, Session]], *, endpoint: time) -> list[dict[str, object]]:
    """Build a non-magnitude-selected event ledger at a registered alternative endpoint."""
    if endpoint not in {time(9, 44), time(10, 29)}:
        raise ValueError("unregistered R080 endpoint")
    result: list[dict[str, object]] = []
    for target in axis:
        stamp = datetime.combine(target, time(9), classifier.session_close(target, Session.DAY).tzinfo)
        end = datetime.combine(target, endpoint, stamp.tzinfo)
        event: dict[str, object] = {"trade_date": target.isoformat(), "r_valid": False, "signal_endpoint_jst": end.isoformat()}
        if (target, Session.DAY) in isolated:
            event["reason"] = "R004_DAY_SESSION_QUARANTINED"
        else:
            lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
            a_bar, b_bar = lookup.get(stamp), lookup.get(end)
            if not _valid(a_bar, target):
                event["reason"] = "CURRENT_0900_OPEN_MISSING_OR_INELIGIBLE"
            elif not _valid(b_bar, target, close=True):
                event["reason"] = "SIGNAL_ENDPOINT_CLOSE_MISSING_OR_INELIGIBLE"
            else:
                assert a_bar is not None and b_bar is not None
                movement = b_bar.close - a_bar.open
                event.update(r_valid=movement != 0, reason="ZERO_R" if movement == 0 else "VALID_R", a_0900_open_points=a_bar.open, b_endpoint_close_points=b_bar.close, r_points=movement, abs_r_points=abs(movement))
        result.append(_execution_event(event, bars))
    return result


def exclude_one_tick_or_less(events: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Fixed sensitivity: retain the event ledger and mark only abs(R) <= one tick as skipped."""
    result: list[dict[str, object]] = []
    for source in events:
        event = dict(source)
        if event.get("status") == "EXECUTABLE" and cast(int, event["abs_r_points"]) <= 5:
            event.update(status="SKIPPED", reason="SENSITIVITY_ABS_R_LE_ONE_TICK_EXCLUDED")
        result.append(event)
    return result


def attach_lagged_sign(events: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Attach only the previous valid-R sign; no current-day magnitude or future data is used."""
    result: list[dict[str, object]] = []
    prior: str | None = None
    for source in events:
        event = dict(source)
        event["lagged_r_direction"] = prior
        if bool(event.get("r_valid")):
            prior = "long" if cast(int, event["r_points"]) > 0 else "short"
        result.append(event)
    return result


def assign_abs_r_quintiles(events: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Stable trade-date tiebreaking is descriptive only and never changes execution."""
    result = [dict(event) for event in events]
    eligible = sorted((event for event in result if event.get("status") == "EXECUTABLE"), key=lambda item: (cast(int, item["abs_r_points"]), cast(str, item["trade_date"])))
    for rank, event in enumerate(eligible, start=1):
        event["abs_r_quintile"] = min(5, ceil(rank * 5 / len(eligible)))
    return result


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R080 MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def _ci(samples: NDArray[np.float64]) -> list[float]:
    low, high = np.quantile(samples, (0.025, 0.975), method="linear")
    return [float(low), float(high)]


def bootstrap(primary_daily: list[int], fade_daily: list[int], placebo_daily: list[int]) -> tuple[dict[str, object], NDArray[np.int64]]:
    if not (len(primary_daily) == len(fade_daily) == len(placebo_daily)):
        raise ValueError("R080 bootstrap axes are misaligned")
    index = mbb_indices(len(primary_daily))
    primary, fade, placebo = (np.asarray(values, dtype=float) for values in (primary_daily, fade_daily, placebo_daily))
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "continuation_scheduled_axis_mean_net_jpy_per_trade_date": {"estimate": fmean(primary_daily), "ci95_percentile_linear": _ci(primary[index].mean(axis=1))},
        "continuation_minus_fade_paired_daily_net_jpy": {"estimate": fmean([left - right for left, right in zip(primary_daily, fade_daily, strict=True)]), "ci95_percentile_linear": _ci((primary[index] - fade[index]).mean(axis=1))},
        "continuation_minus_lagged_sign_placebo_paired_daily_net_jpy": {"estimate": fmean([left - right for left, right in zip(primary_daily, placebo_daily, strict=True)]), "ci95_percentile_linear": _ci((primary[index] - placebo[index]).mean(axis=1))},
    }, index
