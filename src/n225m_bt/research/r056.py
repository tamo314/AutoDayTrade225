"""Causal TSE opening-drive displacement and path-efficiency events for R056."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import cast

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar

DEVELOPMENT_START, DEVELOPMENT_END = date(2021, 1, 1), date(2025, 6, 30)
LOOKBACK, MIN_REFERENCES = 120, 100
WINDOWS = (20, 30, 40)
HOLDINGS = (15, 30, 45)
SCHEDULE_ID = "R056-TSE-OPENING-DRIVE-1"


def nearest_rank(values: list[float], percentile: int) -> float:
    """Return the preregistered nearest-rank quantile (ties remain upper-inclusive)."""
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def _valid(bar: Bar | None, target: date) -> bool:
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and bar.open > 0
        and bar.close > 0
    )


def _start(target: date) -> datetime:
    return datetime.combine(target, time(9, 0), JST)


def opening_observation(target: date, bars: list[Bar] | None, window: int) -> dict[str, object]:
    """Measure one scheduled opening window without substituting observed rows."""
    result: dict[str, object] = {"status": "invalid", "window_minutes": window}
    if window not in WINDOWS:
        raise ValueError("R056 window must be one of 20, 30, 40 scheduled minutes")
    by_time = {bar.ts_jst: bar for bar in bars or []}
    start = _start(target)
    rows = [by_time.get(start + timedelta(minutes=index)) for index in range(window)]
    if not all(_valid(row, target) for row in rows):
        result["reason"] = "OPENING_WINDOW_MISSING_INELIGIBLE_OR_NONPOSITIVE"
        return result
    concrete = [row for row in rows if row is not None]
    opening, final = concrete[0].open, concrete[-1].close
    displacement = final - opening
    path = abs(concrete[0].close - opening) + sum(
        abs(concrete[index].close - concrete[index - 1].close) for index in range(1, window)
    )
    if path <= 0:
        result["reason"] = "NONPOSITIVE_PATH_LENGTH"
        return result
    sign = 1 if displacement > 0 else -1 if displacement < 0 else 0
    result.update(
        status="valid",
        o_points=opening,
        c_points=final,
        x=abs(displacement) / opening,
        v_points=path,
        e=abs(displacement) / path,
        sign=sign,
        high_low_range_bps=(max(row.high for row in concrete) - min(row.low for row in concrete))
        / opening
        * 10_000,
        first5_return_bps=(concrete[4].close - opening) / opening * 10_000 if window >= 5 else None,
    )
    return result


def r056_event(
    target: date,
    target_bars: list[Bar] | None,
    history: Iterable[tuple[date, list[Bar] | None, bool]],
    cash_calendar: TSECashMarketCalendar,
    *,
    quarantined: bool = False,
) -> dict[str, object]:
    """Build one common-E candidate using exactly the prior 120 scheduled TSE days."""
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "schedule_id": SCHEDULE_ID,
        "lookback_scheduled_tse_days": LOOKBACK,
        "minimum_valid_references": MIN_REFERENCES,
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if not cash_calendar.is_open(target):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    prior = list(history)
    if len(prior) != LOOKBACK:
        event["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
        return event
    current = {window: opening_observation(target, target_bars, window) for window in WINDOWS}
    if any(value["status"] != "valid" for value in current.values()):
        event.update(opening_observations=current, reason="CURRENT_OPENING_OBSERVATION_INVALID")
        return event
    references: dict[int, list[dict[str, object]]] = {window: [] for window in WINDOWS}
    for prior_date, prior_bars, isolated in prior:
        if isolated:
            continue
        for window in WINDOWS:
            value = opening_observation(prior_date, prior_bars, window)
            if value["status"] == "valid":
                references[window].append(value)
    if any(len(values) < MIN_REFERENCES for values in references.values()):
        event.update(
            opening_observations=current,
            reference_valid_counts={
                str(window): len(values) for window, values in references.items()
            },
            reason="INSUFFICIENT_VALID_X_OR_E_REFERENCES",
        )
        return event
    by_time = {bar.ts_jst: bar for bar in target_bars or []}
    start = _start(target)
    # Common E needs the first 40 bars and every fixed entry/exit path for all windows.
    required = [
        by_time.get(start + timedelta(minutes=window + offset))
        for window in WINDOWS
        for offset in (0, *HOLDINGS)
    ]
    if not all(_valid(row, target) for row in required):
        event.update(opening_observations=current, reason="COMMON_ENTRY_OR_FIXED_EXIT_PATH_INVALID")
        return event
    thresholds: dict[str, dict[str, float]] = {}
    for window, values in references.items():
        xs = [float(cast(float, row["x"])) for row in values]
        es = [float(cast(float, row["e"])) for row in values]
        thresholds[str(window)] = {
            **{f"qx{q}": nearest_rank(xs, q) for q in (70, 75, 80)},
            **{f"qe{q}": nearest_rank(es, q) for q in (40, 50, 60)},
        }
    base = current[30]
    event.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        reference_trade_dates=[item[0].isoformat() for item in prior],
        reference_scheduled_day_count=len(prior),
        reference_valid_counts={str(window): len(values) for window, values in references.items()},
        opening_observations=current,
        thresholds=thresholds,
        x=base["x"],
        e=base["e"],
        v_points=base["v_points"],
        sign=base["sign"],
        direction_eligible=base["sign"] != 0,
        high_low_range_bps=base["high_low_range_bps"],
        first5_adjusted_return_bps=(
            int(cast(int, base["sign"])) * float(cast(float, base["first5_return_bps"]))
            if base["sign"]
            else None
        ),
        upward_indicator=int(cast(int, base["sign"]) > 0),
        planned_times={
            str(window): {
                "entry": (start + timedelta(minutes=window)).isoformat(),
                **{
                    f"exit{holding}": (start + timedelta(minutes=window + holding)).isoformat()
                    for holding in HOLDINGS
                },
            }
            for window in WINDOWS
        },
    )
    return event
