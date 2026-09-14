"""Causal night-conflict / cash-opening events for R041-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import ceil

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end


def _window(bars: list[Bar], trade_date: date, start: datetime, count: int) -> list[Bar] | None:
    """Return exactly scheduled continuous eligible day bars, or no value."""
    by_time = {bar.ts_jst: bar for bar in bars}
    rows = [by_time.get(start + timedelta(minutes=index)) for index in range(count)]
    if any(row is None for row in rows):
        return None
    valid = [row for row in rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY for row in valid):
        return None
    return valid


def night_conflict_open_followthrough_event(
    classifier: CalendarClassifier,
    cash_calendar: TSECashMarketCalendar,
    trade_date: date,
    day_bars: list[Bar] | None,
    night_bars: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, bool]],
    *,
    day_quarantined: bool = False,
    night_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Classify only with information known at the 09:29 bar close.

    ``history`` is precisely the preceding 60 scheduled TSE trading days in
    newest-first order. Invalid references are recorded rather than replaced.
    """
    opening = datetime(trade_date.year, trade_date.month, trade_date.day, 9, tzinfo=JST)
    signal, entry = opening + timedelta(minutes=29), opening + timedelta(minutes=30)
    exit_signal, exit_time = opening + timedelta(minutes=89), opening + timedelta(minutes=90)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(), "session": "day", "N_trade_date": trade_date.isoformat(),
        "O_window_start_jst": opening.isoformat(), "O_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(), "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": exit_signal.isoformat(), "X_planned_exit_jst": exit_time.isoformat(),
        "required_O_window_bars": 30, "status": "skipped",
    }
    if not development_start <= trade_date <= development_end:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    if not cash_calendar.is_open(trade_date):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
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
        night_open = classifier.session_open(trade_date, Session.NIGHT)
        night_end = normal_session_end(classifier, trade_date, Session.NIGHT)
    except ValueError:
        event["reason"] = "NO_SCHEDULED_NIGHT_MAPPING"
        return event
    if night_end > opening:
        event["reason"] = "REFERENCE_NIGHT_NOT_COMPLETE_BY_0900"
        return event
    night_stamps = [night_open + timedelta(minutes=index) for index in range(int((night_end - night_open).total_seconds() // 60))]
    event.update({"N_scheduled_open_jst": night_open.isoformat(), "N_normal_end_jst": night_end.isoformat(), "N_final_normal_bar_start_jst": (night_end - timedelta(minutes=1)).isoformat(), "N_expected_bar_count": len(night_stamps)})
    night_by_time = {bar.ts_jst: bar for bar in night_bars}
    night_rows = [night_by_time.get(stamp) for stamp in night_stamps]
    if any(row is None for row in night_rows):
        event["reason"] = "REFERENCE_FULL_NORMAL_NIGHT_MISSING"
        return event
    night = [row for row in night_rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.NIGHT for row in night):
        event["reason"] = "REFERENCE_NIGHT_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    opening_rows = _window(day_bars, trade_date, opening, 30)
    if opening_rows is None:
        event["reason"] = "WINDOW_0900_TO_0929_MISSING_OR_INELIGIBLE"
        return event
    r_n = night[-1].close - night[0].open
    r_o = opening_rows[-1].close - opening_rows[0].open
    event.update({"rN_open_night_points": night[0].open, "rN_close_final_normal_night_points": night[-1].close, "rO_open_0900_points": opening_rows[0].open, "rO_close_0929_points": opening_rows[-1].close, "rN_points": r_n, "rO_points": r_o})
    if r_n == 0:
        event["reason"] = "ZERO_rN"
        return event
    if r_o == 0:
        event["reason"] = "ZERO_rO"
        return event
    event.update({"rN_sign": 1 if r_n > 0 else -1, "rO_sign": 1 if r_o > 0 else -1, "rN_direction": "long" if r_n > 0 else "short", "rO_direction": "long" if r_o > 0 else "short"})
    if len(history) != 60:
        event["reason"] = "HISTORY_60_SCHEDULED_TSE_DAYS_SHORT"
        return event
    references: list[int] = []
    reference_audit: list[dict[str, object]] = []
    for reference_date, reference_bars, quarantined in history:
        item: dict[str, object] = {"trade_date": reference_date.isoformat()}
        if not development_start <= reference_date <= development_end:
            item["reason"] = "OUTSIDE_DEVELOPMENT"
        elif not cash_calendar.is_open(reference_date):
            item["reason"] = "TSE_CASH_MARKET_CLOSED"
        elif quarantined:
            item["reason"] = "DAY_SESSION_QUARANTINED"
        elif reference_bars is None:
            item["reason"] = "DAY_SESSION_MISSING"
        else:
            ref_open = datetime(reference_date.year, reference_date.month, reference_date.day, 9, tzinfo=JST)
            rows = _window(reference_bars, reference_date, ref_open, 30)
            if rows is None:
                item["reason"] = "WINDOW_0900_TO_0929_MISSING_OR_INELIGIBLE"
            else:
                value = abs(rows[-1].close - rows[0].open)
                item["abs_rO_points"] = value
                if value == 0:
                    item["reason"] = "ZERO_rO"
                else:
                    item["reason"] = "VALID"
                    references.append(value)
        reference_audit.append(item)
    event.update({"rolling_reference_audit": reference_audit, "valid_reference_count": len(references)})
    if len(references) < 50:
        event["reason"] = "INSUFFICIENT_VALID_rO_REFERENCES"
        return event
    qm = sorted(references)[ceil(len(references) * 0.5) - 1]
    event.update({"QM_abs_rO_points": qm, "opening_magnitude_layer": "low" if abs(r_o) <= qm else "high", "opening_magnitude_rule": "abs(rO)<=QM is low; strict greater is high; target excluded"})
    if r_n * r_o < 0:
        event.update({"status": "conflict", "reason": "rN_rO_OPPOSITE_SIGN", "A_direction": event["rO_direction"], "F_night_direction": event["rN_direction"]})
    else:
        event.update({"status": "agreement", "reason": "rN_rO_SAME_SIGN"})
    return event
