"""Causal TSE-lunch extreme-move selection for R038-Q001."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from math import ceil

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
LOOKBACK_TSE_DAYS = 60
MIN_VALID_RETURNS = 50


def window_return(bars: Iterable[Bar], trade_day: date, start_time: time) -> int | None:
    """Return close(t+59)-open(t) only for the prescribed eligible 60 bars."""
    start = datetime.combine(trade_day, start_time, JST)
    by_time = {bar.ts_jst: bar for bar in bars}
    rows = [by_time.get(start + timedelta(minutes=index)) for index in range(60)]
    if any(row is None for row in rows):
        return None
    concrete = [row for row in rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_day or row.session is not Session.DAY
        for row in concrete
    ):
        return None
    return concrete[-1].close - concrete[0].open


def _window_audit(
    target: date,
    tse_days: tuple[date, ...],
    bars_by_day: dict[date, list[Bar]],
    isolated_days: set[date],
    *,
    start_time: time,
    label: str,
) -> dict[str, object]:
    """Compute one independent strict-Q75 event with a fixed, non-backfilled history."""
    current = window_return(bars_by_day.get(target, []), target, start_time)
    audit: dict[str, object] = {
        f"{label}_window_start_jst": datetime.combine(target, start_time, JST).isoformat(),
        f"{label}_window_end_jst": (
            datetime.combine(target, start_time, JST) + timedelta(minutes=59)
        ).isoformat(),
        f"r{label}_points": current,
        f"{label}_reference_scheduled_day_count": 0,
        f"{label}_reference_valid_abs_return_count": 0,
    }
    index = tse_days.index(target)
    references = tse_days[max(0, index - LOOKBACK_TSE_DAYS) : index]
    audit[f"{label}_reference_trade_dates"] = [item.isoformat() for item in references]
    audit[f"{label}_reference_scheduled_day_count"] = len(references)
    values: list[int] = []
    for prior in references:
        if prior in isolated_days:
            continue
        value = window_return(bars_by_day.get(prior, []), prior, start_time)
        if value is not None and value != 0:
            values.append(abs(value))
    audit[f"{label}_reference_valid_abs_return_count"] = len(values)
    if len(references) != LOOKBACK_TSE_DAYS:
        audit[f"{label}_status"] = "history_short_or_outside_development"
        return audit
    if len(values) < MIN_VALID_RETURNS:
        audit[f"{label}_status"] = "insufficient_valid_reference_returns"
        return audit
    threshold = sorted(values)[ceil(0.75 * len(values)) - 1]
    audit[f"Q{label}_points"] = threshold
    if current is None:
        audit[f"{label}_status"] = "target_window_missing_or_ineligible"
    elif current == 0:
        audit[f"{label}_status"] = "target_return_zero"
    elif abs(current) > threshold:
        audit[f"{label}_status"] = "extreme"
    else:
        audit[f"{label}_status"] = "nonextreme"
    return audit


def tse_lunch_extreme_event(
    trade_day: date,
    tse_days: tuple[date, ...],
    bars_by_day: dict[date, list[Bar]],
    isolated_days: set[date],
) -> dict[str, object]:
    """Return independent lunch (L) and preclose placebo (P) selection ledgers."""
    event: dict[str, object] = {
        "trade_date": trade_day.isoformat(),
        "session": "day",
        "status": "skipped",
        "lookback_scheduled_tse_days": LOOKBACK_TSE_DAYS,
        "minimum_valid_abs_returns": MIN_VALID_RETURNS,
        "quantile": "ascending ceil(0.75*n), strict |r|>Q; current excluded",
    }
    if not DEVELOPMENT_START <= trade_day <= DEVELOPMENT_END:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    if trade_day not in tse_days:
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if trade_day in isolated_days:
        event["reason"] = "TARGET_DAY_QUARANTINED"
        return event
    event.update(
        _window_audit(
            trade_day, tse_days, bars_by_day, isolated_days, start_time=time(11, 30), label="L"
        )
    )
    event.update(
        _window_audit(
            trade_day, tse_days, bars_by_day, isolated_days, start_time=time(10, 30), label="P"
        )
    )
    event.update(
        {
            "L_signal_bar_start_jst": datetime.combine(trade_day, time(12, 29), JST).isoformat(),
            "L_E_planned_entry_jst": datetime.combine(trade_day, time(12, 30), JST).isoformat(),
            "L_X_planned_exit_jst": datetime.combine(trade_day, time(13, 0), JST).isoformat(),
            "P_signal_bar_start_jst": datetime.combine(trade_day, time(11, 29), JST).isoformat(),
            "P_E_planned_entry_jst": datetime.combine(trade_day, time(11, 30), JST).isoformat(),
            "P_X_planned_exit_jst": datetime.combine(trade_day, time(12, 0), JST).isoformat(),
        }
    )
    event["status"] = "evaluated"
    event["reason"] = "INDEPENDENT_L_AND_P_FIXED_SELECTION"
    return event
