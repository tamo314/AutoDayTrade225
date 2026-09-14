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
from n225m_bt.research.r045 import tse_lunch_placebo_event
from n225m_bt.strategies.tse_lunch_placebo_reversal import TSELunchPlaceboReversalStrategy


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2023, 5, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bar(stamp: datetime, day: date, close: int, *, eligible: bool = True) -> Bar:
    return Bar(stamp, day, day, Session.DAY, "synthetic", 10000, max(10000, close), min(10000, close), close, is_eligible=eligible)


def rows(day: date, lunch: int = 20, placebo: int = -20) -> list[Bar]:
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=JST)
    output = [bar(start + timedelta(minutes=index), day, 10000) for index in range(196)]
    by_time = {item.ts_jst: item for item in output}
    for stamp, close in ((datetime(day.year, day.month, day.day, 10, 59, tzinfo=JST), 10000 + placebo), (datetime(day.year, day.month, day.day, 12, 29, tzinfo=JST), 10000 + lunch)):
        output[output.index(by_time[stamp])] = bar(stamp, day, close)
    return output


def test_schedule_windows_common_eligibility_zero_and_prefix() -> None:
    day = date(2024, 11, 5)
    event = tse_lunch_placebo_event(day, rows(day), calendar())
    assert (event["status"], event["delta_minutes"], event["rL_sign"], event["rP_sign"]) == ("eligible", 60, 1, -1)
    assert event["A_planned_entry_jst"].endswith("12:30:00+09:00")
    assert event["A_planned_exit_jst"].endswith("12:45:00+09:00")
    assert event["G_planned_entry_jst"].endswith("11:00:00+09:00")
    assert tse_lunch_placebo_event(day, rows(day, lunch=0), calendar())["reason"] == "ZERO_rL"
    assert tse_lunch_placebo_event(day, rows(day, placebo=0), calendar())["reason"] == "ZERO_rP"
    future = bar(datetime(2024, 11, 5, 14, 0, tzinfo=JST), day, 99999)
    assert event == tse_lunch_placebo_event(day, [*rows(day), future], calendar())


def test_rejections_common_missing_and_holdout_lock() -> None:
    day = date(2024, 11, 5)
    source = rows(day)
    missing = [item for item in source if item.ts_jst != datetime(2024, 11, 5, 10, 15, tzinfo=JST)]
    assert tse_lunch_placebo_event(day, missing, calendar())["reason"] == "COMMON_WINDOWS_OR_FIXED_PATH_MISSING"
    assert tse_lunch_placebo_event(date(2023, 5, 4), rows(date(2023, 5, 4)), calendar())["reason"] == "TSE_CASH_MARKET_CLOSED"
    assert tse_lunch_placebo_event(day, source, calendar(), day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    assert tse_lunch_placebo_event(date(2025, 7, 1), rows(date(2025, 7, 1)), calendar())["reason"] == "OUTSIDE_DEVELOPMENT"
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]


def test_next_open_fixed_exit_and_delay_nonextension() -> None:
    day = date(2024, 11, 5)
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    signal = datetime(2024, 11, 5, 12, 29, tzinfo=JST)
    exit_time = datetime(2024, 11, 5, 12, 45, tzinfo=JST)
    normal = engine.run(rows(day), TSELunchPlaceboReversalStrategy("r045", signal, exit_time, "short")).trades[0]
    delayed = engine.run(rows(day), TSELunchPlaceboReversalStrategy("r045-delay", signal, exit_time, "long", 1)).trades[0]
    assert (normal.side, normal.entry_ts, normal.exit_ts, normal.exit_reason) == (Side.SHORT, signal + timedelta(minutes=1), exit_time, ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts, delayed.exit_reason) == (signal + timedelta(minutes=2), exit_time, ExitReason.SIGNAL)
    assert normal.net_pnl_jpy == normal.gross_pnl_jpy - normal.fees_jpy
