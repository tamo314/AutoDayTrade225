from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r013 import previous_same_session_event
from n225m_bt.strategies.previous_session_direction import PreviousSessionDirectionStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bars(start: datetime, trade_day: date, session: Session, opens: list[int]) -> list[Bar]:
    return [
        Bar(
            start + timedelta(minutes=index), trade_day, (start + timedelta(minutes=index)).date(),
            session, "synthetic", price, price, price, price,
        )
        for index, price in enumerate(opens)
    ]


def reference_and_current(
    session: Session = Session.DAY, *, u: int = 10, v: int = -10
) -> tuple[CalendarClassifier, date, list[Bar], list[Bar]]:
    current, trade_day = classifier(), date(2024, 11, 5)
    prior = current.exchange_calendar.get(trade_day)
    assert prior is not None and prior.previous_trade_date is not None
    P = current.session_open(prior.previous_trade_date, session)
    S = current.session_open(trade_day, session)
    # open(p+1), open(p+61), open(p+121) establish U and V exactly.
    reference = bars(P + timedelta(minutes=1), prior.previous_trade_date, session, [100] * 121)
    reference[60] = Bar(P + timedelta(minutes=61), prior.previous_trade_date, P.date(), session, "synthetic", 100 + u, 100 + u, 100 + u, 100 + u)
    reference[120] = Bar(P + timedelta(minutes=121), prior.previous_trade_date, P.date(), session, "synthetic", 100 + u + v, 100 + u + v, 100 + u + v, 100 + u + v)
    return current, trade_day, bars(S, trade_day, session, [100] * 62), reference


def test_previous_scheduled_session_day_night_weekend_and_u_v_signs() -> None:
    for session in (Session.DAY, Session.NIGHT):
        current, trade_day, today, prior = reference_and_current(session, u=10, v=-10)
        event = previous_same_session_event(current, trade_day, session, today, prior)
        assert (event["status"], event["U_points"], event["V_points"], event["A_direction"], event["D_direction"]) == ("eligible", 10, -10, "long", "short")
        assert event["previous_same_session_trade_date"] == "2024-11-01"
        assert datetime.fromisoformat(str(event["reference_window_end_jst"])) < datetime.fromisoformat(str(event["S_current_open_bar_start_jst"]))
    for u, v, a, d in ((10, 10, "long", "long"), (-10, 10, "short", "long"), (-10, -10, "short", "short")):
        current, trade_day, today, prior = reference_and_current(u=u, v=v)
        event = previous_same_session_event(current, trade_day, Session.DAY, today, prior)
        assert (event["A_direction"], event["D_direction"]) == (a, d)


def test_zero_missing_ineligible_quarantine_and_no_skip_over_reference() -> None:
    current, trade_day, today, prior = reference_and_current(u=0, v=10)
    assert previous_same_session_event(current, trade_day, Session.DAY, today, prior)["reason"] == "ZERO_U"
    current, trade_day, today, prior = reference_and_current(u=10, v=0)
    assert previous_same_session_event(current, trade_day, Session.DAY, today, prior)["reason"] == "ZERO_V"
    assert previous_same_session_event(current, trade_day, Session.DAY, today, prior[:-1])["reason"] == "REFERENCE_WINDOW_MISSING"
    assert previous_same_session_event(current, trade_day, Session.DAY, today, prior, reference_quarantined=True)["reason"] == "REFERENCE_SESSION_QUARANTINED"
    assert previous_same_session_event(current, trade_day, Session.DAY, today, None)["reason"] == "REFERENCE_SESSION_MISSING"
    assert previous_same_session_event(current, date(2021, 1, 4), Session.DAY, [], None)["reason"] == "REFERENCE_OUTSIDE_DEVELOPMENT"


def test_shared_endpoint_contiguous_window_and_prefix_invariance() -> None:
    current, trade_day, today, prior = reference_and_current(u=10, v=-10)
    first = previous_same_session_event(current, trade_day, Session.DAY, today, prior)
    # Same U but a distinct V must change only D, proving the adjacent control is independent.
    _, _, _, changed_v = reference_and_current(u=10, v=10)
    second = previous_same_session_event(current, trade_day, Session.DAY, today, changed_v)
    assert (first["A_direction"], second["A_direction"], first["D_direction"], second["D_direction"]) == ("long", "long", "short", "long")
    assert first == previous_same_session_event(current, trade_day, Session.DAY, today + bars(current.session_open(trade_day, Session.DAY) + timedelta(minutes=62), trade_day, Session.DAY, [999] * 20), prior)


def test_execution_is_next_open_fixed_exit_not_extended_and_final_input_rejected() -> None:
    current, trade_day, today, _ = reference_and_current()
    instrument, sessions, _, config = load_project_config(Path("config"))
    S = current.session_open(trade_day, Session.DAY)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    trade = engine.run(today, PreviousSessionDirectionStrategy("r013", S, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, S + timedelta(minutes=1), S + timedelta(minutes=61), ExitReason.SIGNAL)
    delayed = engine.run([bar for bar in today if bar.ts_jst != S + timedelta(minutes=1)], PreviousSessionDirectionStrategy("r013-delayed", S, "short")).trades[0]
    assert (delayed.entry_ts, delayed.exit_ts, delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy) == (S + timedelta(minutes=2), S + timedelta(minutes=61), True)
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
