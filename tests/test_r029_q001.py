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
from n225m_bt.research.r029 import tse_lunch_confirmation_event
from n225m_bt.strategies.lunch_confirmation import LunchConfirmationStrategy


def cash_calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2024, 11, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def rows(day: date, p: int = 10, q: int = 10) -> list[Bar]:
    start = datetime(day.year, day.month, day.day, 11, 30, tzinfo=JST)
    output: list[Bar] = []
    for index in range(91):
        stamp, close = start + timedelta(minutes=index), 100
        if index == 59:
            close = 100 + p
        if index == 64:
            close = 100 + q
        output.append(Bar(stamp, day, day, Session.DAY, "synthetic", 100, max(100, close), min(100, close), close))
    return output


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def test_exact_lunch_window_sign_groups_zero_counterfactuals_and_prefix() -> None:
    day = date(2024, 11, 5)
    same = tse_lunch_confirmation_event(day, rows(day, 10, 10), cash_calendar())
    negative = tse_lunch_confirmation_event(day, rows(day, -10, -10), cash_calendar())
    changed_q = tse_lunch_confirmation_event(day, rows(day, 10, -10), cash_calendar())
    changed_p = tse_lunch_confirmation_event(day, rows(day, -10, 10), cash_calendar())
    assert (same["status"], same["A_direction"], same["E_planned_entry_jst"], same["X_planned_exit_jst"]) == ("confirmed", "long", "2024-11-05T12:35:00+09:00", "2024-11-05T13:00:00+09:00")
    assert negative["status"] == "confirmed" and negative["A_direction"] == "short"
    assert changed_q["status"] == changed_p["status"] == "nonconfirmed"
    assert changed_q["P_points"] == same["P_points"] and changed_p["Q_points"] == same["Q_points"]
    assert tse_lunch_confirmation_event(day, rows(day, 0, 10), cash_calendar())["reason"] == "ZERO_P"
    assert tse_lunch_confirmation_event(day, rows(day, 10, 0), cash_calendar())["reason"] == "ZERO_Q"
    amplified = tse_lunch_confirmation_event(day, rows(day, 99, 3), cash_calendar())
    assert amplified["status"] == "confirmed" and amplified["A_direction"] == same["A_direction"]
    assert same == tse_lunch_confirmation_event(day, [*rows(day), Bar(datetime(2024, 11, 5, 14, 0, tzinfo=JST), day, day, Session.DAY, "synthetic", 1, 999, 1, 999)], cash_calendar())


def test_calendar_missing_quarantine_boundary_and_holdout_lock() -> None:
    day = date(2024, 11, 5)
    assert tse_lunch_confirmation_event(date(2024, 11, 4), rows(date(2024, 11, 4)), cash_calendar())["reason"] == "TSE_CASH_MARKET_CLOSED"
    assert tse_lunch_confirmation_event(date(2025, 7, 1), rows(date(2025, 7, 1)), cash_calendar())["reason"] == "OUTSIDE_DEVELOPMENT"
    assert tse_lunch_confirmation_event(day, rows(day)[1:], cash_calendar())["reason"] == "WINDOW_1130_TO_1234_MISSING"
    assert tse_lunch_confirmation_event(day, rows(day), cash_calendar(), day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]


def test_next_bar_entry_fixed_exit_no_extension_and_accounting() -> None:
    day, signal = date(2024, 11, 5), datetime(2024, 11, 5, 12, 34, tzinfo=JST)
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(rows(day), LunchConfirmationStrategy("r029", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=26), ExitReason.SIGNAL)
    delayed = engine.run([bar for bar in rows(day) if bar.ts_jst != signal + timedelta(minutes=1)], LunchConfirmationStrategy("r029-delay", signal, "short")).trades[0]
    assert delayed.entry_ts == signal + timedelta(minutes=2) and delayed.exit_ts == signal + timedelta(minutes=26)
    assert delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
