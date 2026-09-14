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
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r048 import r048_event
from n225m_bt.strategies.r048_fixed_time import R048FixedTimeStrategy


@lru_cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


DAY = date(2024, 11, 5)


def inputs(
    *, return_offset: int = 2, opposite: bool = False, missing: int | None = None
) -> tuple[list[Bar], list[Bar]]:
    day_start = classifier().session_open(DAY, Session.DAY)
    night_end = normal_session_end(classifier(), DAY, Session.NIGHT)
    night = [
        Bar(
            night_end - timedelta(minutes=30 - i),
            DAY,
            (night_end - timedelta(minutes=30 - i)).date(),
            Session.NIGHT,
            "synthetic",
            100,
            110,
            90,
            100,
        )
        for i in range(30)
    ]
    day: list[Bar] = []
    for i in range(156):
        if i == missing:
            continue
        close = 100
        if 0 <= i < return_offset or 90 <= i < 90 + return_offset:
            close = 111
        if i == return_offset or i == 90 + return_offset:
            close = 89 if opposite else 100
        high, low = max(100, close), min(100, close)
        if 60 <= i < 90:
            high, low = 110, 90
        day.append(
            Bar(
                day_start + timedelta(minutes=i),
                DAY,
                DAY,
                Session.DAY,
                "synthetic",
                100,
                high,
                low,
                close,
            )
        )
    return day, night


def event(**kwargs: object) -> dict[str, object]:
    day, night = inputs(**kwargs)
    return r048_event(classifier(), DAY, day, night)


def test_first_strict_close_return_ambiguity_and_horizons() -> None:
    selected = event()
    start = classifier().session_open(DAY, Session.DAY)
    assert (selected["status"], selected["main_status"], selected["placebo_status"]) == (
        "E",
        "FAILED_BREAKOUT",
        "FAILED_BREAKOUT",
    )
    assert selected["main_entry_jst"] == (start + timedelta(minutes=3)).isoformat()
    assert selected["main_exit_jst"] == (start + timedelta(minutes=33)).isoformat()
    assert selected["placebo_H_points"] == 110 and selected["placebo_L_points"] == 90
    day, night = inputs(return_offset=4)
    assert (
        r048_event(classifier(), DAY, day, night, horizon=3)["main_status"] == "BREAKOUT_NO_RETURN"
    )
    assert r048_event(classifier(), DAY, day, night, horizon=7)["main_status"] == "FAILED_BREAKOUT"
    assert event(opposite=True)["main_status"] == "AMBIGUOUS_OPPOSITE_BOUNDARY"


def test_common_e_missing_quarantine_holdout_and_prefix() -> None:
    day, night = inputs(missing=124)
    assert (
        r048_event(classifier(), DAY, day, night)["reason"]
        == "COMMON_REQUIRED_SCHEDULED_BARS_MISSING"
    )
    day, night = inputs()
    assert (
        r048_event(classifier(), DAY, day, night, day_quarantined=True)["reason"]
        == "DAY_OR_SAME_TRADE_DATE_NIGHT_QUARANTINED"
    )
    assert r048_event(classifier(), date(2025, 7, 1), day, night)["reason"] == "OUTSIDE_DEVELOPMENT"
    changed = [*day]
    changed[-1] = Bar(changed[-1].ts_jst, DAY, DAY, Session.DAY, "synthetic", 999, 999, 999, 999)
    assert r048_event(classifier(), DAY, day, night) == r048_event(
        classifier(), DAY, changed, night
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]


def test_next_open_fixed_exit_delay_and_accounting() -> None:
    instrument, _, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    day, night = inputs()
    selected = r048_event(classifier(), DAY, day, night)
    signal = __import__("datetime").datetime.fromisoformat(str(selected["main_return_jst"]))
    exit_time = __import__("datetime").datetime.fromisoformat(str(selected["main_exit_jst"]))
    trade = engine.run(day, R048FixedTimeStrategy("r048", signal, exit_time, "short")).trades[0]
    delayed = engine.run(
        day, R048FixedTimeStrategy("r048-delay", signal, exit_time, "long", 1)
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.SHORT,
        signal + timedelta(minutes=1),
        exit_time,
        ExitReason.SIGNAL,
    )
    assert (delayed.entry_ts, delayed.exit_ts) == (signal + timedelta(minutes=2), exit_time)
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
