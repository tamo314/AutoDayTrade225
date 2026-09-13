from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r006 import night_reference_time
from n225m_bt.research.r012 import night_direction_event
from n225m_bt.strategies.night_direction_followthrough import NightDirectionFollowThroughStrategy


def make_bar(
    stamp: datetime,
    session: Session,
    *,
    trade_date: date | None = None,
    open_: int = 100,
    close: int = 100,
    eligible: bool = True,
) -> Bar:
    return Bar(
        stamp,
        trade_date or stamp.date(),
        stamp.date(),
        session,
        "synthetic",
        open_,
        max(open_, close),
        min(open_, close),
        close,
        is_eligible=eligible,
        quality_flags=() if eligible else ("SYNTHETIC_INELIGIBLE",),
    )


def current_classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def event_bars(
    trade_day: date, *, night_open: int, night_close: int, day_open: int
) -> tuple[CalendarClassifier, list[Bar], list[Bar]]:
    classifier = current_classifier()
    N = classifier.session_open(trade_day, Session.NIGHT)
    C, _, _, _ = night_reference_time(classifier, trade_day)
    S = classifier.session_open(trade_day, Session.DAY)
    night = [
        make_bar(N, Session.NIGHT, trade_date=trade_day, open_=night_open),
        make_bar(C, Session.NIGHT, trade_date=trade_day, close=night_close),
    ]
    day = [make_bar(S, Session.DAY, trade_date=trade_day, open_=day_open)]
    return classifier, day, night


def test_exact_schedule_references_weekend_and_all_r_g_sign_combinations() -> None:
    classifier, day, night = event_bars(
        date(2024, 11, 11), night_open=100, night_close=110, day_open=120
    )
    event = night_direction_event(classifier, date(2024, 11, 11), day, night)
    assert event["status"] == "eligible"
    assert event["R_points"] == 10 and event["G_points"] == 10
    assert event["A_direction"] == "long" and event["D_direction"] == "short"
    assert event["N_night_open_bar_start_jst"] == "2024-11-08T17:00:00+09:00"
    for r_positive, g_positive, expected_a, expected_d in (
        (True, True, "long", "short"),
        (True, False, "long", "long"),
        (False, True, "short", "short"),
        (False, False, "short", "long"),
    ):
        close = 110 if r_positive else 90
        open_day = close + (10 if g_positive else -10)
        _, sign_day, sign_night = event_bars(
            date(2024, 11, 11), night_open=100, night_close=close, day_open=open_day
        )
        sign_event = night_direction_event(classifier, date(2024, 11, 11), sign_day, sign_night)
        assert sign_event["A_direction"] == expected_a
        assert sign_event["D_direction"] == expected_d
    _, zero_r_day, zero_r_night = event_bars(
        date(2024, 11, 11), night_open=100, night_close=100, day_open=110
    )
    assert (
        night_direction_event(classifier, date(2024, 11, 11), zero_r_day, zero_r_night)["reason"]
        == "ZERO_R"
    )
    _, zero_g_day, zero_g_night = event_bars(
        date(2024, 11, 11), night_open=100, night_close=110, day_open=110
    )
    assert (
        night_direction_event(classifier, date(2024, 11, 11), zero_g_day, zero_g_night)["reason"]
        == "ZERO_G"
    )


def test_missing_ineligible_quarantine_and_no_reference_substitution_reject() -> None:
    trade_day = date(2024, 11, 11)
    classifier, day, night = event_bars(trade_day, night_open=100, night_close=110, day_open=120)
    assert (
        night_direction_event(classifier, trade_day, day, night, reference_night_quarantined=True)[
            "reason"
        ]
        == "REFERENCE_NIGHT_QUARANTINED"
    )
    assert (
        night_direction_event(classifier, trade_day, day, night[:1])["reason"]
        == "REFERENCE_NORMAL_BAR_MISSING"
    )
    assert (
        night_direction_event(classifier, trade_day, day, night[1:])["reason"]
        == "NIGHT_OPEN_BAR_MISSING"
    )
    day[0] = make_bar(
        classifier.session_open(trade_day, Session.DAY),
        Session.DAY,
        trade_date=trade_day,
        eligible=False,
    )
    assert (
        night_direction_event(classifier, trade_day, day, night)["reason"]
        == "DAY_OPEN_BAR_INELIGIBLE"
    )


def test_same_gap_different_r_is_not_a_gap_reexpression() -> None:
    trade_day = date(2024, 11, 11)
    classifier, day_up, night_up = event_bars(
        trade_day, night_open=100, night_close=110, day_open=120
    )
    _, day_down, night_down = event_bars(trade_day, night_open=120, night_close=110, day_open=120)
    up = night_direction_event(classifier, trade_day, day_up, night_up)
    down = night_direction_event(classifier, trade_day, day_down, night_down)
    assert up["G_points"] == down["G_points"] == 10
    assert up["A_direction"] == "long" and down["A_direction"] == "short"
    assert up["D_direction"] == down["D_direction"] == "short"


def test_next_bar_entry_fixed_exit_delayed_entry_and_prefix_invariance() -> None:
    instrument, sessions, _, config = load_project_config(Path("config"))
    S = datetime(2024, 11, 5, 8, 45, tzinfo=JST)
    bars = [make_bar(S + timedelta(minutes=i), Session.DAY, open_=100 + i) for i in range(62)]
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    result = engine.run(bars, NightDirectionFollowThroughStrategy("test", S, "long"))
    trade = result.trades[0]
    assert trade.entry_ts == S + timedelta(minutes=1)
    assert trade.exit_signal_ts == S + timedelta(minutes=60)
    assert trade.exit_ts == S + timedelta(minutes=61)
    assert trade.exit_reason is ExitReason.SIGNAL and trade.side is Side.LONG
    delayed = [bar for bar in bars if bar.ts_jst != S + timedelta(minutes=1)]
    delayed_trade = engine.run(
        delayed, NightDirectionFollowThroughStrategy("delay", S, "short")
    ).trades[0]
    assert delayed_trade.entry_ts == S + timedelta(minutes=2)
    assert delayed_trade.exit_ts == S + timedelta(minutes=61)
    prefix = engine.run(bars[:61], NightDirectionFollowThroughStrategy("prefix", S, "long"))
    assert prefix.trades[0].entry_ts == result.trades[0].entry_ts
    assert prefix.trades[0].side is result.trades[0].side
    assert all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in result.trades)
