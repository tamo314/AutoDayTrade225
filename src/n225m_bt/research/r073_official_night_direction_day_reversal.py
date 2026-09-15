"""Causal event construction and S2 availability for R073-Q001."""

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
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def aligned_daily_net(
    axis: list[date], trades: tuple[Trade, ...], unknown: set[date]
) -> dict[str, int | None]:
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if (
            trade.trade_date not in daily
            or daily[trade.trade_date] is None
            or daily[trade.trade_date] != 0
        ):
            raise ValueError("R073 trade outside observable scheduled axis or duplicate trade date")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


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
    lower, upper = np.quantile(data[indices].mean(axis=1), (0.025, 0.975), method="linear")
    return {
        "estimate": fmean(values),
        "ci95_percentile_linear": [float(lower), float(upper)],
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "repetitions": MBB_REPETITIONS,
        "seed": seed,
        "method": "non-wrapping MBB; tail truncation; linear percentile",
    }


def _registered_times(night_end: time, entry: time, exit_: time) -> None:
    if night_end not in {time(5), time(5, 30)}:
        raise ValueError("unregistered R073 night endpoint")
    if entry not in {time(9), time(9, 1)}:
        raise ValueError("unregistered R073 day entry")
    if exit_ not in {time(14, 15), time(14, 30), time(14, 45)}:
        raise ValueError("unregistered R073 day exit")


def official_night_direction_event(
    target: date,
    bars: list[Bar],
    classifier: CalendarClassifier,
    *,
    night_end: time = time(5, 30),
    entry: time = time(9),
    exit_: time = time(14, 30),
    reverse_direction: bool = True,
) -> dict[str, object]:
    """Select from the actual versioned night open; later availability cannot unselect it."""
    _registered_times(night_end, entry, exit_)
    night_open = classifier.session_open(target, Session.NIGHT)
    night_close = classifier.session_close(target, Session.NIGHT)
    day_open = classifier.session_open(target, Session.DAY)
    day_close = classifier.session_close(target, Session.DAY)
    feature_end = datetime.combine(target, night_end, night_open.tzinfo)
    entry_open = datetime.combine(target, entry, day_open.tzinfo)
    exit_open = datetime.combine(target, exit_, day_open.tzinfo)
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "night_session_open_jst": night_open.isoformat(),
        "night_session_close_jst": night_close.isoformat(),
        "day_session_open_jst": day_open.isoformat(),
        "day_session_close_jst": day_close.isoformat(),
        "feature_start_open_jst": night_open.isoformat(),
        "feature_end_open_jst": feature_end.isoformat(),
        "signal_fixed_at_jst": feature_end.isoformat(),
        "scheduled_entry_open_jst": entry_open.isoformat(),
        "entry_activation_jst": (entry_open - timedelta(minutes=1)).isoformat(),
        "scheduled_exit_open_jst": exit_open.isoformat(),
        "exit_signal_jst": (exit_open - timedelta(minutes=1)).isoformat(),
        "reverse_direction": reverse_direction,
    }
    if not (
        night_open.date() + timedelta(days=1) == target
        and night_open <= feature_end <= night_close
        and day_open <= entry_open - timedelta(minutes=1) < entry_open < exit_open <= day_close
    ):
        event.update(status="NO_SCHEDULED_CROSS_SESSION_WINDOW", outcome_observable=False)
        return event
    lookup = {bar.ts_jst: bar for bar in bars}
    start_bar, end_bar = lookup.get(night_open), lookup.get(feature_end)
    if start_bar is None or end_bar is None:
        event.update(
            status="SKIPPED", reason="MISSING_NIGHT_FEATURE_OPEN", outcome_observable=False
        )
        return event
    if not start_bar.is_eligible or not end_bar.is_eligible:
        event.update(
            status="SKIPPED", reason="INELIGIBLE_NIGHT_FEATURE_OPEN", outcome_observable=False
        )
        return event
    if start_bar.open == end_bar.open:
        event.update(status="SKIPPED", reason="ZERO_NIGHT_DIRECTION", outcome_observable=False)
        return event
    night_direction = "long" if end_bar.open > start_bar.open else "short"
    execution_direction = (
        night_direction
        if not reverse_direction
        else ("short" if night_direction == "long" else "long")
    )
    event.update(night_direction=night_direction, execution_direction=execution_direction)
    activation_bar, entry_bar = (
        lookup.get(entry_open - timedelta(minutes=1)),
        lookup.get(entry_open),
    )
    if (
        activation_bar is None
        or entry_bar is None
        or not activation_bar.is_eligible
        or not entry_bar.is_eligible
    ):
        event.update(
            status="ENTRY_CANCELLED", reason="DAY_ENTRY_UNAVAILABLE", outcome_observable=False
        )
        return event
    event.update(entry_open_jst=entry_open.isoformat(), entry_observable=True)
    exit_signal, exit_bar = lookup.get(exit_open - timedelta(minutes=1)), lookup.get(exit_open)
    if (
        exit_signal is None
        or exit_bar is None
        or not exit_signal.is_eligible
        or not exit_bar.is_eligible
    ):
        event.update(
            status="ENTRY_FILLED_EXIT_UNKNOWN",
            reason="DAY_EXIT_UNAVAILABLE",
            outcome_observable=False,
        )
        return event
    event.update(status="EXECUTABLE", reason="NONZERO_NIGHT_DIRECTION", outcome_observable=True)
    return event


def feasibility(
    axis: list[date], bars_by_day: dict[date, list[Bar]], classifier: CalendarClassifier
) -> dict[str, object]:
    """Return S2-only execution availability and side counts, never returns or PnL."""
    events = {
        target: official_night_direction_event(target, bars_by_day.get(target, []), classifier)
        for target in axis
    }
    executable = [target for target, event in events.items() if event["status"] == "EXECUTABLE"]
    by_year = Counter(target.year for target in executable)
    by_side = Counter(str(events[target]["execution_direction"]) for target in executable)
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
        "execution_direction_counts": dict(sorted(by_side.items())),
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


def q002_feasibility(
    axis: list[date], bars_by_day: dict[date, list[Bar]], classifier: CalendarClassifier
) -> dict[str, object]:
    """Apply the pre-registered Q002 S2 gate without calculating PnL or returns.

    The 886 denominator is the independently saved, price-independent calendar
    capacity from TASK-R073-D001.  A nonzero direction is still required for an
    executable trade, but equal directions are not counted as data or mapping
    attrition.
    """
    events = {
        target: official_night_direction_event(target, bars_by_day.get(target, []), classifier)
        for target in axis
    }
    calendar_eligible = [
        target
        for target, event in events.items()
        if event["status"] != "NO_SCHEDULED_CROSS_SESSION_WINDOW"
    ]
    unexplained = [
        target
        for target in calendar_eligible
        if (event := events[target]).get("status") not in {"EXECUTABLE", "SKIPPED"}
        or (event["status"] == "SKIPPED" and event.get("reason") != "ZERO_NIGHT_DIRECTION")
    ]
    executable = [target for target, event in events.items() if event["status"] == "EXECUTABLE"]
    by_year = Counter(target.year for target in executable)
    by_side = Counter(str(events[target]["execution_direction"]) for target in executable)
    minimum_executable = ceil(0.95 * 886)
    gate = {
        "calendar_eligible_trade_dates_equals_886": len(calendar_eligible) == 886,
        "unexplained_data_trade_date_or_implementation_exclusions": len(unexplained),
        "unexplained_exclusions_equal_zero": len(unexplained) == 0,
        "executable_excluding_equal_directions": len(executable),
        "minimum_executable_95_percent_of_886": minimum_executable,
        "executable_at_least_95_percent": len(executable) >= minimum_executable,
        "minimum_2021_through_2024_each": 180,
        "minimum_long_and_short_each": 300,
        "year_direction_minima_passed": all(by_year[year] >= 180 for year in range(2021, 2025))
        and by_side["long"] >= 300
        and by_side["short"] >= 300,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "calendar_eligible_trade_dates_equals_886",
            "unexplained_exclusions_equal_zero",
            "executable_at_least_95_percent",
            "year_direction_minima_passed",
        )
    )
    return {
        "scheduled_trade_dates": len(axis),
        "calendar_eligible_trade_dates": len(calendar_eligible),
        "equal_night_direction_trade_dates": sum(
            event.get("reason") == "ZERO_NIGHT_DIRECTION" for event in events.values()
        ),
        "executable_trade_dates": len(executable),
        "executable_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "execution_direction_counts": dict(sorted(by_side.items())),
        "unexplained_exclusion_trade_dates": [target.isoformat() for target in unexplained],
        "status_counts": dict(
            sorted(Counter(str(event.get("reason", event["status"])) for event in events.values()).items())
        ),
        "gate": gate,
        "events": {target.isoformat(): event for target, event in events.items()},
    }
