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
from n225m_bt.research.r058 import R058QNotIdentifiableError, fwl_delta, r058_event


def calendar(first: date, count: int) -> ExchangeCalendar:
    days = [first + timedelta(days=index) for index in range(count)]
    return ExchangeCalendar(
        [
            TradingDay(
                day,
                days[index - 1] if index else None,
                days[index + 1] if index + 1 < count else None,
                days[index - 1] if index else None,
                False,
                "synthetic",
            )
            for index, day in enumerate(days)
        ]
    )


def classifier(value: ExchangeCalendar) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, value)


def day_rows(
    current: CalendarClassifier, day: date, closing_change: int, prior_change: int
) -> list[Bar]:
    endpoint = regime_for_trade_date(current.sessions, day).day.regular_end
    assert endpoint is not None
    regular_end = datetime.combine(day, endpoint, JST)
    result: list[Bar] = []
    for offset, change in ((60, prior_change), (30, closing_change)):
        start = regular_end - timedelta(minutes=offset)
        for index in range(30):
            close = 100 + (change if index == 29 else 0)
            result.append(
                Bar(
                    start + timedelta(minutes=index),
                    day,
                    day,
                    Session.DAY,
                    "synthetic",
                    100,
                    max(100, close),
                    min(100, close),
                    close,
                )
            )
    return result


def night_rows(current: CalendarClassifier, day: date) -> list[Bar]:
    start = current.session_open(day, Session.NIGHT)
    return [
        Bar(
            start + timedelta(minutes=index),
            day,
            start.date(),
            Session.NIGHT,
            "synthetic",
            100,
            100,
            100,
            100,
        )
        for index in range(46)
    ]


def test_causal_q90_equality_prior_placebo_and_prefix() -> None:
    first = date(2024, 11, 5)
    value, current = calendar(first, 122), None
    current = classifier(value)
    days = [first + timedelta(days=index) for index in range(122)]
    grouped = {(day, Session.DAY): day_rows(current, day, 10, 20) for day in days[:-1]}
    target = days[-1]
    grouped[(days[-2], Session.DAY)] = day_rows(current, days[-2], -30, 30)
    grouped[(target, Session.NIGHT)] = night_rows(current, target)
    event = r058_event(current, value, target, grouped, set())
    assert event["status"] == "E"
    assert event["closing_thresholds"]["q90"] == 0.1
    assert event["prior_thresholds"]["q90"] == 0.2
    assert event["closing"]["sign"] == -1 and event["prior"]["sign"] == 1
    equal = r058_event(
        current,
        value,
        target,
        grouped | {(days[-2], Session.DAY): day_rows(current, days[-2], 10, 20)},
        set(),
    )
    assert equal["closing"]["x"] == equal["closing_thresholds"]["q90"]
    assert event == r058_event(current, value, target, grouped, set())


def test_history_and_holdout_lock() -> None:
    day = date(2025, 7, 1)
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
    value = calendar(day, 2)
    assert r058_event(classifier(value), value, day, {}, set())["reason"] == "OUTSIDE_DEVELOPMENT"


def test_fwl_identification_contract() -> None:
    nuisance = np.array([[1.0, -1.0], [1.0, 0.0], [1.0, 1.0], [1.0, 2.0]])
    q = np.array([0.0, 1.0, 0.0, 1.0])
    y = np.array([2.0, 6.0, 3.0, 10.0])
    delta, ss = fwl_delta(q, y, nuisance)
    assert delta == pytest.approx(
        np.linalg.lstsq(np.column_stack((nuisance, q)), y, rcond=None)[0][-1]
    )
    assert ss > 1e-12
    with pytest.raises(R058QNotIdentifiableError):
        fwl_delta(q, y, np.column_stack((nuisance, q)))
