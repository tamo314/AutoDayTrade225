from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r038 import tse_lunch_extreme_event
from n225m_bt.strategies.lunch_extreme_follow import LunchExtremeFollowStrategy


def rows(day: date, lunch: int, preclose: int) -> list[Bar]:
    output: list[Bar] = []
    for start, change in ((time(10, 30), preclose), (time(11, 30), lunch)):
        base = datetime.combine(day, start, JST)
        for index in range(60):
            close = 100 + (change if index == 59 else 0)
            output.append(
                Bar(base + timedelta(minutes=index), day, day, Session.DAY, "synthetic", 100, max(100, close), min(100, close), close)
            )
    for stamp in (
        datetime.combine(day, time(12, 30), JST) + timedelta(minutes=index)
        for index in range(31)
    ):
        if stamp not in {bar.ts_jst for bar in output}:
            output.append(Bar(stamp, day, day, Session.DAY, "synthetic", 100, 100, 100, 100))
    return sorted(output, key=lambda item: item.ts_jst)


def test_strict_q75_independent_placebo_and_prefix() -> None:
    days = tuple(date(2024, 1, 1) + timedelta(days=index) for index in range(61))
    target = days[-1]
    history = {day: rows(day, 10, 20) for day in days[:-1]}
    event = tse_lunch_extreme_event(target, days, history | {target: rows(target, 15, -30)}, set())
    assert event["L_status"] == "extreme" and event["QL_points"] == 10
    assert event["P_status"] == "extreme" and event["QP_points"] == 20
    equal = tse_lunch_extreme_event(target, days, history | {target: rows(target, 10, 20)}, set())
    assert equal["L_status"] == "nonextreme" and equal["P_status"] == "nonextreme"
    changed_future = rows(target, 15, -30)
    changed_future.append(Bar(datetime(2024, 3, 1, 14, 0, tzinfo=JST), target, target, Session.DAY, "synthetic", 999, 999, 999, 999))
    assert tse_lunch_extreme_event(target, days, history | {target: changed_future}, set())["QL_points"] == 10


def test_no_backfill_missing_zero_quarantine_and_holdout_lock() -> None:
    days = tuple(date(2024, 1, 1) + timedelta(days=index) for index in range(61))
    target = days[-1]
    history = {day: rows(day, 10, 10) for day in days[:-1]}
    history[days[0]] = [
        row for row in history[days[0]] if row.ts_jst != datetime(2024, 1, 1, 11, 30, tzinfo=JST)
    ]
    event = tse_lunch_extreme_event(target, days, history | {target: rows(target, -30, 30)}, set(days[1:11]))
    assert event["L_reference_valid_abs_return_count"] == 49
    assert event["L_status"] == "insufficient_valid_reference_returns"
    assert tse_lunch_extreme_event(days[30], days, history, set())["L_status"] == "history_short_or_outside_development"
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]


def test_next_open_fixed_exit_delay_and_placebo_path() -> None:
    day = date(2024, 11, 5)
    instrument, sessions, _, config = load_project_config(Path("config"))
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier)
    lunch_signal = datetime(2024, 11, 5, 12, 29, tzinfo=JST)
    placebo_signal = datetime(2024, 11, 5, 11, 29, tzinfo=JST)
    data = rows(day, 20, -20)
    lunch = engine.run(data, LunchExtremeFollowStrategy("r038", lunch_signal, "long")).trades[0]
    delayed = engine.run(data, LunchExtremeFollowStrategy("r038-delay", lunch_signal, "short", 1)).trades[0]
    placebo = engine.run(data, LunchExtremeFollowStrategy("r038-g", placebo_signal, "short")).trades[0]
    assert (lunch.side, lunch.entry_ts, lunch.exit_ts, lunch.exit_reason) == (Side.LONG, lunch_signal + timedelta(minutes=1), lunch_signal + timedelta(minutes=31), ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts) == (lunch_signal + timedelta(minutes=2), lunch_signal + timedelta(minutes=31))
    assert (placebo.entry_ts, placebo.exit_ts) == (placebo_signal + timedelta(minutes=1), placebo_signal + timedelta(minutes=31))
    assert lunch.net_pnl_jpy == lunch.gross_pnl_jpy - lunch.fees_jpy
