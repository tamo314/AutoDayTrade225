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
from n225m_bt.research.r023 import cash_open_reversal_event
from n225m_bt.strategies.cash_open_reversal import CashOpenReversalStrategy


def cash_calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2023, 5, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def bar(stamp: datetime, day: date, session: Session, open_: int, close: int) -> Bar:
    return Bar(stamp, day, stamp.date(), session, "synthetic", open_, max(open_, close), min(open_, close), close)


def day_rows(day: date, p: int = 10, *, missing: datetime | None = None) -> list[Bar]:
    start = datetime(day.year, day.month, day.day, 8, 45, tzinfo=JST)
    rows = []
    for offset in range(47):
        stamp, close = start + timedelta(minutes=offset), 100
        if stamp == start + timedelta(minutes=14):
            close = 100 + p
        if stamp != missing:
            rows.append(bar(stamp, day, Session.DAY, 100, close))
    return rows


def night_rows(day: date, classifier: CalendarClassifier, n: int = 90) -> list[Bar]:
    from n225m_bt.research.r006 import night_reference_time

    start, _, _, _ = night_reference_time(classifier, day)
    return [bar(start, day, Session.NIGHT, n, n)]


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def event(day: date, p: int = 10, n: int = 90) -> dict[str, object]:
    instance = classifier()
    return cash_open_reversal_event(instance, day, day_rows(day, p), night_rows(day, instance, n), cash_calendar())


def test_signal_uses_p_and_gap_independently_and_requires_common_nonzero() -> None:
    same_gap_changed_p, same_p_changed_gap = event(date(2024, 11, 5), 10, 90), event(date(2024, 11, 5), -10, 90)
    assert same_gap_changed_p["G_points"] == same_p_changed_gap["G_points"] == 10
    assert same_gap_changed_p["A_direction"] != same_p_changed_gap["A_direction"]
    same_p_changed_gap = event(date(2024, 11, 5), 10, 110)
    assert same_gap_changed_p["P_points"] == same_p_changed_gap["P_points"] == 10
    assert same_gap_changed_p["F_gap_direction"] != same_p_changed_gap["F_gap_direction"]
    assert event(date(2024, 11, 5), 0)["reason"] == "ZERO_P"
    assert event(date(2024, 11, 5), 10, 100)["reason"] == "ZERO_G"


def test_calendar_missing_quarantine_and_prefix_rules() -> None:
    day, instance = date(2024, 11, 5), classifier()
    assert cash_open_reversal_event(instance, date(2023, 5, 4), day_rows(date(2023, 5, 4)), night_rows(date(2023, 5, 4), instance), cash_calendar())["reason"] == "TSE_CASH_MARKET_CLOSED"
    assert cash_open_reversal_event(instance, day, day_rows(day, missing=datetime(2024, 11, 5, 8, 50, tzinfo=JST)), night_rows(day, instance), cash_calendar())["reason"] == "WINDOW_0845_TO_0859_MISSING"
    assert cash_open_reversal_event(instance, day, day_rows(day), night_rows(day, instance), cash_calendar(), night_quarantined=True)["reason"] == "REFERENCE_NIGHT_QUARANTINED"
    original = cash_open_reversal_event(instance, day, day_rows(day), night_rows(day, instance), cash_calendar())
    extended = cash_open_reversal_event(instance, day, [*day_rows(day), bar(datetime(2024, 11, 5, 10, 0, tzinfo=JST), day, Session.DAY, 1, 999)], night_rows(day, instance), cash_calendar())
    assert original == extended


def test_next_open_fixed_exit_delay_and_holdout_lock() -> None:
    day, instance = date(2024, 11, 5), classifier()
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, instance)
    signal = datetime(2024, 11, 5, 8, 59, tzinfo=JST)
    trade = engine.run(day_rows(day), CashOpenReversalStrategy("r023", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=31), ExitReason.SIGNAL)
    delayed_entry = engine.run([row for row in day_rows(day) if row.ts_jst != signal + timedelta(minutes=1)], CashOpenReversalStrategy("r023-delay-entry", signal, "short")).trades[0]
    assert delayed_entry.entry_ts == signal + timedelta(minutes=2)
    assert delayed_entry.exit_ts == signal + timedelta(minutes=31)
    delayed_exit = engine.run([row for row in day_rows(day) if row.ts_jst != signal + timedelta(minutes=31)], CashOpenReversalStrategy("r023-delay-exit", signal, "short")).trades[0]
    assert delayed_exit.exit_ts == signal + timedelta(minutes=32)
    assert delayed_exit.net_pnl_jpy == delayed_exit.gross_pnl_jpy - delayed_exit.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
