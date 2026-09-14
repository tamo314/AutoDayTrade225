"""Causal TSE cash-close reversal and placebo event selection for R030-Q001."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r021 import tse_cash_close


def tse_cash_close_reversal_event(
    trade_date: date,
    day_bars: list[Bar] | None,
    cash_calendar: TSECashMarketCalendar,
    *,
    day_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Select R030's causal main and placebo events from scheduled clock times.

    The main-side decision consumes only bars through T-1.  The placebo is a
    separate five-minute event and never changes the main-event eligibility.
    """
    close = tse_cash_close(trade_date)
    main_start = close - timedelta(minutes=5)
    placebo_start = close - timedelta(minutes=35)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "T_cash_close_jst": close.isoformat(),
        "cash_close_regime": "T_1500" if close.hour == 15 and close.minute == 0 else "T_1530",
        "P_window_start_jst": main_start.isoformat(),
        "P_window_end_jst": (close - timedelta(minutes=1)).isoformat(),
        "t_signal_bar_start_jst": (close - timedelta(minutes=1)).isoformat(),
        "E_planned_entry_jst": close.isoformat(),
        "EXIT_signal_bar_start_jst": (close + timedelta(minutes=9)).isoformat(),
        "X_planned_exit_jst": (close + timedelta(minutes=10)).isoformat(),
        "P0_window_start_jst": placebo_start.isoformat(),
        "P0_window_end_jst": (close - timedelta(minutes=31)).isoformat(),
        "G_signal_bar_start_jst": (close - timedelta(minutes=31)).isoformat(),
        "G_planned_entry_jst": (close - timedelta(minutes=30)).isoformat(),
        "G_EXIT_signal_bar_start_jst": (close - timedelta(minutes=21)).isoformat(),
        "G_planned_exit_jst": (close - timedelta(minutes=20)).isoformat(),
        "required_window_bars": 5,
        "status": "skipped",
        "placebo_status": "skipped",
    }
    if not development_start <= trade_date <= development_end:
        event.update({"reason": "OUTSIDE_DEVELOPMENT", "placebo_reason": "OUTSIDE_DEVELOPMENT"})
        return event
    if not cash_calendar.is_open(trade_date):
        event.update(
            {"reason": "TSE_CASH_MARKET_CLOSED", "placebo_reason": "TSE_CASH_MARKET_CLOSED"}
        )
        return event
    if day_quarantined:
        event.update(
            {"reason": "DAY_SESSION_QUARANTINED", "placebo_reason": "DAY_SESSION_QUARANTINED"}
        )
        return event
    if day_bars is None:
        event.update({"reason": "DAY_SESSION_MISSING", "placebo_reason": "DAY_SESSION_MISSING"})
        return event
    by_time = {bar.ts_jst: bar for bar in day_bars}
    main_rows = [by_time.get(main_start + timedelta(minutes=index)) for index in range(5)]
    placebo_rows = [by_time.get(placebo_start + timedelta(minutes=index)) for index in range(5)]

    def valid(rows: list[Bar | None]) -> bool:
        return all(
            row is not None
            and row.is_eligible
            and row.trade_date == trade_date
            and row.session is Session.DAY
            for row in rows
        )

    if any(row is None for row in main_rows):
        event["reason"] = "P_WINDOW_T_MINUS_5_TO_T_MINUS_1_MISSING"
    elif not valid(main_rows):
        event["reason"] = "P_WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
    else:
        rows = [row for row in main_rows if row is not None]
        p = rows[-1].close - rows[0].open
        event.update(
            {
                "P_open_T_minus_5_points": rows[0].open,
                "P_close_T_minus_1_points": rows[-1].close,
                "P_points": p,
            }
        )
        if p == 0:
            event["reason"] = "ZERO_P"
        else:
            direction = "long" if p > 0 else "short"
            event.update(
                {
                    "status": "eligible",
                    "reason": "TSE_OPEN_AND_P_NONZERO",
                    "P_sign": 1 if p > 0 else -1,
                    "P_direction": direction,
                    "A_direction": "short" if direction == "long" else "long",
                    "D_direction": direction,
                }
            )

    if any(row is None for row in placebo_rows):
        event["placebo_reason"] = "P0_WINDOW_T_MINUS_35_TO_T_MINUS_31_MISSING"
    elif not valid(placebo_rows):
        event["placebo_reason"] = "P0_WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
    else:
        rows = [row for row in placebo_rows if row is not None]
        p0 = rows[-1].close - rows[0].open
        event.update(
            {
                "P0_open_T_minus_35_points": rows[0].open,
                "P0_close_T_minus_31_points": rows[-1].close,
                "P0_points": p0,
            }
        )
        if p0 == 0:
            event["placebo_reason"] = "ZERO_P0"
        else:
            direction = "long" if p0 > 0 else "short"
            event.update(
                {
                    "placebo_status": "eligible",
                    "placebo_reason": "TSE_OPEN_AND_P0_NONZERO",
                    "P0_sign": 1 if p0 > 0 else -1,
                    "P0_direction": direction,
                    "G_direction": "short" if direction == "long" else "long",
                }
            )
    return event
