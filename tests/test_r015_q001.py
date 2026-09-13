from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r006 import night_reference_time
from n225m_bt.research.r015 import total_change_event
from n225m_bt.strategies.total_night_day_change import TotalNightDayChangeStrategy


def current_classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def bar(
    stamp: datetime,
    session: Session,
    *,
    trade_day: date,
    open_: int,
    close: int | None = None,
    eligible: bool = True,
) -> Bar:
    end = open_ if close is None else close
    return Bar(
        stamp,
        trade_day,
        stamp.date(),
        session,
        "synthetic",
        open_,
        max(open_, end),
        min(open_, end),
        end,
        is_eligible=eligible,
        quality_flags=() if eligible else ("SYNTHETIC_INELIGIBLE",),
    )


def event_inputs(
    *, night_close: int = 100, day_open: int = 110, current_close: int = 120
) -> tuple[CalendarClassifier, date, list[Bar], list[Bar]]:
    classifier, trade_day = current_classifier(), date(2024, 11, 11)
    start = classifier.session_open(trade_day, Session.DAY)
    day = [
        bar(
            start + timedelta(minutes=index),
            Session.DAY,
            trade_day=trade_day,
            open_=day_open if index == 0 else current_close,
            close=current_close if index == 29 else None,
        )
        for index in range(30)
    ]
    close_start, _, _, _ = night_reference_time(classifier, trade_day)
    night = [bar(close_start, Session.NIGHT, trade_day=trade_day, open_=night_close)]
    return classifier, trade_day, day, night


def test_versioned_night_close_30_bar_window_and_sign_arithmetic() -> None:
    classifier, trade_day, day, night = event_inputs()
    result = total_change_event(classifier, trade_day, day, night)
    assert (
        result["status"],
        result["G_points"],
        result["M_points"],
        result["Q_points"],
        result["A_direction"],
        result["D_direction"],
        result["F_direction"],
    ) == ("eligible", 10, 10, 20, "long", "long", "long")
    assert result["C_normal_bar_start_jst"] == "2024-11-11T05:54:00+09:00"
    assert result["required_day_window_bar_count"] == 30
    assert result == total_change_event(classifier, trade_day, day + day[-1:] * 20, night)


def test_q_is_not_a_reexpression_of_only_gap_or_only_momentum() -> None:
    classifier, trade_day, day, night = event_inputs(night_close=100, day_open=110, current_close=105)
    # Same M=-5; changing only G changes sign(Q) from positive to negative.
    q_up = total_change_event(classifier, trade_day, day, night)
    _, _, second_day, second_night = event_inputs(night_close=120, day_open=122, current_close=117)
    q_down = total_change_event(classifier, trade_day, second_day, second_night)
    assert (q_up["M_points"], q_down["M_points"]) == (-5, -5)
    assert (q_up["A_direction"], q_down["A_direction"]) == ("long", "short")
    # Same G=+10; changing only M changes sign(Q).
    _, _, third_day, third_night = event_inputs(night_close=100, day_open=110, current_close=105)
    _, _, fourth_day, fourth_night = event_inputs(night_close=100, day_open=110, current_close=85)
    third = total_change_event(classifier, trade_day, third_day, third_night)
    fourth = total_change_event(classifier, trade_day, fourth_day, fourth_night)
    assert (third["G_points"], fourth["G_points"]) == (10, 10)
    assert (third["A_direction"], fourth["A_direction"]) == ("long", "short")
    # Same signs make all rules agree; opposite signs make A follow the larger component.
    assert (q_up["A_direction"], q_up["D_direction"], q_up["F_direction"]) == (
        "long",
        "short",
        "long",
    )
    assert (fourth["A_direction"], fourth["D_direction"], fourth["F_direction"]) == (
        "short",
        "short",
        "long",
    )


def test_zero_missing_ineligible_and_quarantined_references_are_skipped() -> None:
    classifier, trade_day, day, night = event_inputs()
    assert total_change_event(classifier, trade_day, day, night[:-1])["reason"] == "REFERENCE_NORMAL_BAR_MISSING"
    assert (
        total_change_event(classifier, trade_day, day, night, reference_night_quarantined=True)["reason"]
        == "REFERENCE_NIGHT_QUARANTINED"
    )
    assert total_change_event(classifier, trade_day, day[:29], night)["reason"] == "DAY_30BAR_WINDOW_MISSING"
    invalid = list(day)
    invalid[10] = bar(
        invalid[10].ts_jst, Session.DAY, trade_day=trade_day, open_=110, eligible=False
    )
    assert (
        total_change_event(classifier, trade_day, invalid, night)["reason"]
        == "DAY_30BAR_WINDOW_INELIGIBLE_OR_SESSION_MISMATCH"
    )
    for night_close, day_open, current_close, reason in (
        (100, 100, 110, "ZERO_G"),
        (100, 110, 110, "ZERO_M"),
        (100, 110, 100, "ZERO_Q"),
    ):
        _, _, zero_day, zero_night = event_inputs(
            night_close=night_close, day_open=day_open, current_close=current_close
        )
        assert total_change_event(classifier, trade_day, zero_day, zero_night)["reason"] == reason


def test_execution_is_next_bar_fixed_exit_and_does_not_extend_for_delay() -> None:
    instrument, sessions, _, config = load_project_config(Path("config"))
    signal = datetime(2024, 11, 5, 9, 14, tzinfo=JST)
    rows = [
        bar(signal - timedelta(minutes=29) + timedelta(minutes=index), Session.DAY, trade_day=date(2024, 11, 5), open_=100 + index)
        for index in range(91)
    ]
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    trade = engine.run(rows, TotalNightDayChangeStrategy("r015", signal, "long")).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.LONG,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    delayed = engine.run(
        [row for row in rows if row.ts_jst != signal + timedelta(minutes=1)],
        TotalNightDayChangeStrategy("r015-delay", signal, "short"),
    ).trades[0]
    assert (delayed.entry_ts, delayed.exit_ts, delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy) == (
        signal + timedelta(minutes=2),
        signal + timedelta(minutes=61),
        True,
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
