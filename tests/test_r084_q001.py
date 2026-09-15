from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r084_morning_compression_lunch_breakout import (
    breakout_event,
    nearest_rank,
    state_ledger,
)


def _classifier(days: list[TradingDay]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar(days))


def _bar(
    target: date, stamp: datetime, value: int, *, high: int | None = None, low: int | None = None,
    close: int | None = None,
) -> Bar:
    return Bar(
        stamp, target, stamp.date(), Session.DAY, "test", value, high or value, low or value,
        value if close is None else close,
    )


def test_nearest_rank_is_registered_and_current_excluded_state_needs_60_prior_valid_w() -> None:
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 30) == 2.0
    target = date(2024, 1, 4)
    days = [TradingDay(target, None, None, None, False, "test")]
    morning = [
        _bar(target, datetime.combine(target, time(9), JST) + timedelta(minutes=index), 100)
        for index in range(150)
    ]
    state = state_ledger(_classifier(days), [target], {(target, Session.DAY): morning}, set())[0]
    assert state["w_valid"] is True
    assert state["reference_valid_count"] == 0
    assert state["state"] == "NONE"
    assert state["reason"] == "INSUFFICIENT_PRIOR_VALID_W_HISTORY"


def test_first_strict_breakout_enters_at_next_eligible_open_and_exits_1430() -> None:
    target = date(2024, 1, 4)
    start = datetime.combine(target, time(12, 30), JST)
    state = {
        "trade_date": target.isoformat(), "state": "C", "morning_start_jst": datetime.combine(target, time(9), JST).isoformat(),
        "h_0900_1129_points": 105, "l_0900_1129_points": 95,
    }
    bars = {(target, Session.DAY): [
        _bar(target, start, 100, close=105),
        _bar(target, start + timedelta(minutes=1), 106, close=106),
        _bar(target, start + timedelta(minutes=2), 107),
        _bar(target, datetime.combine(target, time(14, 29), JST), 110),
        _bar(target, datetime.combine(target, time(14, 30), JST), 111),
    ]}
    event = breakout_event(state, bars)
    assert event["status"] == "EXECUTABLE"
    assert event["breakout_direction"] == "long"
    assert str(event["breakout_close_jst"]).endswith("12:31:00+09:00")
    assert str(event["entry_signal_jst"]).endswith("12:31:00+09:00")
    assert str(event["entry_open_jst"]).endswith("12:32:00+09:00")
    assert str(event["exit_open_jst"]).endswith("14:30:00+09:00")


def test_missing_breakout_window_bar_is_not_silently_skipped() -> None:
    target = date(2024, 1, 4)
    state = {
        "trade_date": target.isoformat(), "state": "M", "morning_start_jst": datetime.combine(target, time(9), JST).isoformat(),
        "h_0900_1129_points": 105, "l_0900_1129_points": 95,
    }
    event = breakout_event(state, {(target, Session.DAY): []})
    assert event["status"] == "SKIPPED"
    assert event["reason"] == "BREAKOUT_WINDOW_BAR_MISSING_OR_INELIGIBLE"
