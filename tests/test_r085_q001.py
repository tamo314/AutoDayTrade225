from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r085_day_night_reopen_gap_fade import r085_event, state_ledger


def _classifier(days: list[TradingDay]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar(days))


def _bar(
    target: date, stamp: datetime, session: Session, value: int, *, close: int | None = None
) -> Bar:
    return Bar(
        stamp,
        target,
        stamp.date(),
        session,
        "test",
        value,
        value,
        value,
        value if close is None else close,
    )


def test_state_uses_one_to_one_day_to_next_night_mapping_and_current_excluded_history() -> None:
    d0, d1, n2 = date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)
    days = [
        TradingDay(d0, None, d1, date(2024, 1, 2), False, "test"),
        TradingDay(d1, d0, n2, d0, False, "test"),
        TradingDay(n2, d1, None, d1, False, "test"),
    ]
    classifier = _classifier(days)
    d0_close = classifier.session_close(d0, Session.DAY)
    d1_close = classifier.session_close(d1, Session.DAY)
    n1_open = classifier.session_open(d1, Session.NIGHT)
    n2_open = classifier.session_open(n2, Session.NIGHT)
    bars = {
        (d0, Session.DAY): [_bar(d0, d0_close, Session.DAY, 100)],
        (d1, Session.NIGHT): [_bar(d1, n1_open, Session.NIGHT, 110)],
        (d1, Session.DAY): [_bar(d1, d1_close, Session.DAY, 120)],
        (n2, Session.NIGHT): [_bar(n2, n2_open, Session.NIGHT, 130)],
    }
    state = state_ledger(classifier, [d0, d1, n2], bars, set(), lookback=40)[2]
    assert state["trade_date"] == n2.isoformat()
    assert state["day_trade_date"] == d1.isoformat()
    assert state["night_trade_date"] == n2.isoformat()
    assert state["night_calendar_start_date"] == d1.isoformat()
    assert state["d_points"] == 120
    assert state["n_points"] == 130
    assert state["j_points"] == 10
    assert state["reference_valid_count"] == 1
    assert state["reason"] == "INSUFFICIENT_PRIOR_VALID_J_HISTORY"


def test_extreme_fade_enters_next_open_and_exits_first_eligible_open_at_open_plus_60() -> None:
    night_trade_date = date(2024, 1, 5)
    n_open = datetime(2024, 1, 4, 16, 30, tzinfo=JST)
    state = {
        "trade_date": "2024-01-04",
        "night_trade_date": night_trade_date.isoformat(),
        "state": "E",
        "j_points": 10,
        "n_formal_open_jst": n_open.isoformat(),
    }
    bars = {
        (night_trade_date, Session.NIGHT): [
            _bar(night_trade_date, n_open, Session.NIGHT, 100),
            _bar(night_trade_date, n_open + timedelta(minutes=1), Session.NIGHT, 101),
            _bar(night_trade_date, n_open + timedelta(minutes=59), Session.NIGHT, 102),
            _bar(night_trade_date, n_open + timedelta(minutes=60), Session.NIGHT, 103),
        ]
    }
    event = r085_event(state, bars)
    assert event["status"] == "EXECUTABLE"
    assert event["fade_direction"] == "short"
    assert str(event["entry_open_jst"]).endswith("16:31:00+09:00")
    assert str(event["exit_open_jst"]).endswith("17:30:00+09:00")


def test_outside_development_day_partner_is_a_scheduled_nonprice_exclusion() -> None:
    d0, n1 = date(2020, 12, 30), date(2021, 1, 4)
    days = [
        TradingDay(d0, None, n1, date(2020, 12, 29), False, "test"),
        TradingDay(n1, d0, None, d0, False, "test"),
    ]
    event = state_ledger(_classifier(days), [n1], {}, set())[0]
    assert event["reason"] == "DAY_PARTNER_OUTSIDE_DEVELOPMENT"
    assert event["j_valid"] is False
