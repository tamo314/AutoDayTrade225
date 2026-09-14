from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r028 import cash_open_discontinuity_event
from n225m_bt.strategies.cash_open_discontinuity import CashOpenDiscontinuityStrategy


def cash() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2024, 11, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def rows(day: date, p: int = 10, jump: int = -15) -> list[Bar]:
    start = datetime(day.year, day.month, day.day, 8, 45, tzinfo=JST)
    output: list[Bar] = []
    for offset in range(32):
        open_, close = 100, 100
        if offset == 14:
            close = 100 + p
        if offset == 15:
            open_ = 100 + p + jump
            close = open_ + 2
        output.append(Bar(start + timedelta(minutes=offset), day, day, Session.DAY, "synthetic", open_, max(open_, close), min(open_, close), close))
    return output


def test_calendar_continuity_signs_zero_and_development_boundary() -> None:
    day = date(2024, 11, 5)
    assert cash_open_discontinuity_event(day, rows(day, 10, -15), cash())["A_direction"] == "long"
    assert cash_open_discontinuity_event(day, rows(day, -10, 15), cash())["A_direction"] == "short"
    assert cash_open_discontinuity_event(day, rows(day, 0, 15), cash())["reason"] == "ZERO_P"
    assert cash_open_discontinuity_event(day, rows(day, 10, 0), cash())["reason"] == "ZERO_J"
    missing = rows(day)
    del missing[8]
    assert cash_open_discontinuity_event(day, missing, cash())["reason"] == "WINDOW_0845_TO_0900_MISSING"
    assert cash_open_discontinuity_event(date(2024, 11, 4), rows(date(2024, 11, 4)), cash())["reason"] == "TSE_CASH_MARKET_CLOSED"
    assert cash_open_discontinuity_event(day, rows(day), cash(), day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    assert cash_open_discontinuity_event(date(2025, 7, 1), rows(date(2025, 7, 1)), cash())["reason"] == "OUTSIDE_DEVELOPMENT"


def test_only_0900_open_affects_jump_and_later_ohlc_does_not() -> None:
    day = date(2024, 11, 5)
    base = rows(day, 10, -15)
    before = cash_open_discontinuity_event(day, base, cash())
    changed_open = [*base]
    k = changed_open[15]
    changed_open[15] = Bar(k.ts_jst, k.trade_date, k.calendar_date, k.session, "synthetic", 130, 999, 1, 2)
    altered = cash_open_discontinuity_event(day, changed_open, cash())
    assert (before["J_points"], before["A_direction"]) != (altered["J_points"], altered["A_direction"])
    changed_preopen = [*base]
    c = changed_preopen[14]
    changed_preopen[14] = Bar(c.ts_jst, c.trade_date, c.calendar_date, c.session, "synthetic", 100, 90, 90, 90)
    changed_preopen[15] = Bar(k.ts_jst, k.trade_date, k.calendar_date, k.session, "synthetic", 75, 999, 1, 2)
    different_p = cash_open_discontinuity_event(day, changed_preopen, cash())
    assert before["J_points"] == different_p["J_points"] and before["P_points"] != different_p["P_points"]
    assert before["A_direction"] == different_p["A_direction"] and before["F_preopen_direction"] != different_p["F_preopen_direction"]
    later = [*base]
    for index in range(15, len(later)):
        bar = later[index]
        later[index] = Bar(bar.ts_jst, bar.trade_date, bar.calendar_date, bar.session, "synthetic", bar.open, 999, 1, -500)
    after = cash_open_discontinuity_event(day, later, cash())
    assert {key: before[key] for key in ("P_points", "J_points", "A_direction", "D_follow_direction", "F_preopen_direction")} == {key: after[key] for key in ("P_points", "J_points", "A_direction", "D_follow_direction", "F_preopen_direction")}


def test_next_bar_entry_fixed_exit_no_extension_and_holdout_lock() -> None:
    day = date(2024, 11, 5)
    instrument, sessions, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    signal = datetime(2024, 11, 5, 9, tzinfo=JST)
    trade = engine.run(rows(day), CashOpenDiscontinuityStrategy("r028", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=16), ExitReason.SIGNAL)
    delayed_rows = [bar for bar in rows(day) if bar.ts_jst != signal + timedelta(minutes=1)]
    delayed = engine.run(delayed_rows, CashOpenDiscontinuityStrategy("r028-delay", signal, "short")).trades[0]
    assert delayed.entry_ts == signal + timedelta(minutes=2) and delayed.exit_ts == signal + timedelta(minutes=16)
    assert delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
