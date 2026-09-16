from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session, Side
from n225m_bt.research.features import HistorySnapshot, OpeningSummary, compression_state
from n225m_bt.research.r003 import load_r003_study
from n225m_bt.strategies.compression import CompressionBreakoutStrategy


def test_compression_state_uses_exact_even_median_and_boundary() -> None:
    assert compression_state(150, [200] * 20, Fraction(3, 4), baseline_sessions=20).allowed
    assert not compression_state(155, [200] * 20, Fraction(3, 4), baseline_sessions=20).allowed
    state = compression_state(100, [100] * 10 + [200] * 10, Fraction(3, 4), baseline_sessions=20)
    assert state is not None
    assert state.baseline_median == 150
    assert state.ratio == Fraction(2, 3)
    assert compression_state(100, [200] * 19, Fraction(3, 4), baseline_sessions=20) is None


def test_r003_breakout_uses_close_then_next_open_and_actual_fill_time() -> None:
    instrument, sessions, _, config = load_project_config(Path("config"))
    start = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    summaries = tuple(
        OpeningSummary("2024-10-01", Session.DAY, start - timedelta(days=index + 1), start - timedelta(days=index + 1), 40300, 40000, 300, 30, 30, True, ())
        for index in range(20)
    )
    strategy = CompressionBreakoutStrategy(
        strategy_id="opening_compression_breakout",
        session_open=start,
        history=HistorySnapshot(summaries, "ready", Fraction(300)),
        threshold=Fraction(3, 4),
        holding_minutes=2,
    )
    bars = []
    for index in range(34):
        close = 40000
        high, low = 40100, 39900
        if index == 30:
            close, high = 40105, 40110
        bars.append(Bar(start + timedelta(minutes=index), date(2024, 11, 5), date(2024, 11, 5), Session.DAY, "test", 40000, high, low, close, is_session_open=index == 0))
    result = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions)).run(bars, strategy)
    assert strategy._armed, strategy.disabled_reason
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side is Side.LONG
    assert trade.entry_signal_ts == bars[30].ts_jst
    assert trade.entry_ts == bars[31].ts_jst
    assert trade.exit_signal_ts == bars[32].ts_jst
    assert trade.exit_ts == bars[33].ts_jst





def test_r003_config_rejects_unknown_nested_key(workspace_tmp: Path) -> None:
    payload = yaml.safe_load(Path("config/strategy_compression.yaml").read_text(encoding="utf-8"))
    payload["control"]["unregistered_parameter"] = True
    path = workspace_tmp / "invalid-r003.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="control keys mismatch"):
        load_r003_study(path)

