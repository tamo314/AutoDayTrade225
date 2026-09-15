"""Causal block/event construction and fixed inference for TASK-R086-Q001."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from math import ceil
from statistics import fmean
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.domain import Bar, Session, Trade

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def nearest_rank(values: list[float], percentile: int) -> float:
    if not values or percentile not in {80, 90, 95}:
        raise ValueError("unregistered R086 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    value = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and value > 0
    )


def _blocks(
    target: date, classifier: CalendarClassifier, shock_minutes: int
) -> list[tuple[int, datetime, datetime]]:
    """Fixed non-overlapping blocks, start+30 through endpoint <= close-30."""
    if shock_minutes not in {3, 5, 10}:
        raise ValueError("unregistered R086 shock length")
    start = classifier.session_open(target, Session.DAY)
    close = classifier.session_close(target, Session.DAY)
    cursor = start + timedelta(minutes=30)
    last_endpoint = close - timedelta(minutes=30)
    blocks: list[tuple[int, datetime, datetime]] = []
    while cursor + timedelta(minutes=shock_minutes - 1) <= last_endpoint:
        endpoint = cursor + timedelta(minutes=shock_minutes - 1)
        blocks.append((int((cursor - start).total_seconds() // 60), cursor, endpoint))
        cursor += timedelta(minutes=shock_minutes)
    return blocks


def _base_blocks(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    shock_minutes: int,
) -> list[dict[str, object]]:
    schedule = classifier.exchange_calendar.get(target)
    if schedule is None:
        return []
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    records: list[dict[str, object]] = []
    for offset, begin, endpoint in _blocks(target, classifier, shock_minutes):
        row: dict[str, object] = {
            "trade_date": target.isoformat(),
            "continuous_interval": "DAY_1",
            "schedule_version": schedule.schedule_version,
            "interval_open_jst": classifier.session_open(target, Session.DAY).isoformat(),
            "interval_close_jst": classifier.session_close(target, Session.DAY).isoformat(),
            "block_start_offset_minutes": offset,
            "block_start_jst": begin.isoformat(),
            "block_end_jst": endpoint.isoformat(),
            "shock_minutes": shock_minutes,
            "x_valid": False,
        }
        if (target, Session.DAY) in isolated:
            row["reason"] = "R004_DAY_SESSION_QUARANTINED"
            records.append(row)
            continue
        stamps = [begin + timedelta(minutes=index) for index in range(shock_minutes)]
        rows = [lookup.get(stamp) for stamp in stamps]
        if not _valid(rows[0], target):
            row["reason"] = "BLOCK_OPEN_MISSING_OR_INELIGIBLE"
        elif not _valid(rows[-1], target, close=True):
            row["reason"] = "BLOCK_CLOSE_MISSING_OR_INELIGIBLE"
        elif not all(_valid(bar, target) and _valid(bar, target, close=True) for bar in rows):
            row["reason"] = "BLOCK_INTERIOR_MISSING_OR_INELIGIBLE"
        else:
            first, last = rows[0], rows[-1]
            assert first is not None and last is not None
            x_bps = 10_000 * (last.close - first.open) / first.open
            row.update(
                x_valid=True,
                reason="VALID_X",
                open_t_minus_points=first.open,
                close_t_points=last.close,
                x_bps=x_bps,
                abs_x_bps=abs(x_bps),
            )
        records.append(row)
    return records


def _execution_event(
    candidate: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_extra_bars: int = 0,
    hold_minutes: int = 20,
) -> dict[str, object]:
    if entry_extra_bars not in {0, 1} or hold_minutes not in {10, 20, 30}:
        raise ValueError("unregistered R086 execution profile")
    event = dict(candidate)
    target = date.fromisoformat(cast(str, event["trade_date"]))
    endpoint = datetime.fromisoformat(cast(str, event["block_end_jst"]))
    interval_close = datetime.fromisoformat(cast(str, event["interval_close_jst"]))
    event.update(
        status="ENTRY_CANCELLED",
        entry_extra_bars=entry_extra_bars,
        hold_minutes=hold_minutes,
        selection_fixed_at_jst=endpoint.isoformat(),
    )
    signal = endpoint + timedelta(minutes=entry_extra_bars)
    # This is a schedule-only prohibition, before looking for eligible fills.
    if signal + timedelta(minutes=1 + hold_minutes) > interval_close:
        event["reason"] = "PLANNED_ENTRY_OR_EXIT_CROSSES_CONTINUOUS_INTERVAL"
        return event
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    if not _valid(lookup.get(signal), target, close=True):
        event["reason"] = "ENTRY_SIGNAL_CLOSE_MISSING_OR_INELIGIBLE"
        return event
    possible_entries = [
        bar
        for bar in bars.get((target, Session.DAY), [])
        if bar.ts_jst > signal and _valid(bar, target) and bar.ts_jst < interval_close
    ]
    if not possible_entries:
        event["reason"] = "NEXT_ELIGIBLE_ENTRY_OPEN_MISSING_OR_INELIGIBLE"
        return event
    entry = possible_entries[0]
    if entry.ts_jst - signal > timedelta(minutes=10):
        event["reason"] = "NEXT_ELIGIBLE_ENTRY_EXCEEDS_ENGINE_MAX_DELAY"
        return event
    exit_open = entry.ts_jst + timedelta(minutes=hold_minutes)
    exit_signal = exit_open - timedelta(minutes=1)
    event.update(
        entry_signal_jst=signal.isoformat(),
        entry_open_jst=entry.ts_jst.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
    )
    if exit_open >= interval_close:
        event["reason"] = "ACTUAL_ENTRY_DELAY_MAKES_EXIT_CROSS_CONTINUOUS_INTERVAL"
        return event
    if not _valid(lookup.get(exit_signal), target, close=True) or not _valid(
        lookup.get(exit_open), target
    ):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    x_value = cast(float, event["x_bps"])
    event.update(
        status="EXECUTABLE",
        reason="FIRST_EXTREME_SAME_CLOCK_X_EXECUTABLE",
        continuation_direction="long" if x_value > 0 else "short",
        fade_direction="short" if x_value > 0 else "long",
    )
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = 60,
    percentile: int = 90,
    shock_minutes: int = 5,
    entry_extra_bars: int = 0,
    hold_minutes: int = 20,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Build a per-date first-event ledger and a complete, causal block ledger."""
    if lookback not in {40, 60, 80} or percentile not in {80, 90, 95}:
        raise ValueError("unregistered R086 state profile")
    histories: dict[tuple[str, int], deque[dict[str, object]]] = {}
    events: list[dict[str, object]] = []
    blocks: list[dict[str, object]] = []
    for target in axis:
        raw = _base_blocks(classifier, target, bars, isolated, shock_minutes)
        selected: dict[str, object] | None = None
        for source in raw:
            row = dict(source)
            key = (cast(str, row["continuous_interval"]), cast(int, row["block_start_offset_minutes"]))
            history = histories.setdefault(key, deque(maxlen=lookback))
            row.update(
                lookback_valid_trade_dates_required=lookback,
                percentile=percentile,
                quantile_method="nearest_rank ceil(n*p/100)-1",
                reference_valid_trade_dates_oldest_to_newest=[
                    cast(str, item["trade_date"]) for item in history
                ],
                reference_valid_count=len(history),
                shock_candidate=False,
            )
            if not bool(row["x_valid"]):
                row["selection_reason"] = cast(str, row["reason"])
            elif len(history) < lookback:
                row["selection_reason"] = "INSUFFICIENT_PRIOR_VALID_SAME_CLOCK_HISTORY"
            else:
                q = nearest_rank([cast(float, item["abs_x_bps"]) for item in history], percentile)
                row["q_abs_x_bps"] = q
                x_value = cast(float, row["x_bps"])
                if x_value != 0 and abs(x_value) >= q:
                    row.update(shock_candidate=True, selection_reason="ABS_X_AT_OR_ABOVE_Q")
                    if selected is None:
                        selected = row
                else:
                    row["selection_reason"] = "NOT_EXTREME_OR_ZERO_X"
            blocks.append(row)
        # Update only after every block used its strictly-prior history.
        for row in raw:
            if bool(row["x_valid"]):
                key = (cast(str, row["continuous_interval"]), cast(int, row["block_start_offset_minutes"]))
                histories[key].append(row)
        candidate_count = sum(
            bool(row["shock_candidate"])
            for row in blocks
            if row["trade_date"] == target.isoformat()
        )
        base: dict[str, object] = {
            "trade_date": target.isoformat(),
            "status": "SKIPPED",
            "reason": "NO_SHOCK_AFTER_HISTORY",
            "shock_minutes": shock_minutes,
            "lookback": lookback,
            "percentile": percentile,
            "candidate_block_count": candidate_count,
        }
        if not raw:
            base["reason"] = "NO_REGISTERED_DAY_CONTINUOUS_BLOCKS"
        elif selected is not None:
            base = _execution_event(
                selected,
                bars,
                entry_extra_bars=entry_extra_bars,
                hold_minutes=hold_minutes,
            )
            base["candidate_block_count"] = candidate_count
            base["later_shock_candidates_ignored"] = candidate_count - 1
        events.append(base)
    return events, blocks


def pre_shock_control_events(
    events: Iterable[dict[str, object]], blocks: Iterable[dict[str, object]]
) -> list[dict[str, object]]:
    by_key = {
        (cast(str, row["trade_date"]), cast(int, row["block_start_offset_minutes"])): row
        for row in blocks
    }
    result: list[dict[str, object]] = []
    for source in events:
        event = dict(source)
        if event.get("status") == "EXECUTABLE":
            prior = by_key.get(
                (cast(str, event["trade_date"]), cast(int, event["block_start_offset_minutes"]) - cast(int, event["shock_minutes"]))
            )
            if prior is not None and bool(prior.get("x_valid")) and cast(float, prior["x_bps"]) != 0:
                event.update(
                    pre_shock_sign_control_status="EXECUTABLE_COMMON_PREVIOUS_NONZERO_BLOCK",
                    pre_shock_direction="short" if cast(float, prior["x_bps"]) > 0 else "long",
                    pre_shock_x_bps=prior["x_bps"],
                )
            else:
                event["pre_shock_sign_control_status"] = "NOT_IN_COMMON_PREVIOUS_NONZERO_BLOCK_SET"
        else:
            event["pre_shock_sign_control_status"] = "NOT_MAIN_EXECUTABLE"
        result.append(event)
    return result


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(events)
    executable = [row for row in rows if row.get("status") == "EXECUTABLE"]
    years = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in executable)
    signs = Counter("positive" if cast(float, row["x_bps"]) > 0 else "negative" for row in executable)
    known = (
        "NO_", "R004_", "BLOCK_", "VALID_", "INSUFFICIENT_", "NOT_", "ABS_", "FIRST_",
        "PLANNED_", "ENTRY_", "NEXT_", "ACTUAL_", "FIXED_",
    )
    unexplained = [
        cast(str, row["trade_date"])
        for row in rows
        if not str(row.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "outcome_observable_executable_events": len(executable),
        "minimum_outcome_observable_executable_events": 300,
        "events_at_least_300": len(executable) >= 300,
        "x_sign_counts": dict(sorted(signs.items())),
        "minimum_positive_and_negative_each": 100,
        "x_sign_minima_passed": signs["positive"] >= 100 and signs["negative"] >= 100,
        "by_year": {str(year): years[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 45,
        "annual_2021_2024_minima_passed": all(years[year] >= 45 for year in range(2021, 2025)),
        "minimum_2025_h1": 20,
        "year_2025_h1_minimum_passed": years[2025] >= 20,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(bool(gate[key]) for key in (
        "events_at_least_300", "x_sign_minima_passed", "annual_2021_2024_minima_passed",
        "year_2025_h1_minimum_passed", "unexplained_exclusions_equal_zero",
    ))
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(sorted(Counter(str(row.get("reason", row["status"])) for row in rows).items())),
        "gate": gate,
    }


def aligned_daily_net(
    axis: list[date], trades_by_day: dict[date, Trade], unknown: set[date]
) -> dict[str, int | None]:
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for target, trade in trades_by_day.items():
        if target not in daily or daily[target] is None or daily[target] != 0:
            raise ValueError("R086 trade outside observable axis or duplicate event")
        daily[target] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R086 MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def _ci(samples: NDArray[np.float64]) -> list[float] | None:
    if not len(samples) or not np.isfinite(samples).all():
        return None
    low, high = np.quantile(samples, (0.025, 0.975), method="linear")
    return [float(low), float(high)]


def bootstrap(
    fade_daily: list[int], continuation_daily: list[int], pre_common_fade_daily: list[int], pre_daily: list[int]
) -> tuple[dict[str, object], NDArray[np.int64], NDArray[np.int64]]:
    if len(fade_daily) != len(continuation_daily) or len(pre_common_fade_daily) != len(pre_daily):
        raise ValueError("R086 bootstrap axes are misaligned")
    primary_index, pre_index = mbb_indices(len(fade_daily)), mbb_indices(len(pre_daily))
    fade, continuation, common_fade, pre = (
        np.asarray(values, dtype=float)
        for values in (fade_daily, continuation_daily, pre_common_fade_daily, pre_daily)
    )
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
        "seed": MBB_SEED, "repetitions": MBB_REPETITIONS, "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "shock_fade_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(fade_daily), "ci95_percentile_linear": _ci(fade[primary_index].mean(axis=1)),
        },
        "shock_fade_minus_continuation_paired_daily_net_jpy": {
            "estimate": fmean([a - b for a, b in zip(fade_daily, continuation_daily, strict=True)]),
            "ci95_percentile_linear": _ci((fade[primary_index] - continuation[primary_index]).mean(axis=1)),
        },
        "shock_fade_minus_pre_shock_sign_control_paired_daily_net_jpy_on_common_event_axis": {
            "estimate": fmean([a - b for a, b in zip(pre_common_fade_daily, pre_daily, strict=True)]),
            "ci95_percentile_linear": _ci((common_fade[pre_index] - pre[pre_index]).mean(axis=1)),
        },
    }, primary_index, pre_index
