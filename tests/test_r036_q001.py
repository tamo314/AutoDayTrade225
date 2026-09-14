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
from n225m_bt.research.r036 import prior_day_failed_breakout_event
from n225m_bt.strategies.prior_day_failed_breakout import PriorDayFailedBreakoutStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def cash() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2024, 11, 4)}), date(2020, 1, 1), date(2026, 12, 31))


def reference(day: date) -> list[Bar]:
    current = classifier()
    start, end = current.session_open(day, Session.DAY), normal_session_end(current, day, Session.DAY)
    return [Bar(start + timedelta(minutes=index), day, day, Session.DAY, "synthetic", 100, 110, 90, 100) for index in range((end - start).seconds // 60)]


def current(day: date, *, lower: bool = False, k_close: int = 110, missing: int | None = None) -> list[Bar]:
    start, rows = classifier().session_open(day, Session.DAY), []
    for index in range(180):
        if index == missing:
            continue
        close = 89 if lower and index == 0 else 111 if not lower and index == 0 else 100
        if index == 5:
            close = k_close
        rows.append(Bar(start + timedelta(minutes=index), day, day, Session.DAY, "synthetic", 100, max(110, close), min(90, close), close))
    return rows


def event(day: date, **kwargs: object) -> dict[str, object]:
    prior = date(2024, 11, 1)
    return prior_day_failed_breakout_event(classifier(), cash(), day, current(day, **kwargs), reference(prior))


def test_first_break_fixed_five_minutes_boundaries_and_prefix() -> None:
    day = date(2024, 11, 5)
    rejected = event(day, k_close=110)
    assert (rejected["status"], rejected["breakout_direction"], rejected["k_close"]) == ("rejected", "long", 110)
    assert event(day, k_close=109)["status"] == "rejected"
    assert event(day, k_close=111)["status"] == "persistent"
    later = current(day, k_close=110)
    later[-1] = Bar(later[-1].ts_jst, day, day, Session.DAY, "synthetic", 999, 999, 999, 999)
    unchanged = prior_day_failed_breakout_event(classifier(), cash(), day, later, reference(date(2024, 11, 1)))
    for key in ("PDH_points", "PDL_points", "status", "breakout_ts_jst", "k_ts_jst"):
        assert unchanged[key] == rejected[key]


def test_lower_missing_isolation_and_development_rejections() -> None:
    day = date(2024, 11, 5)
    assert event(day, lower=True, k_close=90)["status"] == "rejected"
    assert event(day, missing=3)["reason"] == "CONFIRMATION_WINDOW_MISSING"
    assert prior_day_failed_breakout_event(classifier(), cash(), date(2025, 7, 1), current(day), reference(date(2024, 11, 1)))["reason"] == "TARGET_OUTSIDE_DEVELOPMENT"
    assert prior_day_failed_breakout_event(classifier(), cash(), day, current(day), reference(date(2024, 11, 1)), prior_day_quarantined=True)["reason"] == "REFERENCE_DAY_SESSION_QUARANTINED"
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]


def test_next_bar_entry_fixed_exit_and_delay() -> None:
    day, signal = date(2024, 11, 5), classifier().session_open(date(2024, 11, 5), Session.DAY) + timedelta(minutes=5)
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(current(day), PriorDayFailedBreakoutStrategy("r036", signal, "short")).trades[0]
    delayed = engine.run(current(day), PriorDayFailedBreakoutStrategy("r036-delay", signal, "short", 1)).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.SHORT, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts) == (signal + timedelta(minutes=2), signal + timedelta(minutes=61))
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
