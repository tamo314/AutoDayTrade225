from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r056 import opening_observation, r056_event


def bars(day: date, *, flat: bool = False) -> list[Bar]:
    start = __import__("datetime").datetime.combine(day, __import__("datetime").time(9), JST)
    result: list[Bar] = []
    for index in range(90):
        close = 100 if flat else 101 + index
        result.append(
            Bar(
                start + timedelta(minutes=index),
                day,
                day,
                Session.DAY,
                "synthetic",
                100 if index == 0 else close - 1,
                close + 1,
                close - 2,
                close,
            )
        )
    return result


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset(), date(2020, 1, 1), date(2025, 12, 31))


def test_path_formula_zero_direction_and_fixed_paths() -> None:
    day = date(2024, 11, 5)
    observation = opening_observation(day, bars(day), 30)
    assert observation["status"] == "valid"
    assert observation["x"] == 30 / 100
    assert observation["e"] == 1.0
    assert opening_observation(day, bars(day, flat=True), 30)["reason"] == "NONPOSITIVE_PATH_LENGTH"
    history = [
        (day - timedelta(days=index + 1), bars(day - timedelta(days=index + 1)), False)
        for index in range(120)
    ]
    event = r056_event(day, bars(day), history, calendar())
    assert event["status"] == "E"
    assert event["planned_times"]["30"]["entry"].endswith("09:30:00+09:00")
    assert event["planned_times"]["30"]["exit30"].endswith("10:00:00+09:00")


def test_prefix_and_scope_locks() -> None:
    day = date(2024, 11, 5)
    history = [
        (day - timedelta(days=index + 1), bars(day - timedelta(days=index + 1)), False)
        for index in range(120)
    ]
    baseline = r056_event(day, bars(day), history, calendar())
    changed = bars(day)
    changed[70] = Bar(changed[70].ts_jst, day, day, Session.DAY, "synthetic", 999, 999, 999, 999)
    assert r056_event(day, changed, history, calendar())["x"] == baseline["x"]
    assert (
        r056_event(date(2025, 7, 1), bars(day), history, calendar())["reason"]
        == "OUTSIDE_DEVELOPMENT"
    )
