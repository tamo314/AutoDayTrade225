from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r035 import same_clock_shock_event, scheduled_blocks
from n225m_bt.strategies.same_clock_shock import SameClockShockStrategy


def rows(day: date, change: int = 0) -> list[Bar]:
    output: list[Bar] = []
    for start in scheduled_blocks(day):
        for index in range(5):
            close = 100 + (change if index == 4 else 0)
            output.append(Bar(start + timedelta(minutes=index), day, day, Session.DAY, "synthetic", 100, max(100, close), min(100, close), close))
    # Execution bars for the first block through its absolute exit.
    start = scheduled_blocks(day)[0]
    known = {bar.ts_jst for bar in output}
    for index in range(5, 21):
        stamp = start + timedelta(minutes=index)
        if stamp not in known:
            output.append(Bar(stamp, day, day, Session.DAY, "synthetic", 100, 100, 100, 100))
    return sorted(output, key=lambda item: item.ts_jst)


def test_schedule_and_strict_threshold_history_prefix_and_first_only() -> None:
    assert len(scheduled_blocks(date(2024, 11, 5))) == 46
    assert scheduled_blocks(date(2024, 11, 5))[22].isoformat().endswith("11:20:00+09:00")
    assert scheduled_blocks(date(2024, 11, 5))[23].isoformat().endswith("12:35:00+09:00")
    days = tuple(date(2024, 1, 1) + timedelta(days=index) for index in range(61))
    # Synthetic dates are a supplied schedule; no weekday inference is performed by selector.
    target = days[-1]
    history = {day: rows(day, 10) for day in days[:-1]} | {target: rows(target, 15)}
    event = same_clock_shock_event(target, days, history, set())
    assert event["status"] == "shock"
    assert event["threshold_U_points"] == 10
    assert event["shock_r_points"] == 15
    assert event["fade_direction"] == "short"
    equal = same_clock_shock_event(target, days, history | {target: rows(target, 10)}, set())
    assert equal["status"] == "no_event"  # equality is excluded
    future = rows(target, 15)
    future[-1] = Bar(future[-1].ts_jst, target, target, Session.DAY, "synthetic", 999, 999, 999, 999)
    assert same_clock_shock_event(target, days, history | {target: future}, set())["threshold_U_points"] == 10


def test_missing_isolated_and_insufficient_history_are_not_backfilled() -> None:
    days = tuple(date(2024, 1, 1) + timedelta(days=index) for index in range(61))
    target = days[-1]
    data = {day: rows(day, 10) for day in days[:-1]} | {target: rows(target, -20)}
    del data[days[0]][0]
    event = same_clock_shock_event(target, days, data, set(days[:11]))
    first = cast(list[dict[str, object]], event["blocks"])[0]
    assert first["reference_valid_abs_return_count"] == 49
    assert first["status"] == "insufficient_valid_reference_returns"
    assert same_clock_shock_event(target, days, data, {target})["reason"] == "TARGET_DAY_QUARANTINED"
    insufficient = same_clock_shock_event(days[30], days, data, set())
    assert cast(list[dict[str, object]], insufficient["blocks"])[0]["status"] == "history_outside_or_before_development"


def test_execution_next_open_fixed_exit_delay_and_holdout_lock() -> None:
    day = date(2024, 11, 5)
    signal = datetime(2024, 11, 5, 9, 34, tzinfo=JST)
    instrument, sessions, _, config = load_project_config(Path("config"))
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))
    bars = rows(day, 15)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier)
    trade = engine.run(bars, SameClockShockStrategy("r035", signal, "short")).trades[0]
    delayed = engine.run(bars, SameClockShockStrategy("r035-delay", signal, "short", 1)).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.SHORT, signal + timedelta(minutes=1), signal + timedelta(minutes=16), ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts) == (signal + timedelta(minutes=2), signal + timedelta(minutes=16))
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
