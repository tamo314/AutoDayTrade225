from __future__ import annotations

from datetime import date, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r043 import precash_cash_conflict_event
from n225m_bt.strategies.precash_cash_conflict_followthrough import (
    PrecashCashConflictFollowthroughStrategy,
)


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset({date(2023, 5, 4)}), date(2021, 1, 1), date(2025, 12, 31))


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bar(stamp: datetime, day: date, open_: int, close: int, *, eligible: bool = True) -> Bar:
    return Bar(stamp, day, stamp.date(), Session.DAY, "synthetic", open_, max(open_, close), min(open_, close), close, is_eligible=eligible)


def rows(day: date, p: int = 10, c: int = -10, *, missing: datetime | None = None) -> list[Bar]:
    start, output = datetime(day.year, day.month, day.day, 8, 45, tzinfo=JST), []
    for offset in range(81):
        stamp, close = start + timedelta(minutes=offset), 100 + offset
        if offset == 14:
            close = 100 + p
        if offset == 19:
            close = 100 + c
        if stamp != missing:
            output.append(bar(stamp, day, 100, close))
    return output


def test_exact_windows_conflict_agreement_zero_and_prefix() -> None:
    day = date(2024, 11, 5)
    conflict = precash_cash_conflict_event(day, rows(day, 10, -10), calendar())
    agreement_up = precash_cash_conflict_event(day, rows(day, 10, 10), calendar())
    agreement_down = precash_cash_conflict_event(day, rows(day, -10, -10), calendar())
    assert (conflict["status"], conflict["A_direction"], conflict["F_precash_direction"]) == ("conflict", "short", "long")
    assert (agreement_up["status"], agreement_down["status"], agreement_down["rC_direction"]) == ("agreement", "agreement", "short")
    assert precash_cash_conflict_event(day, rows(day, 0, 10), calendar())["reason"] == "ZERO_rP"
    assert precash_cash_conflict_event(day, rows(day, 10, 0), calendar())["reason"] == "ZERO_rC"
    assert conflict == precash_cash_conflict_event(day, [*rows(day, 10, -10), bar(datetime(2024, 11, 5, 10, 30, tzinfo=JST), day, 1, 999)], calendar())


def test_rejections_and_fixed_execution_delay() -> None:
    day = date(2024, 11, 5)
    assert precash_cash_conflict_event(date(2023, 5, 4), rows(date(2023, 5, 4)), calendar())["reason"] == "TSE_CASH_MARKET_CLOSED"
    assert precash_cash_conflict_event(day, rows(day, missing=datetime(2024, 11, 5, 8, 50, tzinfo=JST)), calendar())["reason"] == "WINDOW_0845_TO_0859_MISSING"
    assert precash_cash_conflict_event(day, rows(day), calendar(), day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    instrument, _, _, config = load_project_config(Path("config"))
    signal = datetime(2024, 11, 5, 9, 4, tzinfo=JST)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(rows(day), PrecashCashConflictFollowthroughStrategy("r043", signal, "long")).trades[0]
    delayed = engine.run(rows(day), PrecashCashConflictFollowthroughStrategy("r043-delay", signal, "short", 1)).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts, delayed.exit_reason) == (signal + timedelta(minutes=2), signal + timedelta(minutes=61), ExitReason.SIGNAL)
    assert delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
    assert config.execution.allow_cross_session_pending_order is False
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]


def test_preregistered_ols_has_fixed_global_centers_and_requires_rank() -> None:
    source = Path("scripts/run_r043_q001_precash_cash_conflict_followthrough.py")
    spec = spec_from_file_location("r043_q001_ols_test", source)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    rows_for_ols: list[dict[str, object]] = []
    for index in range(48):
        p_sign = 1 if index % 2 else -1
        c_sign = 1 if (index // 2) % 2 else -1
        r_p = p_sign * (11 + (index * 7) % 83)
        r_c = c_sign * (13 + (index * 11) % 79)
        rows_for_ols.append(
            {
                "base_event_status": "conflict" if p_sign != c_sign else "agreement",
                "raw_return_reason": "OK",
                "rP_points": r_p,
                "rC_points": r_c,
                "open_0845_points": 10_000,
                "open_0900_points": 10_000,
                "rP_sign": p_sign,
                "trade_date": f"{2021 + index % 5}-01-01",
                "raw_sign_adjusted_0905_1005_jpy": index * 100,
            }
        )
    _, audit = module.ols_beta(rows_for_ols, (1.25, 2.5))
    assert audit["full_rank"] is True
    assert audit["means"] == {"zP": 1.25, "zC": 2.5}
    with pytest.raises(ValueError, match="full rank"):
        module.ols_beta([rows_for_ols[0], rows_for_ols[1]], (1.25, 2.5))
