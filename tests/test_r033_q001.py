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
from n225m_bt.research.r033 import opening_range_event
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars(day: date, width: int, *, breakout: int = 1, changed_after: bool = False) -> list[Bar]:
    start = classifier().session_open(day, Session.DAY)
    rows = []
    for index in range(151):
        open_ = 100
        close = 100
        high, low = 100 + width, 100
        if index == 30:
            close = 100 + width + breakout
            high = close
        if changed_after and index > 90:
            close, high = 999, 999
        rows.append(
            Bar(
                start + timedelta(minutes=index),
                day,
                start.date(),
                Session.DAY,
                "synthetic",
                open_,
                high,
                low,
                close,
            )
        )
    return rows


def prior_days(target: date) -> list[date]:
    cal, output, candidate = classifier(), [], target - timedelta(days=1)
    while len(output) < 20:
        try:
            cal.session_open(candidate, Session.DAY)
        except ValueError:
            candidate -= timedelta(days=1)
            continue
        output.append(candidate)
        candidate -= timedelta(days=1)
    return output


def test_fifth_order_statistic_tie_first_breakout_and_prefix() -> None:
    target = date(2024, 11, 5)
    history = [
        (day, bars(day, 10 if index < 5 else 20), False)
        for index, day in enumerate(prior_days(target))
    ]
    event = opening_range_event(classifier(), target, bars(target, 10), history)
    assert event["status"] == "compressed"
    assert event["T_points_fifth_order_statistic"] == 10
    assert event["breakout_direction"] == "long"
    assert event["breakout_ts_jst"] == event["search_start_jst"]
    assert event["breakout_15m_bucket"] == "09:15"
    assert event["R_points"] == 10
    future = opening_range_event(
        classifier(), target, bars(target, 10, changed_after=True), history
    )
    for key in ("T_points_fifth_order_statistic", "compression", "R_points"):
        assert future[key] == event[key]
    assert (
        opening_range_event(classifier(), target, bars(target, 0), history)["reason"]
        == "ZERO_OPENING_RANGE"
    )
    assert (
        opening_range_event(classifier(), target, bars(target, 10), history[:-1])["reason"]
        == "HISTORY_SHORT"
    )
    quarantined = [*history]
    quarantined[0] = (quarantined[0][0], quarantined[0][1], True)
    assert (
        opening_range_event(classifier(), target, bars(target, 10), quarantined)["reason"]
        == "HISTORY_QUARANTINED"
    )


def test_execution_delay_fixed_exit_and_holdout_lock() -> None:
    day = date(2024, 11, 5)
    signal = classifier().session_open(day, Session.DAY) + timedelta(minutes=30)
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(
        bars(day, 10), OpeningRangeCompressionBreakoutStrategy("r033", signal, "long")
    ).trades[0]
    delayed = engine.run(
        bars(day, 10), OpeningRangeCompressionBreakoutStrategy("r033-delay", signal, "short", 1)
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    assert delayed.entry_ts == signal + timedelta(
        minutes=2
    ) and delayed.exit_ts == signal + timedelta(minutes=61)
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
