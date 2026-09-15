"""Causal state construction and fixed inference for TASK-R087-Q001."""

from __future__ import annotations

from collections import Counter
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
REGISTERED_PERIODS = {5, 10, 20}
REGISTERED_UPPERS = {60, 70, 80}
REGISTERED_REFERENCES = {80, 120, 160}
REGISTERED_CONFIRMATION_MINUTES = {5, 15, 30}
REGISTERED_EXITS = {time(14, 30), time(14, 55), time(15, 10)}


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the immutable Development scheduled trade-date population."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def nearest_rank(values: list[float], percentile: int) -> float:
    if not values or percentile not in {30, 60, 70, 80}:
        raise ValueError("unregistered R087 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def required_valid_reference_count(window: int) -> int:
    """The frozen main 100/120 rule extended at the same five-sixths coverage ratio."""
    if window not in REGISTERED_REFERENCES:
        raise ValueError("unregistered R087 reference window")
    return ceil(window * 5 / 6)


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    value = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and value > 0
    )


def _day_observation(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Use only the first/last eligible bar within the target's versioned DAY interval."""
    schedule = classifier.exchange_calendar.get(target)
    row: dict[str, object] = {"trade_date": target.isoformat(), "day_return_valid": False}
    if schedule is None:
        row["reason"] = "NO_REGISTERED_SCHEDULE"
        return row
    opening = classifier.session_open(target, Session.DAY)
    closing = classifier.session_close(target, Session.DAY)
    row.update(
        schedule_version=schedule.schedule_version,
        day_session_open_jst=opening.isoformat(),
        day_session_close_jst=closing.isoformat(),
    )
    if (target, Session.DAY) in isolated:
        row["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return row
    eligible = [
        bar
        for bar in bars.get((target, Session.DAY), [])
        if opening <= bar.ts_jst <= closing and _valid(bar, target) and _valid(bar, target, close=True)
    ]
    if not eligible:
        row["reason"] = "NO_ELIGIBLE_DAY_BAR_WITH_VALID_OPEN_CLOSE"
        return row
    first, last = eligible[0], eligible[-1]
    r_bps = 10_000 * (last.close - first.open) / first.open
    row.update(
        day_return_valid=True,
        reason="VALID_DAY_OPEN_TO_CLOSE_RETURN",
        o_first_eligible_open_points=first.open,
        o_first_eligible_open_jst=first.ts_jst.isoformat(),
        c_last_eligible_close_points=last.close,
        c_last_eligible_close_jst=last.ts_jst.isoformat(),
        r_bps=r_bps,
    )
    return row


def _acceptance_observation(
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    endpoint_minutes: int,
) -> dict[str, object]:
    if endpoint_minutes not in REGISTERED_CONFIRMATION_MINUTES:
        raise ValueError("unregistered R087 confirmation endpoint")
    rows = bars.get((target, Session.DAY), [])
    zone = rows[0].ts_jst.tzinfo if rows else None
    if zone is None:
        return {"p_valid": False, "p_reason": "CURRENT_DAY_BARS_UNAVAILABLE"}
    opening = datetime.combine(target, time(9), zone)
    ending = opening + timedelta(minutes=endpoint_minutes - 1)
    lookup = {bar.ts_jst: bar for bar in rows}
    a_bar, b_bar = lookup.get(opening), lookup.get(ending)
    result: dict[str, object] = {
        "p_valid": False,
        "a_0900_open_jst": opening.isoformat(),
        "b_confirmation_close_jst": ending.isoformat(),
        "confirmation_minutes": endpoint_minutes,
    }
    if not _valid(a_bar, target):
        result["p_reason"] = "CURRENT_0900_OPEN_MISSING_OR_INELIGIBLE"
    elif not _valid(b_bar, target, close=True):
        result["p_reason"] = "CONFIRMATION_CLOSE_MISSING_OR_INELIGIBLE"
    else:
        assert a_bar is not None and b_bar is not None
        p_bps = 10_000 * (b_bar.close - a_bar.open) / a_bar.open
        result.update(
            p_valid=p_bps != 0,
            p_reason="VALID_P" if p_bps != 0 else "ZERO_P",
            a_0900_open_points=a_bar.open,
            b_confirmation_close_points=b_bar.close,
            p_bps=p_bps,
        )
    return result


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    cumulative_period: int = 10,
    reference_window: int = 120,
    upper_percentile: int = 70,
    confirmation_minutes: int = 15,
) -> list[dict[str, object]]:
    """Build T using exact previous scheduled dates and a separately exact reference window."""
    if (
        cumulative_period not in REGISTERED_PERIODS
        or reference_window not in REGISTERED_REFERENCES
        or upper_percentile not in REGISTERED_UPPERS
        or confirmation_minutes not in REGISTERED_CONFIRMATION_MINUTES
    ):
        raise ValueError("unregistered R087 profile")
    ordered = list(axis)
    day_rows = [_day_observation(classifier, target, bars, isolated) for target in ordered]
    records: list[dict[str, object]] = []
    for index, target in enumerate(ordered):
        day = day_rows[index]
        prior_indices = range(max(0, index - cumulative_period), index)
        prior_days = [day_rows[item] for item in prior_indices]
        prior_dates = [cast(str, item["trade_date"]) for item in prior_days]
        event: dict[str, object] = {
            **day,
            "cumulative_period_scheduled_trade_dates": cumulative_period,
            "prior_scheduled_day_return_trade_dates": prior_dates,
            "prior_scheduled_day_return_count": len(prior_dates),
            "reference_window_scheduled_trade_dates": reference_window,
            "minimum_valid_t_reference_count": required_valid_reference_count(reference_window),
            "upper_percentile": upper_percentile,
            "lower_percentile": 30,
            "quantile_method": "nearest_rank ceil(n*p/100)-1",
            "t_valid": False,
            "condition": "NONE",
        }
        if len(prior_days) != cumulative_period:
            event["reason"] = "INSUFFICIENT_PRIOR_SCHEDULED_DAYS_FOR_T"
        elif not all(bool(item["day_return_valid"]) for item in prior_days):
            event["reason"] = "T_REQUIRES_ALL_PRIOR_SCHEDULED_DAY_RETURNS_VALID_NO_BACKFILL"
        else:
            t_bps = sum(cast(float, item["r_bps"]) for item in prior_days)
            event.update(t_valid=True, t_bps=t_bps, abs_t_bps=abs(t_bps), reason="VALID_T")
        reference = records[max(0, index - reference_window) : index]
        valid_reference = [item for item in reference if bool(item.get("t_valid"))]
        event.update(
            reference_scheduled_trade_dates=[cast(str, item["trade_date"]) for item in reference],
            reference_valid_t_trade_dates=[cast(str, item["trade_date"]) for item in valid_reference],
            reference_valid_t_count=len(valid_reference),
        )
        event.update(_acceptance_observation(target, bars, confirmation_minutes))
        if not bool(event["t_valid"]):
            pass
        elif len(valid_reference) < required_valid_reference_count(reference_window):
            event["reason"] = "INSUFFICIENT_VALID_T_IN_EXACT_PRIOR_SCHEDULED_REFERENCE_WINDOW"
        else:
            abs_reference = [cast(float, item["abs_t_bps"]) for item in valid_reference]
            q30, q_upper = nearest_rank(abs_reference, 30), nearest_rank(abs_reference, upper_percentile)
            event.update(q30_abs_t_bps=q30, q_upper_abs_t_bps=q_upper)
            t_value = cast(float, event["t_bps"])
            p_value = cast(float, event.get("p_bps", 0.0))
            if t_value == 0:
                event["reason"] = "ZERO_T"
            elif not bool(event["p_valid"]):
                event["reason"] = cast(str, event["p_reason"])
            elif abs(t_value) >= q_upper:
                event.update(
                    state="E",
                    condition="EA" if p_value * t_value > 0 else "EO",
                    reason="EXTREME_T_ACCEPTANCE" if p_value * t_value > 0 else "EXTREME_T_OPPOSITE",
                )
            elif q30 < abs(t_value) < q_upper and p_value * t_value > 0:
                event.update(state="M", condition="MA", reason="MIDDLE_T_ACCEPTANCE")
            else:
                event["reason"] = "OUTSIDE_REGISTERED_CONDITION"
        records.append(event)
    return records


def r087_event(
    state: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_extra_bars: int = 0,
    exit_time: time = time(14, 55),
) -> dict[str, object]:
    if entry_extra_bars not in {0, 1} or exit_time not in REGISTERED_EXITS:
        raise ValueError("unregistered R087 execution profile")
    event = dict(state)
    event.update(status="SKIPPED", entry_extra_bars=entry_extra_bars, exit_time_jst=exit_time.isoformat())
    if event.get("condition") not in {"EA", "EO", "MA"}:
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    endpoint = datetime.fromisoformat(cast(str, event["b_confirmation_close_jst"]))
    signal = endpoint + timedelta(minutes=entry_extra_bars)
    exit_open = datetime.combine(target, exit_time, signal.tzinfo)
    exit_signal = exit_open - timedelta(minutes=1)
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    event.update(
        selection_fixed_at_jst=endpoint.isoformat(),
        entry_signal_jst=signal.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
    )
    if not _valid(lookup.get(signal), target, close=True):
        event.update(status="ENTRY_CANCELLED", reason="ENTRY_SIGNAL_CLOSE_MISSING_OR_INELIGIBLE")
        return event
    choices = [bar for bar in bars.get((target, Session.DAY), []) if bar.ts_jst > signal and _valid(bar, target)]
    if not choices:
        event.update(status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_OPEN_MISSING_OR_INELIGIBLE")
        return event
    entry = choices[0]
    event["entry_open_jst"] = entry.ts_jst.isoformat()
    if entry.ts_jst - signal > timedelta(minutes=10):
        event.update(status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_EXCEEDS_ENGINE_MAX_DELAY")
        return event
    if entry.ts_jst >= exit_open or not _valid(lookup.get(exit_signal), target, close=True) or not _valid(lookup.get(exit_open), target):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    p_bps = cast(float, event["p_bps"])
    event.update(
        status="EXECUTABLE",
        reason=f"{event['condition']}_NEXT_ELIGIBLE_ENTRY_FIXED_EXIT_EXECUTABLE",
        continuation_direction="long" if p_bps > 0 else "short",
        fade_direction="short" if p_bps > 0 else "long",
    )
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    **kwargs: int | time,
) -> list[dict[str, object]]:
    cumulative_period = cast(int, kwargs.get("cumulative_period", 10))
    reference_window = cast(int, kwargs.get("reference_window", 120))
    upper_percentile = cast(int, kwargs.get("upper_percentile", 70))
    confirmation_minutes = cast(int, kwargs.get("confirmation_minutes", 15))
    entry_extra_bars = cast(int, kwargs.get("entry_extra_bars", 0))
    exit_time = cast(time, kwargs.get("exit_time", time(14, 55)))
    states = state_ledger(
        classifier,
        axis,
        bars,
        isolated,
        cumulative_period=cumulative_period,
        reference_window=reference_window,
        upper_percentile=upper_percentile,
        confirmation_minutes=confirmation_minutes,
    )
    return [
        r087_event(row, bars, entry_extra_bars=entry_extra_bars, exit_time=exit_time)
        for row in states
    ]


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(events)
    by_condition = {
        condition: [row for row in rows if row.get("condition") == condition and row.get("status") == "EXECUTABLE"]
        for condition in ("EA", "EO", "MA")
    }
    ea = by_condition["EA"]
    years = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in ea)
    signs = Counter("positive" if cast(float, row["t_bps"]) > 0 else "negative" for row in ea)
    known = (
        "NO_", "R004_", "VALID_", "INSUFFICIENT_", "T_REQUIRES_", "ZERO_", "CURRENT_",
        "CONFIRMATION_", "EXTREME_", "MIDDLE_", "OUTSIDE_", "EA_", "EO_", "MA_", "ENTRY_", "NEXT_", "FIXED_",
    )
    unexplained = [cast(str, row["trade_date"]) for row in rows if not str(row.get("reason", "")).startswith(known)]
    gate: dict[str, object] = {
        "executable_by_condition": {key: len(value) for key, value in by_condition.items()},
        "ea_at_least_90": len(ea) >= 90,
        "eo_at_least_90": len(by_condition["EO"]) >= 90,
        "ma_at_least_150": len(by_condition["MA"]) >= 150,
        "ea_t_sign_counts": dict(sorted(signs.items())),
        "ea_t_positive_negative_at_least_35_each": signs["positive"] >= 35 and signs["negative"] >= 35,
        "ea_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "ea_2021_2024_at_least_15_each": all(years[year] >= 15 for year in range(2021, 2025)),
        "ea_2025_h1_at_least_8": years[2025] >= 8,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(bool(gate[key]) for key in (
        "ea_at_least_90", "eo_at_least_90", "ma_at_least_150", "ea_t_positive_negative_at_least_35_each",
        "ea_2021_2024_at_least_15_each", "ea_2025_h1_at_least_8", "unexplained_exclusions_equal_zero",
    ))
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(sorted(Counter(str(row.get("reason", row["status"])) for row in rows).items())),
        "condition_counts": dict(sorted(Counter(str(row.get("condition", "NONE")) for row in rows).items())),
        "gate": gate,
    }


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    """Audit frozen information boundaries before any return or PnL is calculated."""
    executable = [row for row in events if row.get("status") == "EXECUTABLE"]
    checks = {
        "scheduled_axis_complete_unique": [row["trade_date"] for row in events] == [target.isoformat() for target in axis] and len(events) == len({row["trade_date"] for row in events}),
        "t_uses_exact_previous_scheduled_dates_without_backfill": all(
            len(cast(list[str], row["prior_scheduled_day_return_trade_dates"])) <= cast(int, row["cumulative_period_scheduled_trade_dates"])
            and cast(list[str], row["prior_scheduled_day_return_trade_dates"]) == [target.isoformat() for target in axis[max(0, index - cast(int, row["cumulative_period_scheduled_trade_dates"])) : index]]
            for index, row in enumerate(events)
        ),
        "reference_is_exact_prior_scheduled_window_current_excluded": all(
            cast(list[str], row["reference_scheduled_trade_dates"]) == [target.isoformat() for target in axis[max(0, index - cast(int, row["reference_window_scheduled_trade_dates"])) : index]]
            and cast(str, row["trade_date"]) not in cast(list[str], row["reference_valid_t_trade_dates"])
            for index, row in enumerate(events)
        ),
        "all_valid_t_is_required_before_t": all(
            not bool(row["t_valid"])
            or len(cast(list[str], row["prior_scheduled_day_return_trade_dates"])) == cast(int, row["cumulative_period_scheduled_trade_dates"])
            for row in events
        ),
        "signal_is_confirmation_close_and_entry_is_next_eligible_open": all(
            datetime.fromisoformat(cast(str, row["selection_fixed_at_jst"])) == datetime.fromisoformat(cast(str, row["b_confirmation_close_jst"]))
            and datetime.fromisoformat(cast(str, row["entry_open_jst"])) > datetime.fromisoformat(cast(str, row["entry_signal_jst"]))
            and datetime.fromisoformat(cast(str, row["exit_open_jst"])).time() == time.fromisoformat(cast(str, row["exit_time_jst"]))
            for row in executable
        ),
        "main_signal_is_0914": all(
            datetime.fromisoformat(cast(str, row["b_confirmation_close_jst"])).time() == time(9, 14)
            for row in events
            if bool(row.get("p_valid"))
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def aligned_daily_net(axis: list[date], trades: tuple[Trade, ...], unknown: set[date]) -> dict[str, int | None]:
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if trade.trade_date not in daily or daily[trade.trade_date] is None or daily[trade.trade_date] != 0:
            raise ValueError("R087 trade outside observable axis or duplicate event")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R087 MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def _ci(samples: NDArray[np.float64]) -> list[float] | None:
    if not len(samples) or not np.isfinite(samples).all():
        return None
    low, high = np.quantile(samples, (0.025, 0.975), method="linear")
    return [float(low), float(high)]


def bootstrap(
    ea_daily: list[int], fade_daily: list[int], eo_daily: list[int], eo_counts: list[int], ma_daily: list[int], ma_counts: list[int], ea_counts: list[int]
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if not all(len(value) == len(ea_daily) for value in (fade_daily, eo_daily, eo_counts, ma_daily, ma_counts, ea_counts)):
        raise ValueError("R087 bootstrap axes are misaligned")
    index = mbb_indices(len(ea_daily))
    ea, fade, eo, ec, ma, mc, ac = (np.asarray(value, dtype=float) for value in (ea_daily, fade_daily, eo_daily, eo_counts, ma_daily, ma_counts, ea_counts))
    def ratio_difference(left_daily: NDArray[np.float64], left_counts: NDArray[np.float64], right_daily: NDArray[np.float64], right_counts: NDArray[np.float64]) -> tuple[NDArray[np.float64], int]:
        left_n, right_n = left_counts[index].sum(axis=1), right_counts[index].sum(axis=1)
        valid = (left_n > 0) & (right_n > 0)
        samples = np.full(MBB_REPETITIONS, np.nan)
        samples[valid] = left_daily[index][valid].sum(axis=1) / left_n[valid] - right_daily[index][valid].sum(axis=1) / right_n[valid]
        return samples, int((~valid).sum())
    ea_eo, empty_eo = ratio_difference(ea, ac, eo, ec)
    ea_ma, empty_ma = ratio_difference(ea, ac, ma, mc)
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile; common resampled blocks; group ratio estimator",
        "seed": MBB_SEED, "repetitions": MBB_REPETITIONS, "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "ea_scheduled_axis_mean_net_jpy_per_trade_date": {"estimate": fmean(ea_daily), "ci95_percentile_linear": _ci(ea[index].mean(axis=1))},
        "ea_minus_paired_fade_paired_daily_net_jpy": {"estimate": fmean([a-b for a, b in zip(ea_daily, fade_daily, strict=True)]), "ci95_percentile_linear": _ci((ea[index] - fade[index]).mean(axis=1))},
        "ea_minus_eo_net_jpy_per_trade_ratio_estimator": {"estimate": sum(ea_daily) / sum(ea_counts) - sum(eo_daily) / sum(eo_counts), "ci95_percentile_linear": _ci(ea_eo), "empty_condition_resamples": empty_eo},
        "ea_minus_ma_net_jpy_per_trade_ratio_estimator": {"estimate": sum(ea_daily) / sum(ea_counts) - sum(ma_daily) / sum(ma_counts), "ci95_percentile_linear": _ci(ea_ma), "empty_condition_resamples": empty_ma},
    }, index
