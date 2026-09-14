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
from n225m_bt.research.r030 import tse_cash_close_reversal_event
from n225m_bt.strategies.cash_close_reversal import CashCloseReversalStrategy


def cash_calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(
        frozenset({date(2024, 11, 4)}), date(2021, 1, 1), date(2025, 12, 31)
    )


def rows(day: date, p: int = 10, p0: int = -10) -> list[Bar]:
    close = datetime(
        day.year, day.month, day.day, 15, 0 if day <= date(2024, 11, 1) else 30, tzinfo=JST
    )
    start = close - timedelta(minutes=40)
    output: list[Bar] = []
    for index in range(56):
        stamp, value = start + timedelta(minutes=index), 100
        if stamp == close - timedelta(minutes=1):
            value = 100 + p
        if stamp == close - timedelta(minutes=31):
            value = 100 + p0
        output.append(
            Bar(
                stamp,
                day,
                day,
                Session.DAY,
                "synthetic",
                100,
                max(100, value),
                min(100, value),
                value,
            )
        )
    return output


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def test_close_schedule_windows_signs_zero_missing_and_prefix_invariance() -> None:
    old, new = date(2024, 11, 1), date(2024, 11, 5)
    old_event = tse_cash_close_reversal_event(old, rows(old), cash_calendar())
    event = tse_cash_close_reversal_event(new, rows(new, 10, -10), cash_calendar())
    assert old_event["T_cash_close_jst"].endswith("15:00:00+09:00")
    assert (
        event["status"],
        event["A_direction"],
        event["D_direction"],
        event["placebo_status"],
        event["G_direction"],
    ) == ("eligible", "short", "long", "eligible", "long")
    assert (
        event["E_planned_entry_jst"],
        event["X_planned_exit_jst"],
        event["G_planned_entry_jst"],
        event["G_planned_exit_jst"],
    ) == (
        "2024-11-05T15:30:00+09:00",
        "2024-11-05T15:40:00+09:00",
        "2024-11-05T15:00:00+09:00",
        "2024-11-05T15:10:00+09:00",
    )
    assert (
        tse_cash_close_reversal_event(new, rows(new, 0, -10), cash_calendar())["reason"] == "ZERO_P"
    )
    assert (
        tse_cash_close_reversal_event(new, rows(new, 10, 0), cash_calendar())["placebo_reason"]
        == "ZERO_P0"
    )
    missing = rows(new)
    del missing[35]
    assert (
        tse_cash_close_reversal_event(new, missing, cash_calendar())["reason"]
        == "P_WINDOW_T_MINUS_5_TO_T_MINUS_1_MISSING"
    )
    changed_after_t = [
        *rows(new, 10, -10),
        Bar(
            datetime(2024, 11, 5, 15, 50, tzinfo=JST),
            new,
            new,
            Session.DAY,
            "synthetic",
            1,
            999,
            1,
            999,
        ),
    ]
    assert event == tse_cash_close_reversal_event(new, changed_after_t, cash_calendar())


def test_calendar_quarantine_period_and_main_placebo_independence() -> None:
    day = date(2024, 11, 5)
    assert (
        tse_cash_close_reversal_event(date(2024, 11, 4), rows(date(2024, 11, 4)), cash_calendar())[
            "reason"
        ]
        == "TSE_CASH_MARKET_CLOSED"
    )
    assert (
        tse_cash_close_reversal_event(date(2025, 7, 1), rows(date(2025, 7, 1)), cash_calendar())[
            "reason"
        ]
        == "OUTSIDE_DEVELOPMENT"
    )
    assert (
        tse_cash_close_reversal_event(day, rows(day), cash_calendar(), day_quarantined=True)[
            "reason"
        ]
        == "DAY_SESSION_QUARANTINED"
    )
    independent = tse_cash_close_reversal_event(day, rows(day, 10, -10), cash_calendar())
    changed_p0 = tse_cash_close_reversal_event(day, rows(day, 10, 10), cash_calendar())
    changed_p = tse_cash_close_reversal_event(day, rows(day, -10, -10), cash_calendar())
    assert (
        independent["A_direction"] == changed_p0["A_direction"]
        and independent["G_direction"] != changed_p0["G_direction"]
    )
    assert (
        independent["G_direction"] == changed_p["G_direction"]
        and independent["A_direction"] != changed_p["A_direction"]
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]


@pytest.mark.parametrize("day", [date(2024, 11, 1), date(2024, 11, 5)])
def test_next_bar_fixed_exit_delay_nonextension_and_accounting(day: date) -> None:
    event = tse_cash_close_reversal_event(day, rows(day), cash_calendar())
    signal, exit_time = (
        datetime.fromisoformat(str(event["t_signal_bar_start_jst"])),
        datetime.fromisoformat(str(event["X_planned_exit_jst"])),
    )
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(
        rows(day), CashCloseReversalStrategy("r030", signal, exit_time, "short")
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.SHORT,
        signal + timedelta(minutes=1),
        exit_time,
        ExitReason.SIGNAL,
    )
    delayed = engine.run(
        [bar for bar in rows(day) if bar.ts_jst != signal + timedelta(minutes=1)],
        CashCloseReversalStrategy("r030-delay", signal, exit_time, "long"),
    ).trades[0]
    assert delayed.entry_ts == signal + timedelta(minutes=2) and delayed.exit_ts == exit_time
    assert delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
