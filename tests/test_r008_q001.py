from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r008 import compression_breakout_event
from n225m_bt.strategies.session_compression_breakout import SessionCompressionBreakoutStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars_for(
    start: datetime,
    closes: list[int],
    *,
    missing: set[int] | None = None,
    session: Session = Session.DAY,
    trade_day: date | None = None,
) -> list[Bar]:
    result: list[Bar] = []
    for index, close in enumerate(closes):
        if index in (missing or set()):
            continue
        stamp, prior = start + timedelta(minutes=index), closes[index - 1] if index else close
        result.append(
            Bar(
                stamp,
                trade_day or stamp.date(),
                stamp.date(),
                session,
                "synthetic",
                prior,
                max(prior, close),
                min(prior, close),
                close,
            )
        )
    return result


def compressed_breakout_closes() -> list[int]:
    closes = [100] * 211
    # W2 and W1 each range 40; W0 range 20, so 4*R0 == R1+R2.
    for index, value in ((0, 100), (1, 140), (30, 100), (31, 140), (60, 100), (61, 120), (90, 125)):
        closes[index] = value
    return closes


def test_windows_exclude_t_and_compression_and_breakout_boundaries() -> None:
    current, day = classifier(), date(2024, 11, 5)
    start, closes = current.session_open(day, Session.DAY), compressed_breakout_closes()
    event = compression_breakout_event(
        current, day, Session.DAY, bars_for(start, closes), require_compression=True
    )
    assert event["status"] == "event" and event["candidate_offset_minutes"] == 90
    assert (
        event["W0_bar_count"],
        event["W1_bar_count"],
        event["W2_bar_count"],
        event["window_bar_count"],
    ) == (30, 30, 30, 91)
    assert event["R0_points"] == 20 and event["R1_points"] == event["R2_points"] == 40
    assert event["compression_lhs_4R0"] == event["compression_rhs_R1_plus_R2"] == 80
    assert event["direction"] == "long"  # close_t == U + one tick qualifies.
    closes[90] = 124
    assert (
        compression_breakout_event(
            current, day, Session.DAY, bars_for(start, closes), require_compression=True
        )["status"]
        == "no_event"
    )


def test_unfiltered_D_can_event_before_A_and_zero_ranges_do_not_qualify() -> None:
    current, day = classifier(), date(2024, 11, 5)
    start, closes = current.session_open(day, Session.DAY), compressed_breakout_closes()
    closes[60], closes[61], closes[90] = 100, 130, 135  # R0=30; 4R0>R1+R2.
    a = compression_breakout_event(
        current, day, Session.DAY, bars_for(start, closes), require_compression=True
    )
    d = compression_breakout_event(
        current, day, Session.DAY, bars_for(start, closes), require_compression=False
    )
    assert (
        a["status"] == "no_event" and d["status"] == "event" and d["candidate_offset_minutes"] == 90
    )
    flat = [100] * 211
    assert (
        compression_breakout_event(
            current, day, Session.DAY, bars_for(start, flat), require_compression=True
        )["candidate_zero_range"]
        > 0
    )


def test_missing_91_bar_window_recovers_and_session_cannot_cross() -> None:
    current, day = classifier(), date(2024, 11, 5)
    start, closes = current.session_open(day, Session.DAY), [100] * 211
    # A later independent 90-minute window is valid after the initial missing
    # bar has aged out of its own required 91-bar window.
    for index, value in (
        (30, 140),
        (31, 100),
        (60, 140),
        (61, 100),
        (90, 120),
        (91, 100),
        (120, 125),
    ):
        closes[index] = value
    event = compression_breakout_event(
        current, day, Session.DAY, bars_for(start, closes, missing={0}), require_compression=True
    )
    assert (
        event["status"] == "event"
        and event["candidate_offset_minutes"] == 120
        and event["candidate_unevaluable"] >= 1
    )
    assert (
        compression_breakout_event(
            current,
            day,
            Session.NIGHT,
            bars_for(start, closes, session=Session.DAY, trade_day=day),
            require_compression=True,
        )["status"]
        == "no_event"
    )


def test_next_open_fixed_exit_delay_and_shared_controls() -> None:
    current, day = classifier(), date(2024, 11, 5)
    instrument, sessions, _, config = load_project_config(Path("config"))
    start, closes = current.session_open(day, Session.DAY), compressed_breakout_closes()
    bars = bars_for(start, closes)
    signal = start + timedelta(minutes=90)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    trade = engine.run(
        bars, SessionCompressionBreakoutStrategy("r008-a", signal, "A_compression_breakout", "long")
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=31),
        ExitReason.SIGNAL,
    )
    delayed = [bar for bar in bars if bar.ts_jst != signal + timedelta(minutes=1)]
    delayed_trade = engine.run(
        delayed,
        SessionCompressionBreakoutStrategy("r008-delay", signal, "A_compression_breakout", "long"),
    ).trades[0]
    assert delayed_trade.entry_ts == signal + timedelta(
        minutes=2
    ) and delayed_trade.exit_ts == signal + timedelta(minutes=31)
    assert (
        engine.run(
            bars, SessionCompressionBreakoutStrategy("r008-b", signal, "B_always_long", "short")
        )
        .trades[0]
        .side
        is Side.LONG
    )
    assert (
        engine.run(
            bars, SessionCompressionBreakoutStrategy("r008-c", signal, "C_always_short", "long")
        )
        .trades[0]
        .side
        is Side.SHORT
    )
