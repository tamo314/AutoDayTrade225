"""Causal scheduled TSE morning-close / afternoon-open gap events for R053."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
LOOKBACK = 120
MIN_REFERENCES = 100
TSE_SCHEDULE_ID = "R053-TSE-MORNING-CLOSE-AFTERNOON-OPEN-1"


@dataclass(frozen=True)
class TSESchedule:
    """Frozen cash-session boundaries used to form the discontinuity."""

    morning_start: time = time(9, 0)
    morning_end: time = time(11, 30)  # exclusive
    afternoon_start: time = time(12, 30)


SCHEDULE = TSESchedule()


class R053QNotIdentifiableError(ValueError):
    """Raised when Q has no variation left after nuisance residualization."""


def fwl_delta(
    q: NDArray[np.float64],
    y: NDArray[np.float64],
    nuisance: NDArray[np.float64],
    *,
    pinv_rcond: float,
    residual_ss_tolerance: float,
) -> tuple[float, float]:
    """Estimate the fixed Q coefficient using FWL and a nuisance-only pseudoinverse."""
    if q.ndim != 1 or y.ndim != 1 or nuisance.ndim != 2:
        raise ValueError("R053 FWL inputs must be one-dimensional q/y and two-dimensional nuisance")
    if q.shape != y.shape or nuisance.shape[0] != q.shape[0]:
        raise ValueError("R053 FWL inputs have incompatible shapes")
    if pinv_rcond <= 0 or residual_ss_tolerance < 0:
        raise ValueError("R053 FWL tolerances must be nonnegative with positive pinv rcond")
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=pinv_rcond)
    q_residual = q - projection @ q
    y_residual = y - projection @ y
    q_residual_ss = float(q_residual @ q_residual)
    if q_residual_ss <= residual_ss_tolerance:
        raise R053QNotIdentifiableError(
            "R053 Q is not identifiable after nuisance residualization: "
            f"residual_ss={q_residual_ss:.17g} <= tolerance={residual_ss_tolerance:.17g}"
        )
    return float((q_residual @ y_residual) / q_residual_ss), q_residual_ss


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
    morning_start = datetime.combine(target, SCHEDULE.morning_start, JST)
    morning_end = datetime.combine(target, SCHEDULE.morning_end, JST)
    afternoon_start = datetime.combine(target, SCHEDULE.afternoon_start, JST)
    entry = afternoon_start + timedelta(minutes=1)
    return {
        "mS": morning_start,
        "cM": morning_end - timedelta(minutes=1),
        "oA": afternoon_start,
        "entry": entry,
        "exit15": entry + timedelta(minutes=15),
        "exit30": entry + timedelta(minutes=30),
        "exit45": entry + timedelta(minutes=45),
    }


def _observation(target: date, rows: list[Bar] | None) -> dict[str, object]:
    """Use only the scheduled endpoints; no observed-row substitution."""
    result: dict[str, object] = {"status": "invalid"}
    by_time = {row.ts_jst: row for row in rows or []}
    times = _times(target)
    required = [by_time.get(times[key]) for key in ("mS", "cM", "oA")]
    if not all(_valid(row, target) for row in required):
        result["reason"] = "MORNING_CLOSE_OR_AFTERNOON_OPEN_MISSING_OR_INELIGIBLE"
        return result
    morning_open = cast(Bar, required[0])
    morning_close = cast(Bar, required[1])
    afternoon_open = cast(Bar, required[2])
    if morning_close.close <= 0:
        result["reason"] = "NONPOSITIVE_MORNING_CLOSE"
        return result
    gap = (afternoon_open.open - morning_close.close) / morning_close.close
    result.update(
        status="valid",
        morning_open_points=morning_open.open,
        cM_points=morning_close.close,
        oA_points=afternoon_open.open,
        g=gap,
        x=abs(gap),
        morning_reverse_adjusted_return_bps=None,
    )
    return result


def r053_event(
    target: date,
    target_rows: list[Bar] | None,
    history: Iterable[tuple[date, list[Bar] | None, bool]],
    cash_calendar: TSECashMarketCalendar,
    *,
    quarantined: bool = False,
) -> dict[str, object]:
    """Build one R053 common-E event using exactly its prior 120 TSE days."""
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "schedule_id": TSE_SCHEDULE_ID,
        "lookback_scheduled_tse_days": LOOKBACK,
        "minimum_valid_x": MIN_REFERENCES,
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
    current = _observation(target, target_rows)
    if current["status"] != "valid":
        result.update(current, reason="TARGET_GAP_ENDPOINT_INVALID")
        return result
    values: list[float] = []
    for prior, rows, isolated in previous:
        if isolated:
            continue
        observation = _observation(prior, rows)
        if observation["status"] == "valid":
            values.append(float(cast(float, observation["x"])))
    result.update(
        current,
        reference_trade_dates=[item[0].isoformat() for item in previous],
        reference_scheduled_day_count=len(previous),
        reference_valid_x_count=len(values),
    )
    if len(values) < MIN_REFERENCES:
        result["reason"] = "INSUFFICIENT_VALID_X_REFERENCES"
        return result
    times, by_time = _times(target), {row.ts_jst: row for row in target_rows or []}
    path = [by_time.get(times[key]) for key in ("entry", "exit15", "exit30", "exit45")]
    if not all(_valid(row, target) for row in path):
        result["reason"] = "ENTRY_OR_FIXED_EXIT_PATH_MISSING_OR_INELIGIBLE"
        return result
    g = float(cast(float, result["g"]))
    sign = 1 if g > 0 else -1 if g < 0 else 0
    c_m = float(cast(float, result["cM_points"]))
    m_open = float(cast(float, result["morning_open_points"]))
    result.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        q75=_rank(values, 75),
        q85=_rank(values, 85),
        q90=_rank(values, 90),
        q95=_rank(values, 95),
        gap_sign=sign,
        direction_eligible=sign != 0,
        morning_reverse_adjusted_return_bps=(
            -sign * (c_m - m_open) / m_open * 10_000 if sign else None
        ),
        planned_entry_jst=times["entry"].isoformat(),
        planned_exit15_jst=times["exit15"].isoformat(),
        planned_exit30_jst=times["exit30"].isoformat(),
        planned_exit45_jst=times["exit45"].isoformat(),
    )
    return result
