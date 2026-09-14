"""Causal night-terminal-range failed-breakout events for R048-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r022 import normal_session_end


def _eligible(rows: list[Bar], day: date, session: Session) -> bool:
    return bool(rows) and all(
        row.is_eligible and row.trade_date == day and row.session is session for row in rows
    )


def _classify(
    by_time: dict[datetime, Bar],
    start: datetime,
    high: int,
    low: int,
    prefix: str,
    horizon: int,
) -> dict[str, object]:
    """Use only the first strict breakout and its first causal return."""
    result: dict[str, object] = {f"{prefix}_status": "NO_BREAKOUT"}
    breakout: Bar | None = None
    direction = ""
    for offset in range(30):
        row = by_time[start + timedelta(minutes=offset)]
        if row.close > high:
            breakout, direction = row, "up"
            break
        if row.close < low:
            breakout, direction = row, "down"
            break
    if breakout is None:
        return result
    result.update(
        {
            f"{prefix}_status": "BREAKOUT_NO_RETURN",
            f"{prefix}_breakout_direction": direction,
            f"{prefix}_breakout_jst": breakout.ts_jst.isoformat(),
            f"{prefix}_breakout_close": breakout.close,
            f"{prefix}_breakout_excess_points": breakout.close - high
            if direction == "up"
            else low - breakout.close,
            f"{prefix}_breakout_minute": int((breakout.ts_jst - start).total_seconds() // 60),
        }
    )
    for elapsed in range(1, horizon + 1):
        row = by_time[breakout.ts_jst + timedelta(minutes=elapsed)]
        if low <= row.close <= high:
            entry = row.ts_jst + timedelta(minutes=1)
            result.update(
                {
                    f"{prefix}_status": "FAILED_BREAKOUT",
                    f"{prefix}_return_jst": row.ts_jst.isoformat(),
                    f"{prefix}_return_minutes": elapsed,
                    f"{prefix}_entry_jst": entry.isoformat(),
                    f"{prefix}_exit_jst": (entry + timedelta(minutes=30)).isoformat(),
                    f"{prefix}_fade_direction": "short" if direction == "up" else "long",
                    f"{prefix}_continue_direction": "long" if direction == "up" else "short",
                }
            )
            return result
        if (direction == "up" and row.close < low) or (direction == "down" and row.close > high):
            result[f"{prefix}_status"] = "AMBIGUOUS_OPPOSITE_BOUNDARY"
            result[f"{prefix}_ambiguous_jst"] = row.ts_jst.isoformat()
            return result
    return result


def r048_event(
    classifier: CalendarClassifier,
    target: date,
    day_bars: list[Bar] | None,
    night_bars: list[Bar] | None,
    *,
    day_quarantined: bool = False,
    night_quarantined: bool = False,
    horizon: int = 5,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Create one common-eligibility ledger row without reading future days."""
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "return_horizon_minutes": horizon,
    }
    if horizon not in {3, 5, 7}:
        raise ValueError("R048 horizon must be preregistered 3, 5, or 7 minutes")
    if not development_start <= target <= development_end:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if day_quarantined or night_quarantined:
        event["reason"] = "DAY_OR_SAME_TRADE_DATE_NIGHT_QUARANTINED"
        return event
    if day_bars is None or night_bars is None:
        event["reason"] = "DAY_OR_SAME_TRADE_DATE_NIGHT_MISSING"
        return event
    try:
        day_start = classifier.session_open(target, Session.DAY)
        night_end = normal_session_end(classifier, target, Session.NIGHT)
    except ValueError:
        event["reason"] = "SCHEDULED_DAY_OR_NIGHT_MAPPING_UNAVAILABLE"
        return event
    day_by_time = {row.ts_jst: row for row in day_bars}
    night_by_time = {row.ts_jst: row for row in night_bars}
    terminal_times = [night_end - timedelta(minutes=index) for index in range(30, 0, -1)]
    # 0..155 covers both 30m searches, their maximum confirmation, next-open
    # entries, and each 30m exit.  It intentionally makes E event-independent.
    day_times = [day_start + timedelta(minutes=index) for index in range(156)]
    terminal = [night_by_time.get(stamp) for stamp in terminal_times]
    required_day = [day_by_time.get(stamp) for stamp in day_times]
    if any(row is None for row in terminal) or any(row is None for row in required_day):
        event["reason"] = "COMMON_REQUIRED_SCHEDULED_BARS_MISSING"
        return event
    terminal_rows = [row for row in terminal if row is not None]
    day_rows = [row for row in required_day if row is not None]
    if not _eligible(terminal_rows, target, Session.NIGHT) or not _eligible(
        day_rows, target, Session.DAY
    ):
        event["reason"] = "COMMON_REQUIRED_BARS_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    high, low, p0 = (
        max(row.high for row in terminal_rows),
        min(row.low for row in terminal_rows),
        terminal_rows[0].open,
    )
    placebo_rows = [day_by_time[day_start + timedelta(minutes=index)] for index in range(60, 90)]
    placebo_high, placebo_low, placebo_p0 = (
        max(row.high for row in placebo_rows),
        min(row.low for row in placebo_rows),
        placebo_rows[0].open,
    )
    if p0 <= 0 or high <= low or placebo_p0 <= 0 or placebo_high <= placebo_low:
        event["reason"] = "NONPOSITIVE_REFERENCE_PRICE_OR_ZERO_RANGE"
        return event
    event.update(
        {
            "W0_start_jst": terminal_rows[0].ts_jst.isoformat(),
            "W0_end_jst": terminal_rows[-1].ts_jst.isoformat(),
            "WP_start_jst": (day_start + timedelta(minutes=60)).isoformat(),
            "WP_end_jst": (day_start + timedelta(minutes=89)).isoformat(),
            "S0_start_jst": day_start.isoformat(),
            "S0_end_jst": (day_start + timedelta(minutes=29)).isoformat(),
            "SP_start_jst": (day_start + timedelta(minutes=90)).isoformat(),
            "SP_end_jst": (day_start + timedelta(minutes=119)).isoformat(),
            "H0_points": high,
            "L0_points": low,
            "P0_points": p0,
            "range_width_points": high - low,
            "main_H_points": high,
            "main_L_points": low,
            "main_P_points": p0,
            "placebo_H_points": placebo_high,
            "placebo_L_points": placebo_low,
            "placebo_P_points": placebo_p0,
            "placebo_range_width_points": placebo_high - placebo_low,
        }
    )
    event.update(_classify(day_by_time, day_start, high, low, "main", horizon))
    event.update(
        _classify(
            day_by_time,
            day_start + timedelta(minutes=90),
            placebo_high,
            placebo_low,
            "placebo",
            horizon,
        )
    )
    event.update(status="E", reason="COMMON_CAUSAL_ELIGIBLE")
    return event
