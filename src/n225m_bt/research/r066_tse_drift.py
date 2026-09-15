"""Fixed TSE-hours drift helpers for TASK-R066-Q001.

This module deliberately contains no price-based selection.  Its only market
condition is the availability of the scheduled execution path.
"""

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
ENTRY_SIGNAL_TIME = time(8, 59)
ENTRY_TIME = time(9, 0)
EXIT_SIGNAL_TIME = time(14, 29)
EXIT_TIME = time(14, 30)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the immutable Development calendar axis, including no-trade days."""
    return [
        item.trade_date
        for item in calendar.trading_days()
        if DEVELOPMENT_START <= item.trade_date <= DEVELOPMENT_END
    ]


def path_status(target: date, bars: list[Bar], entry: time = ENTRY_TIME, exit_: time = EXIT_TIME) -> str:
    """Classify scheduled-path observability without calculating any return.

    Bar timestamps are timezone aware.  The same local clock times are used
    here after inheriting their timezone from the day's bars.
    """
    if not bars:
        return "NO_DAY_BARS"
    tz = bars[0].ts_jst.tzinfo
    if tz is None:
        return "NAIVE_TIMESTAMP"
    entry_stamp = datetime.combine(target, entry, tz)
    exit_stamp = datetime.combine(target, exit_, tz)
    clocks = {
        "entry_signal": entry_stamp - timedelta(minutes=1),
        "entry": entry_stamp,
        "exit_signal": exit_stamp - timedelta(minutes=1),
        "exit": exit_stamp,
    }
    by_time = {bar.ts_jst: bar for bar in bars}
    for name in ("entry_signal", "entry", "exit_signal", "exit"):
        bar = by_time.get(clocks[name])
        if bar is None:
            return f"MISSING_{name.upper()}"
        if not bar.is_eligible:
            return f"INELIGIBLE_{name.upper()}"
        price = bar.close if name == "entry_signal" else bar.open
        if price <= 0:
            return f"NONPOSITIVE_{name.upper()}"
    return "EXECUTABLE"


def feasibility(axis: list[date], bars_by_day: dict[date, list[Bar]]) -> dict[str, object]:
    """Produce the S2 non-PnL availability gate for the primary profile."""
    statuses = {target: path_status(target, bars_by_day.get(target, [])) for target in axis}
    executable = [target for target, status in statuses.items() if status == "EXECUTABLE"]
    by_year = Counter(target.year for target in executable)
    pass_gate = len(executable) >= 900 and all(by_year[year] >= 180 for year in range(2021, 2025))
    return {
        "scheduled_trade_dates": len(axis),
        "executable_trade_dates": len(executable),
        "executable_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "status_counts": dict(sorted(Counter(statuses.values()).items())),
        "gate": {
            "minimum_executable_trade_dates": 900,
            "minimum_2021_through_2024_each": 180,
            "passed": pass_gate,
        },
        "per_trade_date_status": {target.isoformat(): statuses[target] for target in axis},
    }


def aligned_daily_net(axis: list[date], trades: tuple[Trade, ...], unavailable: set[date]) -> dict[str, int | None]:
    """Align outcomes to the scheduled axis; known no-trade is zero, unknown is null."""
    values: dict[date, int | None] = {target: (None if target in unavailable else 0) for target in axis}
    for trade in trades:
        if trade.trade_date not in values:
            raise ValueError("trade is outside the frozen scheduled axis")
        if values[trade.trade_date] is None:
            raise ValueError("trade supplied for a date with unknown outcome")
        if values[trade.trade_date] != 0:
            raise ValueError("more than one trade on a scheduled date")
        values[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): values[target] for target in axis}


def mbb_mean_ci(values: list[int], *, seed: int = MBB_SEED) -> dict[str, object]:
    """20-day non-wrapping, tail-truncated percentile MBB for the daily mean."""
    if len(values) < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than the preregistered MBB block")
    array = np.asarray(values, dtype=np.float64)
    rng = np.random.default_rng(seed)
    blocks = ceil(len(array) / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, len(array) - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    offsets = np.arange(MBB_BLOCK_LENGTH, dtype=np.int64)
    indices = (starts[:, :, None] + offsets).reshape(MBB_REPETITIONS, -1)[:, : len(array)]
    means = array[indices].mean(axis=1)
    ci = np.quantile(means, (0.025, 0.975), method="linear")
    return {
        "estimand": "scheduled_trade_date_daily_net_mean_jpy",
        "estimate": fmean(values),
        "ci95_percentile_linear": [float(ci[0]), float(ci[1])],
        "method": "20 trade_date non-wrap moving-block bootstrap; tail truncation; linear percentile",
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "repetitions": MBB_REPETITIONS,
        "seed": seed,
        "axis_observations": len(values),
    }
