"""Causal cash-session state and scheduled cash-to-night orders for R093-Q001."""

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
    if not values or percentile not in {70, 75, 80}:
        raise ValueError("unregistered R093 percentile")
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


def _cash_observation(
    target: date, rows: list[Bar] | None, *, quarantined: bool
) -> dict[str, object]:
    result: dict[str, object] = {"r_valid": False}
    if quarantined:
        result["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return result
    by_stamp = {bar.ts_jst: bar for bar in rows or []}
    opening = datetime.combine(target, time(9), JST)
    cash_end = tse_cash_close(target)
    final_open = cash_end - timedelta(minutes=1)
    o_bar, c_bar = by_stamp.get(opening), by_stamp.get(final_open)
    if not _valid(o_bar, target, Session.DAY):
        result["reason"] = "TSE_0900_OPEN_MISSING_OR_INELIGIBLE"
        return result
    if not _valid(c_bar, target, Session.DAY, close=True):
        result["reason"] = "TSE_FINAL_SCHEDULED_MINUTE_CLOSE_MISSING_OR_INELIGIBLE"
        return result
    assert o_bar is not None and c_bar is not None
    r = (c_bar.close - o_bar.open) / o_bar.open
    result.update(
        r_valid=True,
        reason="VALID_CASH_RETURN",
        opening_open_jst=opening.isoformat(),
        final_cash_bar_open_jst=final_open.isoformat(),
        cash_end_jst=cash_end.isoformat(),
        o_points=o_bar.open,
        c_points=c_bar.close,
        r=r,
        x=abs(r),
        r_sign=1 if r > 0 else -1 if r < 0 else 0,
    )
    return result


def cash_state_ledger(
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    percentile: int = 75,
) -> list[dict[str, object]]:
    """Use exactly 120 prior scheduled TSE days; invalid observations are not backfilled."""
    if percentile not in {70, 75, 80}:
        raise ValueError("unregistered R093 state profile")
    ordered = list(axis)
    events: list[dict[str, object]] = []
    for position, target in enumerate(ordered):
        observation = _cash_observation(
            target,
            bars.get((target, Session.DAY)),
            quarantined=(target, Session.DAY) in isolated,
        )
        references = ordered[max(0, position - LOOKBACK) : position]
        event: dict[str, object] = {
            "trade_date": target.isoformat(),
            "lookback_scheduled_tse_days": LOOKBACK,
            "minimum_valid_x": MIN_REFERENCES,
            "percentile": percentile,
            "quantile_method": "nearest_rank ceil(n*p/100)-1; equality upper",
            "reference_trade_dates_oldest_to_newest": [item.isoformat() for item in references],
            "observation": observation,
            "state": "NONE",
            "reason": observation["reason"],
        }
        if len(references) != LOOKBACK:
            event["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
            events.append(event)
            continue
        values: list[float] = []
        for reference in references:
            item = _cash_observation(
                reference,
                bars.get((reference, Session.DAY)),
                quarantined=(reference, Session.DAY) in isolated,
            )
            if bool(item["r_valid"]):
                values.append(float(cast(float, item["x"])))
        event["reference_valid_x_count"] = len(values)
        if not bool(observation["r_valid"]):
            events.append(event)
            continue
        if len(values) < MIN_REFERENCES:
            event["reason"] = "INSUFFICIENT_VALID_X_REFERENCES"
            events.append(event)
            continue
        q70, q75, q80 = (nearest_rank(values, item) for item in (70, 75, 80))
        event.update(q70=q70, q75=q75, q80=q80)
        sign = int(cast(int, observation["r_sign"]))
        if sign == 0:
            event["reason"] = "ZERO_CASH_RETURN"
        elif float(cast(float, observation["x"])) >= float(cast(float, event[f"q{percentile}"])):
            event.update(state="E", reason="EXTREME_CASH_RETURN")
        else:
            event["reason"] = f"X_BELOW_Q{percentile}"
        events.append(event)
    return events


def scheduled_reference_audit(
    events: list[dict[str, object]], axis: list[date]
) -> dict[str, bool]:
    """Audit the frozen scheduled-date U rule, including its warm-up prefix.

    The expected window at scheduled-axis position ``i`` is the ordered,
    current-excluded interval ``axis[max(0, i - 120):i]``.  In particular,
    one through 119 prior dates during initialization are valid windows; they
    are not malformed 120-day windows.  R093's frozen event rule separately
    waits for the complete 120-date window before it makes q75 available.
    """
    expected_axis = [item.isoformat() for item in axis]
    axis_complete_unique = (
        [cast(str, row["trade_date"]) for row in events] == expected_axis
        and len(events) == len({cast(str, row["trade_date"]) for row in events})
    )
    expected_windows = [
        [item.isoformat() for item in axis[max(0, position - LOOKBACK) : position]]
        for position in range(len(axis))
    ]
    actual_windows = [
        list(cast(list[str], row.get("reference_trade_dates_oldest_to_newest", [])))
        for row in events
    ]
    exact_windows = len(actual_windows) == len(expected_windows) and all(
        actual == expected
        for actual, expected in zip(actual_windows, expected_windows, strict=True)
    )
    ordered_unique = all(
        window == sorted(window) and len(window) == len(set(window))
        for window in actual_windows
    )
    current_or_future_absent = all(
        all(reference < cast(str, row["trade_date"]) for reference in window)
        for row, window in zip(events, actual_windows, strict=True)
    )
    q_ready_only_with_valid_references = all(
        "q75" not in row
        or (
            len(cast(list[str], row["reference_trade_dates_oldest_to_newest"]))
            == LOOKBACK
            and int(cast(int, row.get("reference_valid_x_count", 0))) >= MIN_REFERENCES
        )
        for row in events
    )
    return {
        "scheduled_tse_axis_complete_unique": axis_complete_unique,
        "references_match_exact_current_excluded_prior_min_i_120": exact_windows,
        "reference_dates_preserve_order_and_uniqueness": ordered_unique,
        "reference_dates_exclude_current_and_future": current_or_future_absent,
        "q_ready_requires_complete_120_scheduled_window_and_100_valid_x": q_ready_only_with_valid_references,
    }


def _following_night(
    calendar: ExchangeCalendar, cash_day: date
) -> tuple[date | None, str | None]:
    matches = [item for item in calendar.trading_days() if item.night_calendar_start_date == cash_day]
    if len(matches) != 1:
        return None, "FOLLOWING_OSE_NIGHT_MAPPING_NOT_UNIQUE"
    night = matches[0]
    cash = calendar.get(cash_day)
    if cash is None or cash.next_trade_date != night.trade_date or night.previous_trade_date != cash_day:
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
    """Submit after the cash close; inspect night availability only after that order exists."""
    if entry_delay_minutes not in {0, 1} or exit_early_minutes not in {0, 30}:
        raise ValueError("unregistered R093 execution profile")
    event = dict(state)
    event.update(
        status="SKIPPED",
        entry_delay_minutes=entry_delay_minutes,
        exit_early_minutes=exit_early_minutes,
        entry_order_submitted=False,
        entry_eligible=False,
        completion_status="NO_ORDER",
    )
    if event.get("state") != "E":
        return event
    cash_day = date.fromisoformat(cast(str, event["trade_date"]))
    observation = cast(dict[str, object], event["observation"])
    cash_end = datetime.fromisoformat(cast(str, observation["cash_end_jst"]))
    event.update(
        submit_jst=cash_end.isoformat(),
        entry_order_submitted=True,
        entry_eligible=True,
        status="E_ORDER_SUBMITTED",
        completion_status="PENDING_FOLLOWING_NIGHT_ENTRY",
        reversal_direction="short" if int(cast(int, observation["r_sign"])) > 0 else "long",
        continuation_direction="long" if int(cast(int, observation["r_sign"])) > 0 else "short",
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
    night_record = classifier.exchange_calendar.get(night_trade_date)
    if night_record is None:
        raise ValueError("mapped night trade date is absent from exchange calendar")
    if (night_trade_date, Session.NIGHT) in isolated:
        event.update(
            night_trade_date=night_trade_date.isoformat(),
            status="ENTRY_CANCELLED",
            completion_status="NO_FILL",
            reason="R004_FOLLOWING_NIGHT_SESSION_QUARANTINED",
        )
        return event
    try:
        formal_open = classifier.session_open(night_trade_date, Session.NIGHT)
        normal_exit = _last_normal_night_open(classifier, night_trade_date) - timedelta(
            minutes=exit_early_minutes
        )
    except ValueError as exc:
        event.update(status="ENTRY_CANCELLED", completion_status="NO_FILL", reason=str(exc))
        return event
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
        night_schedule_version=night_record.schedule_version,
        formal_night_open_jst=formal_open.isoformat(),
        entry_open_jst=entry_open.isoformat(),
        exit_open_planned_jst=normal_exit.isoformat(),
        exit_signal_jst=(normal_exit - timedelta(minutes=1)).isoformat(),
        status="ENTRY_FILLED_EXIT_UNKNOWN",
        completion_status="UNRESOLVED_EXIT",
    )
    if not _valid(lookup.get(normal_exit - timedelta(minutes=1)), night_trade_date, Session.NIGHT, close=True):
        event["reason"] = "FINAL_NORMAL_EXIT_DECISION_BAR_MISSING_OR_INELIGIBLE"
        return event
    if not _valid(lookup.get(normal_exit), night_trade_date, Session.NIGHT):
        event["reason"] = "FINAL_NORMAL_EXIT_OPEN_MISSING_OR_INELIGIBLE"
        return event
    if entry_open >= normal_exit:
        event["reason"] = "ENTRY_NOT_BEFORE_FINAL_NORMAL_EXIT"
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
    state_kwargs = {key: value for key, value in kwargs.items() if key == "percentile"}
    execution_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key in {"entry_delay_minutes", "exit_early_minutes"}
    }
    return [
        scheduled_event(item, classifier, bars, isolated, **execution_kwargs)
        for item in cash_state_ledger(axis, bars, isolated, **state_kwargs)
    ]


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("R093 scheduled axis is shorter than the frozen MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[
        :, :length
    ]


def bootstrap(
    reversal_daily: list[int], continuation_daily: list[int], long_daily: list[int], short_daily: list[int]
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if len({len(reversal_daily), len(continuation_daily), len(long_daily), len(short_daily)}) != 1:
        raise ValueError("R093 bootstrap axes are misaligned")
    index = mbb_indices(len(reversal_daily))
    a, b, c, d = (np.asarray(item, dtype=float) for item in (reversal_daily, continuation_daily, long_daily, short_daily))

    def interval(values: NDArray[np.float64]) -> dict[str, object]:
        samples = values[index].mean(axis=1)
        ci = np.quantile(samples, (0.025, 0.975), method="linear")
        return {"estimate": fmean(values.tolist()), "ci95_percentile_linear": [float(ci[0]), float(ci[1])]}

    return {
        "method": "20 trade_date non-circular moving-block bootstrap; common indices; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "A_reversal_daily_net_jpy_per_trade_date": interval(a),
        "A_minus_B_daily_net_jpy_per_trade_date": interval(a - b),
        "A_minus_C_daily_net_jpy_per_trade_date": interval(a - c),
        "A_minus_D_daily_net_jpy_per_trade_date": interval(a - d),
    }, index
