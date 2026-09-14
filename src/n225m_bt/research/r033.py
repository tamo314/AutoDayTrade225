"""Causal opening-range compression/breakout events for R033-Q001."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def opening_range_event(
    classifier: CalendarClassifier,
    target: date,
    target_bars: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, bool]],
    *,
    target_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Build one event using exactly the supplied 20 planned preceding day sessions.

    ``history`` is deliberately supplied by the schedule layer, newest first.  This
    prevents observed bars from selecting or backfilling the rolling reference set.
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
    signal_start, search_end = start + timedelta(minutes=30), start + timedelta(minutes=89)
    event.update(
        {
            "S_scheduled_open_jst": start.isoformat(),
            "opening_end_jst": (start + timedelta(minutes=29)).isoformat(),
            "search_start_jst": signal_start.isoformat(),
            "search_end_jst": search_end.isoformat(),
            "required_opening_bars": 30,
            "required_history_sessions": 20,
        }
    )
    if target_bars is None:
        event["reason"] = "TARGET_MISSING"
        return event
    if len(history) != 20:
        event["reason"] = "HISTORY_SHORT"
        return event
    target_map = {bar.ts_jst: bar for bar in target_bars}
    opening = [target_map.get(start + timedelta(minutes=index)) for index in range(30)]
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
    ranges: list[int] = []
    history_dates: list[str] = []
    for previous, bars, quarantined in history:
        history_dates.append(previous.isoformat())
        if not development_start <= previous <= development_end:
            event.update(
                {"history_trade_dates": history_dates, "reason": "HISTORY_OUTSIDE_DEVELOPMENT"}
            )
            return event
        if quarantined:
            event.update({"history_trade_dates": history_dates, "reason": "HISTORY_QUARANTINED"})
            return event
        if bars is None:
            event.update({"history_trade_dates": history_dates, "reason": "HISTORY_MISSING"})
            return event
        previous_start = classifier.session_open(previous, Session.DAY)
        by_time = {bar.ts_jst: bar for bar in bars}
        rows = [by_time.get(previous_start + timedelta(minutes=index)) for index in range(30)]
        if any(row is None for row in rows):
            event.update(
                {"history_trade_dates": history_dates, "reason": "HISTORY_OPENING_MISSING"}
            )
            return event
        valid = [row for row in rows if row is not None]
        if any(
            not row.is_eligible or row.trade_date != previous or row.session is not Session.DAY
            for row in valid
        ):
            event.update(
                {"history_trade_dates": history_dates, "reason": "HISTORY_OPENING_INELIGIBLE"}
            )
            return event
        ranges.append(max(row.high for row in valid) - min(row.low for row in valid))
    high, low = max(row.high for row in opening_rows), min(row.low for row in opening_rows)
    current_range, threshold = high - low, sorted(ranges)[4]
    event.update(
        {
            "history_trade_dates": history_dates,
            "history_ranges_points_newest_first": ranges,
            "H_points": high,
            "L_points": low,
            "R_points": current_range,
            "T_points_fifth_order_statistic": threshold,
            "compression": current_range <= threshold,
        }
    )
    if current_range <= 0:
        event["reason"] = "ZERO_OPENING_RANGE"
        return event
    search = [target_map.get(signal_start + timedelta(minutes=index)) for index in range(60)]
    if any(row is None for row in search):
        event["reason"] = "BREAKOUT_SEARCH_MISSING"
        return event
    search_rows = [row for row in search if row is not None]
    if any(
        not row.is_eligible or row.trade_date != target or row.session is not Session.DAY
        for row in search_rows
    ):
        event["reason"] = "BREAKOUT_SEARCH_INELIGIBLE"
        return event
    for row in search_rows:
        if row.close > high or row.close < low:
            direction = "long" if row.close > high else "short"
            entry, exit_ = row.ts_jst + timedelta(minutes=1), row.ts_jst + timedelta(minutes=61)
            event.update(
                {
                    "status": "compressed" if current_range <= threshold else "noncompressed",
                    "reason": "FIRST_CLOSE_BREAKOUT",
                    "breakout_ts_jst": row.ts_jst.isoformat(),
                    "breakout_direction": direction,
                    "E_planned_entry_jst": entry.isoformat(),
                    "EXIT_signal_bar_start_jst": (exit_ - timedelta(minutes=1)).isoformat(),
                    "X_planned_exit_jst": exit_.isoformat(),
                    "breakout_15m_bucket": row.ts_jst.replace(
                        minute=(row.ts_jst.minute // 15) * 15, second=0, microsecond=0
                    ).strftime("%H:%M"),
                }
            )
            return event
    event["reason"] = "NO_CLOSE_BREAKOUT"
    return event
