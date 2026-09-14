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
from n225m_bt.research.r034 import opening_range_rejection_event
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars(
    day: date,
    *,
    k_close: int = 100,
    breakout_index: int | None = 30,
    direction: str = "upper",
    missing_index: int | None = None,
    calendar_date: date | None = None,
) -> list[Bar]:
    start = classifier().session_open(day, Session.DAY)
    rows: list[Bar] = []
    for index in range(180):
        close, high, low = 100, 110, 100
        if index == breakout_index:
            close, high, low = (111, 111, 100) if direction == "upper" else (99, 110, 99)
        if breakout_index is not None and index == breakout_index + 15:
            close, high = k_close, max(110, k_close)
        if index == missing_index:
            continue
        rows.append(
            Bar(
                start + timedelta(minutes=index),
                day,
                calendar_date or start.date(),
                Session.DAY,
                "synthetic",
                100,
                high,
                low,
                close,
            )
        )
    return rows


def test_fixed_k_classification_equality_crossing_and_prefix() -> None:
    target = date(2024, 11, 5)
    rejected = opening_range_rejection_event(classifier(), target, bars(target, k_close=110))
    assert rejected["status"] == "rejected"
    assert rejected["breakout_direction"] == "long"
    assert rejected["k_close"] == 110
    crossed = opening_range_rejection_event(classifier(), target, bars(target, k_close=99))
    assert crossed["status"] == "rejected"
    persistent = opening_range_rejection_event(classifier(), target, bars(target, k_close=111))
    assert persistent["status"] == "persistent"
    changed = bars(target, k_close=110)
    changed[-1] = Bar(
        changed[-1].ts_jst,
        target,
        changed[-1].calendar_date,
        Session.DAY,
        "synthetic",
        999,
        999,
        999,
        999,
    )
    future = opening_range_rejection_event(classifier(), target, changed)
    for key in ("status", "breakout_ts_jst", "k_ts_jst", "k_close"):
        assert future[key] == rejected[key]


def test_confirmation_continuity_scope_and_holdout_lock() -> None:
    target = date(2024, 11, 5)
    missing = opening_range_rejection_event(classifier(), target, bars(target, missing_index=45))
    assert missing["reason"] == "CONFIRMATION_WINDOW_MISSING"
    outside = opening_range_rejection_event(classifier(), date(2025, 7, 1), bars(target))
    assert outside["reason"] == "TARGET_OUTSIDE_DEVELOPMENT"
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]


def test_lower_edge_equal_touch_calendar_and_schedule_versions() -> None:
    old, new = date(2021, 9, 21), date(2024, 11, 5)
    lower = opening_range_rejection_event(
        classifier(),
        old,
        bars(
            old,
            k_close=100,
            direction="lower",
            calendar_date=date(2021, 9, 20),
        ),
    )
    assert lower["status"] == "rejected"
    assert lower["breakout_direction"] == "short"
    assert lower["k_close"] == 100
    persistent = opening_range_rejection_event(
        classifier(), new, bars(new, k_close=99, direction="lower")
    )
    assert persistent["status"] == "persistent"
    edge = opening_range_rejection_event(
        classifier(), new, bars(new, k_close=110, breakout_index=89)
    )
    assert edge["status"] == "rejected"
    assert edge["breakout_ts_jst"] == edge["search_end_jst"]
    assert (
        edge["k_ts_jst"]
        == (classifier().session_open(new, Session.DAY) + timedelta(minutes=104)).isoformat()
    )
    touch = opening_range_rejection_event(classifier(), new, bars(new, breakout_index=None))
    assert touch["reason"] == "NO_CLOSE_BREAKOUT"
    quarantined = opening_range_rejection_event(
        classifier(), new, bars(new), target_quarantined=True
    )
    assert quarantined["reason"] == "TARGET_QUARANTINED"


def test_next_bar_entry_absolute_exit_and_delay() -> None:
    target = date(2024, 11, 5)
    signal = classifier().session_open(target, Session.DAY) + timedelta(minutes=45)
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(
        bars(target), OpeningRangeCompressionBreakoutStrategy("r034", signal, "short")
    ).trades[0]
    delayed = engine.run(
        bars(target), OpeningRangeCompressionBreakoutStrategy("r034-delay", signal, "short", 1)
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.SHORT,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    assert delayed.entry_ts == signal + timedelta(minutes=2)
    assert delayed.exit_ts == signal + timedelta(minutes=61)
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
