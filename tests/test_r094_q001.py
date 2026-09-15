from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r094_cash_path_efficiency_night import (
    cash_state_ledger,
    scheduled_cash_opens,
    scheduled_event,
    scheduled_reference_audit,
)


def _bar(day: date, stamp: datetime, session: Session, open_: int, close: int) -> Bar:
    return Bar(
        stamp,
        day,
        stamp.date(),
        session,
        "synthetic",
        open_,
        max(open_, close),
        min(open_, close),
        close,
    )


def _cash_rows(day: date) -> list[Bar]:
    rows = [_bar(day, stamp, Session.DAY, 100, 100) for stamp in scheduled_cash_opens(day)]
    rows[0] = _bar(day, rows[0].ts_jst, Session.DAY, 100, 101)
    rows[149] = _bar(day, rows[149].ts_jst, Session.DAY, 100, 105)
    rows[150] = _bar(day, rows[150].ts_jst, Session.DAY, 100, 99)
    rows[-1] = _bar(day, rows[-1].ts_jst, Session.DAY, 100, 110)
    return rows


def _classifier(days: list[TradingDay]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar(days))


def test_r094_full_cash_path_counts_lunch_transition_once_and_classifies_h() -> None:
    first = date(2024, 1, 1)
    axis = [first + timedelta(days=index) for index in range(121)]
    bars = {(day, Session.DAY): _cash_rows(day) for day in axis}
    ledger = cash_state_ledger(axis, bars, set())
    observation = ledger[-1]["observation"]
    assert observation["V"] == 24
    assert observation["e"] == 10 / 24
    assert observation["scheduled_cash_bar_count"] == 300
    assert ledger[-1]["state"] == "H"
    assert ledger[-1]["reference_valid_x_e_count"] == 120
    assert all(scheduled_reference_audit(ledger, axis).values())


def test_r094_state_submits_after_cash_close_then_uses_first_and_last_normal_night_open() -> None:
    cash_day, night_day = date(2024, 4, 30), date(2024, 5, 1)
    classifier = _classifier(
        [
            TradingDay(cash_day, date(2024, 4, 29), night_day, date(2024, 4, 29), False, "old"),
            TradingDay(night_day, cash_day, None, cash_day, False, "old"),
        ]
    )
    open_ = classifier.session_open(night_day, Session.NIGHT)
    bars = {
        (night_day, Session.NIGHT): [
            _bar(night_day, open_, Session.NIGHT, 100, 100),
            _bar(
                night_day,
                datetime.combine(night_day, datetime.min.time(), JST).replace(hour=5, minute=58),
                Session.NIGHT,
                100,
                100,
            ),
            _bar(
                night_day,
                datetime.combine(night_day, datetime.min.time(), JST).replace(hour=5, minute=59),
                Session.NIGHT,
                100,
                100,
            ),
        ]
    }
    event = scheduled_event(
        {
            "trade_date": cash_day.isoformat(),
            "state": "H",
            "observation": {
                "cash_end_jst": datetime.combine(cash_day, datetime.min.time(), JST)
                .replace(hour=15)
                .isoformat(),
                "r_sign": 1,
            },
        },
        classifier,
        bars,
        set(),
    )
    assert event["entry_order_submitted"] is True
    assert event["status"] == "EXECUTABLE"
    assert event["continuation_direction"] == "long"
    assert event["submit_jst"] < event["entry_open_jst"]


def test_r094_audit_rejects_current_reference() -> None:
    first = date(2024, 1, 1)
    axis = [first + timedelta(days=index) for index in range(121)]
    ledger = cash_state_ledger(axis, {(day, Session.DAY): _cash_rows(day) for day in axis}, set())
    altered = deepcopy(ledger)
    altered[-1]["reference_trade_dates_oldest_to_newest"][-1] = axis[-1].isoformat()
    assert not scheduled_reference_audit(altered, axis)[
        "references_match_exact_current_excluded_prior_min_i_120"
    ]
