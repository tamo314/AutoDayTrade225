"""Causal fixed-delay opening-range rejection events for R034-Q001."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def opening_range_rejection_event(
    classifier: CalendarClassifier,
    target: date,
    target_bars: list[Bar] | None,
    *,
    target_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Classify the first strict opening-range break exactly 15 bars later.

    The event consumes no observations after the scheduled ``k=b+15`` bar.  The
    runner deliberately delegates the subsequent entry and fixed exit to the
    unchanged shared execution engine.
    """
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "session": "day",
        "status": "skipped",
    }
    if not development_start <= target <= development_end:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    if target_quarantined:
        event["reason"] = "TARGET_QUARANTINED"
        return event
    try:
        start = classifier.session_open(target, Session.DAY)
    except ValueError:
        event["reason"] = "NO_SCHEDULED_TARGET_DAY"
        return event
    event.update(
        {
            "S_scheduled_open_jst": start.isoformat(),
            "opening_end_jst": (start + timedelta(minutes=29)).isoformat(),
            "search_start_jst": (start + timedelta(minutes=30)).isoformat(),
            "search_end_jst": (start + timedelta(minutes=89)).isoformat(),
            "required_opening_bars": 30,
            "required_breakout_search_bars": 60,
            "confirmation_delay_bars": 15,
        }
    )
    if target_bars is None:
        event["reason"] = "TARGET_MISSING"
        return event
    bars = {bar.ts_jst: bar for bar in target_bars}
    opening = [bars.get(start + timedelta(minutes=index)) for index in range(30)]
    if any(row is None for row in opening):
        event["reason"] = "TARGET_OPENING_MISSING"
        return event
    opening_rows = [row for row in opening if row is not None]
    if any(
        not row.is_eligible or row.trade_date != target or row.session is not Session.DAY
        for row in opening_rows
    ):
        event["reason"] = "TARGET_OPENING_INELIGIBLE"
        return event
    high, low = max(row.high for row in opening_rows), min(row.low for row in opening_rows)
    event.update({"H_points": high, "L_points": low, "R_points": high - low})
    if high <= low:
        event["reason"] = "ZERO_OPENING_RANGE"
        return event
    for index in range(30, 90):
        breakout = bars.get(start + timedelta(minutes=index))
        if breakout is None:
            event["reason"] = "BREAKOUT_SEARCH_MISSING"
            return event
        if (
            not breakout.is_eligible
            or breakout.trade_date != target
            or breakout.session is not Session.DAY
        ):
            event["reason"] = "BREAKOUT_SEARCH_INELIGIBLE"
            return event
        if not (breakout.close > high or breakout.close < low):
            continue
        direction = "long" if breakout.close > high else "short"
        k_time = breakout.ts_jst + timedelta(minutes=15)
        intermediate = [
            bars.get(breakout.ts_jst + timedelta(minutes=index)) for index in range(1, 16)
        ]
        if any(row is None for row in intermediate):
            event.update(
                {
                    "reason": "CONFIRMATION_WINDOW_MISSING",
                    "breakout_ts_jst": breakout.ts_jst.isoformat(),
                }
            )
            return event
        confirmation_rows = [row for row in intermediate if row is not None]
        if any(
            not row.is_eligible or row.trade_date != target or row.session is not Session.DAY
            for row in confirmation_rows
        ):
            event.update(
                {
                    "reason": "CONFIRMATION_WINDOW_INELIGIBLE",
                    "breakout_ts_jst": breakout.ts_jst.isoformat(),
                }
            )
            return event
        k = confirmation_rows[-1]
        persistent = k.close > high if direction == "long" else k.close < low
        event.update(
            {
                "status": "persistent" if persistent else "rejected",
                "reason": "FIXED_15M_CLASSIFICATION",
                "breakout_ts_jst": breakout.ts_jst.isoformat(),
                "breakout_direction": direction,
                "breakout_close": breakout.close,
                "k_ts_jst": k_time.isoformat(),
                "k_close": k.close,
                "E_planned_entry_jst": (k_time + timedelta(minutes=1)).isoformat(),
                "EXIT_signal_bar_start_jst": (k_time + timedelta(minutes=60)).isoformat(),
                "X_planned_exit_jst": (k_time + timedelta(minutes=61)).isoformat(),
                "breakout_15m_bucket": breakout.ts_jst.replace(
                    minute=(breakout.ts_jst.minute // 15) * 15, second=0, microsecond=0
                ).strftime("%H:%M"),
            }
        )
        return event
    event["reason"] = "NO_CLOSE_BREAKOUT"
    return event
