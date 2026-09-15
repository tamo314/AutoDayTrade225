"""Causal state/event construction and fixed inference for TASK-R085-Q001."""

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
    """Immutable official NIGHT trade-date axis; the DAY partner may be unavailable."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def nearest_rank(values: list[int], percentile: int) -> int:
    if not values or percentile not in {20, 70, 80, 90}:
        raise ValueError("unregistered R085 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def _valid(bar: Bar | None, trade_date: date, session: Session, *, close: bool = False) -> bool:
    price = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == trade_date
        and bar.session is session
        and price > 0
    )


def _base_observation(
    classifier: CalendarClassifier,
    target: date,
    axis_set: set[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Map an official NIGHT trade date to its immediately preceding DAY."""
    result: dict[str, object] = {"trade_date": target.isoformat(), "j_valid": False}
    night_day = classifier.exchange_calendar.get(target)
    prior = night_day.previous_trade_date if night_day is not None else None
    if prior is None:
        result["reason"] = "NO_PREVIOUS_TRADE_DATE_IN_CALENDAR"
        return result
    assert night_day is not None
    prior_day = classifier.exchange_calendar.get(prior)
    if (
        prior_day is None
        or prior_day.next_trade_date != target
        or night_day.night_calendar_start_date != prior
    ):
        result["reason"] = "NONRECIPROCAL_DAY_TO_NIGHT_SCHEDULE_CHAIN"
        return result
    result.update(
        day_trade_date=prior.isoformat(),
        night_trade_date=target.isoformat(),
        night_calendar_start_date=night_day.night_calendar_start_date.isoformat(),
    )
    if prior not in axis_set:
        result["reason"] = "DAY_PARTNER_OUTSIDE_DEVELOPMENT"
        return result
    if (prior, Session.DAY) in isolated or (target, Session.NIGHT) in isolated:
        result["reason"] = "R004_DAY_OR_NIGHT_SESSION_QUARANTINED"
        return result
    try:
        d_stamp = classifier.session_close(prior, Session.DAY)
        n_stamp = classifier.session_open(target, Session.NIGHT)
    except ValueError as exc:
        result["reason"] = str(exc)
        return result
    d_bar = {bar.ts_jst: bar for bar in bars.get((prior, Session.DAY), [])}.get(d_stamp)
    n_bar = {bar.ts_jst: bar for bar in bars.get((target, Session.NIGHT), [])}.get(n_stamp)
    if not _valid(d_bar, prior, Session.DAY, close=True):
        result["reason"] = "FORMAL_DAY_CLOSE_MISSING_OR_INELIGIBLE"
        return result
    if not _valid(n_bar, target, Session.NIGHT):
        result["reason"] = "FORMAL_NIGHT_OPEN_MISSING_OR_INELIGIBLE"
        return result
    assert d_bar is not None and n_bar is not None
    gap = n_bar.open - d_bar.close
    result.update(
        j_valid=gap != 0,
        reason="ZERO_J" if gap == 0 else "VALID_J",
        d_final_day_close_jst=d_stamp.isoformat(),
        n_formal_open_jst=n_stamp.isoformat(),
        d_points=d_bar.close,
        n_points=n_bar.open,
        j_points=gap,
        abs_j_points=abs(gap),
        day_schedule_version=prior_day.schedule_version,
        night_schedule_version=night_day.schedule_version,
    )
    return result


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = 60,
    upper_percentile: int = 80,
) -> list[dict[str, object]]:
    if lookback not in {40, 60, 80} or upper_percentile not in {70, 80, 90}:
        raise ValueError("unregistered R085 state profile")
    ordered = list(axis)
    history: deque[dict[str, object]] = deque(maxlen=lookback)
    events: list[dict[str, object]] = []
    for target in ordered:
        base = _base_observation(classifier, target, set(ordered), bars, isolated)
        event: dict[str, object] = {
            **base,
            "lookback_valid_sessions_required": lookback,
            "lower_percentile": 20,
            "upper_percentile": upper_percentile,
            "quantile_method": "nearest_rank ceil(n*p/100)-1",
            "reference_valid_trade_dates_oldest_to_newest": [
                str(item["trade_date"]) for item in history
            ],
            "reference_valid_count": len(history),
        }
        if not bool(base["j_valid"]):
            event["state"] = "NONE"
        elif len(history) < lookback:
            event.update(state="NONE", reason="INSUFFICIENT_PRIOR_VALID_J_HISTORY")
        else:
            reference = [cast(int, item["abs_j_points"]) for item in history]
            q20, q_upper = nearest_rank(reference, 20), nearest_rank(reference, upper_percentile)
            event.update(q20_points=q20, q_upper_points=q_upper)
            current = cast(int, base["abs_j_points"])
            if q20 >= q_upper:
                event.update(state="NONE", reason="DEGENERATE_Q20_GTE_Q_UPPER")
            elif current >= q_upper:
                event.update(state="E", reason="EXTREME_ABS_J")
            elif q20 < current < q_upper:
                event.update(state="M", reason="MIDDLE_ABS_J")
            else:
                event.update(state="NONE", reason="OUTSIDE_EXCLUSIVE_STATE")
        if bool(base["j_valid"]):
            history.append(base)
        events.append(event)
    return events


def r085_event(
    state: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_not_before_minutes: int = 0,
    exit_minutes: int = 60,
) -> dict[str, object]:
    """Add observability after N; no response price is used to choose direction or state."""
    if entry_not_before_minutes not in {0, 5} or exit_minutes not in {30, 60, 90}:
        raise ValueError("unregistered R085 execution profile")
    event = dict(state)
    event.update(
        status="SKIPPED",
        entry_not_before_minutes=entry_not_before_minutes,
        exit_minutes=exit_minutes,
    )
    if event.get("state") not in {"E", "M"}:
        return event
    night_target = date.fromisoformat(cast(str, event["night_trade_date"]))
    n_stamp = datetime.fromisoformat(cast(str, event["n_formal_open_jst"]))
    lookup = {bar.ts_jst: bar for bar in bars.get((night_target, Session.NIGHT), [])}
    signal = n_stamp + timedelta(minutes=max(0, entry_not_before_minutes - 1))
    earliest_entry = n_stamp + timedelta(minutes=max(1, entry_not_before_minutes))
    candidates = [
        bar
        for bar in bars.get((night_target, Session.NIGHT), [])
        if bar.ts_jst >= earliest_entry and _valid(bar, night_target, Session.NIGHT)
    ]
    if not _valid(lookup.get(signal), night_target, Session.NIGHT) or not candidates:
        event.update(
            status="ENTRY_CANCELLED",
            reason="ENTRY_SIGNAL_OR_NEXT_ELIGIBLE_OPEN_MISSING_OR_INELIGIBLE",
        )
        return event
    entry = candidates[0]
    if entry.ts_jst - signal > timedelta(minutes=10):
        event.update(
            status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_EXCEEDS_ENGINE_MAX_DELAY"
        )
        return event
    exit_floor = n_stamp + timedelta(minutes=exit_minutes)
    exits = [
        bar
        for bar in bars.get((night_target, Session.NIGHT), [])
        if bar.ts_jst >= exit_floor and _valid(bar, night_target, Session.NIGHT)
    ]
    event.update(
        selection_fixed_at_jst=n_stamp.isoformat(),
        entry_signal_jst=signal.isoformat(),
        entry_open_jst=entry.ts_jst.isoformat(),
        exit_floor_jst=exit_floor.isoformat(),
    )
    if not exits:
        event.update(
            status="ENTRY_FILLED_EXIT_UNKNOWN",
            reason="FIRST_ELIGIBLE_EXIT_OPEN_MISSING_OR_INELIGIBLE",
        )
        return event
    exit_bar = exits[0]
    exit_signal = exit_bar.ts_jst - timedelta(minutes=1)
    event.update(exit_signal_jst=exit_signal.isoformat(), exit_open_jst=exit_bar.ts_jst.isoformat())
    if entry.ts_jst >= exit_bar.ts_jst or not _valid(
        lookup.get(exit_signal), night_target, Session.NIGHT
    ):
        event.update(
            status="ENTRY_FILLED_EXIT_UNKNOWN",
            reason="EXIT_SIGNAL_OR_ORDERING_MISSING_OR_INELIGIBLE",
        )
        return event
    gap = cast(int, event["j_points"])
    event.update(
        status="EXECUTABLE",
        reason="EXTREME_OR_MIDDLE_J_NEXT_ELIGIBLE_OPEN_EXECUTABLE",
        continuation_direction="long" if gap > 0 else "short",
        fade_direction="short" if gap > 0 else "long",
    )
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    **kwargs: int,
) -> list[dict[str, object]]:
    state_args = {
        key: value for key, value in kwargs.items() if key in {"lookback", "upper_percentile"}
    }
    event_args = {
        key: value
        for key, value in kwargs.items()
        if key in {"entry_not_before_minutes", "exit_minutes"}
    }
    return [
        r085_event(row, bars, **event_args)
        for row in state_ledger(classifier, axis, bars, isolated, **state_args)
    ]


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(events)
    extreme = [row for row in rows if row.get("state") == "E" and row.get("status") == "EXECUTABLE"]
    years = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in extreme)
    signs = Counter("positive" if cast(int, row["j_points"]) > 0 else "negative" for row in extreme)
    known = (
        "NO_",
        "NONRECIPROCAL_",
        "DAY_PARTNER_",
        "NIGHT_PARTNER_",
        "R004_",
        "FORMAL_",
        "ZERO_",
        "VALID_",
        "INSUFFICIENT_",
        "DEGENERATE_",
        "OUTSIDE_",
        "EXTREME_",
        "MIDDLE_",
        "ENTRY_",
        "NEXT_",
        "FIRST_",
    )
    unexplained = [
        cast(str, row["trade_date"])
        for row in rows
        if not str(row.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "extreme_executable_trades": len(extreme),
        "minimum_extreme_executable_trades": 180,
        "extreme_at_least_180": len(extreme) >= 180,
        "extreme_j_sign_counts": dict(sorted(signs.items())),
        "minimum_positive_and_negative_each": 60,
        "j_sign_minima_passed": signs["positive"] >= 60 and signs["negative"] >= 60,
        "extreme_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 25,
        "annual_2021_2024_minima_passed": all(years[year] >= 25 for year in range(2021, 2025)),
        "minimum_2025_h1": 12,
        "year_2025_h1_minimum_passed": years[2025] >= 12,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "extreme_at_least_180",
            "j_sign_minima_passed",
            "annual_2021_2024_minima_passed",
            "year_2025_h1_minimum_passed",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(
            sorted(Counter(str(row.get("reason", row["status"])) for row in rows).items())
        ),
        "state_counts": dict(
            sorted(Counter(str(row.get("state", "NONE")) for row in rows).items())
        ),
        "gate": gate,
    }


def aligned_daily_net(
    axis: list[date], trades_by_day: dict[date, Trade], unknown: set[date]
) -> dict[str, int | None]:
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for target, trade in trades_by_day.items():
        if target not in daily or daily[target] is None or daily[target] != 0:
            raise ValueError("R085 trade outside observable axis or duplicate day event")
        daily[target] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R085 MBB block")
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
    extreme_daily: list[int], continuation_daily: list[int], middle_daily: list[int]
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if not (len(extreme_daily) == len(continuation_daily) == len(middle_daily)):
        raise ValueError("R085 bootstrap axes are misaligned")
    index = mbb_indices(len(extreme_daily))
    e, c, m = (
        np.asarray(values, dtype=float)
        for values in (extreme_daily, continuation_daily, middle_daily)
    )
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "extreme_fade_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(extreme_daily),
            "ci95_percentile_linear": _ci(e[index].mean(axis=1)),
        },
        "extreme_fade_minus_continuation_paired_daily_net_jpy": {
            "estimate": fmean(
                [
                    left - right
                    for left, right in zip(extreme_daily, continuation_daily, strict=True)
                ]
            ),
            "ci95_percentile_linear": _ci((e[index] - c[index]).mean(axis=1)),
        },
        "extreme_minus_middle_group_mean_daily_net_jpy": {
            "estimate": fmean(extreme_daily) - fmean(middle_daily),
            "ci95_percentile_linear": _ci((e[index] - m[index]).mean(axis=1)),
        },
    }, index
