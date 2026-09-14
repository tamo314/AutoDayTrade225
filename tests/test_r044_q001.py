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
from n225m_bt.research.r044 import tse_close_conflict_event
from n225m_bt.strategies.tse_close_conflict_reversal import TSECloseConflictReversalStrategy


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2024, 11, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def rows(day: date, p: int = 10, last: int = -10, pb: int = -10, lb: int = 10) -> list[Bar]:
    close = datetime(day.year, day.month, day.day, 15, 0 if day <= date(2024, 11, 1) else 30, tzinfo=JST)
    start, output = close - timedelta(minutes=95), []
    for i in range(116):
        stamp, value = start + timedelta(minutes=i), 100
        if stamp == close - timedelta(minutes=6):
            value = 100 + p
        if stamp == close - timedelta(minutes=1):
            value = 100 + last
        if stamp == close - timedelta(minutes=66):
            value = 100 + pb
        if stamp == close - timedelta(minutes=61):
            value = 100 + lb
        output.append(Bar(stamp, day, day, Session.DAY, "synthetic", 100, max(100, value), min(100, value), value))
    return output


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def test_schedule_windows_conflict_agreement_zero_and_prefix() -> None:
    old, new = date(2024, 11, 1), date(2024, 11, 5)
    assert tse_close_conflict_event(old, rows(old), calendar())["T_cash_close_jst"].endswith("15:00:00+09:00")
    event = tse_close_conflict_event(new, rows(new), calendar())
    assert (event["status"], event["placebo_status"], event["rL_direction"], event["rLB_direction"]) == ("conflict", "conflict", "short", "long")
    assert (event["E_planned_entry_jst"], event["X_planned_exit_jst"], event["G_planned_entry_jst"], event["G_planned_exit_jst"]) == ("2024-11-05T15:30:00+09:00", "2024-11-05T15:40:00+09:00", "2024-11-05T14:30:00+09:00", "2024-11-05T14:40:00+09:00")
    assert tse_close_conflict_event(new, rows(new, 0), calendar())["reason"] == "ZERO_rP"
    assert tse_close_conflict_event(new, rows(new, 10, 0), calendar())["reason"] == "ZERO_rL"
    agreement = tse_close_conflict_event(new, rows(new, 10, 10), calendar())
    assert agreement["status"] == "agreement"
    changed = [*rows(new), Bar(datetime(2024, 11, 5, 15, 50, tzinfo=JST), new, new, Session.DAY, "synthetic", 1, 999, 1, 999)]
    assert event == tse_close_conflict_event(new, changed, calendar())


def test_rejects_and_fixed_execution_delay() -> None:
    day = date(2024, 11, 5)
    assert tse_close_conflict_event(date(2024, 11, 4), rows(date(2024, 11, 4)), calendar())["reason"] == "TSE_CASH_MARKET_CLOSED"
    assert tse_close_conflict_event(day, rows(day), calendar(), day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    assert tse_close_conflict_event(date(2025, 7, 1), rows(date(2025, 7, 1)), calendar())["reason"] == "OUTSIDE_DEVELOPMENT"
    event = tse_close_conflict_event(day, rows(day), calendar())
    signal, exit_ = datetime.fromisoformat(str(event["t_signal_bar_start_jst"])), datetime.fromisoformat(str(event["X_planned_exit_jst"]))
    instrument, _, _, config = load_project_config(Path("config"))
    runtime = config.model_copy(
        update={
            "risk": config.risk.model_copy(
                update={"new_entry_cutoff_minutes_before_session_close": 0}
            )
        }
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), runtime, classifier())
    trade = engine.run(rows(day), TSECloseConflictReversalStrategy("r044", signal, exit_, "long")).trades[0]
    delay = engine.run(rows(day), TSECloseConflictReversalStrategy("r044d", signal, exit_, "short", 1)).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), exit_, ExitReason.SIGNAL)
    assert (delay.entry_ts, delay.exit_ts, delay.exit_reason) == (signal + timedelta(minutes=2), exit_, ExitReason.SIGNAL)
    assert delay.net_pnl_jpy == delay.gross_pnl_jpy - delay.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
