from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r011 import close_mean_reversion_event
from n225m_bt.strategies.session_fixed_time import SessionFixedTimeStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bars_for(start: datetime, closes: list[int], *, missing: set[int] | None = None, session: Session = Session.DAY) -> list[Bar]:
    return [Bar(start + timedelta(minutes=index), (start + timedelta(minutes=index)).date(), (start + timedelta(minutes=index)).date(), session, "synthetic", closes[index - 1] if index else close, max(closes[index - 1] if index else close, close), min(closes[index - 1] if index else close, close), close) for index, close in enumerate(closes) if index not in (missing or set())]


def event(closes: list[int], **kwargs: object) -> dict[str, object]:
    current, day = classifier(), date(2024, 11, 5)
    return close_mean_reversion_event(current, day, Session.DAY, bars_for(current.session_open(day, Session.DAY), closes), **kwargs)


def test_prior_60_only_integer_z_sign_zero_missing_and_prefix() -> None:
    closes = [100] * 119 + [160] + [170] * 61
    result = event(closes)
    assert (result["status"], result["prior_60_close_sum_points"], result["Z_points_times_60"], result["direction"]) == ("event", 6000, 3600, "short")
    negative = [100] * 119 + [40] + [30] * 61
    assert event(negative)["direction"] == "long"
    assert event([100] * 181)["reason"] == "ZERO_Z"
    current, day = classifier(), date(2024, 11, 5)
    start = current.session_open(day, Session.DAY)
    assert close_mean_reversion_event(current, day, Session.DAY, bars_for(closes=closes, start=start, missing={90}))["reason"] == "WINDOW_MISSING_OR_INELIGIBLE"
    assert event(closes) == event(closes + [9999] * 100)


def test_same_endpoint_or_last_change_can_have_opposite_z() -> None:
    # Both paths end at 100 with zero final one-minute change; only their preceding paths differ.
    high_path = [100] * 61 + [160] * 59 + [100] + [100] * 61
    low_path = [100] * 61 + [40] * 59 + [100] + [100] * 61
    assert (event(high_path)["direction"], event(low_path)["direction"]) == ("short", "long")


def test_next_open_fixed_exit_delay_controls_and_fee_accounting() -> None:
    current, day = classifier(), date(2024, 11, 5)
    instrument, sessions, _, config = load_project_config(Path("config"))
    closes = [100] * 119 + [160] + [160] * 61
    bars, signal = bars_for(current.session_open(day, Session.DAY), closes), current.session_open(day, Session.DAY) + timedelta(minutes=119)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    trade = engine.run(bars, SessionFixedTimeStrategy("r011-a", signal, "short")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason, trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy) == (Side.SHORT, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL, True)
    delayed = engine.run([bar for bar in bars if bar.ts_jst != signal + timedelta(minutes=1)], SessionFixedTimeStrategy("r011-delay", signal, "short")).trades[0]
    assert (delayed.entry_ts, delayed.exit_ts) == (signal + timedelta(minutes=2), signal + timedelta(minutes=61))
    assert engine.run(bars, SessionFixedTimeStrategy("r011-long", signal, "long")).trades[0].side is Side.LONG
