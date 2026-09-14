"""Causal scheduled-TSE-close conflict events for R044-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r021 import tse_cash_close


def tse_close_conflict_event(
    trade_date: date,
    day_bars: list[Bar] | None,
    cash_calendar: TSECashMarketCalendar,
    *,
    day_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Classify exact 25+5 minute main and 60-minute-placebo windows.

    The close is obtained exclusively from the versioned TSE schedule.  Main
    and placebo eligibility are deliberately independent.
    """
    close = tse_cash_close(trade_date)
    placebo = close - timedelta(minutes=60)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(), "session": "day",
        "T_cash_close_jst": close.isoformat(), "tB_jst": placebo.isoformat(),
        "cash_close_regime": "T_1500" if close.hour == 15 and close.minute == 0 else "T_1530",
        "P_window_start_jst": (close - timedelta(minutes=30)).isoformat(),
        "P_window_end_jst": (close - timedelta(minutes=6)).isoformat(),
        "L_window_start_jst": (close - timedelta(minutes=5)).isoformat(),
        "L_window_end_jst": (close - timedelta(minutes=1)).isoformat(),
        "t_signal_bar_start_jst": (close - timedelta(minutes=1)).isoformat(),
        "E_planned_entry_jst": close.isoformat(),
        "EXIT_signal_bar_start_jst": (close + timedelta(minutes=9)).isoformat(),
        "X_planned_exit_jst": (close + timedelta(minutes=10)).isoformat(),
        "PB_window_start_jst": (placebo - timedelta(minutes=30)).isoformat(),
        "PB_window_end_jst": (placebo - timedelta(minutes=6)).isoformat(),
        "LB_window_start_jst": (placebo - timedelta(minutes=5)).isoformat(),
        "LB_window_end_jst": (placebo - timedelta(minutes=1)).isoformat(),
        "G_signal_bar_start_jst": (placebo - timedelta(minutes=1)).isoformat(),
        "G_planned_entry_jst": placebo.isoformat(),
        "G_EXIT_signal_bar_start_jst": (placebo + timedelta(minutes=9)).isoformat(),
        "G_planned_exit_jst": (placebo + timedelta(minutes=10)).isoformat(),
        "required_P_bars": 25, "required_L_bars": 5, "status": "skipped", "placebo_status": "skipped",
    }
    if not development_start <= trade_date <= development_end:
        event.update(reason="OUTSIDE_DEVELOPMENT", placebo_reason="OUTSIDE_DEVELOPMENT")
        return event
    if not cash_calendar.is_open(trade_date):
        event.update(reason="TSE_CASH_MARKET_CLOSED", placebo_reason="TSE_CASH_MARKET_CLOSED")
        return event
    if day_quarantined:
        event.update(reason="DAY_SESSION_QUARANTINED", placebo_reason="DAY_SESSION_QUARANTINED")
        return event
    if day_bars is None:
        event.update(reason="DAY_SESSION_MISSING", placebo_reason="DAY_SESSION_MISSING")
        return event
    by_time = {bar.ts_jst: bar for bar in day_bars}

    def classify(prefix: str, boundary: datetime, status_key: str, reason_key: str) -> None:
        p_rows = [by_time.get(boundary - timedelta(minutes=30) + timedelta(minutes=i)) for i in range(25)]
        l_rows = [by_time.get(boundary - timedelta(minutes=5) + timedelta(minutes=i)) for i in range(5)]
        if any(row is None for row in p_rows):
            event[reason_key] = f"{prefix}_25_WINDOW_MISSING"
            return
        if any(row is None for row in l_rows):
            event[reason_key] = f"{prefix}_5_WINDOW_MISSING"
            return
        rows = [row for row in [*p_rows, *l_rows] if row is not None]
        if any(not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY for row in rows):
            event[reason_key] = f"{prefix}_WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
            return
        p_rows_valid = [row for row in p_rows if row is not None]
        l_rows_valid = [row for row in l_rows if row is not None]
        r_p = p_rows_valid[-1].close - p_rows_valid[0].open
        r_l = l_rows_valid[-1].close - l_rows_valid[0].open
        suffix = "" if prefix == "MAIN" else "B"
        event.update({f"open_P{suffix}_points": p_rows_valid[0].open, f"close_P{suffix}_points": p_rows_valid[-1].close,
                      f"open_L{suffix}_points": l_rows_valid[0].open, f"close_L{suffix}_points": l_rows_valid[-1].close,
                      f"rP{suffix}_points": r_p, f"rL{suffix}_points": r_l})
        if r_p == 0 or r_l == 0:
            event[reason_key] = f"ZERO_rP{suffix}" if r_p == 0 else f"ZERO_rL{suffix}"
            return
        p_sign, l_sign = (1 if r_p > 0 else -1), (1 if r_l > 0 else -1)
        event.update({f"rP{suffix}_sign": p_sign, f"rL{suffix}_sign": l_sign,
                      f"rL{suffix}_direction": "long" if l_sign > 0 else "short"})
        event[status_key] = "conflict" if p_sign != l_sign else "agreement"
        event[reason_key] = "OPPOSITE_SIGNS" if p_sign != l_sign else "SAME_SIGNS"

    classify("MAIN", close, "status", "reason")
    classify("PLACEBO", placebo, "placebo_status", "placebo_reason")
    return event
