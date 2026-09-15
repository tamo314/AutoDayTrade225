"""Causal helpers for the frozen R068-Q001 15-minute opening reversal."""

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
PRIMARY_WINDOW_MINUTES = 15
PRIMARY_ENTRY = time(9, 15)
PRIMARY_EXIT = time(14, 30)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the frozen Development trade-date axis, including no-trade dates."""
    return [
        record.trade_date
        for record in calendar.trading_days()
        if DEVELOPMENT_START <= record.trade_date <= DEVELOPMENT_END
    ]


def _stamp(target: date, clock: time, tz: object) -> datetime:
    return datetime.combine(target, clock, tz)  # type: ignore[arg-type]


def opening_event(
    target: date,
    bars: list[Bar],
    *,
    window_minutes: int = PRIMARY_WINDOW_MINUTES,
    exit_time: time = PRIMARY_EXIT,
    entry_delay_minutes: int = 0,
) -> dict[str, object]:
    """Decide one opening direction solely from a completed causal window."""
    if window_minutes not in {10, 15, 20} or entry_delay_minutes < 0:
        raise ValueError("unregistered R068 window or delay")
    entry_minutes = 9 * 60 + window_minutes + entry_delay_minutes
    entry = time(entry_minutes // 60, entry_minutes % 60)
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "window_minutes": window_minutes,
        "entry_jst": entry.isoformat(timespec="minutes"),
        "exit_jst": exit_time.isoformat(timespec="minutes"),
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
    signal = opening + timedelta(minutes=window_minutes - 1)
    entry_stamp = _stamp(target, entry, tz)
    exit_signal = _stamp(target, exit_time, tz) - timedelta(minutes=1)
    exit_stamp = _stamp(target, exit_time, tz)
    required = {
        "opening": (lookup.get(opening), "open"),
        "signal": (lookup.get(signal), "close"),
        "entry": (lookup.get(entry_stamp), "open"),
        "exit_signal": (lookup.get(exit_signal), "close"),
        "exit": (lookup.get(exit_stamp), "open"),
    }
    for name, (bar, field) in required.items():
        if bar is None:
            event["reason"] = f"MISSING_{name.upper()}"
            return event
        if not bar.is_eligible:
            event["reason"] = f"INELIGIBLE_{name.upper()}"
            return event
        if getattr(bar, field) <= 0:
            event["reason"] = f"NONPOSITIVE_{name.upper()}"
            return event
    first, last = required["opening"][0], required["signal"][0]
    assert first is not None and last is not None
    change = last.close - first.open
    event.update(
        signal_jst=signal.isoformat(),
        opening_open=first.open,
        signal_close=last.close,
        opening_change_points=change,
    )
    if change == 0:
        event["reason"] = "ZERO_OPENING_CHANGE"
        return event
    event.update(
        status="EXECUTABLE",
        reversal_direction="short" if change > 0 else "long",
        momentum_direction="long" if change > 0 else "short",
        reason="CAUSAL_NONZERO_OPENING_CHANGE",
    )
    return event


def feasibility(axis: list[date], bars_by_day: dict[date, list[Bar]]) -> dict[str, object]:
    """S2 availability/direction check; it intentionally does not calculate PnL."""
    events = {target: opening_event(target, bars_by_day.get(target, [])) for target in axis}
    executable = [target for target, event in events.items() if event["status"] == "EXECUTABLE"]
    by_year = Counter(target.year for target in executable)
    by_side = Counter(str(events[target]["reversal_direction"]) for target in executable)
    passed = (
        len(executable) >= 900
        and all(by_year[year] >= 180 for year in range(2021, 2025))
        and by_side["long"] >= 300
        and by_side["short"] >= 300
    )
    return {
        "scheduled_trade_dates": len(axis),
        "executable_trade_dates": len(executable),
        "executable_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "reversal_direction_counts": dict(sorted(by_side.items())),
        "status_counts": dict(
            sorted(
                Counter(
                    str(event.get("reason", event["status"])) for event in events.values()
                ).items()
            )
        ),
        "gate": {
            "minimum_executable_trade_dates": 900,
            "minimum_2021_through_2024_each": 180,
            "minimum_long_and_short_each": 300,
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
            raise ValueError("R068 trade outside observable frozen axis")
        if values[trade.trade_date] != 0:
            raise ValueError("more than one R068 trade on a date")
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
