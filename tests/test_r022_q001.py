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
from n225m_bt.research.r022 import (
    normal_session_end,
    previous_scheduled_session,
    prior_range_end_event,
)
from n225m_bt.strategies.prior_session_range_end import PriorSessionRangeEndStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bars(day: date, session: Session, start: datetime, end: datetime, *, close: int = 120,
         high: int = 140, low: int = 100, first_open: int = 110, late_open: int = 105) -> list[Bar]:
    output: list[Bar] = []
    count = int((end - start).total_seconds() // 60)
    for index in range(count):
        stamp = start + timedelta(minutes=index)
        open_ = first_open if index == 0 else late_open if index == count - 60 else 120
        final = close if index == count - 1 else open_
        output.append(Bar(stamp, day, stamp.date(), session, "synthetic", open_, high, low, final))
    return output


def setup(day: date = date(2024, 11, 5), session: Session = Session.DAY, **kwargs: int):
    current = classifier()
    ref_day, ref_session = (day, Session.NIGHT) if session is Session.DAY else (day, Session.DAY)
    # For a night current session, the caller supplies the next trade_date so its prior is that day session.
    S = current.session_open(day, session)
    current_rows = bars(day, session, S, S + timedelta(minutes=62))
    P = current.session_open(ref_day, ref_session)
    reference = bars(ref_day, ref_session, P, normal_session_end(current, ref_day, ref_session), **kwargs)
    return current, current_rows, (ref_day, ref_session), reference


def test_planned_day_night_links_weekend_and_calendar_date_trade_date() -> None:
    current = classifier()
    days = [date(2024, 11, 1), date(2024, 11, 5), date(2024, 11, 6)]
    assert previous_scheduled_session(current, days, (date(2024, 11, 5), Session.DAY)) == (date(2024, 11, 5), Session.NIGHT)
    assert previous_scheduled_session(current, days, (date(2024, 11, 6), Session.NIGHT)) == (date(2024, 11, 5), Session.DAY)
    assert current.session_open(date(2024, 11, 5), Session.NIGHT).date() == date(2024, 11, 1)
    assert normal_session_end(current, date(2024, 11, 1), Session.DAY).strftime("%H:%M") == "15:15"
    assert normal_session_end(current, date(2024, 11, 5), Session.DAY).strftime("%H:%M") == "15:40"
    assert normal_session_end(current, date(2024, 11, 6), Session.NIGHT).strftime("%H:%M") == "05:55"


def test_range_endpoint_signs_boundaries_zero_and_current_s_independence() -> None:
    current, today, ref_key, reference = setup(close=135, high=140, low=100, first_open=110, late_open=105)
    event = prior_range_end_event(current, date(2024, 11, 5), Session.DAY, today, ref_key, reference)
    assert (event["status"], event["range_quartile"], event["A_direction"], event["D_direction"], event["F_late_direction"]) == ("eligible", "upper", "long", "long", "long")
    # Altering current S prices cannot alter a completed-reference signal.
    altered = list(today)
    altered[0] = Bar(today[0].ts_jst, today[0].trade_date, today[0].calendar_date, Session.DAY, "synthetic", 999, 999, 999, 999)
    assert prior_range_end_event(current, date(2024, 11, 5), Session.DAY, altered, ref_key, reference)["A_direction"] == "long"
    for close, high, low, first_open, expected in ((130, 140, 100, 110, "UPPER_QUARTILE_BOUNDARY_EQUAL"), (110, 140, 100, 100, "LOWER_QUARTILE_BOUNDARY_EQUAL"), (120, 140, 100, 110, "MIDDLE_FIFTY_PERCENT"), (120, 120, 120, 110, "ZERO_RANGE_H_EQUALS_L")):
        _, current_rows, key, refs = setup(close=close, high=high, low=low, first_open=first_open, late_open=105)
        assert prior_range_end_event(current, date(2024, 11, 5), Session.DAY, current_rows, key, refs)["reason"] == expected


def test_full_normal_continuity_no_skipover_zero_c_o_j_and_prefix() -> None:
    current, today, key, reference = setup(close=135, high=140, low=100, first_open=110, late_open=105)
    assert prior_range_end_event(current, date(2024, 11, 5), Session.DAY, today, key, reference[:-1])["reason"] == "REFERENCE_FULL_NORMAL_SESSION_MISSING"
    assert prior_range_end_event(current, date(2024, 11, 5), Session.DAY, today, None, None)["reason"] == "NO_SCHEDULED_PREVIOUS_SESSION"
    _, rows, key, zero_co = setup(close=110, high=140, low=100, first_open=110, late_open=105)
    assert prior_range_end_event(current, date(2024, 11, 5), Session.DAY, rows, key, zero_co)["reason"] == "ZERO_C_MINUS_O"
    _, rows, key, zero_j = setup(close=135, high=140, low=100, first_open=110, late_open=135)
    assert prior_range_end_event(current, date(2024, 11, 5), Session.DAY, rows, key, zero_j)["reason"] == "ZERO_J"
    base = prior_range_end_event(current, date(2024, 11, 5), Session.DAY, today, key, reference)
    assert base == prior_range_end_event(current, date(2024, 11, 5), Session.DAY, today + today[-1:], key, reference)
    assert prior_range_end_event(current, date(2024, 11, 5), Session.DAY, today, key, reference, reference_quarantined=True)["reason"] == "REFERENCE_SESSION_QUARANTINED"


def test_high_low_counterfactual_changes_a_while_c_o_j_fixed() -> None:
    current, today, key, upper = setup(close=135, high=140, low=100, first_open=110, late_open=105)
    _, _, _, lower = setup(close=135, high=180, low=130, first_open=110, late_open=105)
    first = prior_range_end_event(current, date(2024, 11, 5), Session.DAY, today, key, upper)
    second = prior_range_end_event(current, date(2024, 11, 5), Session.DAY, today, key, lower)
    assert (first["C_points"], first["O_points"], first["J_points"], first["A_direction"]) == (135, 110, 30, "long")
    assert (second["C_points"], second["O_points"], second["J_points"], second["A_direction"]) == (135, 110, 30, "short")


def test_next_open_fixed_exit_delay_accounting_and_holdout_lock() -> None:
    current = classifier()
    day, S = date(2024, 11, 5), current.session_open(date(2024, 11, 5), Session.DAY)
    rows = bars(day, Session.DAY, S, S + timedelta(minutes=63))
    instrument, sessions, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    trade = engine.run(rows, PriorSessionRangeEndStrategy("r022", S, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, S + timedelta(minutes=1), S + timedelta(minutes=61), ExitReason.SIGNAL)
    delayed = engine.run([row for row in rows if row.ts_jst != S + timedelta(minutes=1)], PriorSessionRangeEndStrategy("r022-delay", S, "short")).trades[0]
    assert (delayed.entry_ts, delayed.exit_ts, delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy) == (S + timedelta(minutes=2), S + timedelta(minutes=61), True)
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
