"""Causal event selection and fixed inference for frozen R076-Q001."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.domain import Bar, Session, Trade

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915
MAX_FILL_DELAY_MINUTES = 10


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the immutable Development trade-date axis, including valid no-trade days."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def _stamp(target: date, clock: time, tz: object) -> datetime:
    return datetime.combine(target, clock, tz)  # type: ignore[arg-type]


def _valid(bar: Bar | None, target: date) -> bool:
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and min(bar.open, bar.high, bar.low, bar.close) > 0
    )


def _next_eligible(bars: list[Bar], signal: datetime) -> Bar | None:
    candidates = [
        bar
        for bar in bars
        if bar.ts_jst > signal
        and bar.is_eligible
        and bar.trade_date == signal.date()
        and bar.session is Session.DAY
        and bar.ts_jst - signal <= timedelta(minutes=MAX_FILL_DELAY_MINUTES)
    ]
    return min(candidates, key=lambda bar: bar.ts_jst, default=None)


def failed_auction_event(
    target: date,
    bars: list[Bar],
    *,
    opening_minutes: int = 30,
    return_deadline_bars: int = 30,
    consecutive_internal_closes: int = 1,
    entry_delay_minutes: int = 0,
    exit_time: time = time(14, 30),
) -> dict[str, object]:
    """Select an R076 failed breakout only from prefixes known at each signal."""
    if opening_minutes not in {20, 30, 45}:
        raise ValueError("unregistered R076 opening window")
    if return_deadline_bars not in {15, 30, 45}:
        raise ValueError("unregistered R076 return deadline")
    if consecutive_internal_closes not in {1, 2} or entry_delay_minutes not in {0, 1}:
        raise ValueError("unregistered R076 confirmation profile")
    if exit_time not in {time(14, 15), time(14, 30), time(14, 45)}:
        raise ValueError("unregistered R076 exit")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "SKIPPED",
        "opening_minutes": opening_minutes,
        "return_deadline_bars": return_deadline_bars,
        "consecutive_internal_closes": consecutive_internal_closes,
        "entry_delay_minutes": entry_delay_minutes,
        "breakout_search_deadline_jst": "10:30",
        "return_hard_deadline_jst": "11:00",
        "exit_open_jst_planned_clock": exit_time.isoformat(timespec="minutes"),
    }
    if not bars:
        event["reason"] = "NO_DAY_BARS"
        return event
    tz = bars[0].ts_jst.tzinfo
    if tz is None:
        event["reason"] = "NAIVE_TIMESTAMP"
        return event
    lookup = {bar.ts_jst: bar for bar in bars}
    opening_start = _stamp(target, time(9), tz)
    opening_rows = [lookup.get(opening_start + timedelta(minutes=index)) for index in range(opening_minutes)]
    if any(not _valid(bar, target) for bar in opening_rows):
        event["reason"] = "OPENING_RANGE_MISSING_OR_INELIGIBLE"
        return event
    opening = [bar for bar in opening_rows if bar is not None]
    high, low = max(bar.high for bar in opening), min(bar.low for bar in opening)
    event.update(opening_high_points=high, opening_low_points=low)
    search_start = opening_start + timedelta(minutes=opening_minutes)
    search_end = _stamp(target, time(10, 30), tz)
    breakout: Bar | None = None
    breakout_direction: str | None = None
    current = search_start
    while current <= search_end:
        bar = lookup.get(current)
        if not _valid(bar, target):
            event["reason"] = "BREAKOUT_SEARCH_MISSING_OR_INELIGIBLE"
            return event
        assert bar is not None
        if bar.close > high or bar.close < low:
            breakout = bar
            breakout_direction = "long" if bar.close > high else "short"
            break
        current += timedelta(minutes=1)
    if breakout is None or breakout_direction is None:
        event["reason"] = "NO_STRICT_CLOSE_BREAKOUT_BY_1030"
        return event
    event.update(
        breakout_bar_jst=breakout.ts_jst.isoformat(),
        initial_breakout_direction=breakout_direction,
        failed_fade_direction="short" if breakout_direction == "long" else "long",
    )
    internal_run = 0
    confirmation: Bar | None = None
    return_end = min(
        breakout.ts_jst + timedelta(minutes=return_deadline_bars), _stamp(target, time(11), tz)
    )
    for offset in range(1, return_deadline_bars + 1):
        current = breakout.ts_jst + timedelta(minutes=offset)
        if current > return_end:
            break
        bar = lookup.get(current)
        if not _valid(bar, target):
            event["reason"] = "RETURN_CONFIRMATION_SEARCH_MISSING_OR_INELIGIBLE"
            return event
        assert bar is not None
        internal_run = internal_run + 1 if low < bar.close < high else 0
        if internal_run >= consecutive_internal_closes:
            confirmation = bar
            break
    if confirmation is None:
        event["reason"] = "NO_STRICT_INTERNAL_CLOSE_BY_RETURN_DEADLINE"
        return event
    signal = confirmation.ts_jst + timedelta(minutes=entry_delay_minutes)
    if not _valid(lookup.get(signal), target):
        event.update(status="SIGNALLED", reason="DELAY_SIGNAL_MISSING_OR_INELIGIBLE")
        return event
    entry = _next_eligible(bars, signal)
    if entry is None:
        event.update(status="ENTRY_CANCELLED", reason="NO_NEXT_ELIGIBLE_CONFIRMED_ENTRY_WITHIN_10_MINUTES")
        return event
    unconfirmed_entry = _next_eligible(bars, breakout.ts_jst)
    exit_signal = _stamp(target, (datetime.combine(target, exit_time) - timedelta(minutes=1)).time(), tz)
    exit_bar = lookup.get(_stamp(target, exit_time, tz))
    event.update(
        confirmation_bar_jst=confirmation.ts_jst.isoformat(),
        confirmation_signal_jst=signal.isoformat(),
        entry_open_jst=entry.ts_jst.isoformat(),
        unconfirmed_entry_open_jst=(unconfirmed_entry.ts_jst.isoformat() if unconfirmed_entry else None),
        unconfirmed_entry_observable=unconfirmed_entry is not None,
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=(
            exit_bar.ts_jst.isoformat()
            if exit_bar is not None and _valid(exit_bar, target)
            else None
        ),
        exit_observable=_valid(lookup.get(exit_signal), target) and _valid(exit_bar, target),
    )
    if not bool(event["exit_observable"]):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    event.update(status="EXECUTABLE", reason="FAILED_BREAKOUT_CONFIRMED_STRICT_INTERNAL_CLOSE")
    return event


def feasibility(events: dict[date, dict[str, object]]) -> dict[str, object]:
    """Perform frozen PnL-free availability gates for R076-Q001."""
    executable = [target for target, event in events.items() if event["status"] == "EXECUTABLE"]
    by_year = Counter(target.year for target in executable)
    by_side = Counter(str(events[target]["initial_breakout_direction"]) for target in executable)
    known_prefixes = (
        "NO_DAY_",
        "NAIVE_",
        "OPENING_",
        "BREAKOUT_",
        "NO_STRICT_",
        "RETURN_",
        "DELAY_",
        "NO_NEXT_",
        "FIXED_EXIT_",
        "FAILED_",
    )
    unexplained = [
        target.isoformat()
        for target, event in events.items()
        if not str(event.get("reason", "")).startswith(known_prefixes)
    ]
    gate = {
        "executable_failed_events": len(executable),
        "minimum_executable_failed_events": 200,
        "executable_at_least_200": len(executable) >= 200,
        "initial_breakout_direction_counts": dict(sorted(by_side.items())),
        "minimum_initial_long_and_short_each": 60,
        "initial_direction_minima_passed": by_side["long"] >= 60 and by_side["short"] >= 60,
        "executable_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 25,
        "annual_2021_2024_minima_passed": all(by_year[year] >= 25 for year in range(2021, 2025)),
        "minimum_2025_h1": 12,
        "year_2025_h1_minimum_passed": by_year[2025] >= 12,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": len(unexplained) == 0,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "executable_at_least_200",
            "initial_direction_minima_passed",
            "annual_2021_2024_minima_passed",
            "year_2025_h1_minimum_passed",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(
            sorted(Counter(str(event.get("reason", event["status"])) for event in events.values()).items())
        ),
        "gate": gate,
    }


def aligned_daily_net(
    axis: list[date], trades: tuple[Trade, ...], unknown: set[date]
) -> dict[str, int | None]:
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if trade.trade_date not in daily or daily[trade.trade_date] is None or daily[trade.trade_date] != 0:
            raise ValueError("R076 trade outside observable axis or duplicate trade date")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R076 MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def _ci(samples: NDArray[np.float64]) -> list[float]:
    low, high = np.quantile(samples, (0.025, 0.975), method="linear")
    return [float(low), float(high)]


def bootstrap(
    primary_daily: list[int], continuation_daily: list[int], unconfirmed_daily: list[int]
) -> tuple[dict[str, object], NDArray[np.int64]]:
    """Use one fixed date-block index for all R076 paired daily estimands."""
    if not all(len(values) == len(primary_daily) for values in (continuation_daily, unconfirmed_daily)):
        raise ValueError("R076 bootstrap axes are misaligned")
    index = mbb_indices(len(primary_daily))
    primary = np.asarray(primary_daily, dtype=float)
    continuation = np.asarray(continuation_daily, dtype=float)
    unconfirmed = np.asarray(unconfirmed_daily, dtype=float)
    primary_samples = primary[index].mean(axis=1)
    continuation_samples = (primary[index] - continuation[index]).mean(axis=1)
    unconfirmed_samples = (primary[index] - unconfirmed[index]).mean(axis=1)
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "primary_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(primary_daily),
            "ci95_percentile_linear": _ci(primary_samples),
        },
        "primary_minus_confirmed_continuation_daily_net_jpy": {
            "estimate": fmean([a - b for a, b in zip(primary_daily, continuation_daily, strict=True)]),
            "ci95_percentile_linear": _ci(continuation_samples),
        },
        "primary_minus_unconfirmed_fade_daily_net_jpy": {
            "estimate": fmean([a - b for a, b in zip(primary_daily, unconfirmed_daily, strict=True)]),
            "ci95_percentile_linear": _ci(unconfirmed_samples),
        },
    }, index
