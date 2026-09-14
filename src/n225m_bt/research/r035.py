"""Causal same-clock, prior-TSE-day five-minute shock selection for R035-Q001."""

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


def scheduled_blocks(day: date) -> tuple[datetime, ...]:
    """The 46 pre-registered non-overlapping five-minute normal-session blocks."""
    output: list[datetime] = []
    for start, end in ((time(9, 30), time(11, 20)), (time(12, 35), time(14, 25))):
        cursor = datetime.combine(day, start, JST)
        final = datetime.combine(day, end, JST)
        while cursor <= final:
            output.append(cursor)
            cursor += timedelta(minutes=5)
    return tuple(output)


def five_minute_return(bars: Iterable[Bar], trade_day: date, start: datetime) -> int | None:
    """Return close_5-open_1 only for the exact eligible scheduled five bars."""
    by_time = {bar.ts_jst: bar for bar in bars}
    rows = [by_time.get(start + timedelta(minutes=index)) for index in range(5)]
    if any(row is None for row in rows):
        return None
    concrete = [row for row in rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_day or row.session is not Session.DAY
        for row in concrete
    ):
        return None
    return concrete[-1].close - concrete[0].open


def same_clock_shock_event(
    trade_day: date,
    tse_days: tuple[date, ...],
    bars_by_day: dict[date, list[Bar]],
    isolated_days: set[date],
) -> dict[str, object]:
    """Return the first strict shock using only the previous 60 scheduled TSE days."""
    event: dict[str, object] = {
        "trade_date": trade_day.isoformat(),
        "session": "day",
        "status": "no_event",
        "lookback_scheduled_tse_days": LOOKBACK_TSE_DAYS,
        "minimum_valid_abs_returns": MIN_VALID_RETURNS,
        "quantile": "ceil(0.90*n), ascending absolute returns, current day excluded",
        "blocks": [],
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
    index = tse_days.index(trade_day)
    references = tse_days[max(0, index - LOOKBACK_TSE_DAYS) : index]
    for start in scheduled_blocks(trade_day):
        current = five_minute_return(bars_by_day.get(trade_day, []), trade_day, start)
        audit: dict[str, object] = {
            "block_start_jst": start.isoformat(),
            "block_end_jst": (start + timedelta(minutes=4)).isoformat(),
            "current_r_points": current,
            "reference_scheduled_day_count": len(references),
        }
        values: list[int] = []
        for prior_day in references:
            prior_start = start.replace(year=prior_day.year, month=prior_day.month, day=prior_day.day)
            if prior_day not in isolated_days:
                value = five_minute_return(bars_by_day.get(prior_day, []), prior_day, prior_start)
                if value is not None:
                    values.append(abs(value))
        audit["reference_valid_abs_return_count"] = len(values)
        if len(references) != LOOKBACK_TSE_DAYS:
            audit["status"] = "history_outside_or_before_development"
        elif len(values) < MIN_VALID_RETURNS:
            audit["status"] = "insufficient_valid_reference_returns"
        else:
            threshold = sorted(values)[ceil(0.90 * len(values)) - 1]
            audit["threshold_U_points"] = threshold
            if current is None:
                audit["status"] = "current_block_missing_or_ineligible"
            elif current == 0:
                audit["status"] = "current_r_zero"
            elif abs(current) <= threshold:
                audit["status"] = "not_strictly_above_threshold"
            else:
                signal = start + timedelta(minutes=4)
                entry = signal + timedelta(minutes=1)
                exit_time = entry + timedelta(minutes=15)
                direction = "long" if current < 0 else "short"
                audit.update(
                    {
                        "status": "shock",
                        "shock_direction": "up" if current > 0 else "down",
                        "shock_r_points": current,
                        "fade_direction": direction,
                        "momentum_direction": "long" if current > 0 else "short",
                        "t_signal_bar_start_jst": signal.isoformat(),
                        "E_planned_entry_jst": entry.isoformat(),
                        "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
                        "X_planned_exit_jst": exit_time.isoformat(),
                    }
                )
                cast_blocks = event["blocks"]
                assert isinstance(cast_blocks, list)
                cast_blocks.append(audit)
                event.update(audit)
                event["status"] = "shock"
                event["reason"] = "FIRST_STRICT_ABS_R_ABOVE_SAME_CLOCK_PRIOR_60_TSE_DAYS"
                return event
        cast_blocks = event["blocks"]
        assert isinstance(cast_blocks, list)
        cast_blocks.append(audit)
    event["reason"] = "NO_FIRST_SHOCK_IN_FIXED_BLOCKS"
    return event
