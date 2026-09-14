from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.calendar.session_rules import regime_for_trade_date
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r059 import R059QNotIdentifiableError, fwl_delta, r059_event


def calendar(first: date, count: int) -> ExchangeCalendar:
    days = [first + timedelta(days=index) for index in range(count)]
    return ExchangeCalendar([
        TradingDay(day, days[i - 1] if i else None, days[i + 1] if i + 1 < count else None,
                   days[i - 1] if i else None, False, "synthetic")
        for i, day in enumerate(days)
    ])


def classifier(value: ExchangeCalendar) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, value)


def bars(current: CalendarClassifier, day: date, close: int = 100, opening: int = 110) -> tuple[list[Bar], list[Bar]]:
    endpoint = regime_for_trade_date(current.sessions, day).day.regular_end
    assert endpoint is not None
    end = datetime.combine(day, endpoint, JST)
    day_rows = [Bar(end - timedelta(minutes=30) + timedelta(minutes=i), day, day, Session.DAY,
                    "synthetic", 100, close if i == 29 else 100, 100, close if i == 29 else 100)
                for i in range(30)]
    night_start = current.session_open(day, Session.NIGHT)
    night_rows = [Bar(night_start + timedelta(minutes=i), day, night_start.date(), Session.NIGHT,
                      "synthetic", opening + i, opening + i, opening + i, opening + i)
                  for i in range(46)]
    return day_rows, night_rows


def test_gap_is_causal_next_bar_entry_equality_and_prefix() -> None:
    first = date(2024, 11, 5)
    cal = calendar(first, 122)
    current = classifier(cal)
    days = [first + timedelta(days=i) for i in range(122)]
    grouped: dict[tuple[date, Session], list[Bar]] = {}
    for index, day in enumerate(days):
        if index:
            drows, nrows = bars(current, day)
        else:
            template, _ = bars(current, days[1])
            drows = [
                Bar(row.ts_jst - timedelta(days=1), day, day, Session.DAY, "synthetic",
                    row.open, row.high, row.low, row.close)
                for row in template
            ]
            nrows = []
        grouped[day, Session.DAY], grouped[day, Session.NIGHT] = drows, nrows
    event = r059_event(current, cal, days[-1], grouped, set())
    assert event["status"] == "E"
    assert event["x"] == pytest.approx(0.1) and event["q90"] == pytest.approx(0.1)
    assert event["planned_entry_jst"] == (
        current.session_open(days[-1], Session.NIGHT) + timedelta(minutes=1)
    ).isoformat()
    changed = grouped | {(days[-1], Session.NIGHT): [*grouped[days[-1], Session.NIGHT], Bar(
        current.session_open(days[-1], Session.NIGHT) + timedelta(minutes=99), days[-1], days[-1],
        Session.NIGHT, "synthetic", 999, 999, 999, 999)]}
    assert event == r059_event(current, cal, days[-1], changed, set())


def test_locks_and_fwl_identification() -> None:
    cal = calendar(date(2024, 1, 1), 2)
    current = classifier(cal)
    assert r059_event(current, cal, date(2025, 7, 1), {}, set())["reason"] == "OUTSIDE_DEVELOPMENT"
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
    nuisance = np.array([[1.0, -1.0], [1.0, 0.0], [1.0, 1.0], [1.0, 2.0]])
    q, y = np.array([0.0, 1.0, 0.0, 1.0]), np.array([1.0, 5.0, 2.0, 7.0])
    expected = np.linalg.lstsq(np.column_stack((nuisance[:, 0], q, nuisance[:, 1])), y, rcond=None)[0][1]
    actual, ss = fwl_delta(q, y, nuisance)
    assert actual == pytest.approx(expected) and ss > 1e-12
    with pytest.raises(R059QNotIdentifiableError):
        fwl_delta(q, y, np.column_stack((nuisance, q)))
