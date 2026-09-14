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
from n225m_bt.research.r021 import opening_to_cash_close_event, tse_cash_close
from n225m_bt.strategies.opening_cash_close import OpeningCashCloseStrategy


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(
        frozenset({date(2023, 5, 4)}), date(2021, 1, 1), date(2025, 12, 31)
    )


def rows(day: date, o: int = 10, m: int = 10) -> list[Bar]:
    end = tse_cash_close(day)
    start = datetime(day.year, day.month, day.day, 8, 45, tzinfo=JST)
    output: list[Bar] = []
    for offset in range(int((end - start).total_seconds() // 60) + 2):
        stamp, open_, close = start + timedelta(minutes=offset), 100, 100
        if stamp.hour == 9 and stamp.minute == 29:
            close = 100 + o
        if stamp == end - timedelta(minutes=31):
            close = 100 + m
        output.append(
            Bar(
                stamp,
                day,
                day,
                Session.DAY,
                "synthetic",
                open_,
                max(open_, close),
                min(open_, close),
                close,
            )
        )
    return output


def test_tse_close_versions_windows_and_o_m_directions() -> None:
    assert tse_cash_close(date(2024, 11, 1)).isoformat().endswith("15:00:00+09:00")
    assert tse_cash_close(date(2024, 11, 5)).isoformat().endswith("15:30:00+09:00")
    event = opening_to_cash_close_event(
        date(2024, 11, 5), rows(date(2024, 11, 5), 10, -15), calendar()
    )
    assert (
        event["O_points"],
        event["M_points"],
        event["A_direction"],
        event["D_direction"],
        event["F_reverse_direction"],
    ) == (10, -15, "long", "short", "short")
    assert (
        event["O_window_end_jst"],
        event["M_window_start_jst"],
        event["E_planned_entry_jst"],
        event["X_planned_exit_jst"],
    ) == (
        "2024-11-05T09:29:00+09:00",
        "2024-11-05T14:30:00+09:00",
        "2024-11-05T15:00:00+09:00",
        "2024-11-05T15:30:00+09:00",
    )


def test_zero_missing_prefix_and_independent_o_m_inputs() -> None:
    day = date(2024, 11, 5)
    assert opening_to_cash_close_event(day, rows(day, 0, 10), calendar())["reason"] == "ZERO_O"
    assert opening_to_cash_close_event(day, rows(day, 10, 0), calendar())["reason"] == "ZERO_M"
    missing = rows(day, 10, 10)
    del missing[15]
    assert (
        opening_to_cash_close_event(day, missing, calendar())["reason"]
        == "O_WINDOW_0900_TO_0929_MISSING"
    )
    same_m = opening_to_cash_close_event(day, rows(day, 10, -10), calendar())
    changed_o = opening_to_cash_close_event(day, rows(day, -10, -10), calendar())
    changed_m = opening_to_cash_close_event(day, rows(day, 10, 10), calendar())
    assert (
        same_m["A_direction"] != changed_o["A_direction"]
        and same_m["D_direction"] == changed_o["D_direction"]
    )
    assert (
        same_m["A_direction"] == changed_m["A_direction"]
        and same_m["D_direction"] != changed_m["D_direction"]
    )
    assert same_m == opening_to_cash_close_event(
        day, rows(day, 10, -10) + rows(day, 10, -10)[-1:], calendar()
    )


def test_engine_next_open_fixed_cash_close_exit_and_holdout_lock() -> None:
    day, signal, exit_time = (
        date(2024, 11, 5),
        datetime(2024, 11, 5, 14, 59, tzinfo=JST),
        datetime(2024, 11, 5, 15, 30, tzinfo=JST),
    )
    instrument, sessions, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(
        instrument.instrument.to_spec(),
        config,
        CalendarClassifier(
            sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
        ),
    )
    trade = engine.run(
        rows(day), OpeningCashCloseStrategy("r021", signal, exit_time, "long")
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        exit_time,
        ExitReason.SIGNAL,
    )
    entry_delayed = engine.run(
        [bar for bar in rows(day) if bar.ts_jst != signal + timedelta(minutes=1)],
        OpeningCashCloseStrategy("r021-entry-delay", signal, exit_time, "short"),
    ).trades[0]
    assert entry_delayed.entry_ts == signal + timedelta(minutes=2)
    assert entry_delayed.exit_ts == exit_time
    delayed = engine.run(
        [bar for bar in rows(day) if bar.ts_jst != exit_time],
        OpeningCashCloseStrategy("r021-exit-delay", signal, exit_time, "short"),
    ).trades[0]
    assert (
        delayed.exit_ts == exit_time + timedelta(minutes=1)
        and delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
