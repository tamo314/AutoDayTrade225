"""Causal event selection and fixed inference for frozen R074-Q001."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.domain import Bar, Session, Trade

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
HISTORY_COUNT = 20
MAX_CALENDAR_LOOKBACK_DAYS = 60
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915
MAX_FILL_DELAY_MINUTES = 10


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the immutable Development trade-date axis."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def nearest_rank(values: list[int], percentile: int) -> int:
    """Frozen nearest-rank percentile, with no interpolation."""
    if not values or percentile not in {20, 25, 33, 67, 75, 80}:
        raise ValueError("unregistered R074 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def previous_calendar_eligible_nights(
    classifier: CalendarClassifier, target: date
) -> list[date] | None:
    """Get exactly the latest 20 scheduled prior nights, never valid-bar replacements."""
    floor = target - timedelta(days=MAX_CALENDAR_LOOKBACK_DAYS)
    cursor = target
    dates: list[date] = []
    while True:
        row = classifier.exchange_calendar.get(cursor)
        prior = row.previous_trade_date if row else None
        if prior is None or prior < floor:
            break
        cursor = prior
        night_open = classifier.session_open(prior, Session.NIGHT)
        if night_open.date() + timedelta(days=1) == prior:
            dates.append(prior)
            if len(dates) == HISTORY_COUNT:
                return dates
    return None


def _stamp(target: date, clock: time, tz: object) -> datetime:
    return datetime.combine(target, clock, tz)  # type: ignore[arg-type]


def _valid(row: Bar | None, target: date, session: Session) -> bool:
    return bool(
        row is not None
        and row.is_eligible
        and row.trade_date == target
        and row.session is session
        and min(row.open, row.high, row.low, row.close) > 0
    )


def _night_range(
    classifier: CalendarClassifier,
    target: date,
    bars: list[Bar],
    *,
    isolated: set[tuple[date, Session]],
) -> tuple[int | None, str | None, list[Bar] | None]:
    if (target, Session.NIGHT) in isolated:
        return None, "R004_NIGHT_SESSION_QUARANTINED", None
    start = classifier.session_open(target, Session.NIGHT)
    end = _stamp(target, time(5, 29), start.tzinfo)
    if start.date() + timedelta(days=1) != target or start > end:
        return None, "NO_SCHEDULED_OVERNIGHT_WINDOW", None
    lookup = {bar.ts_jst: bar for bar in bars}
    rows = [lookup.get(start + timedelta(minutes=index)) for index in range(int((end - start).total_seconds() // 60) + 1)]
    if any(not _valid(row, target, Session.NIGHT) for row in rows):
        return None, "NIGHT_RANGE_MISSING_OR_INELIGIBLE", None
    complete = [row for row in rows if row is not None]
    return max(row.high for row in complete) - min(row.low for row in complete), None, complete


def _next_eligible(bars: list[Bar], signal: datetime) -> Bar | None:
    candidates = [
        bar
        for bar in bars
        if bar.ts_jst > signal
        and bar.is_eligible
        and bar.ts_jst - signal <= timedelta(minutes=MAX_FILL_DELAY_MINUTES)
    ]
    return min(candidates, key=lambda bar: bar.ts_jst, default=None)


def r074_event(
    classifier: CalendarClassifier,
    target: date,
    bars_by_day: dict[date, list[Bar]],
    *,
    isolated: set[tuple[date, Session]],
    state_group: str = "compression",
    compression_percentile: int = 25,
    opening_minutes: int = 30,
    entry_delay_minutes: int = 0,
    exit_time: time = time(14, 30),
) -> dict[str, object]:
    """Select a state-conditioned strict-close breakout using only available prefixes."""
    if state_group not in {"compression", "middle"}:
        raise ValueError("unregistered R074 state group")
    if compression_percentile not in {20, 25, 33}:
        raise ValueError("unregistered R074 compression percentile")
    if opening_minutes not in {20, 30, 45} or entry_delay_minutes not in {0, 1}:
        raise ValueError("unregistered R074 timing profile")
    if exit_time not in {time(14, 15), time(14, 30), time(14, 45)}:
        raise ValueError("unregistered R074 exit")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "SKIPPED",
        "state_group": state_group,
        "compression_percentile": compression_percentile,
        "opening_minutes": opening_minutes,
        "entry_delay_minutes": entry_delay_minutes,
        "exit_open_jst_planned_clock": exit_time.isoformat(timespec="minutes"),
        "history_count_required": HISTORY_COUNT,
        "max_calendar_lookback_days": MAX_CALENDAR_LOOKBACK_DAYS,
        "quantile_method": "nearest_rank ceil(n*p/100)-1",
    }
    references = previous_calendar_eligible_nights(classifier, target)
    if references is None:
        event["reason"] = "INSUFFICIENT_20_CALENDAR_ELIGIBLE_NIGHT_HISTORY_WITHIN_60_DAYS"
        return event
    event["reference_trade_dates_p1_to_p20"] = [item.isoformat() for item in references]
    if any(reference < DEVELOPMENT_START for reference in references):
        event["reason"] = "REFERENCE_OUTSIDE_DEVELOPMENT"
        return event
    reference_ranges: list[int] = []
    for index, reference in enumerate(references, start=1):
        value, reason, _ = _night_range(
            classifier, reference, bars_by_day.get(reference, []), isolated=isolated
        )
        if value is None:
            event.update(reason=f"REFERENCE_{reason}", reference_index=index)
            return event
        reference_ranges.append(value)
    current_range, current_reason, _ = _night_range(
        classifier, target, bars_by_day.get(target, []), isolated=isolated
    )
    if current_range is None:
        event["reason"] = f"CURRENT_{current_reason}"
        return event
    q20, q25, q33, q75 = (
        nearest_rank(reference_ranges, 20),
        nearest_rank(reference_ranges, 25),
        nearest_rank(reference_ranges, 33),
        nearest_rank(reference_ranges, 75),
    )
    event.update(
        reference_ranges_points_p1_to_p20=reference_ranges,
        current_night_range_points=current_range,
        q20_points=q20,
        q25_points=q25,
        q33_points=q33,
        q75_points=q75,
    )
    selected = (
        current_range <= nearest_rank(reference_ranges, compression_percentile)
        if state_group == "compression"
        else q25 < current_range <= q75
    )
    if not selected:
        event["reason"] = "OUTSIDE_SELECTED_NIGHT_RANGE_STATE"
        return event
    if (target, Session.DAY) in isolated:
        event["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return event
    bars = bars_by_day.get(target, [])
    lookup = {bar.ts_jst: bar for bar in bars}
    tz = classifier.session_open(target, Session.DAY).tzinfo
    opening_start = _stamp(target, time(9), tz)
    opening_rows = [lookup.get(opening_start + timedelta(minutes=index)) for index in range(opening_minutes)]
    if any(not _valid(row, target, Session.DAY) for row in opening_rows):
        event["reason"] = "OPENING_RANGE_MISSING_OR_INELIGIBLE"
        return event
    opening = [row for row in opening_rows if row is not None]
    high, low = max(row.high for row in opening), min(row.low for row in opening)
    event.update(opening_high_points=high, opening_low_points=low)
    search_start = opening_start + timedelta(minutes=opening_minutes)
    search_end = _stamp(target, time(11), tz)
    signal_bar: Bar | None = None
    direction: str | None = None
    current = search_start
    while current <= search_end:
        row = lookup.get(current)
        if not _valid(row, target, Session.DAY):
            event["reason"] = "BREAKOUT_SEARCH_MISSING_OR_INELIGIBLE"
            return event
        assert row is not None
        if row.close > high or row.close < low:
            signal_bar = row
            direction = "long" if row.close > high else "short"
            break
        current += timedelta(minutes=1)
    if signal_bar is None or direction is None:
        event["reason"] = "NO_STRICT_CLOSE_BREAKOUT_BY_1100"
        return event
    delayed_signal = signal_bar.ts_jst + timedelta(minutes=entry_delay_minutes)
    delay_row = lookup.get(delayed_signal)
    if not _valid(delay_row, target, Session.DAY):
        event.update(status="SIGNALLED", reason="DELAY_SIGNAL_MISSING_OR_INELIGIBLE")
        return event
    entry = _next_eligible(bars, delayed_signal)
    if entry is None:
        event.update(
            status="ENTRY_CANCELLED",
            reason="NO_NEXT_ELIGIBLE_ENTRY_WITHIN_10_MINUTES",
            breakout_direction=direction,
            breakout_bar_jst=signal_bar.ts_jst.isoformat(),
            signal_bar_jst=delayed_signal.isoformat(),
            entry_open_jst=None,
            entry_observable=False,
        )
        return event
    event.update(
        breakout_direction=direction,
        breakout_bar_jst=signal_bar.ts_jst.isoformat(),
        signal_bar_jst=delayed_signal.isoformat(),
        entry_open_jst=entry.ts_jst.isoformat(),
        entry_observable=True,
    )
    exit_signal = _stamp(target, (datetime.combine(target, exit_time) - timedelta(minutes=1)).time(), tz)
    exit_bar = lookup.get(_stamp(target, exit_time, tz))
    event.update(
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=(
            exit_bar.ts_jst.isoformat()
            if exit_bar is not None and _valid(exit_bar, target, Session.DAY)
            else None
        ),
        exit_observable=_valid(lookup.get(exit_signal), target, Session.DAY)
        and _valid(exit_bar, target, Session.DAY),
    )
    if not bool(event["exit_observable"]):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    event.update(status="EXECUTABLE", reason="SELECTED_STATE_FIRST_STRICT_CLOSE_BREAKOUT")
    return event


def feasibility(
    compression_events: dict[date, dict[str, object]], middle_events: dict[date, dict[str, object]]
) -> dict[str, object]:
    """S2-only counts: no PnL, return, win/loss, or rank is calculated."""
    executable_a = [
        target
        for target, event in compression_events.items()
        if event["status"] == "EXECUTABLE" and event["state_group"] == "compression"
    ]
    executable_d = [
        target
        for target, event in middle_events.items()
        if event["status"] == "EXECUTABLE" and event["state_group"] == "middle"
    ]
    by_year = Counter(target.year for target in executable_a)
    by_side = Counter(str(compression_events[target]["breakout_direction"]) for target in executable_a)
    known_prefixes = (
        "INSUFFICIENT_",
        "REFERENCE_",
        "CURRENT_",
        "OUTSIDE_",
        "R004_",
        "OPENING_",
        "BREAKOUT_",
        "NO_STRICT_",
        "DELAY_",
        "NO_NEXT_",
        "FIXED_EXIT_",
        "SELECTED_",
    )
    unexplained = [
        target.isoformat()
        for target, event in [*compression_events.items(), *middle_events.items()]
        if not str(event.get("reason", "")).startswith(known_prefixes)
    ]
    gate = {
        "compression_observable_trades": len(executable_a),
        "minimum_compression_observable_trades": 150,
        "compression_at_least_150": len(executable_a) >= 150,
        "compression_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 25,
        "compression_year_minima_passed": all(by_year[year] >= 25 for year in range(2021, 2025)),
        "compression_direction_counts": dict(sorted(by_side.items())),
        "minimum_compression_long_and_short_each": 45,
        "compression_direction_minima_passed": by_side["long"] >= 45 and by_side["short"] >= 45,
        "middle_state_observable_trades": len(executable_d),
        "minimum_middle_state_observable_trades": 300,
        "middle_state_at_least_300": len(executable_d) >= 300,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": len(unexplained) == 0,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "compression_at_least_150",
            "compression_year_minima_passed",
            "compression_direction_minima_passed",
            "middle_state_at_least_300",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "compression_status_counts": dict(
            sorted(Counter(str(event.get("reason", event["status"])) for event in compression_events.values()).items())
        ),
        "middle_status_counts": dict(
            sorted(Counter(str(event.get("reason", event["status"])) for event in middle_events.values()).items())
        ),
        "gate": gate,
    }


def aligned_daily_net(
    axis: list[date], trades: tuple[Trade, ...], unknown: set[date]
) -> dict[str, int | None]:
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if trade.trade_date not in daily or daily[trade.trade_date] is None or daily[trade.trade_date] != 0:
            raise ValueError("R074 trade outside observable axis or duplicate trade date")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R074 MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def _ci(samples: NDArray[np.float64]) -> list[float]:
    low, high = np.quantile(samples, (0.025, 0.975), method="linear")
    return [float(low), float(high)]


def bootstrap(
    primary_daily: list[int], reverse_daily: list[int], primary_counts: list[int], middle_daily: list[int], middle_counts: list[int]
) -> tuple[dict[str, object], NDArray[np.int64]]:
    """Use one fixed date-block index for all pre-registered R074 estimands."""
    if not all(len(values) == len(primary_daily) for values in (reverse_daily, primary_counts, middle_daily, middle_counts)):
        raise ValueError("R074 bootstrap axes are misaligned")
    index = mbb_indices(len(primary_daily))
    p, r, pc, d, dc = (
        np.asarray(primary_daily, dtype=float),
        np.asarray(reverse_daily, dtype=float),
        np.asarray(primary_counts, dtype=float),
        np.asarray(middle_daily, dtype=float),
        np.asarray(middle_counts, dtype=float),
    )
    primary_samples = p[index].mean(axis=1)
    reverse_samples = (p[index] - r[index]).mean(axis=1)
    p_count, d_count = pc[index].sum(axis=1), dc[index].sum(axis=1)
    valid = (p_count > 0) & (d_count > 0)
    state_samples = p[index].sum(axis=1)[valid] / p_count[valid] - d[index].sum(axis=1)[valid] / d_count[valid]
    if not len(state_samples):
        raise ValueError("all R074 state-control bootstrap samples are empty")
    result = {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "primary_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(primary_daily),
            "ci95_percentile_linear": _ci(primary_samples),
        },
        "primary_minus_reverse_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean([left - right for left, right in zip(primary_daily, reverse_daily, strict=True)]),
            "ci95_percentile_linear": _ci(reverse_samples),
        },
        "primary_minus_middle_net_jpy_per_trade": {
            "estimate": sum(primary_daily) / sum(primary_counts) - sum(middle_daily) / sum(middle_counts),
            "ci95_percentile_linear": _ci(state_samples),
            "empty_condition_resamples": int((~valid).sum()),
        },
    }
    return result, index
