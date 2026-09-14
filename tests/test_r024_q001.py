from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r024 import cash_open_confirmation_event
from n225m_bt.strategies.cash_open_confirmation import CashOpenConfirmationStrategy


def cash_calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2023, 5, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def bar(stamp: datetime, day: date, open_: int, close: int) -> Bar:
    return Bar(stamp, day, stamp.date(), Session.DAY, "synthetic", open_, max(open_, close), min(open_, close), close)


def day_rows(day: date, p: int = 10, q: int = 10, *, missing: datetime | None = None) -> list[Bar]:
    start, rows = datetime(day.year, day.month, day.day, 8, 45, tzinfo=JST), []
    for offset in range(46):
        stamp, close = start + timedelta(minutes=offset), 100
        if offset == 14:
            close = 100 + p
        if offset == 19:
            close = 100 + q
        if stamp != missing:
            rows.append(bar(stamp, day, 100, close))
    return rows


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def test_p_q_signs_confirmation_counterfactuals_zero_and_prefix() -> None:
    day = date(2024, 11, 5)
    confirmed = cash_open_confirmation_event(day, day_rows(day, 10, 10), cash_calendar())
    changed_q = cash_open_confirmation_event(day, day_rows(day, 10, -10), cash_calendar())
    changed_p = cash_open_confirmation_event(day, day_rows(day, -10, 10), cash_calendar())
    double_negative = cash_open_confirmation_event(day, day_rows(day, -10, -10), cash_calendar())
    assert confirmed["status"] == "confirmed" and confirmed["A_direction"] == "long"
    assert double_negative["status"] == "confirmed" and double_negative["A_direction"] == "short"
    assert changed_q["status"] == changed_p["status"] == "nonconfirmed"
    assert changed_q["P_points"] == confirmed["P_points"] and changed_p["Q_points"] == confirmed["Q_points"]
    assert cash_open_confirmation_event(day, day_rows(day, 0, 10), cash_calendar())["reason"] == "ZERO_P"
    assert cash_open_confirmation_event(day, day_rows(day, 10, 0), cash_calendar())["reason"] == "ZERO_Q"
    amplified = cash_open_confirmation_event(day, day_rows(day, 99, 3), cash_calendar())
    assert amplified["status"] == "confirmed" and amplified["A_direction"] == confirmed["A_direction"]
    extended = cash_open_confirmation_event(day, [*day_rows(day), bar(datetime(2024, 11, 5, 10, 0, tzinfo=JST), day, 1, 999)], cash_calendar())
    assert confirmed == extended


def test_calendar_missing_execution_delay_and_holdout_lock() -> None:
    day = date(2024, 11, 5)
    assert cash_open_confirmation_event(date(2023, 5, 4), day_rows(date(2023, 5, 4)), cash_calendar())["reason"] == "TSE_CASH_MARKET_CLOSED"
    assert cash_open_confirmation_event(day, day_rows(day, missing=datetime(2024, 11, 5, 9, 2, tzinfo=JST)), cash_calendar())["reason"] == "WINDOW_0900_TO_0904_MISSING"
    assert cash_open_confirmation_event(day, day_rows(day), cash_calendar(), day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    instrument, _, _, config = load_project_config(Path("config"))
    signal = datetime(2024, 11, 5, 9, 4, tzinfo=JST)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(day_rows(day), CashOpenConfirmationStrategy("r024", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=26), ExitReason.SIGNAL)
    delayed = engine.run([row for row in day_rows(day) if row.ts_jst != signal + timedelta(minutes=1)], CashOpenConfirmationStrategy("r024-delay", signal, "short")).trades[0]
    assert delayed.entry_ts == signal + timedelta(minutes=2) and delayed.exit_ts == signal + timedelta(minutes=26)
    assert delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
