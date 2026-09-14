"""Causal TSE previous-close / opening-gap acceptance events for R057-Q001."""

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
SCHEDULE_ID = "R057-TSE-PREVIOUS-CLOSE-OPENING-GAP-1"


def nearest_rank(values: list[float], percentile: int) -> float:
    """Return the pre-registered upper-inclusive nearest-rank quantile."""
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def tse_normal_close(day: date) -> time:
    """Frozen TSE normal-close schedule; the 2024 extension is explicit."""
    return time(15, 0) if day <= date(2024, 11, 1) else time(15, 30)


def _valid(row: Bar | None, target: date) -> bool:
    return bool(
        row is not None
        and row.is_eligible
        and row.trade_date == target
        and row.session is Session.DAY
        and row.open > 0
        and row.close > 0
    )


def _times(day: date, window: int = 15, holding: int = 30) -> dict[str, datetime]:
    if window not in (10, 15, 20) or holding not in (15, 30, 45):
        raise ValueError("R057 window/holding is not preregistered")
    start = datetime.combine(day, time(9), JST)
    entry = start + timedelta(minutes=window)
    return {
        "open": start,
        "confirm": start + timedelta(minutes=window - 1),
        "entry": entry,
        "exit": entry + timedelta(minutes=holding),
    }


def _raw(
    target: date,
    previous: date,
    target_rows: list[Bar] | None,
    previous_rows: list[Bar] | None,
    window: int,
) -> dict[str, object]:
    """Use exact scheduled endpoints and no observed-row replacement."""
    target_by_time = {row.ts_jst: row for row in target_rows or []}
    prior_by_time = {row.ts_jst: row for row in previous_rows or []}
    start = datetime.combine(target, time(9), JST)
    prior_last = datetime.combine(previous, tse_normal_close(previous), JST) - timedelta(minutes=1)
    rows = [target_by_time.get(start + timedelta(minutes=index)) for index in range(window)]
    p = prior_by_time.get(prior_last)
    if not _valid(p, previous):
        return {"status": "invalid", "reason": "PREVIOUS_TSE_FINAL_BAR_INVALID"}
    if not all(_valid(row, target) for row in rows):
        return {"status": "invalid", "reason": "OPENING_CONFIRMATION_WINDOW_INVALID"}
    concrete = [cast(Bar, row) for row in rows]
    prior = cast(Bar, p)
    o, c = concrete[0].open, concrete[-1].close
    if prior.close <= 0 or o <= 0:
        return {"status": "invalid", "reason": "NONPOSITIVE_REFERENCE_PRICE"}
    g, r15 = (o - prior.close) / prior.close, (c - o) / o
    sign = 1 if g > 0 else -1 if g < 0 else 0
    return {
        "status": "valid",
        "p_points": prior.close,
        "o_points": o,
        "c_confirm_points": c,
        "g": g,
        "x": abs(g),
        "gap_sign": sign,
        "r_confirm": r15,
        "confirmation_sign": 1 if r15 > 0 else -1 if r15 < 0 else 0,
        "confirmation_range_bps": (max(r.high for r in concrete) - min(r.low for r in concrete))
        / o
        * 10_000,
    }


def r057_event(
    target: date,
    target_rows: list[Bar] | None,
    previous_day: date | None,
    previous_rows: list[Bar] | None,
    history: Iterable[tuple[date, date | None, list[Bar] | None, list[Bar] | None, bool]],
    cash_calendar: TSECashMarketCalendar,
    *,
    quarantined: bool = False,
) -> dict[str, object]:
    """Build a common-E candidate using only the preceding 120 scheduled TSE days."""
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "schedule_id": SCHEDULE_ID,
        "lookback_scheduled_tse_days": LOOKBACK,
        "minimum_valid_x": MIN_REFERENCES,
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
    reference = list(history)
    if len(reference) != LOOKBACK:
        event["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
        return event
    if previous_day is None:
        event["reason"] = "PREVIOUS_TSE_SESSION_NOT_UNIQUE"
        return event
    observations = {
        window: _raw(target, previous_day, target_rows, previous_rows, window)
        for window in (10, 15, 20)
    }
    base = observations[15]
    if base["status"] != "valid":
        event.update(observations=observations, reason="TARGET_GAP_OR_CONFIRMATION_INVALID")
        return event
    values: list[float] = []
    for day, prior, rows, prior_rows, isolated in reference:
        if isolated or prior is None:
            continue
        value = _raw(day, prior, rows, prior_rows, 15)
        if value["status"] == "valid":
            values.append(float(cast(float, value["x"])))
    event.update(
        observations=observations,
        reference_trade_dates=[item[0].isoformat() for item in reference],
        reference_valid_x_count=len(values),
        previous_tse_trade_date=previous_day.isoformat(),
        **base,
    )
    if len(values) < MIN_REFERENCES:
        event["reason"] = "INSUFFICIENT_VALID_X_REFERENCES"
        return event
    by_time = {row.ts_jst: row for row in target_rows or []}
    paths = [_times(target, window, holding) for window in (10, 15, 20) for holding in (15, 30, 45)]
    required = [by_time.get(value[key]) for value in paths for key in ("entry", "exit")]
    # E explicitly also requires the first 20 scheduled TSE bars.
    required.extend(
        by_time.get(datetime.combine(target, time(9), JST) + timedelta(minutes=i))
        for i in range(20)
    )
    if not all(_valid(row, target) for row in required):
        event["reason"] = "COMMON_ENTRY_EXIT_OR_FIRST20_PATH_INVALID"
        return event
    event.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        q70=nearest_rank(values, 70),
        q75=nearest_rank(values, 75),
        q80=nearest_rank(values, 80),
        direction_eligible=int(cast(int, base["gap_sign"])) != 0
        and int(cast(int, base["confirmation_sign"])) != 0,
        planned_times={
            str(w): {
                str(h): {k: v.isoformat() for k, v in _times(target, w, h).items()}
                for h in (15, 30, 45)
            }
            for w in (10, 15, 20)
        },
    )
    return event
