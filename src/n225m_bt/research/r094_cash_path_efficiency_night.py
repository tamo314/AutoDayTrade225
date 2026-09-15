"""Causal full-TSE-path efficiency states and following-night orders for R094-Q001."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.calendar.session_rules import regime_for_trade_date
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r091_fixed_tse_short import tse_cash_close

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
LOOKBACK = 120
MIN_REFERENCES = 100
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915


def nearest_rank(values: list[float], percentile: int) -> float:
    if not values or percentile not in {40, 50, 60, 70, 75, 80}:
        raise ValueError("unregistered R094 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def _valid(bar: Bar | None, target: date, session: Session, *, close: bool = False) -> bool:
    price = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is session
        and price > 0
    )


def scheduled_cash_opens(target: date) -> list[datetime]:
    """Return the versioned normal TSE minute-bar opens, never inferred from data."""
    end = tse_cash_close(target)
    starts = (datetime.combine(target, time(9), JST), datetime.combine(target, time(12, 30), JST))
    ends = (datetime.combine(target, time(11, 30), JST), end)
    return [
        start + timedelta(minutes=offset)
        for start, finish in zip(starts, ends, strict=True)
        for offset in range(int((finish - start).total_seconds() // 60))
    ]


def _cash_observation(
    target: date, rows: list[Bar] | None, *, quarantined: bool
) -> dict[str, object]:
    result: dict[str, object] = {"valid": False}
    if quarantined:
        result["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return result
    planned = scheduled_cash_opens(target)
    by_stamp: dict[datetime, list[Bar]] = {}
    for bar in rows or []:
        by_stamp.setdefault(bar.ts_jst, []).append(bar)
    selected: list[Bar] = []
    for stamp in planned:
        candidates = by_stamp.get(stamp, [])
        if len(candidates) != 1 or not _valid(candidates[0], target, Session.DAY, close=True):
            result["reason"] = "TSE_CASH_SCHEDULED_BAR_MISSING_DUPLICATE_OR_INELIGIBLE"
            return result
        selected.append(candidates[0])
    opening = selected[0]
    if not _valid(opening, target, Session.DAY):
        result["reason"] = "TSE_0900_OPEN_MISSING_OR_INELIGIBLE"
        return result
    closes = [bar.close for bar in selected]
    path_variation = abs(closes[0] - opening.open) + sum(
        abs(right - left) for left, right in zip(closes[:-1], closes[1:], strict=True)
    )
    displacement = closes[-1] - opening.open
    if path_variation <= 0:
        result["reason"] = "ZERO_CASH_PATH_VARIATION"
        return result
    if displacement == 0:
        result["reason"] = "ZERO_CASH_RETURN"
        return result
    cash_end = tse_cash_close(target)
    r = displacement / opening.open
    result.update(
        valid=True,
        reason="VALID_CASH_PATH",
        opening_open_jst=planned[0].isoformat(),
        final_cash_bar_open_jst=planned[-1].isoformat(),
        cash_end_jst=cash_end.isoformat(),
        scheduled_cash_bar_count=len(planned),
        o_points=opening.open,
        c_points=closes[-1],
        r=r,
        x=abs(r),
        V=path_variation,
        e=abs(displacement) / path_variation,
        r_sign=1 if displacement > 0 else -1,
    )
    return result


def cash_state_ledger(
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    qx_percentile: int = 50,
    qe_percentile: int = 75,
) -> list[dict[str, object]]:
    """Use only exact current-excluded 120 TSE days without invalid-date backfill."""
    if qx_percentile not in {40, 50, 60} or qe_percentile not in {70, 75, 80}:
        raise ValueError("unregistered R094 state profile")
    ordered = list(axis)
    observations = {
        target: _cash_observation(
            target, bars.get((target, Session.DAY)), quarantined=(target, Session.DAY) in isolated
        )
        for target in ordered
    }
    events: list[dict[str, object]] = []
    for position, target in enumerate(ordered):
        observation = observations[target]
        references = ordered[max(0, position - LOOKBACK) : position]
        event: dict[str, object] = {
            "trade_date": target.isoformat(),
            "lookback_scheduled_tse_days": LOOKBACK,
            "minimum_valid_x_e": MIN_REFERENCES,
            "qx_percentile": qx_percentile,
            "qe_percentile": qe_percentile,
            "quantile_method": "nearest_rank ceil(n*p/100)-1; qe equality high",
            "reference_trade_dates_oldest_to_newest": [item.isoformat() for item in references],
            "observation": observation,
            "state": "NONE",
            "reason": observation["reason"],
        }
        if len(references) != LOOKBACK:
            event["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
            events.append(event)
            continue
        valid = [observations[item] for item in references if bool(observations[item]["valid"])]
        event["reference_valid_x_e_count"] = len(valid)
        if not bool(observation["valid"]):
            events.append(event)
            continue
        if len(valid) < MIN_REFERENCES:
            event["reason"] = "INSUFFICIENT_VALID_X_E_REFERENCES"
            events.append(event)
            continue
        xs, es = ([float(cast(float, item[key])) for item in valid] for key in ("x", "e"))
        qx, qe = nearest_rank(xs, qx_percentile), nearest_rank(es, qe_percentile)
        x, efficiency = float(cast(float, observation["x"])), float(cast(float, observation["e"]))
        rolling = sum(value <= x for value in xs) / len(xs)
        event.update(qx=qx, qe=qe, current_x_rolling_percentile=rolling)
        if x >= qx and efficiency >= qe:
            event.update(state="H", reason="HIGH_DISPLACEMENT_HIGH_EFFICIENCY")
        elif x >= qx:
            event.update(state="K", reason="HIGH_DISPLACEMENT_LOW_EFFICIENCY")
        else:
            event["reason"] = "X_BELOW_QX"
        events.append(event)
    return events


def _following_night(calendar: ExchangeCalendar, cash_day: date) -> tuple[date | None, str | None]:
    matches = [
        item for item in calendar.trading_days() if item.night_calendar_start_date == cash_day
    ]
    if len(matches) != 1:
        return None, "FOLLOWING_OSE_NIGHT_MAPPING_NOT_UNIQUE"
    night = matches[0]
    cash = calendar.get(cash_day)
    if (
        cash is None
        or cash.next_trade_date != night.trade_date
        or night.previous_trade_date != cash_day
    ):
        return None, "NONRECIPROCAL_TSE_TO_FOLLOWING_OSE_NIGHT_MAPPING"
    return night.trade_date, None


def _last_normal_night_open(classifier: CalendarClassifier, night_trade_date: date) -> datetime:
    item = classifier.exchange_calendar.get(night_trade_date)
    if item is None or item.night_calendar_start_date is None:
        raise ValueError("night trade date has no explicit calendar start")
    regime = regime_for_trade_date(classifier.sessions, item.night_calendar_start_date)
    normal_end = regime.night.regular_end_next_day or regime.night.session_close_next_day
    if normal_end is None:
        raise ValueError("night regime has no normal end")
    return datetime.combine(night_trade_date, normal_end, JST) - timedelta(minutes=1)


def scheduled_event(
    state: dict[str, object],
    classifier: CalendarClassifier,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    entry_delay_minutes: int = 0,
    exit_early_minutes: int = 0,
) -> dict[str, object]:
    """Submit only after H/K is fixed; later night availability cannot change that state."""
    if entry_delay_minutes not in {0, 1} or exit_early_minutes not in {0, 30}:
        raise ValueError("unregistered R094 execution profile")
    event = dict(state)
    event.update(
        status="SKIPPED",
        entry_delay_minutes=entry_delay_minutes,
        exit_early_minutes=exit_early_minutes,
        entry_order_submitted=False,
        completion_status="NO_ORDER",
    )
    if event.get("state") not in {"H", "K"}:
        return event
    cash_day = date.fromisoformat(cast(str, event["trade_date"]))
    observation = cast(dict[str, object], event["observation"])
    cash_end = datetime.fromisoformat(cast(str, observation["cash_end_jst"]))
    event.update(
        submit_jst=cash_end.isoformat(),
        entry_order_submitted=True,
        status="STATE_ORDER_SUBMITTED",
        completion_status="PENDING_FOLLOWING_NIGHT_ENTRY",
        continuation_direction="long" if int(cast(int, observation["r_sign"])) > 0 else "short",
        reversal_direction="short" if int(cast(int, observation["r_sign"])) > 0 else "long",
    )
    night_trade_date, mapping_reason = _following_night(classifier.exchange_calendar, cash_day)
    if mapping_reason is not None or night_trade_date is None:
        event.update(status="ENTRY_CANCELLED", completion_status="NO_FILL", reason=mapping_reason)
        return event
    if not DEVELOPMENT_START <= night_trade_date <= DEVELOPMENT_END:
        event.update(
            night_trade_date=night_trade_date.isoformat(),
            status="ENTRY_CANCELLED",
            completion_status="NO_FILL",
            reason="FOLLOWING_NIGHT_OUTSIDE_DEVELOPMENT",
        )
        return event
    record = classifier.exchange_calendar.get(night_trade_date)
    if record is None:
        raise ValueError("mapped night trade date is absent from exchange calendar")
    if (night_trade_date, Session.NIGHT) in isolated:
        event.update(
            night_trade_date=night_trade_date.isoformat(),
            status="ENTRY_CANCELLED",
            completion_status="NO_FILL",
            reason="R004_FOLLOWING_NIGHT_SESSION_QUARANTINED",
        )
        return event
    formal_open = classifier.session_open(night_trade_date, Session.NIGHT)
    normal_exit = _last_normal_night_open(classifier, night_trade_date) - timedelta(
        minutes=exit_early_minutes
    )
    lookup = {bar.ts_jst: bar for bar in bars.get((night_trade_date, Session.NIGHT), [])}
    entry_open = formal_open + timedelta(minutes=entry_delay_minutes)
    if not _valid(lookup.get(entry_open), night_trade_date, Session.NIGHT):
        event.update(
            night_trade_date=night_trade_date.isoformat(),
            formal_night_open_jst=formal_open.isoformat(),
            entry_open_planned_jst=entry_open.isoformat(),
            status="ENTRY_CANCELLED",
            completion_status="NO_FILL",
            reason="FIRST_NORMAL_NIGHT_ENTRY_OPEN_MISSING_OR_INELIGIBLE",
        )
        return event
    event.update(
        night_trade_date=night_trade_date.isoformat(),
        night_calendar_start_date=cash_day.isoformat(),
        night_schedule_version=record.schedule_version,
        formal_night_open_jst=formal_open.isoformat(),
        entry_open_jst=entry_open.isoformat(),
        exit_open_planned_jst=normal_exit.isoformat(),
        exit_signal_jst=(normal_exit - timedelta(minutes=1)).isoformat(),
        status="ENTRY_FILLED_EXIT_UNKNOWN",
        completion_status="UNRESOLVED_EXIT",
    )
    if not _valid(
        lookup.get(normal_exit - timedelta(minutes=1)), night_trade_date, Session.NIGHT, close=True
    ):
        event["reason"] = "FINAL_NORMAL_EXIT_DECISION_BAR_MISSING_OR_INELIGIBLE"
        return event
    if (
        not _valid(lookup.get(normal_exit), night_trade_date, Session.NIGHT)
        or entry_open >= normal_exit
    ):
        event["reason"] = (
            "FINAL_NORMAL_EXIT_OPEN_MISSING_OR_INELIGIBLE"
            if entry_open < normal_exit
            else "ENTRY_NOT_BEFORE_FINAL_NORMAL_EXIT"
        )
        return event
    event.update(
        exit_open_jst=normal_exit.isoformat(),
        status="EXECUTABLE",
        completion_status="COMPLETE",
        reason="SCHEDULED_CASH_TO_NIGHT_PATH_COMPLETE",
    )
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    **kwargs: int,
) -> list[dict[str, object]]:
    state_keys = {"qx_percentile", "qe_percentile"}
    execution_keys = {"entry_delay_minutes", "exit_early_minutes"}
    state = cash_state_ledger(
        axis, bars, isolated, **{key: value for key, value in kwargs.items() if key in state_keys}
    )
    return [
        scheduled_event(
            item,
            classifier,
            bars,
            isolated,
            **{key: value for key, value in kwargs.items() if key in execution_keys},
        )
        for item in state
    ]


def scheduled_reference_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, bool]:
    expected_axis = [item.isoformat() for item in axis]
    windows = [
        [item.isoformat() for item in axis[max(0, i - LOOKBACK) : i]] for i in range(len(axis))
    ]
    actual = [
        list(cast(list[str], row.get("reference_trade_dates_oldest_to_newest", [])))
        for row in events
    ]
    return {
        "scheduled_tse_axis_complete_unique": [cast(str, row["trade_date"]) for row in events]
        == expected_axis
        and len(events) == len(set(expected_axis)),
        "references_match_exact_current_excluded_prior_min_i_120": actual == windows,
        "reference_dates_ordered_unique_current_future_absent": all(
            window == sorted(window)
            and len(window) == len(set(window))
            and all(item < cast(str, row["trade_date"]) for item in window)
            for row, window in zip(events, actual, strict=True)
        ),
        "q_ready_requires_120_scheduled_days_and_100_valid_x_e": all(
            "qx" not in row
            or (
                len(cast(list[str], row["reference_trade_dates_oldest_to_newest"])) == LOOKBACK
                and int(cast(int, row["reference_valid_x_e_count"])) >= MIN_REFERENCES
            )
            for row in events
        ),
    }


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("R094 scheduled axis is shorter than the frozen MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[
        :, :length
    ]


def bootstrap(
    a_daily: list[int], b_daily: list[int], c_daily: list[int], d_daily: list[int]
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if len({len(a_daily), len(b_daily), len(c_daily), len(d_daily)}) != 1:
        raise ValueError("R094 bootstrap axes are misaligned")
    index = mbb_indices(len(a_daily))
    a, b, c, d = (np.asarray(item, dtype=float) for item in (a_daily, b_daily, c_daily, d_daily))

    def interval(values: NDArray[np.float64]) -> dict[str, object]:
        samples = values[index].mean(axis=1)
        ci = np.quantile(samples, (0.025, 0.975), method="linear")
        return {
            "estimate": fmean(values.tolist()),
            "ci95_percentile_linear": [float(ci[0]), float(ci[1])],
        }

    return {
        "method": "20 trade_date non-circular moving-block bootstrap; common indices; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "A_continuation_daily_net_jpy_per_trade_date": interval(a),
        "A_minus_B_daily_net_jpy_per_trade_date": interval(a - b),
        "A_minus_C_daily_net_jpy_per_trade_date": interval(a - c),
        "A_minus_D_daily_net_jpy_per_trade_date": interval(a - d),
    }, index
