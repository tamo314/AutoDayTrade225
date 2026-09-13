from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.strategies.session_end_momentum import SessionEndMomentumStrategy


def make_bar(start: datetime, index: int, *, open_: int = 100, close: int = 105) -> Bar:
    stamp = start + timedelta(minutes=index)
    return Bar(stamp, date(2024, 11, 5), stamp.date(), Session.DAY, "test", open_, 110, 95, close)


def run(bars: list[Bar], entry: datetime, exit_: datetime):
    instrument, sessions, _, config = load_project_config(Path("config"))
    strategy = SessionEndMomentumStrategy("r005-test", entry, exit_)
    result = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions)).run(bars, strategy)
    return result, strategy


def test_scheduled_times_not_observed_end_and_next_open_execution() -> None:
    start = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    entry = start + timedelta(minutes=70)
    exit_ = entry + timedelta(minutes=55)
    bars = [make_bar(start, index, open_=100 + index, close=105 + index) for index in range(130)]
    result, strategy = run(bars, entry, exit_)
    trade = result.trades[0]
    assert trade.side is Side.LONG
    assert trade.entry_signal_ts == entry - timedelta(minutes=1)
    assert trade.entry_ts == entry
    assert trade.exit_signal_ts == exit_ - timedelta(minutes=1)
    assert trade.exit_ts == exit_
    assert trade.exit_reason is ExitReason.SIGNAL
    assert strategy.event["direction_points"] == (105 + 69) - (100 + 10)


def test_contiguity_zero_direction_and_future_prefix_invariance() -> None:
    start = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    entry = start + timedelta(minutes=70)
    exit_ = entry + timedelta(minutes=55)
    missing = [make_bar(start, index) for index in range(130) if index != 20]
    _, missing_strategy = run(missing, entry, exit_)
    assert missing_strategy.finalize(missing)["reason"] == "LOOKBACK_WINDOW_NOT_60_CONSECUTIVE_ELIGIBLE_BARS"
    flat = [make_bar(start, index, open_=100, close=100) for index in range(130)]
    flat_result, flat_strategy = run(flat, entry, exit_)
    assert not flat_result.trades
    assert flat_strategy.event["reason"] == "ZERO_DIRECTION"
    first = [make_bar(start, index, open_=100 + index, close=105 + index) for index in range(130)]
    second = first[:70] + [make_bar(start, index, open_=500, close=505) for index in range(70, 130)]
    _, one = run(first, entry, exit_)
    _, two = run(second, entry, exit_)
    assert one.event["direction_points"] == two.event["direction_points"]


def test_force_flat_conflict_and_hold_not_extended_when_entry_delayed() -> None:
    start = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    entry = start + timedelta(minutes=70)
    exit_ = entry + timedelta(minutes=55)
    bars = [make_bar(start, index, open_=100 + index, close=105 + index) for index in range(130)]
    result, _ = run(bars, entry, exit_)
    assert result.trades[0].exit_reason is ExitReason.SIGNAL
    assert result.trades[0].exit_ts == exit_  # scheduled order wins before same-time force-flat
    delayed = [bar for bar in bars if bar.ts_jst != entry]
    delayed_result, _ = run(delayed, entry, exit_)
    assert delayed_result.trades[0].exit_ts == exit_  # F is unchanged, not fill+55


def test_actual_r005_exit_signal_is_allowed_after_new_entry_cutoff() -> None:
    start = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    # Configured close is 15:45: F=15:40 and the new-entry cutoff is 15:30.
    # A held position must still issue its EXIT after F-1 closes.
    force = datetime(2024, 11, 5, 15, 40, tzinfo=JST)
    entry = force - timedelta(minutes=55)
    bars = [make_bar(start, index, open_=100 + index, close=105 + index) for index in range(421)]
    result, strategy = run(bars, entry, force)
    assert result.trades[0].exit_reason is ExitReason.SIGNAL
    assert result.trades[0].exit_signal_ts == force - timedelta(minutes=1)
    assert result.trades[0].exit_ts == force
    assert strategy.finalize(bars)["exit_status"] == "exit_order_issued"


def test_b_exit_precedes_the_new_entry_cutoff() -> None:
    start = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    force = datetime(2024, 11, 5, 15, 40, tzinfo=JST)
    entry = force - timedelta(minutes=175)
    scheduled_exit = entry + timedelta(minutes=55)
    bars = [make_bar(start, index, open_=100 + index, close=105 + index) for index in range(421)]
    result, strategy = run(bars, entry, scheduled_exit)
    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.SIGNAL
    assert trade.exit_signal_ts == force - timedelta(minutes=121)
    assert trade.exit_ts == force - timedelta(minutes=120)
    assert strategy.finalize(bars)["exit_status"] == "exit_order_issued"


def test_schedule_versions_night_trade_date_and_locked_partitions() -> None:
    _, sessions, _, _ = load_project_config(Path("config"))
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))
    assert classifier.session_close(date(2021, 9, 20), Session.DAY).hour == 15
    assert classifier.session_close(date(2024, 11, 5), Session.DAY).minute == 45
    assert classifier.session_close(date(2024, 11, 5), Session.NIGHT).date() == date(2024, 11, 5)
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("does_not_exist"), "final_holdout")  # type: ignore[arg-type]
