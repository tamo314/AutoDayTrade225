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
from n225m_bt.research.r041 import night_conflict_open_followthrough_event
from n225m_bt.strategies.night_conflict_open_followthrough import (
    NightConflictOpenFollowthroughStrategy,
)

DAY = date(2024, 11, 5)


def cash_calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2023, 5, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bar(stamp: datetime, trade_day: date, session: Session, open_: int, close: int) -> Bar:
    return Bar(stamp, trade_day, stamp.date(), session, "synthetic", open_, max(open_, close), min(open_, close), close)


def day_bars(day: date, r_o: int, *, missing: int | None = None) -> list[Bar]:
    start = datetime(day.year, day.month, day.day, 9, tzinfo=JST)
    return [bar(start + timedelta(minutes=i), day, Session.DAY, 100, 100 + (r_o if i == 29 else 0)) for i in range(91) if i != missing]


def night_bars(day: date, r_n: int, *, missing: bool = False) -> list[Bar]:
    current = classifier()
    start, end = current.session_open(day, Session.NIGHT), normal_session_end(current, day, Session.NIGHT)
    count = int((end - start).total_seconds() // 60)
    rows = [bar(start + timedelta(minutes=i), day, Session.NIGHT, 100, 100 + (r_n if i == count - 1 else 0)) for i in range(count)]
    if missing:
        rows.pop(1)
    return rows


def history() -> list[tuple[date, list[Bar] | None, bool]]:
    values = [10] * 30 + [20] * 30
    days: list[date] = []
    cursor = DAY - timedelta(days=1)
    while len(days) < 60:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return [(day, day_bars(day, value), False) for day, value in zip(days, values, strict=True)]


def event(r_n: int = 10, r_o: int = -10, **kwargs: object) -> dict[str, object]:
    return night_conflict_open_followthrough_event(classifier(), cash_calendar(), DAY, day_bars(DAY, r_o), night_bars(DAY, r_n), history(), **kwargs)


def test_conflict_agreement_qm_tie_zero_and_prefix() -> None:
    conflict = event(10, -10)
    agreement = event(10, 10)
    assert (conflict["status"], conflict["A_direction"], conflict["QM_abs_rO_points"], conflict["opening_magnitude_layer"]) == ("conflict", "short", 10, "low")
    assert agreement["status"] == "agreement"
    assert event(-10, 10)["A_direction"] == "long"
    assert event(0, 10)["reason"] == "ZERO_rN"
    assert event(10, 0)["reason"] == "ZERO_rO"
    extended = [*day_bars(DAY, -10), bar(datetime(2024, 11, 5, 9, 30, tzinfo=JST), DAY, Session.DAY, 1, 999)]
    unchanged = night_conflict_open_followthrough_event(classifier(), cash_calendar(), DAY, extended, night_bars(DAY, 10), history())
    for key in ("rN_points", "rO_points", "QM_abs_rO_points", "opening_magnitude_layer", "status", "A_direction"):
        assert unchanged[key] == conflict[key]


def test_exact_rolling_60_requires_50_valid_and_never_backfills() -> None:
    fixed = history()
    fifty_valid = [*fixed[:50], *((day, None, False) for day, _, _ in fixed[50:])]
    result = night_conflict_open_followthrough_event(
        classifier(), cash_calendar(), DAY, day_bars(DAY, -10), night_bars(DAY, 10), fifty_valid
    )
    assert (result["valid_reference_count"], result["QM_abs_rO_points"], result["opening_magnitude_layer"]) == (50, 10, "low")
    forty_nine_valid = [*fifty_valid[:49], (fifty_valid[49][0], None, False), *fifty_valid[50:]]
    assert night_conflict_open_followthrough_event(
        classifier(), cash_calendar(), DAY, day_bars(DAY, -10), night_bars(DAY, 10), forty_nine_valid
    )["reason"] == "INSUFFICIENT_VALID_rO_REFERENCES"


def test_rejections_history_missing_quarantine_and_execution() -> None:
    assert event(day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    assert night_conflict_open_followthrough_event(classifier(), cash_calendar(), DAY, day_bars(DAY, -10, missing=5), night_bars(DAY, 10), history())["reason"] == "WINDOW_0900_TO_0929_MISSING_OR_INELIGIBLE"
    assert night_conflict_open_followthrough_event(classifier(), cash_calendar(), DAY, day_bars(DAY, -10), night_bars(DAY, 10, missing=True), history())["reason"] == "REFERENCE_FULL_NORMAL_NIGHT_MISSING"
    assert night_conflict_open_followthrough_event(classifier(), cash_calendar(), DAY, day_bars(DAY, -10), night_bars(DAY, 10), history()[:-1])["reason"] == "HISTORY_60_SCHEDULED_TSE_DAYS_SHORT"
    instrument, _, _, config = load_project_config(Path("config"))
    signal = datetime(2024, 11, 5, 9, 29, tzinfo=JST)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(day_bars(DAY, -10), NightConflictOpenFollowthroughStrategy("r041", signal, "short")).trades[0]
    delayed = engine.run(day_bars(DAY, -10), NightConflictOpenFollowthroughStrategy("r041-delay", signal, "long", 1)).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.SHORT, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts) == (signal + timedelta(minutes=2), signal + timedelta(minutes=61))
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
