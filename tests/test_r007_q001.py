from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r007 import local_shock_event
from n225m_bt.strategies.local_shock_reversal import LocalShockReversalStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars_for(
    start: datetime,
    closes: list[int],
    *,
    session: Session = Session.DAY,
    missing: set[int] | None = None,
    trade_day: date | None = None,
) -> list[Bar]:
    missing = missing or set()
    bars: list[Bar] = []
    for index, close in enumerate(closes):
        if index in missing:
            continue
        stamp = start + timedelta(minutes=index)
        previous = closes[index - 1] if index else close
        bars.append(
            Bar(
                stamp,
                trade_day or stamp.date(),
                stamp.date(),
                session,
                "synthetic",
                previous,
                max(previous, close),
                min(previous, close),
                close,
            )
        )
    return bars


def test_exact_window_even_median_threshold_boundaries_and_first_event() -> None:
    current = classifier()
    day = date(2024, 11, 5)
    start = current.session_open(day, Session.DAY)
    closes = [100] * 181
    # Candidate t=S+61: the 60 prior changes contain 30 zeros and 30 tens,
    # hence the required even median is their arithmetic mean 5, T=20.
    for index in range(31, 61):
        closes[index] = closes[index - 1] + 10
    closes[61] = closes[60] + 20
    closes[62] = closes[61] - 25
    event = local_shock_event(current, day, Session.DAY, bars_for(start, closes))
    assert event["status"] == "event"
    assert event["candidate_offset_minutes"] == 61
    assert event["window_bar_count"] == 62 and event["scale_change_count"] == 60
    assert event["m_t_points"] == 5 and event["threshold_T_points"] == 20
    assert event["q_t_points"] == 20  # equality is included; later shock is ignored.


def test_floor_threshold_zero_scale_and_missing_window_can_recover() -> None:
    current = classifier()
    day = date(2024, 11, 5)
    start = current.session_open(day, Session.DAY)
    exact = [100] * 181
    exact[61] = 120
    exact_event = local_shock_event(current, day, Session.DAY, bars_for(start, exact))
    assert exact_event["m_t_points"] == 0 and exact_event["threshold_T_points"] == 20
    below = [100] * 181
    below[61] = 119
    assert (
        local_shock_event(current, day, Session.DAY, bars_for(start, below))["status"] == "no_event"
    )
    recovered = [100] * 181
    recovered[67] = 120
    event = local_shock_event(current, day, Session.DAY, bars_for(start, recovered, missing={5}))
    assert event["status"] == "event" and event["candidate_offset_minutes"] == 67
    assert event["candidate_unevaluable"] == 6


def test_session_boundary_next_open_fill_fixed_exit_and_controls() -> None:
    current = classifier()
    instrument, sessions, _, config = load_project_config(Path("config"))
    day = date(2024, 11, 5)
    start = current.session_open(day, Session.DAY)
    closes = [100] * 181
    closes[61] = 120
    bars = bars_for(start, closes)
    event = local_shock_event(current, day, Session.DAY, bars)
    t = start + timedelta(minutes=61)
    strategy = LocalShockReversalStrategy("r007-test", t, "A_local_shock_reversal", 20)
    result = BacktestEngine(
        instrument.instrument.to_spec(), config, CalendarClassifier(sessions)
    ).run(bars, strategy)
    trade = result.trades[0]
    assert trade.side is Side.SHORT
    assert trade.entry_ts == t + timedelta(minutes=1)
    assert trade.exit_ts == t + timedelta(minutes=16)
    assert trade.exit_reason is ExitReason.SIGNAL
    delayed = [bar for bar in bars if bar.ts_jst != t + timedelta(minutes=1)]
    delayed_result = BacktestEngine(
        instrument.instrument.to_spec(), config, CalendarClassifier(sessions)
    ).run(delayed, LocalShockReversalStrategy("r007-delay", t, "A_local_shock_reversal", 20))
    assert delayed_result.trades[0].entry_ts == t + timedelta(minutes=2)
    assert delayed_result.trades[0].exit_ts == t + timedelta(minutes=16)
    assert event["X_planned_exit_jst"] == (t + timedelta(minutes=16)).isoformat()
    long = LocalShockReversalStrategy("r007-b", t, "B_always_long", 20)
    short = LocalShockReversalStrategy("r007-c", t, "C_always_short", 20)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    assert engine.run(bars, long).trades[0].side is Side.LONG
    assert engine.run(bars, short).trades[0].side is Side.SHORT


def test_night_trade_date_schedule_and_out_of_session_bars_do_not_fill_window() -> None:
    current = classifier()
    day = date(2024, 11, 5)
    start = current.session_open(day, Session.NIGHT)
    closes = [100] * 181
    closes[61] = 120
    night = bars_for(start, closes, session=Session.NIGHT, trade_day=day)
    assert local_shock_event(current, day, Session.NIGHT, night)["status"] == "event"
    cross_session = bars_for(start, closes, session=Session.DAY, trade_day=day)
    assert local_shock_event(current, day, Session.NIGHT, cross_session)["status"] == "no_event"
