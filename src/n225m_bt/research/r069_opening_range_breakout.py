"""Causal event selection and inference helpers for frozen R069-Q001."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean

import numpy as np

from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.domain import Bar, Trade

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
PRIMARY_OPENING_MINUTES = 30
PRIMARY_SEARCH_END = time(11, 0)
PRIMARY_EXIT = time(14, 30)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915
MAX_FILL_DELAY_MINUTES = 10


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the fixed Development trade-date axis, including known no-trade days."""
    return [
        record.trade_date
        for record in calendar.trading_days()
        if DEVELOPMENT_START <= record.trade_date <= DEVELOPMENT_END
    ]


def _stamp(target: date, clock: time, tz: object) -> datetime:
    return datetime.combine(target, clock, tz)  # type: ignore[arg-type]


def _opening_end(minutes: int) -> time:
    if minutes not in {20, 30, 45}:
        raise ValueError("unregistered R069 opening window")
    return (datetime.combine(date.min, time(9, 0)) + timedelta(minutes=minutes - 1)).time()


def _next_eligible(
    bars: list[Bar], after: datetime, *, max_delay_minutes: int = MAX_FILL_DELAY_MINUTES
) -> Bar | None:
    candidates = [
        bar
        for bar in bars
        if bar.ts_jst > after
        and bar.is_eligible
        and bar.ts_jst - after <= timedelta(minutes=max_delay_minutes)
    ]
    return min(candidates, key=lambda bar: bar.ts_jst, default=None)


def opening_range_breakout_event(
    target: date,
    bars: list[Bar],
    *,
    opening_minutes: int = PRIMARY_OPENING_MINUTES,
    search_end: time = PRIMARY_SEARCH_END,
    entry_delay_minutes: int = 0,
) -> dict[str, object]:
    """Select the first strict close break using only the causal day prefix.

    The returned entry/exit availability is deliberately diagnostic.  It is not
    an entry filter: an unavailable later exit is reported as an unknown outcome
    by the runner rather than retrospectively cancelling a signalled trade.
    """
    if search_end not in {time(10, 30), time(11, 0), time(11, 30)}:
        raise ValueError("unregistered R069 search end")
    if entry_delay_minutes not in {0, 1}:
        raise ValueError("unregistered R069 entry delay")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "opening_minutes": opening_minutes,
        "search_end_jst": search_end.isoformat(timespec="minutes"),
        "entry_delay_minutes": entry_delay_minutes,
        "status": "SKIPPED",
    }
    if not bars:
        event["reason"] = "NO_DAY_BARS"
        return event
    tz = bars[0].ts_jst.tzinfo
    if tz is None:
        event["reason"] = "NAIVE_TIMESTAMP"
        return event
    lookup = {bar.ts_jst: bar for bar in bars}
    opening = _stamp(target, time(9, 0), tz)
    range_end = _stamp(target, _opening_end(opening_minutes), tz)
    for offset in range(opening_minutes):
        stamp = opening + timedelta(minutes=offset)
        bar = lookup.get(stamp)
        if bar is None:
            event["reason"] = "MISSING_OPENING_RANGE"
            return event
        if not bar.is_eligible:
            event["reason"] = "INELIGIBLE_OPENING_RANGE"
            return event
    opening_bars = [lookup[opening + timedelta(minutes=offset)] for offset in range(opening_minutes)]
    opening_high = max(bar.high for bar in opening_bars)
    opening_low = min(bar.low for bar in opening_bars)
    event.update(
        opening_high=opening_high,
        opening_low=opening_low,
        opening_range_last_bar_jst=range_end.isoformat(),
    )
    search_start = range_end + timedelta(minutes=1)
    search_stop = _stamp(target, search_end, tz)
    if search_start > search_stop:
        raise ValueError("search end must follow the opening window")
    current = search_start
    while current <= search_stop:
        bar = lookup.get(current)
        if bar is None:
            event["reason"] = "MISSING_BREAKOUT_SEARCH"
            return event
        if not bar.is_eligible:
            event["reason"] = "INELIGIBLE_BREAKOUT_SEARCH"
            return event
        if bar.close > opening_high or bar.close < opening_low:
            direction = "long" if bar.close > opening_high else "short"
            delayed_signal = current + timedelta(minutes=entry_delay_minutes)
            if entry_delay_minutes:
                delay_bar = lookup.get(delayed_signal)
                if delay_bar is None:
                    event.update(status="SIGNALLED", reason="MISSING_DELAY_SIGNAL_BAR")
                    return event
                if not delay_bar.is_eligible:
                    event.update(status="SIGNALLED", reason="INELIGIBLE_DELAY_SIGNAL_BAR")
                    return event
            entry = _next_eligible(bars, delayed_signal)
            exit_signal = _stamp(target, time(14, 29), tz)
            exit = lookup.get(_stamp(target, PRIMARY_EXIT, tz))
            event.update(
                status="SIGNALLED",
                reason="FIRST_STRICT_CLOSE_BREAKOUT",
                breakout_bar_jst=current.isoformat(),
                signal_jst=delayed_signal.isoformat(),
                breakout_close=bar.close,
                breakout_direction=direction,
                entry_open_jst=entry.ts_jst.isoformat() if entry else None,
                entry_observable=entry is not None,
                exit_signal_observable=(
                    lookup.get(exit_signal) is not None and lookup[exit_signal].is_eligible
                ),
                exit_open_jst=(exit.ts_jst.isoformat() if exit and exit.is_eligible else None),
                exit_observable=exit is not None and exit.is_eligible,
            )
            event["outcome_observable"] = bool(
                event["entry_observable"]
                and event["exit_signal_observable"]
                and event["exit_observable"]
            )
            return event
        current += timedelta(minutes=1)
    event["reason"] = "NO_STRICT_BREAKOUT_BY_DEADLINE"
    return event


def feasibility(axis: list[date], bars_by_day: dict[date, list[Bar]]) -> dict[str, object]:
    """S2 availability/direction diagnostic; intentionally does not calculate PnL."""
    events = {target: opening_range_breakout_event(target, bars_by_day.get(target, [])) for target in axis}
    executable = [
        target
        for target, event in events.items()
        if event["status"] == "SIGNALLED" and event.get("outcome_observable") is True
    ]
    by_year = Counter(target.year for target in executable)
    by_side = Counter(str(events[target]["breakout_direction"]) for target in executable)
    passed = (
        len(executable) >= 400
        and all(by_year[year] >= 60 for year in range(2021, 2025))
        and by_side["long"] >= 120
        and by_side["short"] >= 120
    )
    return {
        "scheduled_trade_dates": len(axis),
        "executable_trade_dates": len(executable),
        "executable_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "breakout_direction_counts": dict(sorted(by_side.items())),
        "status_counts": dict(
            sorted(Counter(str(event.get("reason", event["status"])) for event in events.values()).items())
        ),
        "gate": {
            "minimum_executable_trade_dates": 400,
            "minimum_2021_through_2024_each": 60,
            "minimum_long_and_short_each": 120,
            "passed": passed,
        },
        "events": {target.isoformat(): event for target, event in events.items()},
    }


def aligned_daily_net(
    axis: list[date], trades: tuple[Trade, ...], unknown: set[date]
) -> dict[str, int | None]:
    values: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if trade.trade_date not in values or values[trade.trade_date] is None:
            raise ValueError("R069 trade outside observable fixed axis")
        if values[trade.trade_date] != 0:
            raise ValueError("more than one R069 trade on a date")
        values[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): values[target] for target in axis}


def mbb_mean_ci(values: list[int], *, seed: int = MBB_SEED) -> dict[str, object]:
    if len(values) < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than the frozen MBB block")
    data = np.asarray(values, dtype=np.float64)
    rng = np.random.default_rng(seed)
    blocks = ceil(len(data) / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, len(data) - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    indices = (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[
        :, : len(data)
    ]
    means = data[indices].mean(axis=1)
    ci = np.quantile(means, (0.025, 0.975), method="linear")
    return {
        "estimate": fmean(values),
        "ci95_percentile_linear": [float(ci[0]), float(ci[1])],
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "repetitions": MBB_REPETITIONS,
        "seed": seed,
        "method": "non-wrapping MBB; tail truncation; linear percentile",
    }
