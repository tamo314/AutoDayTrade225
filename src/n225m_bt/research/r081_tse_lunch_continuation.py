"""Causal event construction and fixed inference for TASK-R081-Q001."""

from __future__ import annotations

from collections import Counter
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
MAIN_WINDOW = (time(11, 30), time(12, 29))
TRIMMED_WINDOW = (time(11, 35), time(12, 24))
REGISTERED_EXITS = {time(14, 15), time(14, 30), time(14, 45)}


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    value = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and value > 0
    )


def _stamp(target: date, clock: time, classifier: CalendarClassifier) -> datetime:
    return datetime.combine(target, clock, classifier.session_close(target, Session.DAY).tzinfo)


def _execution_event(
    base: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_delay_minutes: int = 0,
    exit_time: time = time(14, 30),
) -> dict[str, object]:
    """Make a fixed event executable only after its L endpoint close is observable."""
    if entry_delay_minutes not in {0, 1} or exit_time not in REGISTERED_EXITS:
        raise ValueError("unregistered R081 execution profile")
    event = dict(base)
    event.update(
        status="SKIPPED",
        entry_delay_minutes=entry_delay_minutes,
        exit_open_jst_planned_clock=exit_time.isoformat(timespec="minutes"),
    )
    if not bool(event.get("l_valid")):
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
    if not _valid(lookup.get(exit_signal), target, close=True) or not _valid(
        lookup.get(exit_open), target
    ):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    movement = cast(int, event["l_points"])
    event.update(
        status="EXECUTABLE",
        reason="NONZERO_L_EXECUTABLE_NO_MAGNITUDE_SELECTION",
        continuation_direction="long" if movement > 0 else "short",
        fade_direction="short" if movement > 0 else "long",
    )
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    window: tuple[time, time] = MAIN_WINDOW,
) -> list[dict[str, object]]:
    """Build L from a fixed lunch window; M is recorded but never selects L trades."""
    if window not in {MAIN_WINDOW, TRIMMED_WINDOW}:
        raise ValueError("unregistered R081 lunch window")
    result: list[dict[str, object]] = []
    for target in axis:
        start, endpoint = (_stamp(target, clock, classifier) for clock in window)
        morning_open, morning_end = (
            _stamp(target, clock, classifier) for clock in (time(9), time(11, 29))
        )
        event: dict[str, object] = {
            "trade_date": target.isoformat(),
            "l_valid": False,
            "window_a_open_jst": start.isoformat(),
            "signal_endpoint_jst": endpoint.isoformat(),
            "window_definition": f"{window[0].isoformat(timespec='minutes')} open to {window[1].isoformat(timespec='minutes')} close",
            "m_valid": False,
        }
        if (target, Session.DAY) in isolated:
            event.update(reason="R004_DAY_SESSION_QUARANTINED", m_reason="R004_DAY_SESSION_QUARANTINED")
            result.append(_execution_event(event, bars))
            continue
        lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
        a_bar, b_bar = lookup.get(start), lookup.get(endpoint)
        m_a, m_b = lookup.get(morning_open), lookup.get(morning_end)
        if not _valid(m_a, target):
            event["m_reason"] = "MORNING_0900_OPEN_MISSING_OR_INELIGIBLE"
        elif not _valid(m_b, target, close=True):
            event["m_reason"] = "MORNING_1129_CLOSE_MISSING_OR_INELIGIBLE"
        else:
            assert m_a is not None and m_b is not None
            movement_m = m_b.close - m_a.open
            event.update(
                m_valid=movement_m != 0,
                m_reason="ZERO_M" if movement_m == 0 else "VALID_M",
                m_0900_open_points=m_a.open,
                m_1129_close_points=m_b.close,
                m_points=movement_m,
            )
        if not _valid(a_bar, target):
            event["reason"] = "L_WINDOW_A_OPEN_MISSING_OR_INELIGIBLE"
        elif not _valid(b_bar, target, close=True):
            event["reason"] = "L_WINDOW_B_CLOSE_MISSING_OR_INELIGIBLE"
        else:
            assert a_bar is not None and b_bar is not None
            movement_l = b_bar.close - a_bar.open
            event.update(
                l_valid=movement_l != 0,
                reason="ZERO_L" if movement_l == 0 else "VALID_L",
                l_a_open_points=a_bar.open,
                l_b_close_points=b_bar.close,
                l_points=movement_l,
                abs_l_points=abs(movement_l),
            )
        result.append(_execution_event(event, bars))
    return result


def morning_sign_events(events: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Restrict M control to the preregistered same-date observable L/M intersection."""
    result: list[dict[str, object]] = []
    for source in events:
        event = dict(source)
        if event.get("status") == "EXECUTABLE" and bool(event.get("m_valid")):
            movement = cast(int, event["m_points"])
            event.update(
                morning_direction="long" if movement > 0 else "short",
                morning_sign_control_status="EXECUTABLE_COMMON_L_AND_M_NONZERO",
            )
        else:
            event.update(
                status="SKIPPED" if event.get("status") != "ENTRY_FILLED_EXIT_UNKNOWN" else event["status"],
                morning_sign_control_status="NOT_IN_COMMON_L_AND_M_NONZERO_SET",
            )
        result.append(event)
    return result


def assign_abs_l_quintiles(events: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Fixed descriptive quintiles do not alter execution."""
    result = [dict(event) for event in events]
    eligible = sorted(
        (event for event in result if event.get("status") == "EXECUTABLE"),
        key=lambda item: (cast(int, item["abs_l_points"]), cast(str, item["trade_date"])),
    )
    for rank, event in enumerate(eligible, start=1):
        event["abs_l_quintile"] = min(5, ceil(rank * 5 / len(eligible)))
    return result


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    """Return only PnL-free availability and L/M composition diagnostics."""
    rows = list(events)
    executable = [event for event in rows if event.get("status") == "EXECUTABLE"]
    by_year = Counter(date.fromisoformat(cast(str, event["trade_date"])).year for event in executable)
    signs = Counter("positive" if cast(int, event["l_points"]) > 0 else "negative" for event in executable)
    common = [event for event in executable if bool(event.get("m_valid"))]
    disagreement = sum(
        cast(int, event["l_points"]) * cast(int, event["m_points"]) < 0 for event in common
    )
    known_prefixes = ("R004_", "L_WINDOW_", "ZERO_", "VALID_", "NONZERO_", "ENTRY_", "FIXED_")
    unexplained = [
        cast(str, event["trade_date"])
        for event in rows
        if not str(event.get("reason", "")).startswith(known_prefixes)
    ]
    gate: dict[str, object] = {
        "executable_trades": len(executable),
        "minimum_executable_trades": 900,
        "executable_at_least_900": len(executable) >= 900,
        "l_sign_counts": dict(sorted(signs.items())),
        "minimum_positive_and_negative_each": 350,
        "l_sign_minima_passed": signs["positive"] >= 350 and signs["negative"] >= 350,
        "by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 150,
        "annual_2021_2024_minima_passed": all(by_year[year] >= 150 for year in range(2021, 2025)),
        "minimum_2025_h1": 70,
        "year_2025_h1_minimum_passed": by_year[2025] >= 70,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "executable_at_least_900",
            "l_sign_minima_passed",
            "annual_2021_2024_minima_passed",
            "year_2025_h1_minimum_passed",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(sorted(Counter(str(event.get("reason", event["status"])) for event in rows).items())),
        "morning_sign_control": {
            "definition": "main executable L!=0 and M=11:29 close-09:00 open is eligible and nonzero",
            "common_l_and_m_nonzero_executable_count": len(common),
            "l_m_sign_disagreement_count": disagreement,
            "l_m_sign_agreement_count": len(common) - disagreement,
            "m_reason_counts": dict(sorted(Counter(str(event.get("m_reason", "NOT_RECORDED")) for event in rows).items())),
        },
        "gate": gate,
    }


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R081 MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def _ci(samples: NDArray[np.float64]) -> list[float]:
    low, high = np.quantile(samples, (0.025, 0.975), method="linear")
    return [float(low), float(high)]


def bootstrap(
    continuation_daily: list[int],
    fade_daily: list[int],
    morning_common_continuation_daily: list[int],
    morning_control_daily: list[int],
) -> tuple[dict[str, object], NDArray[np.int64], NDArray[np.int64]]:
    """Use fixed 20-date MBB on primary and preregistered L/M-common axes."""
    if len(continuation_daily) != len(fade_daily):
        raise ValueError("R081 continuation/fade bootstrap axes are misaligned")
    if len(morning_common_continuation_daily) != len(morning_control_daily):
        raise ValueError("R081 morning-control bootstrap axes are misaligned")
    primary_index = mbb_indices(len(continuation_daily))
    morning_index = mbb_indices(len(morning_control_daily))
    continuation, fade = (
        np.asarray(values, dtype=float) for values in (continuation_daily, fade_daily)
    )
    common_continuation, morning = (
        np.asarray(values, dtype=float)
        for values in (morning_common_continuation_daily, morning_control_daily)
    )
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "continuation_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(continuation_daily),
            "ci95_percentile_linear": _ci(continuation[primary_index].mean(axis=1)),
        },
        "continuation_minus_fade_paired_daily_net_jpy": {
            "estimate": fmean([left - right for left, right in zip(continuation_daily, fade_daily, strict=True)]),
            "ci95_percentile_linear": _ci((continuation[primary_index] - fade[primary_index]).mean(axis=1)),
        },
        "continuation_minus_morning_sign_control_paired_daily_net_jpy_on_common_l_m_axis": {
            "estimate": fmean([left - right for left, right in zip(morning_common_continuation_daily, morning_control_daily, strict=True)]),
            "ci95_percentile_linear": _ci((common_continuation[morning_index] - morning[morning_index]).mean(axis=1)),
        },
    }, primary_index, morning_index
