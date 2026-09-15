from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r049 import anchor_times, r049_event, r049_exec_candidate
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy
from scripts.run_r049_q001_same_clock_extreme_5m_fade import select


@lru_cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def cash() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset(), date(2020, 1, 1), date(2026, 1, 1))


def bars(day: date, *, missing: int | None = None, future_shift: int = 0) -> list[Bar]:
    start, rows = classifier().session_open(day, Session.DAY), []
    for i in range(330):
        if i == missing:
            continue
        close = 100 + (10 if i in {44, 74, 104, 239, 269, 299} else 0)
        if i > 320:
            close += future_shift
        rows.append(
            Bar(
                start + timedelta(minutes=i),
                day,
                day,
                Session.DAY,
                "synthetic",
                100,
                max(100, close),
                min(100, close),
                close,
            )
        )
    return rows


def history(target: date) -> list[tuple[date, list[Bar] | None, bool]]:
    return [
        (target - timedelta(days=i + 1), bars(target - timedelta(days=i + 1)), False)
        for i in range(120)
    ]


def test_all_anchor_quantiles_ties_prefix_and_common_e() -> None:
    day = date(2024, 11, 5)
    result = r049_event(day, bars(day), history(day), cash())
    assert result["status"] == "E"
    assert len(result["observations"]) == 6
    assert all(row["q90"] == 0.1 and row["q90_le_x"] for row in result["observations"])
    changed = bars(day, future_shift=999)
    assert result == r049_event(day, changed, history(day), cash())


def test_zero_missing_history_isolation_and_holdout_rejected() -> None:
    day = date(2024, 11, 5)
    assert (
        r049_event(day, bars(day, missing=45), history(day), cash())["reason"]
        == "TARGET_ANCHOR_INVALID"
    )
    zero = bars(day)
    at = anchor_times(day)["mS+30"]
    for i, row in enumerate(zero):
        if at - timedelta(minutes=5) <= row.ts_jst < at:
            zero[i] = Bar(row.ts_jst, day, day, Session.DAY, "synthetic", 100, 100, 100, 100)
    assert r049_event(day, zero, history(day), cash())["reason"] == "TARGET_ANCHOR_INVALID"
    assert (
        r049_event(day, bars(day), history(day)[:-1], cash())["reason"]
        == "HISTORY_NOT_EXACTLY_120_TSE_DAYS"
    )
    assert (
        r049_event(day, bars(day), history(day), cash(), target_quarantined=True)["reason"]
        == "DAY_SESSION_QUARANTINED"
    )
    assert (
        r049_event(date(2025, 7, 1), bars(day), history(day), cash())["reason"]
        == "OUTSIDE_DEVELOPMENT"
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]


def test_next_open_fixed_exit_delay_and_accounting() -> None:
    day, entry = date(2024, 11, 5), anchor_times(date(2024, 11, 5))["mS+30"]
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    normal = engine.run(
        bars(day), R049FixedTimeStrategy("r049", entry, entry + timedelta(minutes=15), "long")
    ).trades[0]
    delayed = engine.run(
        bars(day), R049FixedTimeStrategy("r049d", entry, entry + timedelta(minutes=15), "short", 1)
    ).trades[0]
    assert (normal.side, normal.entry_ts, normal.exit_ts, normal.exit_reason) == (
        Side.LONG,
        entry,
        entry + timedelta(minutes=15),
        ExitReason.SIGNAL,
    )
    assert (delayed.entry_ts, delayed.exit_ts) == (
        entry + timedelta(minutes=1),
        entry + timedelta(minutes=15),
    )
    assert normal.net_pnl_jpy == normal.gross_pnl_jpy - normal.fees_jpy


def test_independent_first_anchor_selection_when_moderate_precedes_extreme() -> None:
    event: dict[str, object] = {
        "status": "E",
        "observations": [
            {
                "anchor": "mS+30",
                "x": 0.80,
                "q75": 0.75,
                "q90": 0.90,
                "moderate_q75_q90": True,
            },
            {
                "anchor": "mS+60",
                "x": 0.95,
                "q75": 0.75,
                "q90": 0.90,
                "moderate_q75_q90": False,
            },
        ],
    }
    assert select("A_extreme_fade", event)["anchor"] == "mS+60"
    assert select("B_broad_fade", event, 75)["anchor"] == "mS+30"
    assert select("C_moderate_fade", event)["anchor"] == "mS+30"


def test_r1_exec_candidate_ignores_later_anchor_mutation() -> None:
    day = date(2024, 11, 5)
    original = r049_exec_candidate(day, "mS+30", bars(day), history(day), cash())
    later_anchor_missing = r049_exec_candidate(day, "mS+30", bars(day, missing=74), history(day), cash())
    assert original["status"] == later_anchor_missing["status"] == "E_EXEC"
    assert original == later_anchor_missing
    assert r049_event(day, bars(day, missing=74), history(day), cash())["status"] == "skipped"
