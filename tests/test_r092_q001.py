from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r092_open_gap_cash_close_fade import r092_event, selected

DAY, PRIOR = date(2024, 7, 2), date(2024, 7, 1)


def bar(day: date, stamp: datetime, value: int) -> Bar:
    return Bar(stamp, day, day, Session.DAY, "synthetic", value, value, value, value)


def rows(day: date, *, previous: bool = False) -> list[Bar]:
    close = datetime.combine(day, time(14, 59), JST)
    opening = datetime.combine(day, time(9), JST)
    values = (
        [(close, 100)]
        if previous
        else [
            (opening, 105),
            (opening + timedelta(minutes=1), 106),
            (datetime.combine(day, time(14, 59), JST), 106),
            (datetime.combine(day, time(15), JST), 106),
        ]
    )
    return [bar(day, stamp, value) for stamp, value in values]


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset(), date(2024, 1, 1), date(2025, 12, 31))


def test_r092_entry_is_set_before_exit_availability_and_equality_is_upper() -> None:
    history = [
        (
            date(2024, 7, 1) + timedelta(days=index),
            PRIOR,
            rows(date(2024, 7, 1) + timedelta(days=index)),
            rows(PRIOR, previous=True),
            False,
        )
        for index in range(120)
    ]
    unresolved = r092_event(DAY, rows(DAY)[:-1], PRIOR, rows(PRIOR, previous=True), history, calendar())
    assert unresolved["entry_eligible"] is True
    assert unresolved["completion_status"] == "UNRESOLVED_EXIT_FILL"
    complete = r092_event(DAY, rows(DAY), PRIOR, rows(PRIOR, previous=True), history, calendar())
    assert selected(complete) is True


def test_r092_never_backfills_history() -> None:
    event = r092_event(DAY, rows(DAY), PRIOR, rows(PRIOR, previous=True), [], calendar())
    assert event["reason"] == "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"


def test_r092_r004_quarantined_reference_is_not_an_x_observation() -> None:
    history = [
        (
            date(2024, 7, 1) + timedelta(days=index),
            PRIOR,
            rows(date(2024, 7, 1) + timedelta(days=index)),
            rows(PRIOR, previous=True),
            index == 60,
        )
        for index in range(120)
    ]
    event = r092_event(DAY, rows(DAY), PRIOR, rows(PRIOR, previous=True), history, calendar())
    assert event["reference_valid_x_count"] == 119
    assert event["q75"] == 0.05
