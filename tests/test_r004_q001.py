from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.strategies.failed_breakout import FailedBreakoutReversalStrategy

START = datetime(2024, 11, 5, 8, 45, tzinfo=JST)


def make_bar(
    index: int, *, open_: int = 95, high: int = 100, low: int = 90, close: int = 95
) -> Bar:
    stamp = START + timedelta(minutes=index)
    return Bar(stamp, date(2024, 11, 5), stamp.date(), Session.DAY, "test", open_, high, low, close)


def opening() -> list[Bar]:
    return [make_bar(index) for index in range(30)]


def engine_and_strategy(
    condition: str, bars: list[Bar]
) -> tuple[object, FailedBreakoutReversalStrategy]:
    instrument, sessions, _, config = load_project_config(Path("config"))
    strategy = FailedBreakoutReversalStrategy(
        strategy_id="r004-test",
        session_open=START,
        entry_cutoff=START + timedelta(hours=6),
        condition=condition,  # type: ignore[arg-type]
    )
    result = BacktestEngine(
        instrument.instrument.to_spec(), config, CalendarClassifier(sessions)
    ).run(bars, strategy)
    strategy.finalize(bars)
    return result, strategy


def test_A_uses_return_after_break_not_same_bar_and_next_open() -> None:
    bars = opening()
    bars.extend(
        [
            make_bar(30, high=110, low=95, close=105),  # first upper break b
            make_bar(31, high=108, low=95, close=100),  # first in-range r
            make_bar(32, open_=100, high=104, low=96, close=100),
            make_bar(33, open_=100, high=104, low=96, close=100),
        ]
    )
    result, strategy = engine_and_strategy("A_return_confirmation", bars)
    assert len(result.trades) == 1  # type: ignore[attr-defined]
    trade = result.trades[0]  # type: ignore[index]
    assert trade.side is Side.SHORT
    assert trade.entry_signal_ts == bars[31].ts_jst
    assert trade.entry_ts == bars[32].ts_jst
    assert strategy.event["break_ts_jst"] == bars[30].ts_jst.isoformat()
    assert strategy.event["return_ts_jst"] == bars[31].ts_jst.isoformat()
    assert strategy.event["stop_price"] == 115
    assert strategy.event["target_price"] == 95


def test_return_window_distinguishes_fifth_minute_and_later() -> None:
    base = [*opening(), make_bar(30, high=110, low=95, close=105)]
    exact = base + [make_bar(index, high=108, low=95, close=105) for index in range(31, 35)]
    exact += [make_bar(35, high=108, low=95, close=100), make_bar(36, high=104, low=96, close=100)]
    _, at_five = engine_and_strategy("A_return_confirmation", exact)
    assert at_five.event["return_ts_jst"] == exact[35].ts_jst.isoformat()
    late = base + [make_bar(index, high=108, low=95, close=105) for index in range(31, 36)]
    late += [make_bar(36, high=108, low=95, close=100), make_bar(37)]
    _, after_five = engine_and_strategy("A_return_confirmation", late)
    assert after_five.event["reason"] == "NO_RETURN_WITHIN_5M"


def test_missing_return_and_no_return_skip_A_but_not_B() -> None:
    no_return = [*opening(), make_bar(30, high=110, low=95, close=105)]
    no_return += [make_bar(index, high=108, low=95, close=105) for index in range(31, 38)]
    result_a, strategy_a = engine_and_strategy("A_return_confirmation", no_return)
    result_b, strategy_b = engine_and_strategy("B_immediate_control", no_return)
    assert result_a.trades == ()  # type: ignore[attr-defined]
    assert strategy_a.event["reason"] == "NO_RETURN_WITHIN_5M"
    assert len(result_b.trades) == 1  # type: ignore[attr-defined]
    assert strategy_b.event["event_id"] == strategy_a.event["event_id"]
    missing = [
        *opening(),
        make_bar(30, high=110, low=95, close=105),
        make_bar(32, high=108, low=95, close=100),
    ]
    _, missing_strategy = engine_and_strategy("A_return_confirmation", missing)
    assert missing_strategy.event["reason"] == "RETURN_WINDOW_MISSING"


def test_midpoint_rounding_stop_freeze_gap_and_same_bar_conservative_exit() -> None:
    bars = [make_bar(index, high=102, low=91, close=96) for index in range(30)]
    bars += [
        make_bar(30, high=112, low=96, close=107),
        make_bar(31, high=109, low=96, close=101),
        make_bar(32, open_=120, high=121, low=119, close=120),
    ]
    result, strategy = engine_and_strategy("A_return_confirmation", bars)
    trade = result.trades[0]  # type: ignore[index]
    assert strategy.event["target_price"] == 100  # ceil(96.5 / 5) * 5
    assert strategy.event["stop_price"] == 117  # high[b:r]=112, then +5; no entry-bar high
    assert trade.exit_reason is ExitReason.STOP
    assert trade.exit_reference_price == 120  # gap through the frozen stop exits at open
    assert trade.fees_jpy == 60
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
    simultaneous = [
        *opening(),
        make_bar(30, high=110, low=95, close=105),
        make_bar(31, high=108, low=95, close=100),
        make_bar(32, open_=100, high=120, low=90, close=100),
    ]
    simultaneous_result, _ = engine_and_strategy("A_return_confirmation", simultaneous)
    assert simultaneous_result.trades[0].exit_reason is ExitReason.STOP  # type: ignore[index]


def test_time_exit_one_trade_and_future_prefix_invariance() -> None:
    prefix = [
        *opening(),
        make_bar(30, high=110, low=95, close=105),
        make_bar(31, high=108, low=95, close=100),
    ]
    bars = prefix + [make_bar(index, high=104, low=96, close=100) for index in range(32, 94)]
    result, strategy = engine_and_strategy("A_return_confirmation", bars)
    trade = result.trades[0]  # type: ignore[index]
    assert trade.holding_minutes == 60
    assert trade.exit_reason is ExitReason.SIGNAL
    assert len(result.trades) == 1  # type: ignore[attr-defined]
    changed = bars[:33] + [make_bar(index, high=200, low=1, close=150) for index in range(33, 94)]
    _, changed_strategy = engine_and_strategy("A_return_confirmation", changed)
    assert changed_strategy.event["event_id"] == strategy.event["event_id"]
    assert changed_strategy.event["order_signal_ts_jst"] == strategy.event["order_signal_ts_jst"]


def test_final_holdout_is_not_a_legal_research_partition() -> None:
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("does_not_exist"), "final_holdout")  # type: ignore[arg-type]
