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
from n225m_bt.research.r046 import r046_event, r046_exec_event
from n225m_bt.strategies.r046_fixed_time import R046FixedTimeStrategy


@lru_cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars(day: date, *, high: bool = True, missing: int | None = None) -> list[Bar]:
    start, rows = classifier().session_open(day, Session.DAY), []
    for index in range(155):
        if index == missing:
            continue
        window_index = index if index < 30 else index - 60 if 60 <= index < 90 else None
        if window_index is None:
            close = 100
        elif high:
            close = 100 + window_index + 1
        else:
            close = 110 if window_index % 2 == 0 else 90
        rows.append(
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
    return rows


def prior_days(target: date) -> list[date]:
    output, candidate = [], target - timedelta(days=1)
    while len(output) < 60:
        try:
            classifier().session_open(candidate, Session.DAY)
            output.append(candidate)
        except ValueError:
            pass
        candidate -= timedelta(days=1)
    return output


def history(target: date) -> list[tuple[date, list[Bar] | None, bool]]:
    return [(day, bars(day, high=False), False) for day in prior_days(target)]


def test_exact_q75_equality_is_high_and_both_window_thresholds_are_causal() -> None:
    target, refs = date(2024, 11, 5), history(date(2024, 11, 5))
    tied = r046_event(classifier(), target, bars(target, high=False), refs)
    assert tied["status"] == "E"
    assert tied["open_high_q75"] is True and tied["placebo_high_q75"] is True
    high = r046_event(classifier(), target, bars(target), refs)
    assert (
        high["open_high_q70"] is True
        and high["open_high_q75"] is True
        and high["open_high_q80"] is True
    )
    assert high["open_valid_reference_count"] == high["placebo_valid_reference_count"] == 60


def test_target_is_excluded_and_no_backfill_or_zero_substitution() -> None:
    target, refs = date(2024, 11, 5), history(date(2024, 11, 5))
    assert (
        r046_event(classifier(), target, bars(target), refs[:-1])["reason"]
        == "HISTORY_NOT_EXACTLY_60_TSE_DAYS"
    )
    bad = [*refs]
    bad[:11] = [(day, None, False) for day, _, _ in bad[:11]]
    assert (
        r046_event(classifier(), target, bars(target), bad)["reason"]
        == "INSUFFICIENT_VALID_REFERENCES"
    )
    assert r046_event(classifier(), target, bars(target, high=False), refs)["open_q75_r"] != 0


def test_zero_missing_quarantine_and_development_rejection() -> None:
    target, refs = date(2024, 11, 5), history(date(2024, 11, 5))
    zero = bars(target)
    zero[0] = Bar(zero[0].ts_jst, target, target, Session.DAY, "synthetic", 100, 100, 100, 100)
    for index in range(1, 30):
        zero[index] = Bar(
            zero[index].ts_jst, target, target, Session.DAY, "synthetic", 100, 100, 100, 100
        )
    assert r046_event(classifier(), target, zero, refs)["reason"] == "TARGET_ZERO_R_OR_L"
    assert (
        r046_event(classifier(), target, bars(target, missing=70), refs)["reason"]
        == "COMMON_WINDOWS_OR_EXECUTION_PATH_MISSING"
    )
    assert (
        r046_event(classifier(), target, bars(target), refs, target_quarantined=True)["reason"]
        == "DAY_SESSION_QUARANTINED"
    )
    assert (
        r046_event(classifier(), date(2025, 7, 1), bars(target), refs)["reason"]
        == "OUTSIDE_DEVELOPMENT"
    )


def test_prefix_invariance_and_exact_window_endpoints() -> None:
    target, refs = date(2024, 11, 5), history(date(2024, 11, 5))
    source = r046_event(classifier(), target, bars(target), refs)
    changed = bars(target)
    changed[-1] = Bar(
        changed[-1].ts_jst, target, target, Session.DAY, "synthetic", 999, 999, 999, 999
    )
    assert source == r046_event(classifier(), target, changed, refs)
    start = classifier().session_open(target, Session.DAY)
    assert source["A_entry_jst"] == (start + timedelta(minutes=30)).isoformat()
    assert source["G_entry_jst"] == (start + timedelta(minutes=90)).isoformat()
    assert source["G_exit_jst"] == (start + timedelta(minutes=150)).isoformat()


def test_r1_exec_adapter_ignores_future_placebo_availability() -> None:
    target, refs = date(2024, 11, 5), history(date(2024, 11, 5))
    original = r046_exec_event(classifier(), target, bars(target), refs)
    missing_placebo = r046_exec_event(classifier(), target, bars(target, missing=70), refs)
    assert original["status"] == missing_placebo["status"] == "E_EXEC"
    assert original == missing_placebo
    assert r046_event(classifier(), target, bars(target, missing=70), refs)["status"] == "skipped"


def test_next_open_fixed_exit_delay_and_holdout_lock() -> None:
    day, start = date(2024, 11, 5), classifier().session_open(date(2024, 11, 5), Session.DAY)
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    opening = engine.run(
        bars(day), R046FixedTimeStrategy("r046", start + timedelta(minutes=29), "long")
    ).trades[0]
    delayed = engine.run(
        bars(day), R046FixedTimeStrategy("r046-delay", start + timedelta(minutes=29), "short", 1)
    ).trades[0]
    placebo = engine.run(
        bars(day), R046FixedTimeStrategy("r046-placebo", start + timedelta(minutes=89), "long")
    ).trades[0]
    assert (opening.side, opening.entry_ts, opening.exit_ts, opening.exit_reason) == (
        Side.LONG,
        start + timedelta(minutes=30),
        start + timedelta(minutes=90),
        ExitReason.SIGNAL,
    )
    assert (delayed.entry_ts, delayed.exit_ts) == (
        start + timedelta(minutes=31),
        start + timedelta(minutes=90),
    )
    assert (placebo.entry_ts, placebo.exit_ts) == (
        start + timedelta(minutes=90),
        start + timedelta(minutes=150),
    )
    assert opening.net_pnl_jpy == opening.gross_pnl_jpy - opening.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
