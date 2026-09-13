"""Synthetic regression coverage for EXIT handling after the new-entry cutoff."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Signal, SignalAction
from n225m_bt.research.features import HistorySnapshot, OpeningSummary
from n225m_bt.strategies.base import StrategyContext
from n225m_bt.strategies.compression import CompressionBreakoutStrategy
from n225m_bt.strategies.failed_breakout import FailedBreakoutReversalStrategy
from n225m_bt.strategies.opening import OpeningStrategy
from n225m_bt.strategies.session_end_momentum import SessionEndMomentumStrategy


def make_bar(stamp: datetime, session: Session = Session.DAY) -> Bar:
    return Bar(
        stamp,
        stamp.date(),
        stamp.date(),
        session,
        "synthetic",
        100,
        110,
        95,
        105,
    )


def engine() -> BacktestEngine:
    instrument, sessions, _, config = load_project_config(Path("config"))
    calendar = ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    return BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions, calendar))


@dataclass
class RecordedSignals:
    signals: dict[datetime, Signal]
    callbacks: list[datetime]
    strategy_id: str = "recorded"
    strategy_version: str = "1"

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        self.callbacks.append(bar.ts_jst)
        return self.signals.get(bar.ts_jst)


@pytest.mark.parametrize("exit_offset", [0, 1])
def test_exit_at_or_after_cutoff_is_filled_but_new_entry_remains_blocked(
    exit_offset: int,
) -> None:
    cutoff = datetime(2024, 11, 5, 15, 30, tzinfo=JST)
    entry_signal = cutoff - timedelta(minutes=3)
    exit_signal = cutoff + timedelta(minutes=exit_offset)
    bars = [make_bar(entry_signal + timedelta(minutes=index)) for index in range(14)]
    rejected_entry = cutoff + timedelta(minutes=2)
    strategy = RecordedSignals(
        {
            entry_signal: Signal(SignalAction.ENTER_LONG, entry_signal),
            exit_signal: Signal(SignalAction.EXIT, exit_signal),
            rejected_entry: Signal(SignalAction.ENTER_LONG, rejected_entry),
        },
        [],
    )
    result = engine().run(bars, strategy)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.SIGNAL
    assert trade.exit_signal_ts == exit_signal
    assert trade.exit_ts == exit_signal + timedelta(minutes=1)
    assert rejected_entry not in strategy.callbacks


def test_r005_delays_do_not_extend_its_scheduled_exit() -> None:
    start = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    force = datetime(2024, 11, 5, 15, 40, tzinfo=JST)
    entry = force - timedelta(minutes=55)
    all_bars = [make_bar(start + timedelta(minutes=index)) for index in range(430)]

    delayed_entry_bars = [bar for bar in all_bars if bar.ts_jst != entry]
    delayed_entry = SessionEndMomentumStrategy("delayed-entry", entry, force)
    delayed_entry_result = engine().run(delayed_entry_bars, delayed_entry)
    assert delayed_entry_result.trades[0].entry_ts == entry + timedelta(minutes=1)
    assert delayed_entry_result.trades[0].exit_ts == force
    assert delayed_entry_result.trades[0].exit_reason is ExitReason.SIGNAL

    delayed_exit_bars = [bar for bar in all_bars if bar.ts_jst != force]
    delayed_exit = SessionEndMomentumStrategy("delayed-exit", entry, force)
    delayed_exit_result = engine().run(delayed_exit_bars, delayed_exit)
    assert delayed_exit.scheduled_exit_time == force
    assert delayed_exit_result.trades[0].exit_signal_ts == force - timedelta(minutes=1)
    assert delayed_exit_result.trades[0].exit_ts == force + timedelta(minutes=1)
    assert delayed_exit_result.trades[0].exit_reason is ExitReason.SIGNAL

    canceled_exit_bars = [
        bar for bar in all_bars if not (force <= bar.ts_jst <= force + timedelta(minutes=10))
    ]
    canceled_exit = SessionEndMomentumStrategy("canceled-exit", entry, force)
    canceled_exit_result = engine().run(canceled_exit_bars, canceled_exit)
    assert canceled_exit_result.canceled_orders == 1
    assert canceled_exit_result.trades[0].exit_reason is ExitReason.FORCE_FLAT
    assert canceled_exit_result.trades[0].exit_ts == force + timedelta(minutes=11)


@pytest.mark.parametrize(
    ("trade_date", "session", "expected_close"),
    [
        (date(2021, 9, 17), Session.DAY, datetime(2021, 9, 17, 15, 15, tzinfo=JST)),
        # Night rules select by the actual evening start date: 2021-09-20 is
        # still regime A even though its morning carries trade_date 2021-09-21.
        (date(2021, 9, 21), Session.NIGHT, datetime(2021, 9, 21, 5, 30, tzinfo=JST)),
        (date(2024, 11, 5), Session.DAY, datetime(2024, 11, 5, 15, 45, tzinfo=JST)),
        (date(2024, 11, 5), Session.NIGHT, datetime(2024, 11, 5, 6, 0, tzinfo=JST)),
    ],
)
def test_b_uses_versioned_day_night_schedule_and_trade_date(
    trade_date: date, session: Session, expected_close: datetime
) -> None:
    backtest = engine()
    close = backtest.classifier.session_close(trade_date, session)
    assert close == expected_close
    force = close - timedelta(minutes=5)
    entry, scheduled_exit = force - timedelta(minutes=175), force - timedelta(minutes=120)
    bars = [
        Bar(
            entry - timedelta(minutes=60) + timedelta(minutes=index),
            trade_date,
            (entry - timedelta(minutes=60) + timedelta(minutes=index)).date(),
            session,
            "synthetic",
            100,
            110,
            95,
            105,
        )
        for index in range(116)
    ]
    strategy = SessionEndMomentumStrategy("b-versioned", entry, scheduled_exit)
    result = backtest.run(bars, strategy)
    assert len(result.trades) == 1
    assert result.trades[0].trade_date == trade_date
    assert result.trades[0].exit_signal_ts == force - timedelta(minutes=121)
    assert result.trades[0].exit_ts == force - timedelta(minutes=120)
    assert result.trades[0].exit_reason is ExitReason.SIGNAL


def legacy_callback_gate(has_position: bool, bar: Bar, backtest: BacktestEngine) -> bool:
    """Exact pre-fix predicate, retained only to compare synthetic ledgers."""
    return not backtest._entry_cutoff(bar)


StrategyFactory = Callable[[], object]


def r001_factory() -> tuple[list[Bar], StrategyFactory]:
    start = datetime(2024, 11, 5, 15, 0, tzinfo=JST)
    bars = [make_bar(start + timedelta(minutes=index)) for index in range(41)]
    bars[0] = replace(bars[0], is_session_open=True)
    return bars, lambda: OpeningStrategy("opening_momentum", 2, 29, start)


def r002_factory() -> tuple[list[Bar], StrategyFactory]:
    start = datetime(2024, 11, 5, 15, 0, tzinfo=JST)
    bars = [make_bar(start + timedelta(minutes=index)) for index in range(41)]
    bars[0] = replace(bars[0], is_session_open=True)
    bars[2] = replace(bars[2], close=115, high=115)
    return bars, lambda: OpeningStrategy("opening_breakout", 2, 28, start)


def r003_factory() -> tuple[list[Bar], StrategyFactory]:
    start = datetime(2024, 11, 5, 14, 0, tzinfo=JST)
    bars = [make_bar(start + timedelta(minutes=index)) for index in range(101)]
    bars[30] = replace(bars[30], close=115, high=115)
    summaries = tuple(
        OpeningSummary(
            "2024-10-01",
            Session.DAY,
            start - timedelta(days=index + 1),
            start - timedelta(days=index + 1),
            120,
            100,
            20,
            30,
            30,
            True,
            (),
        )
        for index in range(20)
    )
    history = HistorySnapshot(summaries, "ready", Fraction(20))
    return bars, lambda: CompressionBreakoutStrategy(
        strategy_id="r003", session_open=start, history=history, threshold=Fraction(1), holding_minutes=60
    )


def r004_factory() -> tuple[list[Bar], StrategyFactory]:
    start = datetime(2024, 11, 5, 14, 0, tzinfo=JST)
    bars = [replace(make_bar(start + timedelta(minutes=index)), high=104, low=96, close=100) for index in range(101)]
    bars[:30] = [replace(bar, high=100, low=90, close=95) for bar in bars[:30]]
    bars[30] = replace(bars[30], high=110, low=96, close=105)
    return bars, lambda: FailedBreakoutReversalStrategy(
        strategy_id="r004",
        session_open=start,
        entry_cutoff=datetime(2024, 11, 5, 15, 30, tzinfo=JST),
        condition="B_immediate_control",
        holding_minutes=60,
    )


@pytest.mark.parametrize("factory", [r001_factory, r002_factory, r003_factory, r004_factory])
def test_r001_to_r004_legacy_and_fixed_synthetic_ledgers(factory: Callable[[], tuple[list[Bar], StrategyFactory]]) -> None:
    bars, strategy_factory = factory()
    legacy_engine = engine()
    legacy_engine._strategy_callback_allowed = lambda has_position, bar: legacy_callback_gate(  # type: ignore[method-assign]
        has_position, bar, legacy_engine
    )
    legacy = legacy_engine.run(bars, strategy_factory()).trades[0]
    fixed = engine().run(bars, strategy_factory()).trades[0]

    assert legacy.entry_signal_ts == fixed.entry_signal_ts
    assert legacy.entry_ts == fixed.entry_ts
    assert legacy.entry_reference_price == fixed.entry_reference_price
    assert legacy.entry_fill_price == fixed.entry_fill_price
    assert legacy.exit_reason is ExitReason.FORCE_FLAT
    assert fixed.exit_reason is ExitReason.SIGNAL
    assert legacy.exit_ts == datetime(2024, 11, 5, 15, 40, tzinfo=JST)
    assert fixed.exit_ts == datetime(2024, 11, 5, 15, 31, tzinfo=JST)
    assert fixed.exit_signal_ts == datetime(2024, 11, 5, 15, 30, tzinfo=JST)
    assert legacy.fees_jpy == fixed.fees_jpy == 60
    assert legacy.net_pnl_jpy == legacy.gross_pnl_jpy - legacy.fees_jpy
    assert fixed.net_pnl_jpy == fixed.gross_pnl_jpy - fixed.fees_jpy
