from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r040 import prior_day_compression_acceptance_event
from n225m_bt.strategies.prior_day_compression_breakout import PriorDayCompressionBreakoutStrategy


@lru_cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def day_bars(
    day: date,
    width: int,
    *,
    first_open: int = 100,
    close_break: int | None = None,
    k_close: int | None = None,
    missing: int | None = None,
) -> list[Bar]:
    current = classifier()
    start, end = (
        current.session_open(day, Session.DAY),
        normal_session_end(current, day, Session.DAY),
    )
    rows: list[Bar] = []
    for index in range((end - start).seconds // 60):
        if index == missing:
            continue
        close = 100
        if close_break is not None and index == 0:
            close = close_break
        if k_close is not None and index == 5:
            close = k_close
        rows.append(
            Bar(
                start + timedelta(minutes=index),
                day,
                day,
                Session.DAY,
                "synthetic",
                first_open if index == 0 else 100,
                max(100 + width, close),
                min(100, close),
                close,
            )
        )
    return rows


def event(
    *,
    width: int = 10,
    first_open: int = 100,
    close_break: int = 111,
    k_close: int = 111,
    history_widths: list[int] | None = None,
    missing: int | None = None,
) -> dict[str, object]:
    target, prior = date(2024, 11, 5), date(2024, 11, 1)
    widths = history_widths or [20] * 46 + [10] * 14
    history: list[tuple[date, list[Bar] | None, bool]] = [
        (
            date(2024, 8, 31) - timedelta(days=index),
            day_bars(date(2024, 8, 31) - timedelta(days=index), value),
            False,
        )
        for index, value in enumerate(widths)
    ]
    return prior_day_compression_acceptance_event(
        classifier(),
        target,
        day_bars(
            target,
            20,
            first_open=first_open,
            close_break=close_break,
            k_close=k_close,
            missing=missing,
        ),
        (prior, day_bars(prior, width), False),
        history,
    )


def test_prior_range_rank_strict_tie_acceptance_and_prefix() -> None:
    accepted = event(width=10)
    assert (accepted["status"], accepted["QW_points"], accepted["breakout_direction"]) == (
        "compressed_accepted",
        20,
        "long",
    )
    assert event(width=20, close_break=121, k_close=121)["status"] == "noncompressed_accepted"
    assert event(k_close=110)["status"] == "unaccepted"
    assert event(first_open=111)["reason"] == "TARGET_OPEN_OUTSIDE_PRIOR_RANGE_GAP"
    assert event(missing=3)["reason"] == "CONFIRMATION_WINDOW_MISSING"
    future = event(width=10)
    for key in (
        "PDH_points",
        "PDL_points",
        "W_points",
        "QW_points",
        "compression",
        "breakout_ts_jst",
        "k_ts_jst",
    ):
        assert future[key] == accepted[key]


def test_reference_gates_and_fixed_execution() -> None:
    target, prior = date(2024, 11, 5), date(2024, 11, 1)
    assert (
        prior_day_compression_acceptance_event(
            classifier(), target, day_bars(target, 20), (prior, day_bars(prior, 10), False), []
        )["reason"]
        == "HISTORY_SHORT"
    )
    sparse: list[tuple[date, list[Bar] | None, bool]] = [
        (date(2024, 8, 31) - timedelta(days=index), None, False) for index in range(60)
    ]
    assert (
        prior_day_compression_acceptance_event(
            classifier(), target, day_bars(target, 20), (prior, day_bars(prior, 10), False), sparse
        )["reason"]
        == "INSUFFICIENT_VALID_REFERENCES"
    )
    instrument, _, _, config = load_project_config(Path("config"))
    signal = classifier().session_open(target, Session.DAY) + timedelta(minutes=5)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(
        day_bars(target, 20), PriorDayCompressionBreakoutStrategy("r040", signal, "long")
    ).trades[0]
    delayed = engine.run(
        day_bars(target, 20), PriorDayCompressionBreakoutStrategy("r040-delay", signal, "short", 1)
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    assert (delayed.entry_ts, delayed.exit_ts) == (
        signal + timedelta(minutes=2),
        signal + timedelta(minutes=61),
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
