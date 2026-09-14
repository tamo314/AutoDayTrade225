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
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r031 import night_cash_open_confirmation_event
from n225m_bt.strategies.night_cash_open_confirmation import NightCashOpenConfirmationStrategy

DAY = date(2024, 11, 5)


def cash_calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2023, 5, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bar(stamp: datetime, trade_day: date, session: Session, open_: int, close: int) -> Bar:
    return Bar(stamp, trade_day, stamp.date(), session, "synthetic", open_, max(open_, close), min(open_, close), close)


def inputs(s: int = 10, q: int = 10, *, missing_night: bool = False, missing_day: datetime | None = None) -> tuple[list[Bar], list[Bar]]:
    cal = classifier()
    n_start, n_end = cal.session_open(DAY, Session.NIGHT), normal_session_end(cal, DAY, Session.NIGHT)
    night = [bar(n_start + timedelta(minutes=i), DAY, Session.NIGHT, 100, 100 + (s if i == int((n_end - n_start).total_seconds() // 60) - 1 else 0)) for i in range(int((n_end - n_start).total_seconds() // 60))]
    if missing_night:
        night.pop(1)
    start = datetime(DAY.year, DAY.month, DAY.day, 9, tzinfo=JST)
    day = [bar(start + timedelta(minutes=i), DAY, Session.DAY, 100, 100 + (q if i == 4 else 0)) for i in range(66) if start + timedelta(minutes=i) != missing_day]
    return day, night


def test_full_night_q_signs_counterfactuals_zero_and_prefix() -> None:
    cal, (day, night) = classifier(), inputs(10, 10)
    confirmed = night_cash_open_confirmation_event(cal, cash_calendar(), DAY, day, night)
    changed_q = night_cash_open_confirmation_event(cal, cash_calendar(), DAY, *inputs(10, -10))
    changed_s = night_cash_open_confirmation_event(cal, cash_calendar(), DAY, *inputs(-10, 10))
    double_negative = night_cash_open_confirmation_event(cal, cash_calendar(), DAY, *inputs(-10, -10))
    assert confirmed["status"] == "confirmed" and confirmed["A_direction"] == "long"
    assert double_negative["status"] == "confirmed" and double_negative["A_direction"] == "short"
    assert changed_q["status"] == changed_s["status"] == "nonconfirmed"
    assert changed_q["S_points"] == confirmed["S_points"] and changed_s["Q_points"] == confirmed["Q_points"]
    assert night_cash_open_confirmation_event(cal, cash_calendar(), DAY, *inputs(0, 10))["reason"] == "ZERO_S"
    assert night_cash_open_confirmation_event(cal, cash_calendar(), DAY, *inputs(10, 0))["reason"] == "ZERO_Q"
    extended = [*day, bar(datetime(2024, 11, 5, 10, 30, tzinfo=JST), DAY, Session.DAY, 1, 999)]
    assert confirmed == night_cash_open_confirmation_event(cal, cash_calendar(), DAY, extended, night)


def test_schedule_missing_quarantine_execution_delay_and_holdout_lock() -> None:
    cal, (day, night) = classifier(), inputs()
    assert night_cash_open_confirmation_event(cal, cash_calendar(), date(2023, 5, 4), day, night)["reason"] == "TSE_CASH_MARKET_CLOSED"
    assert night_cash_open_confirmation_event(cal, cash_calendar(), DAY, *inputs(missing_night=True))["reason"] == "REFERENCE_FULL_NORMAL_NIGHT_MISSING"
    assert night_cash_open_confirmation_event(cal, cash_calendar(), DAY, *inputs(missing_day=datetime(2024, 11, 5, 9, 2, tzinfo=JST)))["reason"] == "WINDOW_0900_TO_0904_MISSING"
    assert night_cash_open_confirmation_event(cal, cash_calendar(), DAY, day, night, night_quarantined=True)["reason"] == "REFERENCE_NIGHT_SESSION_QUARANTINED"
    instrument, _, _, config = load_project_config(Path("config"))
    signal = datetime(2024, 11, 5, 9, 4, tzinfo=JST)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, cal)
    trade = engine.run(day, NightCashOpenConfirmationStrategy("r031", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    delayed = engine.run([row for row in day if row.ts_jst != signal + timedelta(minutes=1)], NightCashOpenConfirmationStrategy("r031-delay", signal, "short")).trades[0]
    assert delayed.entry_ts == signal + timedelta(minutes=2) and delayed.exit_ts == signal + timedelta(minutes=61)
    assert delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
