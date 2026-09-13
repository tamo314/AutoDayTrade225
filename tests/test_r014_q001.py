from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r014 import range_midpoint_event
from n225m_bt.strategies.range_midpoint import RangeMidpointStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bars_for(start: datetime, closes: list[int], *, missing: set[int] | None = None) -> list[Bar]:
    result: list[Bar] = []
    for index, close in enumerate(closes):
        if index in (missing or set()):
            continue
        prior = closes[index - 1] if index else close
        result.append(
            Bar(
                start + timedelta(minutes=index),
                start.date(),
                (start + timedelta(minutes=index)).date(),
                Session.DAY,
                "synthetic",
                prior,
                max(prior, close),
                min(prior, close),
                close,
            )
        )
    return result


def event(closes: list[int], *, missing: set[int] | None = None) -> dict[str, object]:
    current, day = classifier(), date(2024, 11, 5)
    return range_midpoint_event(
        current,
        day,
        Session.DAY,
        bars_for(current.session_open(day, Session.DAY), closes, missing=missing),
    )


def baseline_closes() -> list[int]:
    return [100] * 119 + [110] + [110] * 61


def test_61_input_60_range_current_inclusion_and_signs() -> None:
    result = event(baseline_closes())
    assert (
        result["status"],
        result["required_window_bar_count"],
        result["range_bar_count"],
        result["K_points_times_1"],
        result["Delta_points"],
        result["A_direction"],
        result["D_direction"],
    ) == ("eligible", 61, 60, 10, 10, "long", "long")
    closes = [100] * 119 + [90] + [90] * 61
    result = event(closes)
    assert (
        result["K_points_times_1"],
        result["Delta_points"],
        result["A_direction"],
        result["D_direction"],
    ) == (-10, -10, "short", "short")


def test_all_sign_combinations_zero_half_midpoint_and_high_low_only_flip() -> None:
    closes = baseline_closes()
    start = classifier().session_open(date(2024, 11, 5), Session.DAY)
    source = bars_for(start, closes)
    # Same closes; changing only a legal OHLC high changes K from + to -.
    changed = list(source)
    changed[100] = Bar(
        changed[100].ts_jst,
        changed[100].trade_date,
        changed[100].calendar_date,
        Session.DAY,
        "synthetic",
        100,
        130,
        100,
        100,
    )
    first, second = (
        range_midpoint_event(classifier(), date(2024, 11, 5), Session.DAY, source),
        range_midpoint_event(classifier(), date(2024, 11, 5), Session.DAY, changed),
    )
    assert (
        first["A_direction"],
        first["D_direction"],
        second["A_direction"],
        second["D_direction"],
    ) == ("long", "long", "short", "long")
    k_up_delta_down = list(source)
    k_up_delta_down[119] = Bar(
        k_up_delta_down[119].ts_jst,
        k_up_delta_down[119].trade_date,
        k_up_delta_down[119].calendar_date,
        Session.DAY,
        "synthetic",
        100,
        100,
        70,
        90,
    )
    opposite_result = range_midpoint_event(
        classifier(), date(2024, 11, 5), Session.DAY, k_up_delta_down
    )
    assert (opposite_result["A_direction"], opposite_result["D_direction"]) == ("long", "short")
    half = list(source)
    half[119] = Bar(
        half[119].ts_jst,
        half[119].trade_date,
        half[119].calendar_date,
        Session.DAY,
        "synthetic",
        100,
        111,
        100,
        110,
    )
    assert (
        range_midpoint_event(classifier(), date(2024, 11, 5), Session.DAY, half)["K_points_times_1"]
        == 9
    )
    zero_k = list(source)
    zero_k[119] = Bar(
        zero_k[119].ts_jst,
        zero_k[119].trade_date,
        zero_k[119].calendar_date,
        Session.DAY,
        "synthetic",
        100,
        120,
        100,
        110,
    )
    assert (
        range_midpoint_event(classifier(), date(2024, 11, 5), Session.DAY, zero_k)["reason"]
        == "ZERO_K"
    )
    zero_delta = list(source)
    zero_delta[119] = Bar(
        zero_delta[119].ts_jst,
        zero_delta[119].trade_date,
        zero_delta[119].calendar_date,
        Session.DAY,
        "synthetic",
        100,
        120,
        100,
        100,
    )
    assert (
        range_midpoint_event(classifier(), date(2024, 11, 5), Session.DAY, zero_delta)["reason"]
        == "ZERO_DELTA"
    )
    assert event([100] * 181)["reason"] == "NONPOSITIVE_RANGE"


def test_missing_ineligible_boundary_prefix_and_execution_contract() -> None:
    closes, current, day = baseline_closes(), classifier(), date(2024, 11, 5)
    assert event(closes, missing={90})["reason"] == "WINDOW_MISSING"
    start, rows = (
        current.session_open(day, Session.DAY),
        bars_for(closes=closes, start=current.session_open(day, Session.DAY)),
    )
    ineligible = list(rows)
    ineligible[90] = Bar(
        ineligible[90].ts_jst,
        ineligible[90].trade_date,
        ineligible[90].calendar_date,
        ineligible[90].session,
        ineligible[90].schedule_version,
        ineligible[90].open,
        ineligible[90].high,
        ineligible[90].low,
        ineligible[90].close,
        is_eligible=False,
    )
    assert (
        range_midpoint_event(current, day, Session.DAY, ineligible)["reason"]
        == "WINDOW_INELIGIBLE_OR_SESSION_MISMATCH"
    )
    wrong = [
        Bar(
            row.ts_jst,
            row.trade_date,
            row.calendar_date,
            Session.NIGHT,
            row.schedule_version,
            row.open,
            row.high,
            row.low,
            row.close,
        )
        for row in rows
    ]
    assert (
        range_midpoint_event(current, day, Session.DAY, wrong)["reason"]
        == "WINDOW_INELIGIBLE_OR_SESSION_MISMATCH"
    )
    assert event(closes) == event(closes + [999] * 120)
    instrument, sessions, _, config = load_project_config(Path("config"))
    engine, signal = (
        BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions)),
        start + timedelta(minutes=119),
    )
    trade = engine.run(rows, RangeMidpointStrategy("r014", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    delayed = engine.run(
        [row for row in rows if row.ts_jst != signal + timedelta(minutes=1)],
        RangeMidpointStrategy("r014-delay", signal, "short"),
    ).trades[0]
    assert (
        delayed.entry_ts,
        delayed.exit_ts,
        delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy,
    ) == (signal + timedelta(minutes=2), signal + timedelta(minutes=61), True)
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
