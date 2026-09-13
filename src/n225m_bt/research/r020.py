"""Causal TSE-cash-lunch event selection for R020-Q001."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import StringIO

from n225m_bt.domain import Bar, Session


@dataclass(frozen=True)
class TSECashMarketCalendar:
    """TSE cash business-day rule, fixed from an official holiday source.

    This intentionally has no dependency on the OSE trading calendar.  The
    caller supplies a saved Cabinet Office holiday CSV; dates outside its
    coverage are rejected rather than inferred.
    """

    holidays: frozenset[date]
    source_start: date
    source_end: date

    @classmethod
    def from_cabinet_office_csv(cls, text: str) -> TSECashMarketCalendar:
        rows = list(csv.reader(StringIO(text.lstrip("\ufeff"))))
        parsed = [datetime.strptime(row[0], "%Y/%m/%d").date() for row in rows[1:] if row]
        if not parsed:
            raise ValueError("official holiday CSV has no dates")
        return cls(frozenset(parsed), min(parsed), max(parsed))

    def is_open(self, trade_date: date) -> bool:
        if not self.source_start <= trade_date <= self.source_end:
            raise ValueError("TSE calendar evidence does not cover trade_date")
        return (
            trade_date.weekday() < 5
            and trade_date not in self.holidays
            and not (trade_date.month == 12 and trade_date.day == 31)
            and not (trade_date.month == 1 and trade_date.day in {1, 2, 3})
        )


def tse_lunch_event(
    trade_date: date,
    bars: list[Bar],
    cash_calendar: TSECashMarketCalendar,
) -> dict[str, object]:
    """Select one day-only R020 event using data known at 12:29 JST."""
    start = datetime.combine(trade_date, datetime.min.time(), bars[0].ts_jst.tzinfo).replace(
        hour=10, minute=30
    ) if bars else datetime.combine(trade_date, datetime.min.time()).replace(hour=10, minute=30)
    lunch_start = start + timedelta(minutes=60)
    signal = start + timedelta(minutes=119)
    entry = signal + timedelta(minutes=1)
    exit_time = entry + timedelta(minutes=60)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "P_window_start_jst": start.isoformat(),
        "P_window_end_jst": (lunch_start - timedelta(minutes=1)).isoformat(),
        "L_window_start_jst": lunch_start.isoformat(),
        "L_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": (exit_time - timedelta(minutes=1)).isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(),
        "required_window_bars": 120,
        "status": "skipped",
    }
    if not cash_calendar.is_open(trade_date):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    rows_by_time = {bar.ts_jst: bar for bar in bars}
    expected = [start + timedelta(minutes=offset) for offset in range(120)]
    rows = [rows_by_time.get(stamp) for stamp in expected]
    if any(row is None for row in rows):
        event["reason"] = "WINDOW_1030_TO_1229_MISSING"
        return event
    concrete = [row for row in rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_date or row.session is not Session.DAY
        for row in concrete
    ):
        event["reason"] = "WINDOW_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    p = concrete[59].close - concrete[0].open
    lunch_change = concrete[119].close - concrete[60].open
    event.update(
        {
            "open_1030_points": concrete[0].open,
            "close_1129_points": concrete[59].close,
            "open_1130_points": concrete[60].open,
            "close_1229_points": concrete[119].close,
            "P_points": p,
            "L_points": lunch_change,
        }
    )
    if p == 0:
        event["reason"] = "ZERO_P"
        return event
    if lunch_change == 0:
        event["reason"] = "ZERO_L"
        return event
    lunch_follow = "long" if lunch_change > 0 else "short"
    pre_lunch_follow = "long" if p > 0 else "short"
    event.update(
        {
            "status": "eligible",
            "reason": "TSE_OPEN_AND_P_L_NONZERO",
            "A_direction": "short" if lunch_follow == "long" else "long",
            "D_direction": "short" if pre_lunch_follow == "long" else "long",
            "F_direction": lunch_follow,
        }
    )
    return event
