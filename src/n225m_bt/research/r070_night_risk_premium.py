"""Frozen event construction and inference helpers for R070-Q001."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean

import numpy as np

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.domain import Bar, Session, Trade

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
PRIMARY_ENTRY = time(16, 31)
PRIMARY_EXIT = time(5, 30)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915
MAX_FILL_DELAY_MINUTES = 10


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return all version-controlled Development trade dates, without price filtering."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def _next_eligible(bars: list[Bar], after: datetime) -> Bar | None:
    candidates = [
        bar
        for bar in bars
        if bar.ts_jst > after
        and bar.is_eligible
        and bar.ts_jst - after <= timedelta(minutes=MAX_FILL_DELAY_MINUTES)
    ]
    return min(candidates, key=lambda bar: bar.ts_jst, default=None)


def fixed_night_event(
    target: date,
    bars: list[Bar],
    classifier: CalendarClassifier,
    *,
    entry_time: time = PRIMARY_ENTRY,
    exit_time: time = PRIMARY_EXIT,
    entry_delay_minutes: int = 0,
) -> dict[str, object]:
    """Construct one fixed-time night order without conditioning it on its exit.

    A versioned schedule can rule a fixed clock out before the decision.  Once
    the entry fills, however, a later missing exit is recorded as unknown and
    never converted into a retrospective no-trade.
    """
    if entry_delay_minutes not in {0, 1}:
        raise ValueError("R070 only registers a zero or one-minute entry delay")
    night_open = classifier.session_open(target, Session.NIGHT)
    night_close = classifier.session_close(target, Session.NIGHT)
    entry = datetime.combine(night_open.date(), entry_time, night_open.tzinfo)
    exit_ = datetime.combine(target, exit_time, night_close.tzinfo)
    entry_signal = entry - timedelta(minutes=1) + timedelta(minutes=entry_delay_minutes)
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "night_session_open_jst": night_open.isoformat(),
        "night_session_close_jst": night_close.isoformat(),
        "scheduled_entry_open_jst": entry.isoformat(),
        "entry_signal_jst": entry_signal.isoformat(),
        "scheduled_exit_open_jst": exit_.isoformat(),
        "exit_signal_jst": (exit_ - timedelta(minutes=1)).isoformat(),
        "entry_delay_minutes": entry_delay_minutes,
    }
    base_entry_signal = entry - timedelta(minutes=1)
    if not (
        night_open <= base_entry_signal < entry <= night_close
        and night_open <= entry_signal < night_close
        and night_open <= exit_ <= night_close
    ):
        event.update(status="NO_SCHEDULED_FIXED_WINDOW", outcome_observable=False)
        return event
    lookup = {bar.ts_jst: bar for bar in bars}
    signal_bar = lookup.get(entry_signal)
    if signal_bar is None or not signal_bar.is_eligible:
        event.update(status="ENTRY_CANCELLED", reason="ENTRY_SIGNAL_UNAVAILABLE", outcome_observable=False)
        return event
    entry_bar = _next_eligible(bars, entry_signal)
    if entry_bar is None:
        event.update(status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_UNAVAILABLE", outcome_observable=False)
        return event
    event.update(entry_open_jst=entry_bar.ts_jst.isoformat(), entry_observable=True)
    exit_signal_bar = lookup.get(exit_ - timedelta(minutes=1))
    exit_bar = lookup.get(exit_)
    if (
        exit_signal_bar is None
        or not exit_signal_bar.is_eligible
        or exit_bar is None
        or not exit_bar.is_eligible
    ):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="EXIT_UNAVAILABLE", outcome_observable=False)
        return event
    event.update(status="EXECUTABLE", reason="FIXED_NIGHT_TIMES", outcome_observable=True)
    return event


def feasibility(
    axis: list[date], bars_by_day: dict[date, list[Bar]], classifier: CalendarClassifier
) -> dict[str, object]:
    """Return only availability diagnostics, never prices, returns, or PnL."""
    events = {
        target: fixed_night_event(target, bars_by_day.get(target, []), classifier)
        for target in axis
    }
    executable = [target for target, event in events.items() if event["status"] == "EXECUTABLE"]
    years = Counter(target.year for target in executable)
    passed = len(executable) >= 900 and all(years[year] >= 180 for year in range(2021, 2025))
    return {
        "scheduled_trade_dates": len(axis),
        "entry_exit_executable_trade_dates": len(executable),
        "entry_exit_executable_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "status_counts": dict(sorted(Counter(str(event["status"]) for event in events.values()).items())),
        "gate": {
            "minimum_entry_exit_executable_trade_dates": 900,
            "minimum_2021_through_2024_each": 180,
            "passed": passed,
        },
        "events": {target.isoformat(): event for target, event in events.items()},
    }


def aligned_daily_net(
    axis: list[date], trades: tuple[Trade, ...], unknown: set[date]
) -> dict[str, int | None]:
    """Align completed orders to the fixed axis; unknown filled exits remain null."""
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if trade.trade_date not in daily or daily[trade.trade_date] is None:
            raise ValueError("R070 trade is outside the observable scheduled axis")
        if daily[trade.trade_date] != 0:
            raise ValueError("R070 permits at most one trade per trade date")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_mean_ci(values: list[int], *, seed: int = MBB_SEED) -> dict[str, object]:
    """Frozen 20-trade-date non-wrapping moving-block bootstrap."""
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
    lower, upper = np.quantile(means, (0.025, 0.975), method="linear")
    return {
        "estimate": fmean(values),
        "ci95_percentile_linear": [float(lower), float(upper)],
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "repetitions": MBB_REPETITIONS,
        "seed": seed,
        "method": "non-wrapping MBB; tail truncation; linear percentile",
    }
