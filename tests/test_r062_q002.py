from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r062_q002 import r062_q002_event, rolling_u_ledger
from n225m_bt.strategies.r062_overnight_inventory_rejection import (
    R062OvernightInventoryRejectionStrategy,
)
from scripts.run_r062_q002_overnight_inventory_rejection_fixed_u import direction


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def candidate(index: int, *, x: float | None = None) -> dict[str, object]:
    return {"trade_date": f"2024-01-{index + 1:03d}", "u_member": x is not None, "x": x}


def day_rows(target: date) -> list[Bar]:
    start = classifier().session_open(target, Session.DAY)
    output: list[Bar] = []
    for ordinal in range(66):
        close = 9_995 if ordinal == 14 else 10_000
        output.append(Bar(start + timedelta(minutes=ordinal), target, target, Session.DAY, "test", 10_000, 10_005, 9_995, close))
    return output


def frozen_u(target: date) -> dict[str, object]:
    return {
        "trade_date": target.isoformat(), "u_member": True, "x": 0.02, "rN": 0.02,
        "oN_points": 10_000, "cN_points": 10_200, "prior_tse_trade_date": "2024-11-01",
        "ose_night_calendar_start_date": "2024-11-01", "rolling_u_valid": True,
        "rolling_u_reference_count": 120, "rolling_u_reference_trade_dates": ["2024-01-01"],
        "q50": 0.01, "q70": 0.015, "q75": 0.02, "q80": 0.025,
    }


def test_u_membership_and_thresholds_are_prefix_causal() -> None:
    # No TSE information is an input to a U candidate.  The first threshold is
    # available only after exactly 100 *previous* U members.
    source = [candidate(index, x=float(index + 1)) for index in range(102)]
    ledger = rolling_u_ledger(source)
    assert ledger[99]["rolling_u_valid"] is False
    assert ledger[100]["rolling_u_valid"] is True
    assert ledger[100]["rolling_u_reference_count"] == 100
    assert ledger[100]["q75"] == 75.0
    assert "2024-01-101" not in ledger[100]["rolling_u_reference_trade_dates"]
    extended = rolling_u_ledger([*source, candidate(102, x=9_999.0)])
    assert ledger == extended[: len(ledger)]


def test_event_requires_only_prior_u_and_common_execution_path() -> None:
    target = date(2024, 11, 5)
    event = r062_q002_event(classifier(), target, day_rows(target), frozen_u(target))
    assert event["status"] == "E"
    assert event["cell"] == "A"  # q75 equality remains upper/extreme.
    assert event["planned_signal_jst"].endswith("08:59:00+09:00")
    assert event["planned_entry_jst"].endswith("09:00:00+09:00")
    assert r062_q002_event(classifier(), target, day_rows(target)[:-1], frozen_u(target))["reason"] == "TSE_COMMON_EXECUTION_PATH_INVALID"
    insufficient = dict(frozen_u(target), rolling_u_valid=False, rolling_u_reference_count=99)
    assert r062_q002_event(classifier(), target, day_rows(target), insufficient)["reason"] == "INSUFFICIENT_PRIOR_U_REFERENCES"


def test_next_open_fixed_exit_delay_nonextension_and_one_position() -> None:
    target = date(2024, 11, 5)
    instrument, _, _, config = load_project_config(Path("config"))
    event = r062_q002_event(classifier(), target, day_rows(target), frozen_u(target))
    signal = datetime.fromisoformat(str(event["planned_signal_jst"]))
    exit_ = datetime.fromisoformat(str(event["planned_exit30_jst"]))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    base = engine.run(day_rows(target), R062OvernightInventoryRejectionStrategy("r062", signal, "short", exit_)).trades
    delayed = engine.run(day_rows(target), R062OvernightInventoryRejectionStrategy("r062d", signal, "short", exit_, 1)).trades
    assert len(base) == len(delayed) == 1
    assert (base[0].side, base[0].entry_ts, base[0].exit_ts, base[0].exit_reason) == (Side.SHORT, signal + timedelta(minutes=1), exit_, ExitReason.SIGNAL)
    assert (delayed[0].entry_ts, delayed[0].exit_ts) == (signal + timedelta(minutes=2), exit_)
    assert base[0].net_pnl_jpy == base[0].gross_pnl_jpy - base[0].fees_jpy


def test_same_event_follow_and_fixed_controls_use_their_declared_sides() -> None:
    event = {"night_sign": 1}
    assert direction("A", event) == "short"
    assert direction("A_follow", event) == "long"
    assert direction("A_buy", event) == "long"
    assert direction("A_sell", event) == "short"
