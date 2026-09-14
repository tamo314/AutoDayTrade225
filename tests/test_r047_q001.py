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
from n225m_bt.research.r047 import r047_event
from n225m_bt.strategies.r047_fixed_time import R047FixedTimeStrategy


@lru_cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bars(day: date, *, compressed: bool, missing: int | None = None) -> list[Bar]:
    start, rows = classifier().session_open(day, Session.DAY), []
    for index in range(151):
        if index == missing:
            continue
        in_window = index < 30 or 60 <= index < 90
        close = 100 if not in_window else (101 if compressed else (110 if index % 2 else 90))
        if index in (30, 90):
            close = 112
        rows.append(Bar(start + timedelta(minutes=index), day, day, Session.DAY, "synthetic", 100, max(100, close), min(100, close), close))
    return rows


def history(target: date) -> list[tuple[date, list[Bar] | None, bool]]:
    days, candidate = [], target - timedelta(days=1)
    while len(days) < 60:
        try:
            classifier().session_open(candidate, Session.DAY)
            days.append(candidate)
        except ValueError:
            pass
        candidate -= timedelta(days=1)
    return [(day, bars(day, compressed=False), False) for day in days]


def test_q20_q25_q30_tie_first_strict_breakout_and_prefix() -> None:
    target, refs = date(2024, 11, 5), history(date(2024, 11, 5))
    event = r047_event(classifier(), target, bars(target, compressed=False), refs)
    assert event["status"] == "E"
    assert all(event[f"open_compressed_q{q}"] is True for q in (20, 25, 30))
    start = classifier().session_open(target, Session.DAY)
    assert event["A_signal_jst"] == (start + timedelta(minutes=30)).isoformat()
    changed = bars(target, compressed=False)
    changed[-1] = Bar(changed[-1].ts_jst, target, target, Session.DAY, "synthetic", 999, 999, 999, 999)
    assert event == r047_event(classifier(), target, changed, refs)


def test_missing_history_quarantine_and_holdout_are_rejected() -> None:
    target, refs = date(2024, 11, 5), history(date(2024, 11, 5))
    assert r047_event(classifier(), target, bars(target, compressed=True), refs[:-1])["reason"] == "HISTORY_NOT_EXACTLY_60_TSE_DAYS"
    bad = [*refs]
    bad[:11] = [(day, None, False) for day, _, _ in bad[:11]]
    assert r047_event(classifier(), target, bars(target, compressed=True), bad)["reason"] == "INSUFFICIENT_VALID_REFERENCES"
    assert r047_event(classifier(), target, bars(target, compressed=True, missing=44), refs)["reason"] == "COMMON_WINDOWS_OR_EXECUTION_PATH_MISSING"
    assert r047_event(classifier(), target, bars(target, compressed=True), refs, target_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    assert r047_event(classifier(), date(2025, 7, 1), bars(target, compressed=True), refs)["reason"] == "OUTSIDE_DEVELOPMENT"
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]


def test_next_open_fixed_exit_delay_and_accounting() -> None:
    day, start = date(2024, 11, 5), classifier().session_open(date(2024, 11, 5), Session.DAY)
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    opening = engine.run(bars(day, compressed=True), R047FixedTimeStrategy("r047", start + timedelta(minutes=30), start + timedelta(minutes=90), "long")).trades[0]
    delayed = engine.run(bars(day, compressed=True), R047FixedTimeStrategy("r047-delay", start + timedelta(minutes=30), start + timedelta(minutes=90), "short", 1)).trades[0]
    assert (opening.side, opening.entry_ts, opening.exit_ts, opening.exit_reason) == (Side.LONG, start + timedelta(minutes=31), start + timedelta(minutes=90), ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts) == (start + timedelta(minutes=32), start + timedelta(minutes=90))
    assert opening.net_pnl_jpy == opening.gross_pnl_jpy - opening.fees_jpy
