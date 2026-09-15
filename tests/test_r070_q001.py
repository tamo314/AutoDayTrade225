from datetime import date, datetime, time
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r070_night_risk_premium import fixed_night_event, mbb_mean_ci
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy


def _classifier(target: date, night_start: date) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions,
        ExchangeCalendar([TradingDay(target, None, None, night_start, False, "test")]),
    )


def _bar(target: date, stamp: datetime) -> Bar:
    return Bar(stamp, target, stamp.date(), Session.NIGHT, "test", 100, 100, 100, 100)


def test_primary_night_event_uses_prior_calendar_date_and_next_open() -> None:
    target, prior = date(2021, 9, 20), date(2021, 9, 17)
    classifier = _classifier(target, prior)
    bars = [
        _bar(target, datetime.combine(prior, time(16, 30), JST)),
        _bar(target, datetime.combine(prior, time(16, 31), JST)),
        _bar(target, datetime.combine(target, time(5, 29), JST)),
        _bar(target, datetime.combine(target, time(5, 30), JST)),
    ]
    event = fixed_night_event(target, bars, classifier)
    assert event["status"] == "EXECUTABLE"
    assert str(event["entry_open_jst"]).endswith("16:31:00+09:00")
    assert event["scheduled_exit_open_jst"] == "2021-09-20T05:30:00+09:00"


def test_later_exit_mutation_does_not_cancel_fixed_entry() -> None:
    target, prior = date(2021, 9, 20), date(2021, 9, 17)
    classifier = _classifier(target, prior)
    bars = [
        _bar(target, datetime.combine(prior, time(16, 30), JST)),
        _bar(target, datetime.combine(prior, time(16, 31), JST)),
    ]
    event = fixed_night_event(target, bars, classifier)
    assert event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN"
    assert event["entry_observable"] is True


def test_one_minute_delay_reaches_the_open_after_the_registered_entry() -> None:
    target, prior = date(2021, 9, 20), date(2021, 9, 17)
    classifier = _classifier(target, prior)
    bars = [
        _bar(target, datetime.combine(prior, time(16, 31), JST)),
        _bar(target, datetime.combine(prior, time(16, 32), JST)),
        _bar(target, datetime.combine(target, time(5, 29), JST)),
        _bar(target, datetime.combine(target, time(5, 30), JST)),
    ]
    event = fixed_night_event(target, bars, classifier, entry_delay_minutes=1)
    assert event["status"] == "EXECUTABLE"
    assert str(event["entry_open_jst"]).endswith("16:32:00+09:00")


def test_schedule_change_is_known_no_trade_not_observed_row_inference() -> None:
    target, prior = date(2025, 1, 6), date(2025, 1, 3)
    classifier = _classifier(target, prior)
    assert fixed_night_event(target, [], classifier)["status"] == "NO_SCHEDULED_FIXED_WINDOW"


def test_mbb_is_fixed_seed_and_nonwrapping() -> None:
    assert mbb_mean_ci(list(range(20))) == mbb_mean_ci(list(range(20)))


def test_fixed_night_execution_is_next_open_and_same_trade_date_exit() -> None:
    target, prior = date(2021, 9, 20), date(2021, 9, 17)
    classifier = _classifier(target, prior)
    bars = [
        _bar(target, datetime.combine(prior, time(16, 30), JST)),
        _bar(target, datetime.combine(prior, time(16, 31), JST)),
        _bar(target, datetime.combine(target, time(5, 29), JST)),
        _bar(target, datetime.combine(target, time(5, 30), JST)),
    ]
    instrument, _, _, baseline = load_project_config(Path("config"))
    config = baseline.model_copy(
        update={
            "mode": "night_only",
            "risk": baseline.risk.model_copy(
                update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    trade = BacktestEngine(instrument.instrument.to_spec(), config, classifier).run(
        bars,
        R049FixedTimeStrategy(
            "r070", datetime.combine(prior, time(16, 31), JST), datetime.combine(target, time(5, 30), JST), "long"
        ),
    ).trades[0]
    assert (trade.side, trade.entry_ts.time(), trade.exit_ts.time(), trade.exit_reason) == (
        Side.LONG,
        time(16, 31),
        time(5, 30),
        ExitReason.SIGNAL,
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
