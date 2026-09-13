from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r006 import night_reference_time, reference_event
from n225m_bt.strategies.night_gap_reversal import NightGapReversalStrategy


def bar(stamp: datetime, session: Session, *, open_: int = 100, close: int = 100) -> Bar:
    return Bar(
        stamp,
        stamp.date(),
        stamp.date(),
        session,
        "synthetic",
        open_,
        max(open_, close),
        min(open_, close),
        close,
    )


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def run(condition: str, gap: int, bars: list[Bar]):
    instrument, sessions, _, config = load_project_config(Path("config"))
    strategy = NightGapReversalStrategy("r006-test", bars[0].ts_jst, condition, gap)  # type: ignore[arg-type]
    result = BacktestEngine(
        instrument.instrument.to_spec(), config, CalendarClassifier(sessions)
    ).run(bars, strategy)
    return result, strategy


def test_versioned_normal_night_bar_excludes_current_closing_auction() -> None:
    current = classifier()
    old_start, old_known, old_version, old_basis = night_reference_time(current, date(2021, 9, 17))
    boundary_start, boundary_known, boundary_version, _ = night_reference_time(
        current, date(2021, 9, 21)
    )
    extended_start, extended_known, extended_version, _ = night_reference_time(
        current, date(2021, 9, 22)
    )
    transition_start, transition_known, transition_version, _ = night_reference_time(
        current, date(2024, 11, 5)
    )
    auction_start, auction_known, auction_version, auction_basis = night_reference_time(
        current, date(2024, 11, 6)
    )
    assert (old_start.hour, old_start.minute, old_known.hour, old_known.minute) == (5, 29, 5, 30)
    assert (
        boundary_start.hour,
        boundary_start.minute,
        boundary_known.hour,
        boundary_known.minute,
    ) == (5, 29, 5, 30)
    assert (
        extended_start.hour,
        extended_start.minute,
        extended_known.hour,
        extended_known.minute,
    ) == (5, 59, 6, 0)
    assert (
        transition_start.hour,
        transition_start.minute,
        transition_known.hour,
        transition_known.minute,
    ) == (5, 59, 6, 0)
    assert (auction_start.hour, auction_start.minute, auction_known.hour, auction_known.minute) == (
        5,
        54,
        5,
        55,
    )
    assert (
        old_version.endswith("20210920")
        and boundary_version.endswith("20210920")
        and extended_version.endswith("20241104")
    )
    assert transition_version.endswith("20241104") and auction_version.endswith("20241105")
    assert old_basis == "scheduled_session_close_no_finer_auction_metadata"
    assert auction_basis == "regular_end_before_separate_closing_auction"


def test_weekend_mapping_and_exact_reference_bar_not_observed_last_row() -> None:
    current = classifier()
    trade_day = date(2024, 11, 11)  # Monday; the linked night started on Friday.
    start, _, _, _ = night_reference_time(current, trade_day)
    exchange_day = current.exchange_calendar.get(trade_day)
    assert exchange_day is not None and exchange_day.night_calendar_start_date == date(2024, 11, 8)
    S = current.session_open(trade_day, Session.DAY)
    day = [bar(S, Session.DAY, open_=120)]
    night = [
        bar(start - timedelta(minutes=1), Session.NIGHT, close=999),
        bar(start, Session.NIGHT, close=100),
    ]
    event = reference_event(current, trade_day, day, night)
    assert event["status"] == "eligible"
    assert event["C_night_normal_close"] == 100
    assert event["O_day_open"] == 120
    assert event["G_points"] == 20
    missing = reference_event(current, trade_day, day, night[:1])
    assert missing["reason"] == "REFERENCE_NORMAL_BAR_MISSING"


def test_gap_direction_next_bar_fill_fixed_exit_and_prefix_invariance() -> None:
    S = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    bars = [
        bar(S + timedelta(minutes=index), Session.DAY, open_=100 + index, close=100 + index)
        for index in range(62)
    ]
    result, strategy = run("A_gap_reversal", 10, bars)
    trade = result.trades[0]
    assert trade.side is Side.SHORT
    assert trade.entry_signal_ts == S and trade.entry_ts == S + timedelta(minutes=1)
    assert trade.exit_signal_ts == S + timedelta(minutes=60)
    assert trade.exit_ts == S + timedelta(minutes=61) and trade.exit_reason is ExitReason.SIGNAL
    assert strategy.finalize(bars)["exit_status"] == "exit_order_issued"
    long_result, _ = run("B_always_long", 10, bars)
    short_result, _ = run("C_always_short", -10, bars)
    assert long_result.trades[0].side is Side.LONG and short_result.trades[0].side is Side.SHORT
    delayed = [item for item in bars if item.ts_jst != S + timedelta(minutes=1)]
    delayed_result, _ = run("A_gap_reversal", -10, delayed)
    assert delayed_result.trades[0].entry_ts == S + timedelta(minutes=2)
    assert delayed_result.trades[0].exit_ts == S + timedelta(minutes=61)
    zero_result, zero_strategy = run("A_gap_reversal", 0, bars)
    assert not zero_result.trades and zero_strategy.event["reason"] == "ZERO_GAP"
