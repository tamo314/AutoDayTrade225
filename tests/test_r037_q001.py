from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r037 import opening_path_efficiency_event
from n225m_bt.strategies.opening_path_efficiency import OpeningPathEfficiencyStrategy

_runner_spec = spec_from_file_location(
    "r037_runner", Path("scripts/run_r037_q001_opening_path_efficiency.py")
)
assert _runner_spec is not None and _runner_spec.loader is not None
_runner = module_from_spec(_runner_spec)
_runner_spec.loader.exec_module(_runner)
bootstrap = _runner.bootstrap


@lru_cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars(day: date, closes: list[int] | None = None, *, missing: int | None = None) -> list[Bar]:
    start, rows = classifier().session_open(day, Session.DAY), []
    closes = closes or [110 if index % 2 == 0 else 90 for index in range(30)]
    for index in range(100):
        if index == missing:
            continue
        close = closes[index] if index < 30 else 100
        rows.append(
            Bar(
                start + timedelta(minutes=index),
                day,
                start.date(),
                Session.DAY,
                "synthetic",
                100,
                max(100, close),
                min(100, close),
                close,
            )
        )
    return rows


def prior_days(target: date) -> list[date]:
    output, candidate = [], target - timedelta(days=1)
    while len(output) < 60:
        try:
            classifier().session_open(candidate, Session.DAY)
            output.append(candidate)
        except ValueError:
            pass
        candidate -= timedelta(days=1)
    return output


def history(target: date) -> list[tuple[date, list[Bar] | None, bool]]:
    return [
        (
            day,
            bars(day, [110 if n % 2 == 0 else 90 for n in range(30)] if index < 45 else [110] * 30),
            False,
        )
        for index, day in enumerate(prior_days(target))
    ]


def test_exact_rank_tie_and_prefix_invariance() -> None:
    target, rows = date(2024, 11, 5), history(date(2024, 11, 5))
    event = opening_path_efficiency_event(
        classifier(), target, bars(target, [110 if n % 2 == 0 else 90 for n in range(30)]), rows
    )
    assert event["status"] == "noneff"  # eta ties the high threshold and must not pass.
    high = opening_path_efficiency_event(
        classifier(), target, bars(target, [101 + index for index in range(30)]), rows
    )
    assert high["status"] == "high_efficiency" and high["magnitude_bucket"] in {1, 2, 3, 4}
    changed = bars(target, [101 + index for index in range(30)])
    changed[-1] = Bar(
        changed[-1].ts_jst, target, target, Session.DAY, "synthetic", 999, 999, 999, 999
    )
    future = opening_path_efficiency_event(classifier(), target, changed, rows)
    for key in (
        "Q_eta_D_points",
        "Q_eta_V_points",
        "Q_abs_D_25_points",
        "Q_abs_D_50_points",
        "Q_abs_D_75_points",
    ):
        assert future[key] == high[key]


def test_zero_missing_quarantine_and_no_backfill() -> None:
    target, rows = date(2024, 11, 5), history(date(2024, 11, 5))
    assert (
        opening_path_efficiency_event(classifier(), target, bars(target, [100] * 30), rows)[
            "reason"
        ]
        == "TARGET_ZERO_D_OR_V"
    )
    assert (
        opening_path_efficiency_event(classifier(), target, bars(target, missing=4), rows)["reason"]
        == "TARGET_OPENING_MISSING_OR_INELIGIBLE"
    )
    bad = [*rows]
    for index in range(11):
        bad[index] = (bad[index][0], None, False)
    assert (
        opening_path_efficiency_event(classifier(), target, bars(target), bad)["reason"]
        == "INSUFFICIENT_VALID_REFERENCES"
    )
    assert (
        opening_path_efficiency_event(
            classifier(), target, bars(target), rows, target_quarantined=True
        )["reason"]
        == "TARGET_QUARANTINED"
    )
    assert (
        opening_path_efficiency_event(classifier(), date(2025, 7, 1), bars(target), rows)["reason"]
        == "TARGET_OUTSIDE_DEVELOPMENT"
    )


def test_next_bar_fixed_exit_delay_accounting_and_holdout_lock() -> None:
    day, signal = (
        date(2024, 11, 5),
        classifier().session_open(date(2024, 11, 5), Session.DAY) + timedelta(minutes=29),
    )
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(bars(day), OpeningPathEfficiencyStrategy("r037", signal, "long")).trades[0]
    delayed = engine.run(
        bars(day), OpeningPathEfficiencyStrategy("r037-delay", signal, "short", 1)
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    assert (delayed.entry_ts, delayed.exit_ts) == (
        signal + timedelta(minutes=2),
        signal + timedelta(minutes=61),
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]


def test_delta_m_is_unavailable_when_a_required_magnitude_stratum_is_empty() -> None:
    days = [f"2024-01-{index:02d}" for index in range(1, 21)]
    values = {
        name: dict.fromkeys(days, 0)
        for name in ("A_eff", "B_all", "C_noneff", "D_buy", "E_sell", "F_reverse")
    }
    events = {name: [] for name in values}
    events["B_all"] = [
        {
            "trade_date": day,
            "status": "filled",
            "net_pnl_jpy": 0,
            "base_event_status": "noneff",
            "magnitude_bucket": 1,
        }
        for day in days
    ]
    result = bootstrap(values, events)
    assert result["magnitude_adjusted_delta_M_jpy"] == {
        "estimate": None,
        "ci95_percentile_linear": None,
        "status": "NOT_ESTIMABLE_INFORMATION_GATE",
        "definition": "ΔM requires all four observed magnitude strata to contain high>=20 and noneff>=60; no zero is substituted for an empty stratum.",
    }
