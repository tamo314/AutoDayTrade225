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
from n225m_bt.research.r019 import prior_range_regime_event, prior_scheduled_same_session_dates
from n225m_bt.strategies.prior_range_regime import PriorRangeRegimeStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bar(
    stamp: datetime,
    trade_day: date,
    session: Session,
    open_: int,
    high: int,
    low: int,
    close: int,
    *,
    eligible: bool = True,
) -> Bar:
    return Bar(
        stamp,
        trade_day,
        stamp.date(),
        session,
        "synthetic",
        open_,
        high,
        low,
        close,
        is_eligible=eligible,
        quality_flags=() if eligible else ("SYNTHETIC_INELIGIBLE",),
    )


def range_rows(calendar: CalendarClassifier, trade_day: date, session: Session, width: int, shift: int = 0) -> list[Bar]:
    start = calendar.session_open(trade_day, session)
    return [
        bar(start + timedelta(minutes=index), trade_day, session, 100 + shift, 100 + shift + width, 100 + shift, 100 + shift)
        for index in range(60)
    ]


def current_rows(calendar: CalendarClassifier, trade_day: date, session: Session, movement: int) -> list[Bar]:
    start = calendar.session_open(trade_day, session)
    return [
        bar(
            start + timedelta(minutes=index),
            trade_day,
            session,
            100,
            100 if index < 29 else 100 + movement,
            100,
            100 if index < 29 else 100 + movement,
        )
        for index in range(30)
    ]


def inputs(
    *,
    session: Session = Session.DAY,
    ranges: list[int] | None = None,
    movement: int = 10,
    shift: int = 0,
) -> tuple[CalendarClassifier, date, list[Bar], dict[date, list[Bar]], list[date]]:
    calendar, trade_day = classifier(), date(2024, 11, 5)
    scheduled = prior_scheduled_same_session_dates(calendar, trade_day)
    assert scheduled is not None and len(scheduled) == 20
    widths = ranges if ranges is not None else [20] * 5 + [5] * 15
    assert len(widths) == 20
    history = {
        item: range_rows(calendar, item, session, widths[index], shift)
        for index, item in enumerate(scheduled)
    }
    return calendar, trade_day, current_rows(calendar, trade_day, session, movement), history, scheduled


def test_calendar_order_same_type_and_nonoverlapping_five_fifteen_ranges() -> None:
    for session in (Session.DAY, Session.NIGHT):
        calendar, trade_day, current, history, scheduled = inputs(session=session)
        event = prior_range_regime_event(calendar, trade_day, session, current, history, set())
        assert event["scheduled_prior_trade_dates_p1_to_p20"] == [item.isoformat() for item in scheduled]
        assert (event["recent_R1_to_R5_sum_points"], event["baseline_R6_to_R20_sum_points"], event["V_points"]) == (100, 75, 225)
        assert (event["range_regime"], event["M_points"], event["A_direction"], event["D_direction"], event["F_direction"]) == ("expanded", 10, "long", "long", "short")


def test_only_past_ranges_change_a_and_parallel_price_shift_is_invariant() -> None:
    calendar, trade_day, current, expanded, _ = inputs(ranges=[20] * 5 + [5] * 15)
    _, _, _, contracted, _ = inputs(ranges=[5] * 5 + [20] * 15)
    event_expanded = prior_range_regime_event(calendar, trade_day, Session.DAY, current, expanded, set())
    event_contracted = prior_range_regime_event(calendar, trade_day, Session.DAY, current, contracted, set())
    assert (event_expanded["M_points"], event_contracted["M_points"]) == (10, 10)
    assert (event_expanded["A_direction"], event_contracted["A_direction"]) == ("long", "short")
    _, _, _, shifted, _ = inputs(ranges=[20] * 5 + [5] * 15, shift=10_000)
    shifted_event = prior_range_regime_event(calendar, trade_day, Session.DAY, current, shifted, set())
    assert (shifted_event["R_points_p1_to_p20"], shifted_event["V_points"], shifted_event["A_direction"]) == (event_expanded["R_points_p1_to_p20"], event_expanded["V_points"], event_expanded["A_direction"])


def test_zero_range_is_observed_but_zero_v_m_and_bad_history_skip() -> None:
    calendar, trade_day, current, history, scheduled = inputs(ranges=[0, 20, 20, 20, 20] + [5] * 15)
    event = prior_range_regime_event(calendar, trade_day, Session.DAY, current, history, set())
    assert event["status"] == "eligible"
    assert event["R_points_p1_to_p20"][:2] == [0, 20]
    _, _, _, zero_v_history, _ = inputs(ranges=[10] * 20)
    assert prior_range_regime_event(calendar, trade_day, Session.DAY, current, zero_v_history, set())["reason"] == "ZERO_V"
    _, _, zero_m_current, zero_m_history, _ = inputs(movement=0)
    assert prior_range_regime_event(calendar, trade_day, Session.DAY, zero_m_current, zero_m_history, set())["reason"] == "ZERO_M"
    assert prior_range_regime_event(calendar, trade_day, Session.DAY, current, history, {(scheduled[3], Session.DAY)})["reason"] == "REFERENCE_SESSION_QUARANTINED"
    missing = dict(history)
    missing[scheduled[1]] = missing[scheduled[1]][:-1]
    assert prior_range_regime_event(calendar, trade_day, Session.DAY, current, missing, set())["reason"] == "REFERENCE_WINDOW_MISSING"
    assert prior_range_regime_event(calendar, trade_day, Session.DAY, current, history, set(), development_start=date(2024, 11, 1))["reason"] == "REFERENCE_OUTSIDE_DEVELOPMENT"


def test_current_window_prefix_and_fixed_execution_path() -> None:
    calendar, trade_day, current, history, _ = inputs()
    baseline = prior_range_regime_event(calendar, trade_day, Session.DAY, current, history, set())
    after_t = current[-1].ts_jst + timedelta(minutes=1)
    assert baseline == prior_range_regime_event(
        calendar,
        trade_day,
        Session.DAY,
        [*current, bar(after_t, trade_day, Session.DAY, 999, 999, 999, 999)],
        history,
        set(),
    )
    instrument, sessions, _, config = load_project_config(Path("config"))
    signal = datetime(2024, 11, 5, 9, 29, tzinfo=JST)
    rows = [
        bar(
            signal - timedelta(minutes=29) + timedelta(minutes=index),
            trade_day,
            Session.DAY,
            100,
            100,
            100,
            100,
        )
        for index in range(91)
    ]
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    trade = engine.run(rows, PriorRangeRegimeStrategy("r019", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    delayed = engine.run([row for row in rows if row.ts_jst != signal + timedelta(minutes=1)], PriorRangeRegimeStrategy("r019-delay", signal, "short")).trades[0]
    assert (delayed.entry_ts, delayed.exit_ts, delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy) == (signal + timedelta(minutes=2), signal + timedelta(minutes=61), True)
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
