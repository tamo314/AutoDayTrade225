"""Causal closing-pressure reversal event construction for R058-Q001."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from math import ceil
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.calendar.session_rules import regime_for_trade_date
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
LOOKBACK = 120
MIN_REFERENCES = 100
PINV_RCOND = 1e-12
RESIDUAL_SS_TOLERANCE = 1e-12


class R058QNotIdentifiableError(ValueError):
    """Raised if Q has no variation after residualizing the fixed nuisance set."""


def fwl_delta(
    q: NDArray[np.float64],
    y: NDArray[np.float64],
    nuisance: NDArray[np.float64],
    *,
    pinv_rcond: float = PINV_RCOND,
    residual_ss_tolerance: float = RESIDUAL_SS_TOLERANCE,
) -> tuple[float, float]:
    """Return the Q coefficient using the fixed nuisance-only FWL projection."""
    if q.ndim != 1 or y.ndim != 1 or nuisance.ndim != 2 or q.shape != y.shape:
        raise ValueError("R058 invalid FWL dimensions")
    if nuisance.shape[0] != q.shape[0]:
        raise ValueError("R058 nuisance row count differs from Q")
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=pinv_rcond)
    q_residual = q - projection @ q
    y_residual = y - projection @ y
    residual_ss = float(q_residual @ q_residual)
    if residual_ss <= residual_ss_tolerance:
        raise R058QNotIdentifiableError(
            f"R058 Q not identifiable: residual_ss={residual_ss:.17g} <= {residual_ss_tolerance:.17g}"
        )
    return float((q_residual @ y_residual) / residual_ss), residual_ss


def _regular_day_end(classifier: CalendarClassifier, trade_day: date) -> datetime:
    regime = regime_for_trade_date(classifier.sessions, trade_day)
    endpoint = regime.day.regular_end or regime.day.session_close
    if endpoint is None:
        raise ValueError("R058 versioned TSE normal endpoint absent")
    return datetime.combine(trade_day, endpoint, JST)


def _window(
    classifier: CalendarClassifier,
    trade_day: date,
    bars: list[Bar] | None,
    length_minutes: int,
    *,
    end_offset_minutes: int = 0,
) -> tuple[dict[str, object] | None, list[Bar] | None]:
    """Return the final/preceding planned minute block without observed-edge fallback."""
    end = _regular_day_end(classifier, trade_day) - timedelta(minutes=end_offset_minutes)
    start = end - timedelta(minutes=length_minutes)
    by_ts = {bar.ts_jst: bar for bar in bars or []}
    rows = [by_ts.get(start + timedelta(minutes=index)) for index in range(length_minutes)]
    if any(row is None for row in rows):
        return None, None
    concrete = [row for row in rows if row is not None]
    if any(
        not row.is_eligible
        or row.trade_date != trade_day
        or row.session is not Session.DAY
        or min(row.open, row.high, row.low, row.close) <= 0
        for row in concrete
    ):
        return None, None
    r = (concrete[-1].close - concrete[0].open) / concrete[0].open
    return {
        "start_jst": start.isoformat(),
        "last_bar_start_jst": concrete[-1].ts_jst.isoformat(),
        "open": concrete[0].open,
        "close": concrete[-1].close,
        "r": r,
        "x": abs(r),
        "sign": 1 if r > 0 else -1 if r < 0 else 0,
        "range_bps": (max(row.high for row in concrete) - min(row.low for row in concrete))
        / concrete[0].open
        * 10_000,
        "efficiency": abs(concrete[-1].close - concrete[0].open)
        / max(1, sum(abs(row.close - row.open) for row in concrete)),
    }, concrete


def _prior_scheduled_days(calendar: ExchangeCalendar, reference: date) -> list[date] | None:
    values: list[date] = []
    current = reference
    for _ in range(LOOKBACK):
        record = calendar.get(current)
        if record is None or record.previous_trade_date is None:
            return None
        current = record.previous_trade_date
        values.append(current)
    return values


def _rank(values: list[float], percentile: int) -> float:
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def _thresholds(
    classifier: CalendarClassifier,
    calendar: ExchangeCalendar,
    reference: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    minutes: int,
    *,
    end_offset_minutes: int = 0,
) -> tuple[dict[str, object], list[date] | None]:
    history = _prior_scheduled_days(calendar, reference)
    audit: dict[str, object] = {
        "reference_scheduled_tse_days": 0 if history is None else len(history),
        "reference_valid_x_count": 0,
    }
    if history is None:
        return audit, None
    audit["reference_trade_dates"] = [day.isoformat() for day in history]
    if any(not DEVELOPMENT_START <= day <= DEVELOPMENT_END for day in history):
        audit["reason"] = "HISTORY_OUTSIDE_DEVELOPMENT"
        return audit, history
    values: list[float] = []
    for prior in history:
        if (prior, Session.DAY) in isolated:
            continue
        observation, _ = _window(
            classifier,
            prior,
            bars.get((prior, Session.DAY)),
            minutes,
            end_offset_minutes=end_offset_minutes,
        )
        if observation is not None and int(cast(int, observation["sign"])) != 0:
            values.append(float(cast(float, observation["x"])))
    audit["reference_valid_x_count"] = len(values)
    if len(values) < MIN_REFERENCES:
        audit["reason"] = "INSUFFICIENT_VALID_X_REFERENCES"
        return audit, history
    audit.update({f"q{q}": _rank(values, q) for q in (75, 85, 90, 95)})
    return audit, history


def r058_event(
    classifier: CalendarClassifier,
    calendar: ExchangeCalendar,
    target_night: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    closing_minutes: int = 30,
) -> dict[str, object]:
    """Build one common-E night event from its uniquely linked prior TSE day."""
    result: dict[str, object] = {
        "trade_date": target_night.isoformat(),
        "status": "skipped",
        "closing_minutes": closing_minutes,
        "lookback_scheduled_tse_days": LOOKBACK,
        "minimum_valid_x": MIN_REFERENCES,
        "quantile": "nearest rank ceil(q*n); target excluded; equality belongs to upper tail",
    }
    if not DEVELOPMENT_START <= target_night <= DEVELOPMENT_END:
        result["reason"] = "OUTSIDE_DEVELOPMENT"
        return result
    schedule = calendar.get(target_night)
    if schedule is None or schedule.night_calendar_start_date is None:
        result["reason"] = "TARGET_OSE_NIGHT_NOT_SCHEDULED"
        return result
    reference = schedule.previous_trade_date
    if reference is None or not DEVELOPMENT_START <= reference <= DEVELOPMENT_END:
        result["reason"] = "TSE_TO_OSE_MAPPING_AMBIGUOUS_OR_OUTSIDE_DEVELOPMENT"
        return result
    if (target_night, Session.NIGHT) in isolated or (reference, Session.DAY) in isolated:
        result["reason"] = "R004_QUARANTINED"
        return result
    # This schedule edge is the sole permitted correspondence, not an observed timestamp inference.
    result["tse_reference_trade_date"] = reference.isoformat()
    close, _ = _window(classifier, reference, bars.get((reference, Session.DAY)), closing_minutes)
    prior, _ = _window(
        classifier,
        reference,
        bars.get((reference, Session.DAY)),
        closing_minutes,
        end_offset_minutes=closing_minutes,
    )
    close_thresholds, history = _thresholds(
        classifier, calendar, reference, bars, isolated, closing_minutes
    )
    # P is the adjacent preceding block, with an independently causal history threshold.
    prior_thresholds, _ = _thresholds(
        classifier,
        calendar,
        reference,
        bars,
        isolated,
        closing_minutes,
        end_offset_minutes=closing_minutes,
    )
    result["closing"] = close
    result["prior"] = prior
    result["closing_thresholds"] = close_thresholds
    result["prior_thresholds"] = prior_thresholds
    if history is None or "reason" in close_thresholds or "reason" in prior_thresholds:
        result["reason"] = "ROLLING_HISTORY_OR_REFERENCES_INELIGIBLE"
        return result
    night_open = classifier.session_open(target_night, Session.NIGHT)
    by_ts = {bar.ts_jst: bar for bar in bars.get((target_night, Session.NIGHT), [])}
    path = [by_ts.get(night_open + timedelta(minutes=index)) for index in range(46)]
    if any(
        bar is None
        or not bar.is_eligible
        or bar.trade_date != target_night
        or bar.session is not Session.NIGHT
        or min(bar.open, bar.high, bar.low, bar.close) <= 0
        for bar in path
    ):
        result["reason"] = "OSE_ENTRY_OR_15_30_45_EXIT_PATH_INELIGIBLE"
        return result
    if close is None or prior is None:
        result["reason"] = "TSE_CONTIGUOUS_60_SCHEDULED_MINUTES_INELIGIBLE"
        return result
    result.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        direction_eligible=int(cast(int, close["sign"])) != 0,
        planned_entry_jst=night_open.isoformat(),
        planned_exit15_jst=(night_open + timedelta(minutes=15)).isoformat(),
        planned_exit30_jst=(night_open + timedelta(minutes=30)).isoformat(),
        planned_exit45_jst=(night_open + timedelta(minutes=45)).isoformat(),
    )
    return result


def all_events(
    classifier: CalendarClassifier,
    calendar: ExchangeCalendar,
    targets: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    closing_minutes: int,
) -> list[dict[str, object]]:
    return [
        r058_event(classifier, calendar, target, bars, isolated, closing_minutes=closing_minutes)
        for target in targets
    ]
