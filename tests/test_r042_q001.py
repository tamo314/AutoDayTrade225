from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r042 import night_terminal_range_event
from n225m_bt.strategies.night_terminal_range_followthrough import (
    NightTerminalRangeFollowthroughStrategy,
)

DAY = date(2024, 11, 5)


@cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def make_bar(
    stamp: datetime, trade_day: date, session: Session, open_: int, high: int, low: int, close: int
) -> Bar:
    return Bar(stamp, trade_day, stamp.date(), session, "synthetic", open_, high, low, close)


def night(
    day: date, *, on: int = 100, hn: int = 120, ln: int = 90, cn: int = 115, missing: bool = False
) -> list[Bar]:
    current = classifier()
    start, end = (
        current.session_open(day, Session.NIGHT),
        normal_session_end(current, day, Session.NIGHT),
    )
    count = int((end - start).total_seconds() // 60)
    rows = [
        make_bar(
            start + timedelta(minutes=i),
            day,
            Session.NIGHT,
            on,
            hn if i == 1 else on,
            ln if i == 2 else on,
            cn if i == count - 1 else on,
        )
        for i in range(count)
    ]
    if missing:
        rows.pop(3)
    return rows


def day_bars(day: date) -> list[Bar]:
    start = datetime(day.year, day.month, day.day, 8, 45, tzinfo=JST)
    return [
        make_bar(start + timedelta(minutes=i), day, Session.DAY, 100 + i, 105 + i, 95 + i, 100 + i)
        for i in range(62)
    ]


def history() -> list[tuple[date, list[Bar] | None, bool]]:
    dates = [
        item.trade_date
        for item in reversed(classifier().exchange_calendar.trading_days())
        if item.trade_date < DAY
    ][:60]
    return [(item, night(item, hn=120, ln=100, cn=110), False) for item in dates]


def event(**kwargs: object) -> dict[str, object]:
    return night_terminal_range_event(
        classifier(), DAY, night(DAY, hn=120, ln=100, cn=115), history(), **kwargs
    )


def test_schedule_mapping_terminal_equality_and_rolling_tie() -> None:
    result = event()
    assert (
        result["status"],
        result["A_direction"],
        result["range_layer"],
        result["QM_RN_over_ON"],
    ) == ("terminal", "long", "low", 0.2)
    equal = night_terminal_range_event(
        classifier(), DAY, night(DAY, hn=120, ln=100, cn=115), history()
    )
    assert equal["status"] == "terminal"
    down = night_terminal_range_event(
        classifier(), DAY, night(DAY, hn=120, ln=90, cn=98), history()
    )
    assert (down["status"], down["A_direction"]) == ("nonterminal", "short")
    assert normal_session_end(classifier(), date(2021, 9, 21), Session.NIGHT) == datetime(
        2021, 9, 21, 5, 30, tzinfo=JST
    )
    assert normal_session_end(classifier(), DAY, Session.NIGHT) == datetime(
        2024, 11, 5, 6, 0, tzinfo=JST
    )


def test_rejections_exact_history_no_backfill_and_prefix_invariance() -> None:
    assert (
        night_terminal_range_event(classifier(), DAY, night(DAY, missing=True), history())["reason"]
        == "REFERENCE_FULL_NORMAL_NIGHT_MISSING"
    )
    assert event(day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
    assert event(night_quarantined=True)["reason"] == "REFERENCE_NIGHT_SESSION_QUARANTINED"
    fifty = [*history()[:50], *((day, None, False) for day, _, _ in history()[50:])]
    result = night_terminal_range_event(classifier(), DAY, night(DAY), fifty)
    assert result["valid_reference_count"] == 50
    forty_nine = [*fifty[:49], (fifty[49][0], None, False), *fifty[50:]]
    assert (
        night_terminal_range_event(classifier(), DAY, night(DAY), forty_nine)["reason"]
        == "INSUFFICIENT_VALID_RN_OVER_ON_REFERENCES"
    )
    for key in (
        "status",
        "A_direction",
        "terminal",
        "QM_RN_over_ON",
        "range_layer",
        "ON_points",
        "CN_points",
        "HN_points",
        "LN_points",
    ):
        assert event()[key] == event()[key]


def test_q003_removes_only_rolling_range_eligibility() -> None:
    result = night_terminal_range_event(
        classifier(),
        DAY,
        night(DAY, hn=120, ln=100, cn=115),
        [],
        require_rolling_range_layer=False,
    )
    assert result["status"] == "terminal"
    assert result["A_direction"] == "long"
    assert result["terminal"] is True
    assert "QM_RN_over_ON" not in result
    assert "range_layer" not in result


def test_q003_ols_uses_the_preregistered_global_center() -> None:
    source = Path("scripts/run_r042_q003_night_terminal_continuous_range.py")
    spec = spec_from_file_location("r042_q003_ols_test", source)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [
        {
            "raw_return_reason": "OK",
            "base_event_status": "terminal" if index % 2 else "nonterminal",
            "ON_points": 100.0,
            "RN_points": 100.0 + index,
            "rN_points": 1.0 if index % 3 else -1.0,
            "trade_date": f"{2021 + index % 5}-01-01",
            "raw_sign_adjusted_0846_0946_jpy": float(index),
        }
        for index in range(20)
    ]
    _, audit = module.ols_beta(rows, fixed_mean_z=1.25)
    assert audit["mean_z"] == 1.25


def test_fresh_day_order_contract_and_holdout_lock() -> None:
    instrument, _, _, config = load_project_config(Path("config"))
    signal = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(
        day_bars(DAY), NightTerminalRangeFollowthroughStrategy("r042", signal, "long")
    ).trades[0]
    delayed = engine.run(
        day_bars(DAY), NightTerminalRangeFollowthroughStrategy("r042-delay", signal, "short", 1)
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    assert (delayed.entry_ts, delayed.exit_ts, delayed.exit_reason) == (
        signal + timedelta(minutes=2),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    tse = engine.run(
        day_bars(DAY),
        NightTerminalRangeFollowthroughStrategy(
            "r042-tse",
            signal + timedelta(minutes=14),
            "long",
            exit_signal_time=signal + timedelta(minutes=60),
        ),
    ).trades[0]
    assert (tse.entry_ts, tse.exit_ts, tse.exit_reason) == (
        signal + timedelta(minutes=15),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
    assert trade.gross_pnl_jpy == 5_000
    assert trade.gross_pnl_jpy == (115 - (101 + 5)) * 100 + ((161 - 5) - 115) * 100
    assert config.execution.allow_cross_session_pending_order is False
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths("unused", "final_holdout")  # type: ignore[arg-type]
