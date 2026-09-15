from datetime import date, datetime, time
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r073_official_night_direction_day_reversal import (
    official_night_direction_event,
)


def _classifier(target: date, night_start: date) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar([TradingDay(target, None, None, night_start, False, "test")])
    )


def _bar(target: date, stamp: datetime, session: Session, open_: int) -> Bar:
    return Bar(stamp, target, stamp.date(), session, "test", open_, open_, open_, open_)


def _path(target: date, prior: date, night_open: time) -> list[Bar]:
    return [
        _bar(target, datetime.combine(prior, night_open, JST), Session.NIGHT, 100),
        _bar(target, datetime.combine(target, time(5), JST), Session.NIGHT, 110),
        _bar(target, datetime.combine(target, time(5, 30), JST), Session.NIGHT, 110),
        *[
            _bar(target, datetime.combine(target, stamp, JST), Session.DAY, 110)
            for stamp in (
                time(8, 59),
                time(9),
                time(9, 1),
                time(14, 14),
                time(14, 15),
                time(14, 29),
                time(14, 30),
                time(14, 44),
                time(14, 45),
            )
        ],
    ]


def test_old_regime_uses_official_1630_open_and_reverses() -> None:
    target, prior = date(2024, 11, 1), date(2024, 10, 31)
    event = official_night_direction_event(
        target, _path(target, prior, time(16, 30)), _classifier(target, prior)
    )
    assert event["status"] == "EXECUTABLE"
    assert str(event["feature_start_open_jst"]).endswith("16:30:00+09:00")
    assert event["execution_direction"] == "short"


def test_new_regime_uses_official_1700_open_not_the_old_1630() -> None:
    target, prior = date(2024, 11, 6), date(2024, 11, 5)
    event = official_night_direction_event(
        target, _path(target, prior, time(17)), _classifier(target, prior)
    )
    assert event["status"] == "EXECUTABLE"
    assert str(event["feature_start_open_jst"]).endswith("17:00:00+09:00")
    assert event["execution_direction"] == "short"
    assert (
        official_night_direction_event(
            target, _path(target, prior, time(16, 30)), _classifier(target, prior)
        )["reason"]
        == "MISSING_NIGHT_FEATURE_OPEN"
    )


def test_zero_skips_and_later_exit_missing_is_unknown_not_zero() -> None:
    target, prior = date(2024, 11, 1), date(2024, 10, 31)
    bars = _path(target, prior, time(16, 30))
    bars[2] = _bar(target, bars[2].ts_jst, Session.NIGHT, 100)
    assert (
        official_night_direction_event(target, bars, _classifier(target, prior))["reason"]
        == "ZERO_NIGHT_DIRECTION"
    )
    event = official_night_direction_event(
        target, _path(target, prior, time(16, 30))[:-3], _classifier(target, prior)
    )
    assert event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN"


def test_registered_time_profiles_and_holiday_spanning_night_are_rejected() -> None:
    target, prior = date(2024, 11, 1), date(2024, 10, 31)
    event = official_night_direction_event(
        target,
        _path(target, prior, time(16, 30)),
        _classifier(target, prior),
        night_end=time(5),
        entry=time(9, 1),
        exit_=time(14, 15),
    )
    assert event["status"] == "EXECUTABLE"
    assert str(event["scheduled_exit_open_jst"]).endswith("14:15:00+09:00")
    assert (
        official_night_direction_event(
            date(2024, 11, 5), [], _classifier(date(2024, 11, 5), date(2024, 11, 1))
        )["status"]
        == "NO_SCHEDULED_CROSS_SESSION_WINDOW"
    )
