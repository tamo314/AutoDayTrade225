from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r064 import (
    R064QNotIdentifiableError,
    fwl_delta,
    make_event,
    r064_exec_event,
    rolling_u_ledger,
)
from n225m_bt.strategies.r064_trend_pullback_continuation import (
    R064TrendPullbackContinuationStrategy,
)


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def source(index: int) -> dict[str, object]:
    return {
        "trade_date": f"2024-01-{index + 1:03d}",
        "u": {
            str(window): {"window": window, "u_member": True, "m": float(index + window)}
            for window in (45, 60, 75)
        },
        "future_response": "must_not_be_read",
    }


def frozen_u() -> dict[str, object]:
    return {
        "trade_date": "2024-11-05",
        "u": {
            str(window): {
                "u_member": True,
                "m": 0.02,
                "efficiency": 0.8,
                "rolling_valid": True,
                "q50": 0.01,
                "q70": 0.015,
                "q75": 0.02,
                "q80": 0.025,
            }
            for window in (45, 60, 75)
        },
    }


def day_rows(target: date) -> list[Bar]:
    start = classifier().session_open(target, Session.DAY)
    output: list[Bar] = []
    for index in range(136):
        close = 10_600 if index == 59 else 10_400 if 60 <= index <= 74 else 10_000 + index * 10
        output.append(
            Bar(
                start + timedelta(minutes=index),
                target,
                target,
                Session.DAY,
                "test",
                close,
                close + 5,
                close - 5,
                close,
            )
        )
    return output


def test_u_starts_at_100_is_prior_only_and_prefix_invariant() -> None:
    ledger = rolling_u_ledger([source(index) for index in range(102)])
    assert ledger[99]["u"]["60"]["rolling_valid"] is False
    first = ledger[100]["u"]["60"]
    assert first["rolling_valid"] is True
    assert first["rolling_valid_count"] == 100
    assert first["q75"] == 134.0
    assert all(day < ledger[100]["trade_date"] for day in first["rolling_reference_trade_dates"])
    assert ledger == rolling_u_ledger([*[source(index) for index in range(102)], source(102)])[:102]


def test_event_has_partial_non_destructive_pullback_and_fixed_paths() -> None:
    target = date(2024, 11, 5)
    event = make_event(classifier(), target, day_rows(target), set(), frozen_u())
    assert event["status"] == "E"
    assert event["cell"] == "A"  # q75 equality is extreme; p=20% is in R.
    assert event["planned_signal_jst"].endswith("09:59:00+09:00")
    assert event["planned_entry_jst"].endswith("10:00:00+09:00")
    assert (
        make_event(classifier(), target, day_rows(target)[:-1], set(), frozen_u())["reason"]
        == "TSE_COMMON_PATH_INVALID"
    )


def test_r1_exec_adapter_ignores_future_exit_availability() -> None:
    target = date(2024, 11, 5)
    adapter = r064_exec_event(classifier(), target, day_rows(target)[:-1], set(), frozen_u())
    legacy = make_event(classifier(), target, day_rows(target)[:-1], set(), frozen_u())
    assert adapter["status"] == "E_EXEC"
    assert adapter["selection_status"] == "A"
    assert legacy["reason"] == "TSE_COMMON_PATH_INVALID"


def test_next_open_delay_nonextension_and_accounting() -> None:
    target = date(2024, 11, 5)
    instrument, _, _, config = load_project_config(Path("config"))
    event = make_event(classifier(), target, day_rows(target), set(), frozen_u())
    signal, exit_ = (
        datetime.fromisoformat(str(event["planned_signal_jst"])),
        datetime.fromisoformat(str(event["planned_exit30_jst"])),
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    base = engine.run(
        day_rows(target), R064TrendPullbackContinuationStrategy("r064", signal, "long", exit_)
    ).trades
    delayed = engine.run(
        day_rows(target), R064TrendPullbackContinuationStrategy("r064d", signal, "long", exit_, 1)
    ).trades
    assert len(base) == len(delayed) == 1
    assert (base[0].side, base[0].entry_ts, base[0].exit_ts, base[0].exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        exit_,
        ExitReason.SIGNAL,
    )
    assert (delayed[0].entry_ts, delayed[0].exit_ts) == (signal + timedelta(minutes=2), exit_)
    assert base[0].net_pnl_jpy == base[0].gross_pnl_jpy - base[0].fees_jpy


def test_fwl_identification_contract() -> None:
    nuisance = np.asarray([[1.0, -1.0], [1.0, 0.0], [1.0, 1.0], [1.0, 2.0]])
    q, y = np.asarray([0.0, 1.0, 0.0, 1.0]), np.asarray([2.0, 6.0, 3.0, 10.0])
    delta, ss = fwl_delta(q, y, nuisance)
    assert delta == pytest.approx(
        np.linalg.lstsq(np.column_stack((nuisance, q)), y, rcond=None)[0][-1]
    )
    assert ss > 1e-12
    with pytest.raises(R064QNotIdentifiableError):
        fwl_delta(q, y, np.column_stack((nuisance, q)))
