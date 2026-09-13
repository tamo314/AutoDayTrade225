from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r009 import directional_consistency_event
from n225m_bt.strategies.session_directional_consistency import (
    SessionDirectionalConsistencyStrategy,
)


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


def monotone_closes() -> list[int]:
    return [100 + index for index in range(181)]


def event(closes: list[int], **kwargs: object) -> dict[str, object]:
    current, day = classifier(), date(2024, 11, 5)
    return directional_consistency_event(
        current,
        day,
        Session.DAY,
        bars_for(current.session_open(day, Session.DAY), closes),
        **kwargs,
    )


def test_fixed_61_bars_60_changes_delta_and_half_boundary() -> None:
    result = event(monotone_closes(), require_consistency=True)
    assert result["status"] == "event"
    assert (
        result["required_window_bar_count"],
        result["required_change_count"],
        result["delta_points"],
        result["variation_points"],
        result["sum_changes_points"],
    ) == (61, 60, 60, 60, 60)
    assert result["consistency_lhs_2absdelta"] >= result["consistency_rhs_variation"]

    near = [100] * 181
    for index in range(60, 120):
        near[index] = near[index - 1] + (1 if index <= 90 else -1)
    assert event(near, require_consistency=True)["reason"] == "DIRECTIONAL_CONSISTENCY_BELOW_HALF"
    assert event(near, require_consistency=False)["status"] == "event"


def test_zero_missing_session_and_prefix_guards() -> None:
    flat = [100] * 181
    assert event(flat, require_consistency=True)["reason"] == "ZERO_VARIATION"
    alternating = [100] * 181
    for index in range(60, 120):
        alternating[index] = alternating[index - 1] + (1 if index % 2 else -1)
    assert event(alternating, require_consistency=True)["reason"] == "ZERO_DELTA"

    current, day = classifier(), date(2024, 11, 5)
    start = current.session_open(day, Session.DAY)
    assert directional_consistency_event(
        current, day, Session.DAY, bars_for(start, monotone_closes(), missing={90}), require_consistency=True
    )["reason"] == "WINDOW_MISSING_OR_INELIGIBLE"
    assert directional_consistency_event(
        current, day, Session.NIGHT, bars_for(start, monotone_closes()), require_consistency=True
    )["reason"] == "WINDOW_MISSING_OR_INELIGIBLE"
    before = event(monotone_closes(), require_consistency=True)
    after = event(monotone_closes() + [1_000] * 120, require_consistency=True)
    assert before == after


def test_direction_controls_next_open_fixed_exit_and_delay() -> None:
    current, day = classifier(), date(2024, 11, 5)
    instrument, sessions, _, config = load_project_config(Path("config"))
    start, bars = current.session_open(day, Session.DAY), bars_for(
        current.session_open(day, Session.DAY), monotone_closes()
    )
    signal = start + timedelta(minutes=119)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    trade = engine.run(
        bars,
        SessionDirectionalConsistencyStrategy("r009-a", signal, "A_directional_consistency", "long"),
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    delayed = [bar for bar in bars if bar.ts_jst != signal + timedelta(minutes=1)]
    delayed_trade = engine.run(
        delayed,
        SessionDirectionalConsistencyStrategy("r009-delay", signal, "A_directional_consistency", "long"),
    ).trades[0]
    assert delayed_trade.entry_ts == signal + timedelta(minutes=2)
    assert delayed_trade.exit_ts == signal + timedelta(minutes=61)
    assert len(engine.run(bars, SessionDirectionalConsistencyStrategy("r009-b", signal, "B_always_long", "short")).trades) == 1
    assert engine.run(bars, SessionDirectionalConsistencyStrategy("r009-c", signal, "C_always_short", "long")).trades[0].side is Side.SHORT
