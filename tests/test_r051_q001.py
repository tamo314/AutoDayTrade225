from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pytest

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r051 import r051_event


@lru_cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars(day: date, future: int = 0) -> list[Bar]:
    start = classifier().session_open(day, Session.DAY)
    output: list[Bar] = []
    for i in range(331):
        close = 100 + (5 if i == 20 else 0) + (10 if i == 239 else 0) + (future if i >= 240 else 0)
        output.append(
            Bar(
                start + timedelta(minutes=i),
                day,
                day,
                Session.DAY,
                "synthetic",
                100,
                max(100, close),
                100,
                close,
            )
        )
    return output


def cash() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset(), date(2020, 1, 1), date(2026, 1, 1))


def prior(target: date) -> list[tuple[date, list[Bar], bool]]:
    return [
        (target - timedelta(days=i + 1), bars(target - timedelta(days=i + 1)), False)
        for i in range(120)
    ]


def test_causal_quantile_strict_breakout_and_prefix() -> None:
    target = date(2024, 11, 5)
    event = r051_event(target, bars(target), prior(target), cash())
    assert event["status"] == "E"
    assert event["q40_bps"] == 500
    assert event["breakout_direction"] == "long"
    assert event["planned_entry_jst"].endswith("12:45:00+09:00")
    assert event == r051_event(target, bars(target, future=999), prior(target), cash())


def test_history_boundary_missing_and_holdout_lock() -> None:
    target = date(2024, 11, 5)
    assert (
        r051_event(target, bars(target), prior(target)[:-1], cash())["reason"]
        == "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
    )
    assert (
        r051_event(target, bars(target), prior(target), cash(), quarantined=True)["reason"]
        == "DAY_SESSION_QUARANTINED"
    )
    assert (
        r051_event(date(2025, 7, 1), bars(target), prior(target), cash())["reason"]
        == "OUTSIDE_DEVELOPMENT"
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
