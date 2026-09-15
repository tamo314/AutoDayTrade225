"""Causal state selection and fixed inference for frozen R083-Q001."""

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
REGISTERED_EXITS = {time(10, 30), time(11, 30), time(14, 30)}
REGISTERED_ENTRY_DELAYS = {0, 4}


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the immutable Development trade-date axis before price filtering."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def nearest_rank(values: list[int], percentile: int) -> int:
    """Use the preregistered nearest-rank percentile without interpolation."""
    if not values or percentile not in {20, 70, 80, 90}:
        raise ValueError("unregistered R083 percentile")
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


def _base_observation(
    classifier: CalendarClassifier,
    target: date,
    axis_set: set[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Read D and A only from registered day boundaries available at A."""
    row = classifier.exchange_calendar.get(target)
    prior = row.previous_trade_date if row is not None else None
    result: dict[str, object] = {"trade_date": target.isoformat(), "g_valid": False}
    if prior is None or prior not in axis_set:
        result["reason"] = "NO_PRIOR_TRADE_DATE_IN_DEVELOPMENT_AXIS"
        return result
    prior_row = classifier.exchange_calendar.get(prior)
    if prior_row is None or prior_row.next_trade_date != target:
        result["reason"] = "NONRECIPROCAL_SCHEDULE_CHAIN"
        return result
    if (prior, Session.DAY) in isolated or (target, Session.DAY) in isolated:
        result["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return result
    try:
        d_stamp = classifier.session_close(prior, Session.DAY)
        a_stamp = datetime.combine(target, time(9), d_stamp.tzinfo)
    except ValueError as exc:
        result["reason"] = str(exc)
        return result
    d_bar = {bar.ts_jst: bar for bar in bars.get((prior, Session.DAY), [])}.get(d_stamp)
    a_bar = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}.get(a_stamp)
    if not _valid(d_bar, prior, close=True):
        result["reason"] = "PRIOR_FINAL_DAY_CLOSE_MISSING_OR_INELIGIBLE"
        return result
    if not _valid(a_bar, target):
        result["reason"] = "CURRENT_0900_OPEN_MISSING_OR_INELIGIBLE"
        return result
    assert d_bar is not None and a_bar is not None
    gap = a_bar.open - d_bar.close
    result.update(
        g_valid=gap != 0,
        reason="ZERO_G" if gap == 0 else "VALID_G",
        prior_trade_date=prior.isoformat(),
        d_final_day_close_jst=d_stamp.isoformat(),
        a_0900_open_jst=a_stamp.isoformat(),
        d_points=d_bar.close,
        a_points=a_bar.open,
        g_points=gap,
        abs_g_points=abs(gap),
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
    """Build current-excluded histories; invalid dates never backfill the deque."""
    if lookback not in {40, 60, 80} or upper_percentile not in {70, 80, 90}:
        raise ValueError("unregistered R083 state profile")
    ordered = list(axis)
    history: deque[dict[str, object]] = deque(maxlen=lookback)
    rows: list[dict[str, object]] = []
    for target in ordered:
        base = _base_observation(classifier, target, set(ordered), bars, isolated)
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
        if not bool(base["g_valid"]):
            event["state"] = "NONE"
        elif len(history) < lookback:
            event.update(state="NONE", reason="INSUFFICIENT_PRIOR_VALID_G_HISTORY")
        else:
            reference = [cast(int, item["abs_g_points"]) for item in history]
            q20 = nearest_rank(reference, 20)
            q_upper = nearest_rank(reference, upper_percentile)
            event.update(q20_points=q20, q_upper_points=q_upper)
            current = cast(int, base["abs_g_points"])
            if q20 >= q_upper:
                event.update(state="NONE", reason="DEGENERATE_Q20_GTE_Q_UPPER")
            elif current >= q_upper:
                event.update(state="E", reason="EXTREME_ABS_G")
            elif q20 < current < q_upper:
                event.update(state="M", reason="MIDDLE_ABS_G")
            else:
                event.update(state="NONE", reason="OUTSIDE_EXCLUSIVE_STATE")
        if bool(base["g_valid"]):
            history.append(base)
        rows.append(event)
    return rows


def r083_event(
    state: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_delay_minutes: int = 0,
    exit_time: time = time(11, 30),
) -> dict[str, object]:
    """Add fixed execution observability after A has been observed."""
    if entry_delay_minutes not in REGISTERED_ENTRY_DELAYS or exit_time not in REGISTERED_EXITS:
        raise ValueError("unregistered R083 execution profile")
    event = dict(state)
    event.update(
        status="SKIPPED",
        entry_delay_minutes=entry_delay_minutes,
        exit_open_jst_planned_clock=exit_time.isoformat(timespec="minutes"),
    )
    if event.get("state") not in {"E", "M"}:
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    a_stamp = datetime.fromisoformat(cast(str, event["a_0900_open_jst"]))
    signal = a_stamp + timedelta(minutes=entry_delay_minutes)
    entry = signal + timedelta(minutes=1)
    exit_open = datetime.combine(target, exit_time, a_stamp.tzinfo)
    exit_signal = exit_open - timedelta(minutes=1)
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    event.update(
        selection_fixed_at_jst=a_stamp.isoformat(),
        entry_signal_jst=signal.isoformat(),
        entry_open_jst=entry.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
    )
    if not _valid(lookup.get(signal), target) or not _valid(lookup.get(entry), target):
        event.update(
            status="ENTRY_CANCELLED", reason="ENTRY_SIGNAL_OR_NEXT_OPEN_MISSING_OR_INELIGIBLE"
        )
        return event
    if not _valid(lookup.get(exit_signal), target) or not _valid(lookup.get(exit_open), target):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    gap = cast(int, event["g_points"])
    event.update(
        status="EXECUTABLE",
        reason="EXTREME_OR_MIDDLE_GAP_EXECUTABLE",
        continuation_direction="long" if gap > 0 else "short",
        fade_direction="short" if gap > 0 else "long",
    )
    return event


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    """Return preregistered PnL-free availability and composition gates only."""
    rows = list(events)
    extreme = [row for row in rows if row.get("state") == "E" and row.get("status") == "EXECUTABLE"]
    by_year = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in extreme)
    signs = Counter("positive" if cast(int, row["g_points"]) > 0 else "negative" for row in extreme)
    known_prefixes = (
        "NO_PRIOR_",
        "NONRECIPROCAL_",
        "R004_",
        "PRIOR_",
        "CURRENT_",
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
        cast(str, row["trade_date"])
        for row in rows
        if not str(row.get("reason", "")).startswith(known_prefixes)
    ]
    gate: dict[str, object] = {
        "extreme_executable_trades": len(extreme),
        "minimum_extreme_executable_trades": 180,
        "extreme_at_least_180": len(extreme) >= 180,
        "extreme_g_sign_counts": dict(sorted(signs.items())),
        "minimum_positive_and_negative_each": 70,
        "g_sign_minima_passed": signs["positive"] >= 70 and signs["negative"] >= 70,
        "extreme_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 30,
        "annual_2021_2024_minima_passed": all(by_year[year] >= 30 for year in range(2021, 2025)),
        "minimum_2025_h1": 15,
        "year_2025_h1_minimum_passed": by_year[2025] >= 15,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "extreme_at_least_180",
            "g_sign_minima_passed",
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
    axis: list[date], trades: tuple[Trade, ...], unknown: set[date]
) -> dict[str, int | None]:
    """Keep known no-trades at zero and filled unknown exits as null."""
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if (
            trade.trade_date not in daily
            or daily[trade.trade_date] is None
            or daily[trade.trade_date] != 0
        ):
            raise ValueError("R083 trade outside observable axis or duplicate trade date")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R083 MBB block")
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
        for values in (
            continuation_daily,
            extreme_counts,
            middle_fade_daily,
            middle_counts,
        )
    ):
        raise ValueError("R083 bootstrap axes are misaligned")
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
    e_samples = e[index].mean(axis=1)
    paired_samples = (e[index] - c[index]).mean(axis=1)
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
