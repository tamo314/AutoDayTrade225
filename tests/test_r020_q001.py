from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar, tse_lunch_event
from n225m_bt.strategies.lunch_reversal import LunchReversalStrategy


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2023, 5, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def rows(day: date, p: int = 10, lunch_change: int = 10) -> list[Bar]:
    start = datetime(day.year, day.month, day.day, 10, 30, tzinfo=JST)
    output: list[Bar] = []
    for offset in range(181):
        open_ = 100
        close = 100
        if offset == 59:
            close = 100 + p
        if offset == 119:
            close = 100 + lunch_change
        output.append(Bar(start + timedelta(minutes=offset), day, day, Session.DAY, "synthetic", open_, max(open_, close), min(open_, close), close))
    return output


def test_tse_cash_calendar_does_not_infer_from_ose_and_lunch_boundaries_are_exact() -> None:
    assert calendar().is_open(date(2023, 5, 2))
    assert not calendar().is_open(date(2023, 5, 4))  # OSE holiday trading does not make TSE open.
    result = tse_lunch_event(date(2024, 11, 5), rows(date(2024, 11, 5), 10, -15), calendar())
    assert (result["P_points"], result["L_points"], result["A_direction"], result["D_direction"], result["F_direction"]) == (10, -15, "long", "short", "short")
    assert (result["P_window_end_jst"], result["L_window_start_jst"], result["t_signal_bar_start_jst"], result["X_planned_exit_jst"]) == ("2024-11-05T11:29:00+09:00", "2024-11-05T11:30:00+09:00", "2024-11-05T12:29:00+09:00", "2024-11-05T13:30:00+09:00")
    assert tse_lunch_event(date(2023, 5, 4), rows(date(2023, 5, 4)), calendar())["reason"] == "TSE_CASH_MARKET_CLOSED"


def test_p_l_zero_missing_prefix_and_direction_relationships() -> None:
    day = date(2024, 11, 5)
    assert tse_lunch_event(day, rows(day, 0, 10), calendar())["reason"] == "ZERO_P"
    assert tse_lunch_event(day, rows(day, 10, 0), calendar())["reason"] == "ZERO_L"
    missing = rows(day, 10, 10)
    del missing[60]
    assert tse_lunch_event(day, missing, calendar())["reason"] == "WINDOW_1030_TO_1229_MISSING"
    same = tse_lunch_event(day, rows(day, 10, 10), calendar())
    opposite = tse_lunch_event(day, rows(day, 10, -10), calendar())
    assert same["A_direction"] == same["D_direction"]
    assert opposite["A_direction"] != opposite["D_direction"]
    assert same == tse_lunch_event(day, rows(day, 10, 10) + rows(day, 10, 10)[120:], calendar())


def test_next_open_fixed_exit_no_extension_and_final_holdout_rejected() -> None:
    day = date(2024, 11, 5)
    instrument, sessions, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    all_rows = rows(day, 10, -10)
    signal = datetime(2024, 11, 5, 12, 29, tzinfo=JST)
    trade = engine.run(all_rows, LunchReversalStrategy("r020", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    delayed = engine.run([bar for bar in all_rows if bar.ts_jst != signal + timedelta(minutes=1)], LunchReversalStrategy("r020-delay", signal, "short")).trades[0]
    assert (delayed.entry_ts, delayed.exit_ts, delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy) == (signal + timedelta(minutes=2), signal + timedelta(minutes=61), True)
    try:
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
    except ValueError as exc:
        assert "Final Holdout is locked" in str(exc)
    else:
        raise AssertionError("Final Holdout selection unexpectedly succeeded")
