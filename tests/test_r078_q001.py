from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r078_cash_first_hour_extreme_fade import r078_event, state_ledger


def _classifier(days: list[TradingDay]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar(days))


def _bar(target: date, stamp: datetime, value: int, *, close: int | None = None) -> Bar:
    final_close = value if close is None else close
    return Bar(stamp, target, stamp.date(), Session.DAY, "test", value, value, value, final_close)


def test_first_hour_r_is_current_excluded_and_uses_0900_open_to_endpoint_close() -> None:
    days = [
        TradingDay(date(2021, 1, 4), None, date(2021, 1, 5), None, False, "test"),
        TradingDay(date(2021, 1, 5), date(2021, 1, 4), None, None, False, "test"),
    ]
    prior, target = days[0].trade_date, days[1].trade_date
    bars = {
        (prior, Session.DAY): [
            _bar(prior, datetime.combine(prior, time(9), JST), 100),
            _bar(prior, datetime.combine(prior, time(9, 59), JST), 100, close=110),
        ],
        (target, Session.DAY): [
            _bar(target, datetime.combine(target, time(9), JST), 100),
            _bar(target, datetime.combine(target, time(9, 59), JST), 100, close=115),
        ],
    }
    state = state_ledger(_classifier(days), [prior, target], bars, set(), lookback=40)[1]
    assert state["r_points"] == 15
    assert state["reference_valid_count"] == 1
    assert state["reason"] == "INSUFFICIENT_PRIOR_VALID_R_HISTORY"


def test_extreme_direction_is_faded_after_0959_close_to_1430_open() -> None:
    target = date(2024, 1, 4)
    endpoint = datetime.combine(target, time(9, 59), JST)
    state = {
        "trade_date": target.isoformat(),
        "state": "E",
        "r_points": 10,
        "signal_endpoint_jst": endpoint.isoformat(),
    }
    bars = {
        (target, Session.DAY): [
            _bar(target, endpoint + timedelta(minutes=offset), 110) for offset in (0, 1, 270, 271)
        ]
    }
    event = r078_event(state, bars)
    assert event["status"] == "EXECUTABLE"
    assert event["fade_direction"] == "short"
    assert str(event["entry_open_jst"]).endswith("10:00:00+09:00")
    assert str(event["exit_open_jst"]).endswith("14:30:00+09:00")


def test_missing_exit_is_unknown_not_a_retroactive_cancel() -> None:
    target = date(2024, 1, 4)
    endpoint = datetime.combine(target, time(9, 59), JST)
    state = {
        "trade_date": target.isoformat(),
        "state": "M",
        "r_points": -10,
        "signal_endpoint_jst": endpoint.isoformat(),
    }
    bars = {
        (target, Session.DAY): [
            _bar(target, endpoint + timedelta(minutes=offset), 100) for offset in (0, 1, 270)
        ]
    }
    assert r078_event(state, bars)["status"] == "ENTRY_FILLED_EXIT_UNKNOWN"
