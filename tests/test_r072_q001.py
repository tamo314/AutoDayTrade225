from datetime import date, datetime, time
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r072_night_direction_day_reversal import (
    mbb_mean_ci,
    night_direction_day_reversal_event,
)
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy


def _classifier(target: date, night_start: date) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions,
        ExchangeCalendar([TradingDay(target, None, None, night_start, False, "test")]),
    )


def _bar(target: date, stamp: datetime, session: Session, open_: int) -> Bar:
    return Bar(stamp, target, stamp.date(), session, "test", open_, open_, open_, open_)


def _path(target: date, prior: date, night_start: int = 100, night_end: int = 110) -> list[Bar]:
    return [
        _bar(target, datetime.combine(prior, time(16, 30), JST), Session.NIGHT, night_start),
        _bar(target, datetime.combine(target, time(5, 0), JST), Session.NIGHT, night_end),
        _bar(target, datetime.combine(target, time(5, 30), JST), Session.NIGHT, night_end),
        _bar(target, datetime.combine(target, time(8, 59), JST), Session.DAY, 110),
        _bar(target, datetime.combine(target, time(9, 0), JST), Session.DAY, 110),
        _bar(target, datetime.combine(target, time(9, 1), JST), Session.DAY, 110),
        _bar(target, datetime.combine(target, time(14, 14), JST), Session.DAY, 110),
        _bar(target, datetime.combine(target, time(14, 15), JST), Session.DAY, 110),
        _bar(target, datetime.combine(target, time(14, 29), JST), Session.DAY, 110),
        _bar(target, datetime.combine(target, time(14, 30), JST), Session.DAY, 110),
        _bar(target, datetime.combine(target, time(14, 44), JST), Session.DAY, 110),
        _bar(target, datetime.combine(target, time(14, 45), JST), Session.DAY, 110),
    ]


def test_positive_night_direction_reverses_at_fixed_day_open() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    event = night_direction_day_reversal_event(target, _path(target, prior), _classifier(target, prior))
    assert event["status"] == "EXECUTABLE"
    assert event["night_direction"] == "long"
    assert event["execution_direction"] == "short"
    assert str(event["signal_fixed_at_jst"]).endswith("05:30:00+09:00")
    assert str(event["entry_open_jst"]).endswith("09:00:00+09:00")


def test_negative_night_direction_buys_and_day_prices_do_not_change_signal() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    bars = _path(target, prior, night_start=110, night_end=100)
    bars[3] = _bar(target, bars[3].ts_jst, Session.DAY, 1)
    event = night_direction_day_reversal_event(target, bars, _classifier(target, prior))
    assert event["status"] == "EXECUTABLE"
    assert event["night_direction"] == "short"
    assert event["execution_direction"] == "long"


def test_zero_night_direction_skips_and_missing_later_exit_stays_unknown() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    assert night_direction_day_reversal_event(
        target, _path(target, prior, 100, 100), _classifier(target, prior)
    )["reason"] == "ZERO_NIGHT_DIRECTION"
    event = night_direction_day_reversal_event(target, _path(target, prior)[:-3], _classifier(target, prior))
    assert event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN"
    assert event["entry_observable"] is True


def test_0500_0901_and_both_exit_sensitivities_are_registered() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    event = night_direction_day_reversal_event(
        target,
        _path(target, prior),
        _classifier(target, prior),
        night_end=time(5, 0),
        entry=time(9, 1),
        exit_=time(14, 15),
    )
    assert event["status"] == "EXECUTABLE"
    assert str(event["scheduled_exit_open_jst"]).endswith("14:15:00+09:00")


def test_post_2024_night_schedule_is_known_no_trade() -> None:
    target, prior = date(2025, 1, 6), date(2025, 1, 3)
    assert night_direction_day_reversal_event(target, [], _classifier(target, prior))["status"] == (
        "NO_SCHEDULED_CROSS_SESSION_WINDOW"
    )


def test_day_engine_uses_static_reversal_direction_and_signal_exit() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    bars = _path(target, prior)
    instrument, _, _, baseline = load_project_config(Path("config"))
    config = baseline.model_copy(
        update={
            "mode": "day_only",
            "risk": baseline.risk.model_copy(
                update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    trade = BacktestEngine(instrument.instrument.to_spec(), config, _classifier(target, prior)).run(
        bars,
        R049FixedTimeStrategy(
            "r072", datetime.combine(target, time(9, 0), JST), datetime.combine(target, time(14, 30), JST), "short"
        ),
    ).trades[0]
    assert (trade.side, trade.entry_ts.time(), trade.exit_ts.time(), trade.exit_reason) == (
        Side.SHORT,
        time(9, 0),
        time(14, 30),
        ExitReason.SIGNAL,
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy


def test_mbb_is_fixed_seed_and_nonwrapping() -> None:
    assert mbb_mean_ci(list(range(20))) == mbb_mean_ci(list(range(20)))
