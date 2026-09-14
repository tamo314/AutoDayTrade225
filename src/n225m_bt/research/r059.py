"""Causal scheduled TSE-close to OSE-night-open gap events for R059-Q001."""

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


class R059QNotIdentifiableError(ValueError):
    """Raised when the fixed FWL Q regressor is not identifiable."""


def fwl_delta(
    q: NDArray[np.float64], y: NDArray[np.float64], nuisance: NDArray[np.float64], *,
    pinv_rcond: float = PINV_RCOND, residual_ss_tolerance: float = RESIDUAL_SS_TOLERANCE,
) -> tuple[float, float]:
    """Return the fixed Q coefficient using nuisance-only Moore--Penrose FWL."""
    if q.ndim != 1 or y.ndim != 1 or nuisance.ndim != 2 or q.shape != y.shape:
        raise ValueError("R059 invalid FWL dimensions")
    if nuisance.shape[0] != q.shape[0]:
        raise ValueError("R059 nuisance row count differs from Q")
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=pinv_rcond)
    qr, yr = q - projection @ q, y - projection @ y
    ss = float(qr @ qr)
    if ss <= residual_ss_tolerance:
        raise R059QNotIdentifiableError(
            f"R059 Q not identifiable: residual_ss={ss:.17g} <= {residual_ss_tolerance:.17g}"
        )
    return float((qr @ yr) / ss), ss


def _rank(values: list[float], percentile: int) -> float:
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def _day_close(classifier: CalendarClassifier, target: date, bars: list[Bar] | None) -> Bar | None:
    regime = regime_for_trade_date(classifier.sessions, target)
    endpoint = regime.day.regular_end or regime.day.session_close
    if endpoint is None:
        return None
    wanted = datetime.combine(target, endpoint, JST) - timedelta(minutes=1)
    row = next((item for item in bars or [] if item.ts_jst == wanted), None)
    if row is None or not row.is_eligible or row.trade_date != target or row.session is not Session.DAY:
        return None
    return row if min(row.open, row.high, row.low, row.close) > 0 else None


def _gap_observation(
    classifier: CalendarClassifier, calendar: ExchangeCalendar, target_night: date,
    bars: dict[tuple[date, Session], list[Bar]], isolated: set[tuple[date, Session]],
) -> dict[str, object] | None:
    record = calendar.get(target_night)
    reference = record.previous_trade_date if record is not None else None
    if (
        record is None or record.night_calendar_start_date is None or reference is None
        or (reference, Session.DAY) in isolated or (target_night, Session.NIGHT) in isolated
    ):
        return None
    close = _day_close(classifier, reference, bars.get((reference, Session.DAY)))
    night_open_time = classifier.session_open(target_night, Session.NIGHT)
    by_time = {row.ts_jst: row for row in bars.get((target_night, Session.NIGHT), [])}
    open_row = by_time.get(night_open_time)
    if (
        close is None or open_row is None or not open_row.is_eligible
        or open_row.trade_date != target_night or open_row.session is not Session.NIGHT
        or min(open_row.open, open_row.high, open_row.low, open_row.close) <= 0
    ):
        return None
    gap = (open_row.open - close.close) / close.close
    return {
        "tse_reference_trade_date": reference.isoformat(), "tse_close_jst": close.ts_jst.isoformat(),
        "ose_open_jst": night_open_time.isoformat(), "p": close.close, "oN": open_row.open,
        "g": gap, "x": abs(gap), "gap_sign": 1 if gap > 0 else -1 if gap < 0 else 0,
    }


def _prior(calendar: ExchangeCalendar, target: date) -> list[date] | None:
    result: list[date] = []
    current = target
    for _ in range(LOOKBACK):
        record = calendar.get(current)
        if record is None or record.previous_trade_date is None:
            return None
        current = record.previous_trade_date
        result.append(current)
    return result


def r059_event(
    classifier: CalendarClassifier, calendar: ExchangeCalendar, target: date,
    bars: dict[tuple[date, Session], list[Bar]], isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Build exactly one mapped TSE-close/OSE-open common-E observation."""
    result: dict[str, object] = {
        "trade_date": target.isoformat(), "status": "skipped", "lookback_scheduled_pairs": LOOKBACK,
        "minimum_valid_x": MIN_REFERENCES,
        "quantile": "nearest rank ceil(q*n); target excluded; equality belongs to upper tail",
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        result["reason"] = "OUTSIDE_DEVELOPMENT"
        return result
    target_observation = _gap_observation(classifier, calendar, target, bars, isolated)
    if target_observation is None:
        result["reason"] = "AMBIGUOUS_MAPPING_OR_TSE_CLOSE_OSE_OPEN_INELIGIBLE"
        return result
    history = _prior(calendar, target)
    if history is None or any(not DEVELOPMENT_START <= item <= DEVELOPMENT_END for item in history):
        result["reason"] = "HISTORY_OUTSIDE_DEVELOPMENT_OR_UNLINKED"
        return result
    values = [
        float(cast(float, item["x"])) for prior in history
        if (item := _gap_observation(classifier, calendar, prior, bars, isolated)) is not None
    ]
    result.update(target_observation, reference_trade_dates=[item.isoformat() for item in history],
                  reference_scheduled_pair_count=len(history), reference_valid_x_count=len(values))
    if len(values) < MIN_REFERENCES:
        result["reason"] = "INSUFFICIENT_VALID_X_REFERENCES"
        return result
    open_time = datetime.fromisoformat(cast(str, target_observation["ose_open_jst"]))
    by_time = {row.ts_jst: row for row in bars.get((target, Session.NIGHT), [])}
    required = [by_time.get(open_time + timedelta(minutes=index)) for index in range(46)]
    if any(
        row is None or not row.is_eligible or row.trade_date != target or row.session is not Session.NIGHT
        or min(row.open, row.high, row.low, row.close) <= 0 for row in required
    ):
        result["reason"] = "OSE_FIRST_TWO_OR_15_30_45_EXIT_PATH_INELIGIBLE"
        return result
    reference = date.fromisoformat(cast(str, target_observation["tse_reference_trade_date"]))
    close = _day_close(classifier, reference, bars.get((reference, Session.DAY)))
    assert close is not None
    day_rows = bars.get((reference, Session.DAY), [])
    end = close.ts_jst + timedelta(minutes=1)
    last30 = [row for row in day_rows if end - timedelta(minutes=30) <= row.ts_jst < end]
    if len(last30) != 30:
        result["reason"] = "TSE_LAST30_INELIGIBLE"
        return result
    s = int(cast(int, target_observation["gap_sign"]))
    result.update(
        status="E", reason="COMMON_ELIGIBLE", q75=_rank(values, 75), q85=_rank(values, 85),
        q90=_rank(values, 90), q95=_rank(values, 95), direction_eligible=s != 0,
        planned_entry_jst=(open_time + timedelta(minutes=1)).isoformat(),
        planned_exit15_jst=(open_time + timedelta(minutes=16)).isoformat(),
        planned_exit30_jst=(open_time + timedelta(minutes=31)).isoformat(),
        planned_exit45_jst=(open_time + timedelta(minutes=46)).isoformat(),
        tse_last30_adjusted_return_bps=(-s * (close.close - last30[0].open) / last30[0].open * 10_000) if s else None,
        tse_last30_range_bps=(max(row.high for row in last30) - min(row.low for row in last30)) / last30[0].open * 10_000,
        tse_session_adjusted_return_bps=None,
    )
    return result


def all_events(
    classifier: CalendarClassifier, calendar: ExchangeCalendar, targets: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]], isolated: set[tuple[date, Session]],
) -> list[dict[str, object]]:
    return [r059_event(classifier, calendar, target, bars, isolated) for target in targets]
