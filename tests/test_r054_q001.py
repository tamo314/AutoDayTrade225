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
from n225m_bt.research.r054 import R054Specification, r054_event
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars(
    day: date, *, breakout: int = 30, confirm_close: int = 110, missing: int | None = None
) -> list[Bar]:
    start = classifier().session_open(day, Session.DAY)
    output: list[Bar] = []
    for index in range(180):
        close, high, low = 100, 110, 100
        if index == breakout:
            close, high = 111, 111
        if index == breakout + 5:
            close, high = confirm_close, max(110, confirm_close)
        if index != missing:
            output.append(
                Bar(
                    start + timedelta(minutes=index),
                    day,
                    start.date(),
                    Session.DAY,
                    "synthetic",
                    100,
                    high,
                    low,
                    close,
                )
            )
    return output


def test_five_minute_equality_and_prefix_causality() -> None:
    day = date(2024, 11, 5)
    failed = r054_event(classifier(), day, bars(day, confirm_close=110))
    assert failed["status"] == "failed"
    assert (
        failed["confirmation_ts_jst"]
        == (classifier().session_open(day, Session.DAY) + timedelta(minutes=35)).isoformat()
    )
    accepted = r054_event(classifier(), day, bars(day, confirm_close=111))
    assert accepted["status"] == "accepted"
    future = bars(day, confirm_close=110)
    future[-1] = Bar(
        future[-1].ts_jst,
        day,
        future[-1].calendar_date,
        Session.DAY,
        "synthetic",
        999,
        999,
        999,
        999,
    )
    assert r054_event(classifier(), day, future)["status"] == failed["status"]


def test_scope_window_independent_specs_and_execution() -> None:
    day = date(2024, 11, 5)
    assert r054_event(classifier(), day, bars(day, missing=35))["reason"] == (
        "COMMON_E_PATH_MISSING_OR_INELIGIBLE"
    )
    assert (
        r054_event(classifier(), date(2025, 7, 1), bars(day))["reason"]
        == "TARGET_OUTSIDE_DEVELOPMENT"
    )
    assert (
        r054_event(
            classifier(), day, bars(day), specification=R054Specification(opening_minutes=20)
        )["opening_minutes"]
        == 20
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
    instrument, _, _, config = load_project_config(Path("config"))
    signal = classifier().session_open(day, Session.DAY) + timedelta(minutes=35)
    trade = (
        BacktestEngine(instrument.instrument.to_spec(), config, classifier())
        .run(
            bars(day),
            OpeningRangeCompressionBreakoutStrategy("r054", signal, "short", exit_after_minutes=30),
        )
        .trades[0]
    )
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.SHORT,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=31),
        ExitReason.SIGNAL,
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
