"""Causal state selection and fixed inference for frozen R078-Q001."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
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
REGISTERED_ENDPOINTS = {time(9, 44), time(9, 59), time(10, 29)}
REGISTERED_EXITS = {time(14, 15), time(14, 30), time(14, 45)}


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the immutable Development trade-date axis, before price filtering."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def nearest_rank(values: list[int], percentile: int) -> int:
    """Frozen nearest-rank percentile without interpolation."""
    if not values or percentile not in {20, 70, 80, 90}:
        raise ValueError("unregistered R078 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    price = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and price > 0
    )


def _gap_diagnostic(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    r_points: int,
) -> dict[str, object]:
    """Record the preregistered gap diagnostic without changing state or execution."""
    row = classifier.exchange_calendar.get(target)
    prior = row.previous_trade_date if row is not None else None
    if prior is None or (prior, Session.DAY) in isolated:
        return {
            "prior_day_close_to_0900_gap_status": "UNAVAILABLE",
            "prior_day_close_to_0900_gap_reason": "PRIOR_DAY_UNAVAILABLE_OR_QUARANTINED",
        }
    try:
        close_stamp = classifier.session_close(prior, Session.DAY)
    except ValueError:
        return {
            "prior_day_close_to_0900_gap_status": "UNAVAILABLE",
            "prior_day_close_to_0900_gap_reason": "PRIOR_DAY_CLOSE_UNDEFINED",
        }
    prior_lookup = {bar.ts_jst: bar for bar in bars.get((prior, Session.DAY), [])}
    current_lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    close_bar = prior_lookup.get(close_stamp)
    open_stamp = datetime.combine(target, time(9), close_stamp.tzinfo)
    open_bar = current_lookup.get(open_stamp)
    if not _valid(close_bar, prior, close=True) or not _valid(open_bar, target):
        return {
            "prior_day_close_to_0900_gap_status": "UNAVAILABLE",
            "prior_day_close_to_0900_gap_reason": "GAP_REQUIRED_BAR_MISSING_OR_INELIGIBLE",
        }
    assert close_bar is not None and open_bar is not None
    gap = open_bar.open - close_bar.close
    sign = (
        "ZERO_R"
        if r_points == 0
        else "SAME"
        if gap * r_points > 0
        else "OPPOSITE"
        if gap * r_points < 0
        else "ZERO_GAP"
    )
    return {
        "prior_day_close_to_0900_gap_status": "AVAILABLE",
        "prior_trade_date_for_gap": prior.isoformat(),
        "prior_day_close_to_0900_gap_points": gap,
        "gap_and_r_sign": sign,
    }


def _base_observation(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    endpoint: time,
) -> dict[str, object]:
    opening = datetime.combine(
        target, time(9), classifier.session_close(target, Session.DAY).tzinfo
    )
    ending = datetime.combine(target, endpoint, opening.tzinfo)
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "r_valid": False,
        "signal_endpoint_jst": ending.isoformat(),
    }
    if (target, Session.DAY) in isolated:
        result["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return result
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    a_bar, b_bar = lookup.get(opening), lookup.get(ending)
    if not _valid(a_bar, target):
        result["reason"] = "CURRENT_0900_OPEN_MISSING_OR_INELIGIBLE"
        return result
    if not _valid(b_bar, target, close=True):
        result["reason"] = "SIGNAL_ENDPOINT_CLOSE_MISSING_OR_INELIGIBLE"
        return result
    assert a_bar is not None and b_bar is not None
    movement = b_bar.close - a_bar.open
    result.update(
        r_valid=movement != 0,
        reason="ZERO_R" if movement == 0 else "VALID_R",
        a_0900_open_points=a_bar.open,
        b_endpoint_close_points=b_bar.close,
        r_points=movement,
        abs_r_points=abs(movement),
    )
    result.update(_gap_diagnostic(classifier, target, bars, isolated, movement))
    return result


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = 60,
    upper_percentile: int = 80,
    endpoint: time = time(9, 59),
) -> list[dict[str, object]]:
    """Build current-excluded valid-R histories; invalid dates never backfill by rule."""
    if (
        lookback not in {40, 60, 80}
        or upper_percentile not in {70, 80, 90}
        or endpoint not in REGISTERED_ENDPOINTS
    ):
        raise ValueError("unregistered R078 state profile")
    history: deque[dict[str, object]] = deque(maxlen=lookback)
    results: list[dict[str, object]] = []
    for target in axis:
        base = _base_observation(classifier, target, bars, isolated, endpoint)
        event: dict[str, object] = {
            **base,
            "lookback_valid_trade_dates_required": lookback,
            "upper_percentile": upper_percentile,
            "lower_percentile": 20,
            "quantile_method": "nearest_rank ceil(n*p/100)-1",
            "reference_valid_trade_dates_oldest_to_newest": [
                str(item["trade_date"]) for item in history
            ],
            "reference_valid_count": len(history),
        }
        if not bool(base["r_valid"]):
            event["state"] = "NONE"
        elif len(history) < lookback:
            event.update(state="NONE", reason="INSUFFICIENT_PRIOR_VALID_R_HISTORY")
        else:
            reference = [cast(int, item["abs_r_points"]) for item in history]
            q20, q_upper = nearest_rank(reference, 20), nearest_rank(reference, upper_percentile)
            event.update(q20_points=q20, q_upper_points=q_upper)
            current = cast(int, base["abs_r_points"])
            if q20 >= q_upper:
                event.update(state="NONE", reason="DEGENERATE_Q20_GTE_Q_UPPER")
            elif current >= q_upper:
                event.update(state="E", reason="EXTREME_ABS_R")
            elif q20 < current < q_upper:
                event.update(state="M", reason="MIDDLE_ABS_R")
            else:
                event.update(state="NONE", reason="OUTSIDE_EXCLUSIVE_STATE")
        if bool(base["r_valid"]):
            history.append(base)
        results.append(event)
    return results


def r078_event(
    state: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_delay_minutes: int = 0,
    exit_time: time = time(14, 30),
) -> dict[str, object]:
    """Add fixed execution observability after causal state is fixed at endpoint close."""
    if entry_delay_minutes not in {0, 1} or exit_time not in REGISTERED_EXITS:
        raise ValueError("unregistered R078 execution profile")
    event = dict(state)
    event.update(
        status="SKIPPED",
        entry_delay_minutes=entry_delay_minutes,
        exit_open_jst_planned_clock=exit_time.isoformat(timespec="minutes"),
    )
    if event.get("state") not in {"E", "M"}:
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    signal = datetime.fromisoformat(cast(str, event["signal_endpoint_jst"])) + timedelta(
        minutes=entry_delay_minutes
    )
    entry = signal + timedelta(minutes=1)
    exit_open = datetime.combine(target, exit_time, signal.tzinfo)
    exit_signal = exit_open - timedelta(minutes=1)
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    event.update(
        selection_fixed_at_jst=datetime.fromisoformat(
            cast(str, event["signal_endpoint_jst"])
        ).isoformat(),
        entry_signal_jst=signal.isoformat(),
        entry_open_jst=entry.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
    )
    if not _valid(lookup.get(signal), target, close=True) or not _valid(lookup.get(entry), target):
        event.update(
            status="ENTRY_CANCELLED", reason="ENTRY_SIGNAL_OR_NEXT_OPEN_MISSING_OR_INELIGIBLE"
        )
        return event
    if not _valid(lookup.get(exit_signal), target, close=True) or not _valid(
        lookup.get(exit_open), target
    ):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    movement = cast(int, event["r_points"])
    event.update(
        status="EXECUTABLE",
        reason="EXTREME_OR_MIDDLE_R_EXECUTABLE",
        continuation_direction="long" if movement > 0 else "short",
        fade_direction="short" if movement > 0 else "long",
    )
    return event


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    """Return only pre-PnL availability and composition gates."""
    rows = list(events)
    extreme = [
        item for item in rows if item.get("state") == "E" and item.get("status") == "EXECUTABLE"
    ]
    by_year = Counter(date.fromisoformat(cast(str, item["trade_date"])).year for item in extreme)
    signs = Counter(
        "positive" if cast(int, item["r_points"]) > 0 else "negative" for item in extreme
    )
    known_prefixes = (
        "R004_",
        "CURRENT_",
        "SIGNAL_",
        "ZERO_",
        "VALID_",
        "INSUFFICIENT_",
        "DEGENERATE_",
        "OUTSIDE_",
        "EXTREME_",
        "MIDDLE_",
        "ENTRY_",
        "FIXED_",
    )
    unexplained = [
        cast(str, item["trade_date"])
        for item in rows
        if not str(item.get("reason", "")).startswith(known_prefixes)
    ]
    gate: dict[str, object] = {
        "extreme_executable_trades": len(extreme),
        "minimum_extreme_executable_trades": 150,
        "extreme_at_least_150": len(extreme) >= 150,
        "extreme_r_sign_counts": dict(sorted(signs.items())),
        "minimum_positive_and_negative_each": 50,
        "r_sign_minima_passed": signs["positive"] >= 50 and signs["negative"] >= 50,
        "extreme_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 20,
        "annual_2021_2024_minima_passed": all(by_year[year] >= 20 for year in range(2021, 2025)),
        "minimum_2025_h1": 10,
        "year_2025_h1_minimum_passed": by_year[2025] >= 10,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "extreme_at_least_150",
            "r_sign_minima_passed",
            "annual_2021_2024_minima_passed",
            "year_2025_h1_minimum_passed",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(
            sorted(Counter(str(item.get("reason", item["status"])) for item in rows).items())
        ),
        "state_counts": dict(
            sorted(Counter(str(item.get("state", "NONE")) for item in rows).items())
        ),
        "gap_diagnostic_counts": dict(
            sorted(
                Counter(
                    str(
                        item.get(
                            "gap_and_r_sign",
                            item.get("prior_day_close_to_0900_gap_status", "NOT_APPLICABLE"),
                        )
                    )
                    for item in rows
                ).items()
            )
        ),
        "gate": gate,
    }


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
            raise ValueError("R078 trade outside observable axis or duplicate trade date")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R078 MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[
        :, :length
    ]


def _ci(samples: NDArray[np.float64]) -> list[float] | None:
    if not len(samples) or not np.isfinite(samples).all():
        return None
    low, high = np.quantile(samples, (0.025, 0.975), method="linear")
    return [float(low), float(high)]


def bootstrap(
    extreme_fade_daily: list[int],
    continuation_daily: list[int],
    extreme_counts: list[int],
    middle_fade_daily: list[int],
    middle_counts: list[int],
) -> tuple[dict[str, object], NDArray[np.int64]]:
    """Use one frozen date-block index for daily and per-trade estimands."""
    if not all(
        len(values) == len(extreme_fade_daily)
        for values in (continuation_daily, extreme_counts, middle_fade_daily, middle_counts)
    ):
        raise ValueError("R078 bootstrap axes are misaligned")
    index = mbb_indices(len(extreme_fade_daily))
    e, c, ec, m, mc = (
        np.asarray(value, dtype=float)
        for value in (
            extreme_fade_daily,
            continuation_daily,
            extreme_counts,
            middle_fade_daily,
            middle_counts,
        )
    )
    e_samples, paired_samples = e[index].mean(axis=1), (e[index] - c[index]).mean(axis=1)
    e_count, m_count = ec[index].sum(axis=1), mc[index].sum(axis=1)
    valid = (e_count > 0) & (m_count > 0)
    state_samples = np.full(MBB_REPETITIONS, np.nan)
    state_samples[valid] = (
        e[index][valid].sum(axis=1) / e_count[valid] - m[index][valid].sum(axis=1) / m_count[valid]
    )
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "extreme_fade_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(extreme_fade_daily),
            "ci95_percentile_linear": _ci(e_samples),
        },
        "extreme_fade_minus_continuation_paired_daily_net_jpy": {
            "estimate": fmean(
                [
                    left - right
                    for left, right in zip(extreme_fade_daily, continuation_daily, strict=True)
                ]
            ),
            "ci95_percentile_linear": _ci(paired_samples),
        },
        "extreme_fade_minus_middle_fade_net_jpy_per_trade": {
            "estimate": sum(extreme_fade_daily) / sum(extreme_counts)
            - sum(middle_fade_daily) / sum(middle_counts),
            "ci95_percentile_linear": _ci(state_samples) if valid.all() else None,
            "empty_condition_resamples": int((~valid).sum()),
            "all_resamples_have_both_condition_trade_counts": bool(valid.all()),
        },
    }, index
