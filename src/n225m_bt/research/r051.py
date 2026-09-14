"""Causal TSE-morning-range / afternoon-confirmed-breakout events for R051."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import cast

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
LOOKBACK = 120
MIN_REFERENCES = 100
TSE_SCHEDULE_ID = "R051-TSE-MORNING-AFTERNOON-1"


@dataclass(frozen=True)
class TSESchedule:
    """Versioned cash schedule; never inferred from futures bars."""

    morning_start: time = time(9, 0)
    morning_end: time = time(11, 30)  # exclusive bar boundary
    afternoon_start: time = time(12, 30)


SCHEDULE = TSESchedule()


def _rank(values: list[float], percentile: int) -> float:
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def _valid(row: Bar | None, target: date) -> bool:
    return (
        row is not None
        and row.is_eligible
        and row.trade_date == target
        and row.session is Session.DAY
    )


def _times(target: date) -> dict[str, datetime]:
    m_s = datetime.combine(target, SCHEDULE.morning_start, JST)
    m_e = datetime.combine(target, SCHEDULE.morning_end, JST)
    a_s = datetime.combine(target, SCHEDULE.afternoon_start, JST)
    return {
        "mS": m_s,
        "mE": m_e,
        "aS": a_s,
        "signal": a_s + timedelta(minutes=14),
        "entry": a_s + timedelta(minutes=15),
        "exit30": a_s + timedelta(minutes=45),
        "exit60": a_s + timedelta(minutes=75),
        "exit90": a_s + timedelta(minutes=105),
    }


def morning_range(target: date, rows: list[Bar] | None) -> dict[str, object]:
    """Compute high-low / morning open using exactly the scheduled morning bars."""
    times = _times(target)
    result: dict[str, object] = {"status": "invalid", "schedule_id": TSE_SCHEDULE_ID}
    if rows is None:
        result["reason"] = "DAY_SESSION_MISSING"
        return result
    by_time = {row.ts_jst: row for row in rows}
    window = [by_time.get(times["mS"] + timedelta(minutes=i)) for i in range(150)]
    if not all(_valid(row, target) for row in window):
        result["reason"] = "MORNING_WINDOW_MISSING_OR_INELIGIBLE"
        return result
    concrete = [row for row in window if row is not None]
    opening, high, low = (
        concrete[0].open,
        max(row.high for row in concrete),
        min(row.low for row in concrete),
    )
    if opening <= 0:
        result.update(reason="NONPOSITIVE_MORNING_OPEN", morning_open_points=opening)
        return result
    result.update(
        status="valid",
        morning_open_points=opening,
        morning_high_points=high,
        morning_low_points=low,
        range_bps=(high - low) / opening * 10_000,
    )
    return result


def r051_event(
    target: date,
    target_rows: list[Bar] | None,
    history: Iterable[tuple[date, list[Bar] | None, bool]],
    cash_calendar: TSECashMarketCalendar,
    *,
    quarantined: bool = False,
) -> dict[str, object]:
    """Build one causally-selected event; only its 120 immediately prior days are read."""
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "schedule_id": TSE_SCHEDULE_ID,
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        result["reason"] = "OUTSIDE_DEVELOPMENT"
        return result
    if not cash_calendar.is_open(target):
        result["reason"] = "TSE_CASH_MARKET_CLOSED"
        return result
    if quarantined:
        result["reason"] = "DAY_SESSION_QUARANTINED"
        return result
    previous = list(history)
    if len(previous) != LOOKBACK:
        result["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
        return result
    current = morning_range(target, target_rows)
    if current["status"] != "valid":
        result.update(current, reason="TARGET_MORNING_INVALID")
        return result
    ref_dates = [item[0].isoformat() for item in previous]
    values: list[float] = []
    for prior, rows, isolated in previous:
        if isolated:
            continue
        observation = morning_range(prior, rows)
        if observation["status"] == "valid":
            values.append(float(cast(float, observation["range_bps"])))
    result.update(
        current,
        reference_trade_dates=ref_dates,
        reference_scheduled_day_count=len(previous),
        reference_valid_count=len(values),
    )
    result["status"] = "skipped"
    if len(values) < MIN_REFERENCES:
        result["reason"] = "INSUFFICIENT_VALID_RANGE_REFERENCES"
        return result
    thresholds = {f"q{q}_bps": _rank(values, q) for q in (30, 40, 50, 60)}
    times = _times(target)
    by_time = {row.ts_jst: row for row in target_rows or []}
    required = [by_time.get(times["aS"] + timedelta(minutes=i)) for i in range(15)]
    route = [by_time.get(times[name]) for name in ("entry", "exit30", "exit60", "exit90")]
    if not all(_valid(row, target) for row in (*required, *route)):
        result.update(thresholds, reason="AFTERNOON_OR_HOLDING_PATH_MISSING_OR_INELIGIBLE")
        return result
    concrete = [row for row in required if row is not None]
    signal = concrete[-1]
    high = int(cast(int, current["morning_high_points"]))
    low = int(cast(int, current["morning_low_points"]))
    direction = "long" if signal.close > high else "short" if signal.close < low else None
    a_open = concrete[0].open
    morning_close = by_time[times["mE"] - timedelta(minutes=1)].close
    result.update(
        thresholds,
        status="E",
        reason="COMMON_ELIGIBLE_NO_BREAKOUT" if direction is None else "STRICT_CLOSE_BREAKOUT",
        signal_close_points=signal.close,
        breakout_direction=direction,
        overshoot_bps=(signal.close - high if direction == "long" else low - signal.close)
        / int(cast(int, current["morning_open_points"]))
        * 10_000
        if direction
        else None,
        direction_adjusted_gap_bps=(1 if direction == "long" else -1)
        * (a_open - morning_close)
        / morning_close
        * 10_000
        if direction and morning_close > 0
        else None,
        signal_bar_start_jst=times["signal"].isoformat(),
        planned_entry_jst=times["entry"].isoformat(),
        planned_exit30_jst=times["exit30"].isoformat(),
        planned_exit60_jst=times["exit60"].isoformat(),
        planned_exit90_jst=times["exit90"].isoformat(),
    )
    return result
