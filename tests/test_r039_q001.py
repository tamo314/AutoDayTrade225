from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r039 import day_close_night_event
from n225m_bt.strategies.day_close_night_reversal import DayCloseNightReversalStrategy


def synthetic_calendar(first: date, count: int) -> ExchangeCalendar:
    days = [first + timedelta(days=index) for index in range(count)]
    return ExchangeCalendar(
        [
            TradingDay(
                trade_date=day,
                previous_trade_date=days[index - 1] if index else None,
                next_trade_date=days[index + 1] if index + 1 < count else None,
                night_calendar_start_date=days[index - 1] if index else None,
                is_holiday_trading_day=False,
                schedule_version="synthetic",
            )
            for index, day in enumerate(days)
        ]
    )


def classifier(calendar: ExchangeCalendar) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, calendar)


def day_rows(
    current: CalendarClassifier, day: date, close_change: int, placebo_change: int
) -> list[Bar]:
    end = current.session_close(day, Session.DAY)
    regular_end_time = current.sessions.regimes[-1].day.regular_end
    assert regular_end_time is not None
    regular_end = datetime.combine(day, regular_end_time, JST)
    output: list[Bar] = []
    for offset, change in ((60, placebo_change), (30, close_change)):
        start = regular_end - timedelta(minutes=offset)
        for index in range(30):
            close = 100 + (change if index == 29 else 0)
            output.append(
                Bar(start + timedelta(minutes=index), day, day, Session.DAY, "synthetic", 100, max(100, close), min(100, close), close)
            )
    output.append(Bar(end, day, day, Session.DAY, "synthetic", 999, 999, 999, 999))
    return output


def night_rows(current: CalendarClassifier, day: date) -> list[Bar]:
    start = current.session_open(day, Session.NIGHT)
    return [Bar(start + timedelta(minutes=index), day, start.date(), Session.NIGHT, "synthetic", 100, 100, 100, 100) for index in range(31)]


def test_strict_q75_placebo_no_backfill_and_prefix() -> None:
    first, calendar = date(2024, 11, 5), synthetic_calendar(date(2024, 11, 5), 62)
    current = classifier(calendar)
    days = [first + timedelta(days=index) for index in range(62)]
    grouped = {(day, Session.DAY): day_rows(current, day, 10, 20) for day in days[:-1]}
    target = days[-1]
    grouped[(days[-2], Session.DAY)] = day_rows(current, days[-2], -30, 30)
    grouped[(target, Session.NIGHT)] = night_rows(current, target)
    event = day_close_night_event(current, calendar, target, grouped, set())
    assert event["C_status"] == "extreme" and event["QC_points"] == 10
    assert event["P_status"] == "extreme" and event["QP_points"] == 20
    equal = day_close_night_event(
        current,
        calendar,
        target,
        grouped | {(days[-2], Session.DAY): day_rows(current, days[-2], 10, 20)},
        set(),
    )
    assert equal["C_status"] == "nonextreme" and equal["P_status"] == "nonextreme"
    assert day_close_night_event(current, calendar, target, grouped, {(days[0], Session.DAY)})[
        "C_status"
    ] == "extreme"
    too_many = {(days[index], Session.DAY) for index in range(11)}
    assert day_close_night_event(current, calendar, target, grouped, too_many)["C_status"] == "insufficient_valid_reference_returns"
    changed = grouped | {(target, Session.NIGHT): [*night_rows(current, target), Bar(current.session_open(target, Session.NIGHT) + timedelta(minutes=60), target, target, Session.NIGHT, "synthetic", 999, 999, 999, 999)]}
    assert day_close_night_event(current, calendar, target, changed, set())["QC_points"] == 10


def test_night_first_open_fixed_exit_delay_and_holdout_lock() -> None:
    instrument, sessions, _, baseline = load_project_config(Path("config"))
    calendar = ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    current = CalendarClassifier(sessions, calendar)
    target = date(2024, 11, 6)
    reference = date(2024, 11, 5)
    regular_end_time = sessions.regimes[-1].day.regular_end
    assert regular_end_time is not None
    regular_end = datetime.combine(reference, regular_end_time, JST)
    signal = regular_end - timedelta(minutes=1)
    open_ = current.session_open(target, Session.NIGHT)
    bars = [
        *[row for row in day_rows(current, reference, 20, 10) if row.ts_jst < regular_end],
        *night_rows(current, target),
    ]
    config = baseline.model_copy(
        update={
            "execution": baseline.execution.model_copy(
                update={"allow_cross_session_pending_order": True, "max_fill_delay_minutes": 180}
            ),
            "risk": baseline.risk.model_copy(
                update={"new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), config, current)
    trade = engine.run(
        bars, DayCloseNightReversalStrategy("r039", signal, open_ + timedelta(minutes=29), "long")
    ).trades[0]
    delayed = engine.run(
        bars,
        DayCloseNightReversalStrategy("r039-delay", open_, open_ + timedelta(minutes=29), "short"),
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, open_, open_ + timedelta(minutes=30), ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts) == (open_ + timedelta(minutes=1), open_ + timedelta(minutes=30))
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
