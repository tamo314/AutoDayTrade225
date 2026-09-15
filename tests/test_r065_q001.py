from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r065 import boundary_event


def bar(ts: datetime, target: date, session: Session, price: int = 40_000) -> Bar:
    return Bar(ts, target, ts.date(), session, "test", price, price, price, price)


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def rows(c: CalendarClassifier) -> dict[tuple[date, Session], list[Bar]]:
    target = date(2024, 11, 5)
    record = c.exchange_calendar.get(target)
    assert record is not None and record.next_trade_date is not None
    next_target = record.next_trade_date
    n0, d0, n1 = (
        c.session_open(target, Session.NIGHT),
        c.session_open(target, Session.DAY),
        c.session_open(next_target, Session.NIGHT),
    )
    return {
        (target, Session.NIGHT): [bar(n0 + timedelta(minutes=i), target, Session.NIGHT) for i in range(6)],
        (target, Session.DAY): [bar(d0 + timedelta(minutes=i), target, Session.DAY) for i in range(2)],
        (next_target, Session.NIGHT): [bar(n1, next_target, Session.NIGHT)],
    }


def test_r065_schedule_chain_and_common_path() -> None:
    c = classifier()
    event = boundary_event(c, date(2024, 11, 5), rows(c), set())
    assert event["status"] == "E"
    assert event["n0_jst"] < event["d0_jst"] < event["n1_jst"]  # type: ignore[operator]
    assert event["n1_trade_date"] == "2024-11-06"


def test_r065_requires_delayed_path_and_next_night() -> None:
    c = classifier()
    grouped = rows(c)
    grouped[(date(2024, 11, 5), Session.NIGHT)] = grouped[(date(2024, 11, 5), Session.NIGHT)][:5]
    event = boundary_event(c, date(2024, 11, 5), grouped, set())
    assert event["status"] == "skipped"
    assert "N0_DELAY5" in str(event["reason"])
