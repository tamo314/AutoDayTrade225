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
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r025 import prior_tse_day_range_acceptance_event
from n225m_bt.strategies.prior_tse_day_range_acceptance import PriorTSEDayRangeAcceptanceStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def cash_calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2024, 11, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def make_bar(stamp: datetime, trade_day: date, open_: int, close: int, high: int | None = None, low: int | None = None) -> Bar:
    return Bar(stamp, trade_day, stamp.date(), Session.DAY, "synthetic", open_, high or max(open_, close), low or min(open_, close), close)


def reference_rows(day: date, high: int = 140, low: int = 100, *, missing: bool = False) -> list[Bar]:
    rules, start, end = classifier(), None, None
    start, end = rules.session_open(day, Session.DAY), normal_session_end(rules, day, Session.DAY)
    rows = [make_bar(start + timedelta(minutes=index), day, 120, 120, high, low) for index in range(int((end - start).total_seconds() // 60))]
    return rows[:-1] if missing else rows


def current_rows(day: date, opening: int, closing: int, *, missing: datetime | None = None) -> list[Bar]:
    start, rows = datetime(day.year, day.month, day.day, 8, 45, tzinfo=JST), []
    for offset in range(76):
        stamp, close = start + timedelta(minutes=offset), opening
        if offset == 14:
            close = closing
        if stamp != missing:
            rows.append(make_bar(stamp, day, opening, close))
    return rows


def event(day: date, opening: int, closing: int, **kwargs: object) -> dict[str, object]:
    return prior_tse_day_range_acceptance_event(
        classifier(), cash_calendar(), day, current_rows(day, opening, closing), reference_rows(date(2024, 11, 1)), **kwargs
    )


def test_tse_previous_day_range_opening_confirmation_and_counterfactuals() -> None:
    day = date(2024, 11, 5)  # Nov 4 is a TSE holiday; p must be Nov 1, never observed-bar inferred.
    upper = event(day, 145, 146)
    lower = event(day, 95, 94)
    inside = event(day, 140, 145)
    returned = event(day, 145, 140)
    opposite = event(day, 145, 99)
    assert (upper["p_trade_date"], upper["status"], upper["A_direction"]) == ("2024-11-01", "confirmed", "long")
    assert (lower["status"], lower["A_direction"]) == ("confirmed", "short")
    assert inside["reason"] == "OPENING_INSIDE_OR_BOUNDARY_OF_PRIOR_RANGE"
    assert returned["status"] == opposite["status"] == "nonconfirmed"
    # Same O/K but changing only the completed prior range changes the base event.
    changed_range = prior_tse_day_range_acceptance_event(classifier(), cash_calendar(), day, current_rows(day, 145, 146), reference_rows(date(2024, 11, 1), 150, 140))
    assert changed_range["reason"] == "OPENING_INSIDE_OR_BOUNDARY_OF_PRIOR_RANGE"
    # Same range/O and changing only K changes confirmation, including the boundary.
    assert event(day, 145, 140)["status"] == "nonconfirmed"
    assert event(day, 145, 146)["status"] == "confirmed"


def test_rejections_prefix_and_fixed_execution_path() -> None:
    day, prior = date(2024, 11, 5), date(2024, 11, 1)
    base = event(day, 145, 146)
    assert base == prior_tse_day_range_acceptance_event(classifier(), cash_calendar(), day, [*current_rows(day, 145, 146), make_bar(datetime(2024, 11, 5, 10, 1, tzinfo=JST), day, 1, 999)], reference_rows(prior))
    assert prior_tse_day_range_acceptance_event(classifier(), cash_calendar(), day, current_rows(day, 145, 146), reference_rows(prior, 120, 120))["reason"] == "ZERO_RANGE_H_EQUALS_L"
    assert prior_tse_day_range_acceptance_event(classifier(), cash_calendar(), day, current_rows(day, 145, 146), reference_rows(prior, missing=True))["reason"] == "REFERENCE_FULL_NORMAL_DAY_MISSING"
    assert prior_tse_day_range_acceptance_event(classifier(), cash_calendar(), day, current_rows(day, 145, 146, missing=datetime(2024, 11, 5, 8, 50, tzinfo=JST)), reference_rows(prior))["reason"] == "WINDOW_0845_TO_0859_MISSING"
    assert prior_tse_day_range_acceptance_event(classifier(), cash_calendar(), day, current_rows(day, 145, 146), reference_rows(prior), prior_day_quarantined=True)["reason"] == "REFERENCE_DAY_SESSION_QUARANTINED"
    assert prior_tse_day_range_acceptance_event(classifier(), cash_calendar(), date(2021, 1, 4), current_rows(date(2021, 1, 4), 145, 146), None)["reason"] == "REFERENCE_OUTSIDE_DEVELOPMENT"
    instrument, sessions, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    signal = datetime(2024, 11, 5, 8, 59, tzinfo=JST)
    trade = engine.run(current_rows(day, 145, 146), PriorTSEDayRangeAcceptanceStrategy("r025", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    delayed = engine.run([row for row in current_rows(day, 145, 146) if row.ts_jst != signal + timedelta(minutes=1)], PriorTSEDayRangeAcceptanceStrategy("r025-delay", signal, "short")).trades[0]
    assert (delayed.entry_ts, delayed.exit_ts, delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy) == (signal + timedelta(minutes=2), signal + timedelta(minutes=61), True)
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
