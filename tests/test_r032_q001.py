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
from n225m_bt.research.r022 import normal_session_end, previous_scheduled_session
from n225m_bt.research.r032 import prior_day_night_gap_confirmation_event
from n225m_bt.strategies.prior_day_night_gap_confirmation import (
    PriorDayNightGapConfirmationStrategy,
)

DAY = date(2024, 11, 5)
REFERENCE_DAY = date(2024, 11, 1)


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bar(stamp: datetime, trade_day: date, session: Session, open_: int, close: int) -> Bar:
    return Bar(stamp, trade_day, stamp.date(), session, "synthetic", open_, max(open_, close), min(open_, close), close)


def inputs(g: int = 10, q: int = 10, *, missing_night: datetime | None = None) -> tuple[list[Bar], list[Bar]]:
    cal = classifier()
    day_final = normal_session_end(cal, REFERENCE_DAY, Session.DAY) - timedelta(minutes=1)
    day = [bar(day_final, REFERENCE_DAY, Session.DAY, 100, 100)]
    start = cal.session_open(DAY, Session.NIGHT)
    night = [bar(start + timedelta(minutes=index), DAY, Session.NIGHT, 100 + (g if index == 0 else 0), 100 + g + (q if index == 4 else 0)) for index in range(66) if start + timedelta(minutes=index) != missing_night]
    return day, night


def event(g: int = 10, q: int = 10, **kwargs: object) -> dict[str, object]:
    day, night = inputs(g, q, **kwargs)
    return prior_day_night_gap_confirmation_event(classifier(), DAY, night, (REFERENCE_DAY, Session.DAY), day)


def test_gap_q_signs_zero_counterfactuals_and_prefix() -> None:
    confirmed = event(10, 10)
    q_changed, g_changed = event(10, -10), event(-10, 10)
    double_negative = event(-10, -10)
    assert confirmed["status"] == "confirmed" and confirmed["A_direction"] == "long"
    assert double_negative["status"] == "confirmed" and double_negative["A_direction"] == "short"
    assert q_changed["status"] == g_changed["status"] == "nonconfirmed"
    assert q_changed["G_points"] == confirmed["G_points"] and g_changed["Q_points"] == confirmed["Q_points"]
    assert event(0, 10)["reason"] == "ZERO_G"
    assert event(10, 0)["reason"] == "ZERO_Q"
    day, night = inputs()
    extra = bar(classifier().session_open(DAY, Session.NIGHT) + timedelta(minutes=100), DAY, Session.NIGHT, 1, 999)
    assert confirmed == prior_day_night_gap_confirmation_event(classifier(), DAY, [*night, extra], (REFERENCE_DAY, Session.DAY), day)


def test_schedule_reference_missing_quarantine_execution_delay_and_holdout_lock() -> None:
    cal, (day, night) = classifier(), inputs()
    start = cal.session_open(DAY, Session.NIGHT)
    assert previous_scheduled_session(cal, [REFERENCE_DAY, DAY], (DAY, Session.NIGHT)) == (REFERENCE_DAY, Session.DAY)
    assert prior_day_night_gap_confirmation_event(cal, DAY, night, None, day)["reason"] == "NO_SCHEDULED_PREVIOUS_SESSION"
    assert prior_day_night_gap_confirmation_event(cal, DAY, night, (REFERENCE_DAY, Session.NIGHT), day)["reason"] == "PREVIOUS_SCHEDULED_SESSION_NOT_DAY"
    assert prior_day_night_gap_confirmation_event(cal, DAY, night, (REFERENCE_DAY, Session.DAY), day, day_quarantined=True)["reason"] == "REFERENCE_DAY_QUARANTINED"
    missing_day, missing_night = inputs(missing_night=start + timedelta(minutes=2))
    assert prior_day_night_gap_confirmation_event(cal, DAY, missing_night, (REFERENCE_DAY, Session.DAY), missing_day)["reason"] == "TARGET_NIGHT_FIRST_FIVE_BARS_MISSING"
    instrument, _, _, config = load_project_config(Path("config"))
    signal = start + timedelta(minutes=4)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, cal)
    trade = engine.run(night, PriorDayNightGapConfirmationStrategy("r032", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    delayed = engine.run([row for row in night if row.ts_jst != signal + timedelta(minutes=1)], PriorDayNightGapConfirmationStrategy("r032-delay", signal, "short")).trades[0]
    assert delayed.entry_ts == signal + timedelta(minutes=2) and delayed.exit_ts == signal + timedelta(minutes=61)
    assert delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
