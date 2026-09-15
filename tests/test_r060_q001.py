from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r060 import R060Specification, r060_event, r060_exec_event
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def cash() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(
        frozenset({date(2024, 11, 4)}), date(2020, 1, 1), date(2026, 12, 31)
    )


def prior(day: date) -> list[Bar]:
    start, end = (
        classifier().session_open(day, Session.DAY),
        normal_session_end(classifier(), day, Session.DAY),
    )
    return [
        Bar(start + timedelta(minutes=i), day, day, Session.DAY, "synthetic", 100, 110, 90, 100)
        for i in range(int((end - start).total_seconds() // 60))
    ]


def current(
    day: date, *, both: bool = False, close: int = 105, missing: int | None = None
) -> list[Bar]:
    start, output = classifier().session_open(day, Session.DAY), []
    for i in range(90):
        high, low = (115, 85) if both and i == 0 else (115 if i == 0 else 110, 90)
        c = close if i == 29 else 100
        if i != missing:
            output.append(
                Bar(
                    start + timedelta(minutes=i),
                    day,
                    day,
                    Session.DAY,
                    "synthetic",
                    100,
                    max(high, c),
                    min(low, c),
                    c,
                )
            )
    return output


def event(day: date, **kwargs: object) -> dict[str, object]:
    return r060_event(classifier(), cash(), day, current(day, **kwargs), prior(date(2024, 11, 1)))


def test_boundary_classification_and_prefix() -> None:
    day = date(2024, 11, 5)
    assert event(day, close=105)["status"] == "A"
    assert event(day, close=115)["status"] == "D"
    assert event(day, close=110)["reason"] == "NEUTRAL_BAND_AT_OBSERVATION_CLOSE"
    assert event(day, both=True)["reason"] == "BOTH_BOUNDARIES_BREACHED"
    future = current(day, close=105)
    future[-1] = Bar(future[-1].ts_jst, day, day, Session.DAY, "synthetic", 999, 999, 999, 999)
    assert r060_event(classifier(), cash(), day, future, prior(date(2024, 11, 1)))["status"] == "A"


def test_common_e_scope_and_execution() -> None:
    day = date(2024, 11, 5)
    assert (
        event(day, missing=70)["reason"] == "COMMON_E_PATH_MISSING_OR_INELIGIBLE_OR_SEGMENT_CROSS"
    )
    assert (
        r060_event(classifier(), cash(), date(2025, 7, 1), current(day), prior(date(2024, 11, 1)))[
            "reason"
        ]
        == "TARGET_OUTSIDE_DEVELOPMENT"
    )
    assert (
        r060_event(
            classifier(),
            cash(),
            day,
            current(day),
            prior(date(2024, 11, 1)),
            specification=R060Specification(observation_minutes=20),
        )["observation_minutes"]
        == 20
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
    instrument, _, _, config = load_project_config(Path("config"))
    signal = classifier().session_open(day, Session.DAY) + timedelta(minutes=29)
    trade = (
        BacktestEngine(instrument.instrument.to_spec(), config, classifier())
        .run(
            current(day),
            OpeningRangeCompressionBreakoutStrategy("r060", signal, "short", exit_after_minutes=30),
        )
        .trades[0]
    )
    delayed = (
        BacktestEngine(instrument.instrument.to_spec(), config, classifier())
        .run(
            current(day),
            OpeningRangeCompressionBreakoutStrategy("r060-delay", signal, "short", 1, 30),
        )
        .trades[0]
    )
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.SHORT,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=31),
        ExitReason.SIGNAL,
    )
    assert (delayed.entry_ts, delayed.exit_ts) == (
        signal + timedelta(minutes=2),
        signal + timedelta(minutes=31),
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy


def test_r1_exec_adapter_ignores_future_exit_availability() -> None:
    day = date(2024, 11, 5)
    base = r060_exec_event(classifier(), cash(), day, current(day, close=105), prior(date(2024, 11, 1)))
    missing_exit = r060_exec_event(
        classifier(), cash(), day, current(day, close=105, missing=70), prior(date(2024, 11, 1))
    )
    assert base["status"] == "E_EXEC"
    assert base["selection_status"] == "A"
    assert missing_exit == base
    assert event(day, close=105, missing=70)["reason"] == "COMMON_E_PATH_MISSING_OR_INELIGIBLE_OR_SEGMENT_CROSS"
