from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r093_cash_night_reversal import cash_state_ledger, scheduled_event


def _classifier(days: list[TradingDay]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar(days))


def _bar(day: date, stamp: datetime, session: Session, value: int, *, close: int | None = None) -> Bar:
    return Bar(
        stamp,
        day,
        stamp.date(),
        session,
        "synthetic",
        value,
        value,
        value,
        value if close is None else close,
    )


def test_r093_current_excluded_q75_equality_submits_before_night_availability() -> None:
    first = date(2024, 1, 1)
    axis = [first + timedelta(days=index) for index in range(121)]
    cash_day, night_day = axis[-1], axis[-1] + timedelta(days=1)
    calendar_days = [
        TradingDay(
            cash_day,
            axis[-2],
            night_day,
            cash_day - timedelta(days=1),
            False,
            "old",
        ),
        TradingDay(night_day, cash_day, None, cash_day, False, "old"),
    ]
    classifier = _classifier(calendar_days)
    bars: dict[tuple[date, Session], list[Bar]] = {}
    for target in axis:
        bars[(target, Session.DAY)] = [
            _bar(target, datetime.combine(target, time(9), JST), Session.DAY, 100),
            _bar(target, datetime.combine(target, time(14, 59), JST), Session.DAY, 105, close=105),
        ]
    night_open = classifier.session_open(night_day, Session.NIGHT)
    bars[(night_day, Session.NIGHT)] = [
        _bar(night_day, night_open, Session.NIGHT, 106),
            _bar(night_day, datetime.combine(night_day, time(5, 58), JST), Session.NIGHT, 105),
            _bar(night_day, datetime.combine(night_day, time(5, 59), JST), Session.NIGHT, 104),
    ]
    state = cash_state_ledger(axis, bars, set())[-1]
    assert state["state"] == "E"
    assert state["reference_valid_x_count"] == 120
    assert cash_day.isoformat() not in state["reference_trade_dates_oldest_to_newest"]
    event = scheduled_event(state, classifier, bars, set())
    assert event["entry_order_submitted"] is True
    assert event["status"] == "EXECUTABLE"
    assert event["reversal_direction"] == "short"
    assert event["entry_open_jst"] == night_open.isoformat()
    assert str(event["exit_open_jst"]).endswith("05:59:00+09:00")


def test_r093_missing_night_entry_cancels_after_e_order_without_reselecting_cash_signal() -> None:
    cash_day, night_day = date(2024, 1, 4), date(2024, 1, 5)
    classifier = _classifier(
        [
            TradingDay(cash_day, None, night_day, date(2024, 1, 3), False, "old"),
            TradingDay(night_day, cash_day, None, cash_day, False, "old"),
        ]
    )
    state = {
        "trade_date": cash_day.isoformat(),
        "state": "E",
        "observation": {
            "cash_end_jst": datetime.combine(cash_day, time(15), JST).isoformat(),
            "r_sign": 1,
        },
    }
    event = scheduled_event(state, classifier, {}, set())
    assert event["entry_order_submitted"] is True
    assert event["entry_eligible"] is True
    assert event["status"] == "ENTRY_CANCELLED"
    assert event["reason"] == "FIRST_NORMAL_NIGHT_ENTRY_OPEN_MISSING_OR_INELIGIBLE"
