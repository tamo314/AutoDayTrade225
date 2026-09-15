"""Causal event selection and fixed inference helpers for R071-Q001."""

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
PRIMARY_OPENING_MINUTES = 30
PRIMARY_SEARCH_END = time(23, 30)
PRIMARY_EXIT = time(5, 30)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915
MAX_FILL_DELAY_MINUTES = 10


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return every Development trade date without filtering on market outcomes."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def _opening_end(minutes: int) -> time:
    if minutes not in {20, 30, 45}:
        raise ValueError("unregistered R071 opening window")
    return (datetime.combine(date.min, time(16, 30)) + timedelta(minutes=minutes - 1)).time()


def _night_stamp(target: date, night_start: datetime, clock: time) -> datetime:
    """Map evening clocks to night start, and after-midnight clocks to trade date."""
    stamp_date = night_start.date() if clock >= time(16, 30) else target
    return datetime.combine(stamp_date, clock, night_start.tzinfo)


def _next_eligible(bars: list[Bar], after: datetime) -> Bar | None:
    candidates = [
        bar
        for bar in bars
        if bar.ts_jst > after
        and bar.is_eligible
        and bar.ts_jst - after <= timedelta(minutes=MAX_FILL_DELAY_MINUTES)
    ]
    return min(candidates, key=lambda bar: bar.ts_jst, default=None)


def night_opening_range_event(
    target: date,
    bars: list[Bar],
    classifier: CalendarClassifier,
    *,
    opening_minutes: int = PRIMARY_OPENING_MINUTES,
    search_end: time = PRIMARY_SEARCH_END,
    entry_delay_minutes: int = 0,
    reverse_direction: bool = False,
) -> dict[str, object]:
    """Construct a first strict night breakout using only information available then.

    Later exit availability is reported after an entry has been selected; it does
    not retroactively turn that selected entry into a no-trade.
    """
    if search_end not in {time(22, 30), time(23, 30), time(0, 30)}:
        raise ValueError("unregistered R071 search end")
    if entry_delay_minutes not in {0, 1}:
        raise ValueError("unregistered R071 entry delay")
    night_open = classifier.session_open(target, Session.NIGHT)
    night_close = classifier.session_close(target, Session.NIGHT)
    range_start = _night_stamp(target, night_open, time(16, 30))
    range_end = _night_stamp(target, night_open, _opening_end(opening_minutes))
    search_stop = _night_stamp(target, night_open, search_end)
    exit_ = _night_stamp(target, night_open, PRIMARY_EXIT)
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "night_session_open_jst": night_open.isoformat(),
        "night_session_close_jst": night_close.isoformat(),
        "opening_minutes": opening_minutes,
        "search_end_jst": search_end.isoformat(timespec="minutes"),
        "opening_range_start_jst": range_start.isoformat(),
        "scheduled_search_end_jst": search_stop.isoformat(),
        "entry_delay_minutes": entry_delay_minutes,
        "reverse_direction": reverse_direction,
        "scheduled_exit_open_jst": exit_.isoformat(),
    }
    if not (
        night_open <= range_start <= range_end < search_stop < night_close
        and night_open <= exit_ <= night_close
        and night_open.date() + timedelta(days=1) == target
    ):
        event.update(status="NO_SCHEDULED_NIGHT_WINDOW", outcome_observable=False)
        return event
    lookup = {bar.ts_jst: bar for bar in bars}
    opening: list[Bar] = []
    for offset in range(opening_minutes):
        stamp = range_start + timedelta(minutes=offset)
        bar = lookup.get(stamp)
        if bar is None:
            event.update(status="SKIPPED", reason="MISSING_OPENING_RANGE", outcome_observable=False)
            return event
        if not bar.is_eligible:
            event.update(status="SKIPPED", reason="INELIGIBLE_OPENING_RANGE", outcome_observable=False)
            return event
        opening.append(bar)
    opening_high = max(bar.high for bar in opening)
    opening_low = min(bar.low for bar in opening)
    event.update(
        opening_high=opening_high,
        opening_low=opening_low,
        opening_range_last_bar_jst=range_end.isoformat(),
    )
    current = range_end + timedelta(minutes=1)
    while current <= search_stop:
        bar = lookup.get(current)
        if bar is None:
            event.update(status="SKIPPED", reason="MISSING_BREAKOUT_SEARCH", outcome_observable=False)
            return event
        if not bar.is_eligible:
            event.update(status="SKIPPED", reason="INELIGIBLE_BREAKOUT_SEARCH", outcome_observable=False)
            return event
        if bar.close > opening_high or bar.close < opening_low:
            breakout_direction = "long" if bar.close > opening_high else "short"
            execution_direction = breakout_direction
            if reverse_direction:
                execution_direction = "short" if breakout_direction == "long" else "long"
            signal = current + timedelta(minutes=entry_delay_minutes)
            delayed_signal_bar = lookup.get(signal)
            if delayed_signal_bar is None or not delayed_signal_bar.is_eligible:
                event.update(
                    status="ENTRY_CANCELLED",
                    reason="DELAY_SIGNAL_UNAVAILABLE",
                    breakout_bar_jst=current.isoformat(),
                    breakout_direction=breakout_direction,
                    execution_direction=execution_direction,
                    signal_jst=signal.isoformat(),
                    outcome_observable=False,
                )
                return event
            entry = _next_eligible(bars, signal)
            if entry is None:
                event.update(
                    status="ENTRY_CANCELLED",
                    reason="NEXT_ELIGIBLE_ENTRY_UNAVAILABLE",
                    breakout_bar_jst=current.isoformat(),
                    breakout_direction=breakout_direction,
                    execution_direction=execution_direction,
                    signal_jst=signal.isoformat(),
                    outcome_observable=False,
                )
                return event
            event.update(
                breakout_bar_jst=current.isoformat(),
                breakout_close=bar.close,
                breakout_direction=breakout_direction,
                execution_direction=execution_direction,
                signal_jst=signal.isoformat(),
                entry_open_jst=entry.ts_jst.isoformat(),
                entry_observable=True,
            )
            exit_signal = exit_ - timedelta(minutes=1)
            exit_signal_bar, exit_bar = lookup.get(exit_signal), lookup.get(exit_)
            if (
                exit_signal_bar is None
                or not exit_signal_bar.is_eligible
                or exit_bar is None
                or not exit_bar.is_eligible
            ):
                event.update(
                    status="ENTRY_FILLED_EXIT_UNKNOWN",
                    reason="EXIT_UNAVAILABLE",
                    exit_signal_jst=exit_signal.isoformat(),
                    outcome_observable=False,
                )
                return event
            event.update(
                status="EXECUTABLE",
                reason="FIRST_STRICT_CLOSE_BREAKOUT",
                exit_signal_jst=exit_signal.isoformat(),
                exit_open_jst=exit_.isoformat(),
                outcome_observable=True,
            )
            return event
        current += timedelta(minutes=1)
    event.update(status="SKIPPED", reason="NO_STRICT_BREAKOUT_BY_DEADLINE", outcome_observable=False)
    return event


def feasibility(
    axis: list[date], bars_by_day: dict[date, list[Bar]], classifier: CalendarClassifier
) -> dict[str, object]:
    """Return S2 availability/direction diagnostics only; never calculate PnL."""
    events = {
        target: night_opening_range_event(target, bars_by_day.get(target, []), classifier)
        for target in axis
    }
    executable = [target for target, event in events.items() if event["status"] == "EXECUTABLE"]
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
    """Retain known no-trades as zero and filled unknown exits as null."""
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if trade.trade_date not in daily or daily[trade.trade_date] is None:
            raise ValueError("R071 trade outside observable scheduled axis")
        if daily[trade.trade_date] != 0:
            raise ValueError("R071 permits at most one trade per trade date")
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
