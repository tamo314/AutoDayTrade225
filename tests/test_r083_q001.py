from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r083_overnight_displacement_cash_open_fade import (
    r083_event,
    state_ledger,
)


def _classifier(days: list[TradingDay]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar(days))


def _bar(target: date, stamp: datetime, value: int, *, close: int | None = None) -> Bar:
    final_close = value if close is None else close
    return Bar(stamp, target, stamp.date(), Session.DAY, "test", value, value, value, final_close)


def test_state_is_current_excluded_and_uses_registered_d_and_a() -> None:
    days = [
        TradingDay(date(2021, 1, 4), None, date(2021, 1, 5), None, False, "test"),
        TradingDay(date(2021, 1, 5), date(2021, 1, 4), None, None, False, "test"),
    ]
    prior, target = days[0].trade_date, days[1].trade_date
    bars = {
        (prior, Session.DAY): [_bar(prior, datetime.combine(prior, time(15, 15), JST), 100)],
        (target, Session.DAY): [_bar(target, datetime.combine(target, time(9), JST), 115)],
    }
    state = state_ledger(_classifier(days), [prior, target], bars, set(), lookback=40)[1]
    assert state["d_points"] == 100
    assert state["a_points"] == 115
    assert state["g_points"] == 15
    assert state["reference_valid_count"] == 0
    assert state["reason"] == "INSUFFICIENT_PRIOR_VALID_G_HISTORY"


def test_extreme_fade_uses_first_open_after_a_and_1130_exit() -> None:
    target = date(2024, 1, 4)
    a_stamp = datetime.combine(target, time(9), JST)
    state = {
        "trade_date": target.isoformat(),
        "state": "E",
        "g_points": 10,
        "a_0900_open_jst": a_stamp.isoformat(),
    }
    bars = {
        (target, Session.DAY): [
            _bar(target, a_stamp + timedelta(minutes=offset), 110) for offset in (0, 1, 149, 150)
        ]
    }
    event = r083_event(state, bars)
    assert event["status"] == "EXECUTABLE"
    assert event["fade_direction"] == "short"
    assert str(event["entry_open_jst"]).endswith("09:01:00+09:00")
    assert str(event["exit_open_jst"]).endswith("11:30:00+09:00")


def test_delayed_entry_is_0905_open_and_missing_exit_remains_unknown() -> None:
    target = date(2024, 1, 4)
    a_stamp = datetime.combine(target, time(9), JST)
    state = {
        "trade_date": target.isoformat(),
        "state": "M",
        "g_points": -10,
        "a_0900_open_jst": a_stamp.isoformat(),
    }
    bars = {
        (target, Session.DAY): [
            _bar(target, a_stamp + timedelta(minutes=offset), 100) for offset in (4, 5, 689)
        ]
    }
    event = r083_event(state, bars, entry_delay_minutes=4)
    assert str(event["entry_open_jst"]).endswith("09:05:00+09:00")
    assert event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN"
