"""Causal prior-night rejection events for R026-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end


def prior_night_rejection_event(
    classifier: CalendarClassifier,
    cash_calendar: TSECashMarketCalendar,
    trade_date: date,
    day_bars: list[Bar] | None,
    night_bars: list[Bar] | None,
    *,
    day_quarantined: bool = False,
    night_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
) -> dict[str, object]:
    """Use the one scheduled night carrying ``trade_date``; never skip back."""
    opening = datetime(trade_date.year, trade_date.month, trade_date.day, 9, tzinfo=JST)
    signal, entry = opening + timedelta(minutes=14), opening + timedelta(minutes=15)
    exit_signal, exit_time = opening + timedelta(minutes=74), opening + timedelta(minutes=75)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "N_trade_date": trade_date.isoformat(),
        "window_start_jst": opening.isoformat(),
        "K_bar_start_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": exit_signal.isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(),
        "required_day_window_bars": 15,
        "status": "skipped",
    }
    if not cash_calendar.is_open(trade_date):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if trade_date < development_start:
        event["reason"] = "REFERENCE_OUTSIDE_DEVELOPMENT"
        return event
    if day_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    if night_quarantined:
        event["reason"] = "REFERENCE_NIGHT_SESSION_QUARANTINED"
        return event
    if day_bars is None or night_bars is None:
        event["reason"] = "DAY_OR_REFERENCE_NIGHT_MISSING"
        return event
    try:
        n_open, n_end = (
            classifier.session_open(trade_date, Session.NIGHT),
            normal_session_end(classifier, trade_date, Session.NIGHT),
        )
    except ValueError:
        event["reason"] = "NO_SCHEDULED_NIGHT_MAPPING"
        return event
    if n_end > opening:
        event["reason"] = "REFERENCE_NIGHT_NOT_COMPLETE_BY_0900"
        return event
    stamps = [
        n_open + timedelta(minutes=i) for i in range(int((n_end - n_open).total_seconds() // 60))
    ]
    event.update(
        {
            "N_scheduled_open_jst": n_open.isoformat(),
            "N_normal_end_jst": n_end.isoformat(),
            "N_final_normal_bar_start_jst": (n_end - timedelta(minutes=1)).isoformat(),
            "N_expected_bar_count": len(stamps),
        }
    )
    by_time = {bar.ts_jst: bar for bar in night_bars}
    n_rows = [by_time.get(stamp) for stamp in stamps]
    if any(row is None for row in n_rows):
        event["reason"] = "REFERENCE_FULL_NORMAL_NIGHT_MISSING"
        return event
    night = [row for row in n_rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_date or row.session is not Session.NIGHT
        for row in night
    ):
        event["reason"] = "REFERENCE_NIGHT_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    high, low = max(row.high for row in night), min(row.low for row in night)
    event.update({"H_points": high, "L_points": low})
    if high == low:
        event["reason"] = "ZERO_RANGE_H_EQUALS_L"
        return event
    current = {bar.ts_jst: bar for bar in day_bars}
    rows = [current.get(opening + timedelta(minutes=i)) for i in range(15)]
    if any(row is None for row in rows):
        event["reason"] = "WINDOW_0900_TO_0914_MISSING"
        return event
    window = [row for row in rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY
        for row in window
    ):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    upper, lower, close = (
        max(row.high for row in window),
        min(row.low for row in window),
        window[-1].close,
    )
    event.update({"U_points": upper, "D_points": lower, "K_close_0914_points": close})
    if upper > high and lower >= low:
        side, direction = "upper", "short"
    elif lower < low and upper <= high:
        side, direction = "lower", "long"
    else:
        event["reason"] = "BOTH_OR_NEITHER_NIGHT_EXTREME_BREACH"
        return event
    event.update({"range_side": side, "base_direction": direction})
    if low < close < high:
        event.update(
            {
                "status": "confirmed",
                "reason": "ONE_SIDE_BREACH_AND_STRICT_RANGE_RETURN",
                "A_direction": direction,
                "F_outward_direction": "long" if direction == "short" else "short",
            }
        )
    else:
        event.update(
            {"status": "nonconfirmed", "reason": "ONE_SIDE_BREACH_WITHOUT_STRICT_RANGE_RETURN"}
        )
    return event
