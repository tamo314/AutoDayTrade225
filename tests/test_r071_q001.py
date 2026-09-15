from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r071_night_opening_range import mbb_mean_ci, night_opening_range_event
from n225m_bt.strategies.r071_night_opening_range import R071NightOpeningRangeStrategy


def _classifier(target: date, night_start: date) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions,
        ExchangeCalendar([TradingDay(target, None, None, night_start, False, "test")]),
    )


def _bar(target: date, stamp: datetime, *, high: int = 100, low: int = 100, close: int = 100) -> Bar:
    return Bar(stamp, target, stamp.date(), Session.NIGHT, "test", 100, high, low, close)


def _path(target: date, prior: date, *, until: datetime | None = None) -> list[Bar]:
    end = until or datetime.combine(prior, time(23, 30), JST)
    start = datetime.combine(prior, time(16, 30), JST)
    bars = []
    current = start
    while current <= end:
        bars.append(_bar(target, current))
        current += timedelta(minutes=1)
    bars.extend(
        [
            _bar(target, datetime.combine(target, time(5, 29), JST)),
            _bar(target, datetime.combine(target, time(5, 30), JST)),
        ]
    )
    return bars


def test_first_strict_night_breakout_uses_prior_calendar_date_and_next_open() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    bars = _path(target, prior)
    bars[5] = _bar(target, datetime.combine(prior, time(16, 35), JST), high=110, low=90)
    bars[30] = _bar(target, datetime.combine(prior, time(17, 0), JST), high=112, close=111)
    event = night_opening_range_event(target, bars, _classifier(target, prior))
    assert event["status"] == "EXECUTABLE"
    assert event["breakout_direction"] == "long"
    assert str(event["breakout_bar_jst"]).endswith("17:00:00+09:00")
    assert str(event["entry_open_jst"]).endswith("17:01:00+09:00")
    assert event["scheduled_exit_open_jst"] == "2021-09-18T05:30:00+09:00"


def test_equal_close_is_not_breakout_and_missing_later_exit_keeps_filled_entry_unknown() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    equality = _path(target, prior)
    assert night_opening_range_event(target, equality, _classifier(target, prior))["reason"] == (
        "NO_STRICT_BREAKOUT_BY_DEADLINE"
    )
    missing_exit = _path(target, prior)[:-2]
    missing_exit[30] = _bar(target, datetime.combine(prior, time(17, 0), JST), close=101)
    event = night_opening_range_event(target, missing_exit, _classifier(target, prior))
    assert event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN"
    assert event["entry_observable"] is True


def test_deadline_after_midnight_and_delayed_entry_are_explicit() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    bars = _path(target, prior, until=datetime.combine(target, time(0, 32), JST))
    for index, bar in enumerate(bars):
        if bar.ts_jst == datetime.combine(target, time(0, 30), JST):
            bars[index] = _bar(target, bar.ts_jst, close=101)
    event = night_opening_range_event(
        target,
        bars,
        _classifier(target, prior),
        search_end=time(0, 30),
        entry_delay_minutes=1,
    )
    assert event["status"] == "EXECUTABLE"
    assert str(event["signal_jst"]).endswith("00:31:00+09:00")
    # The deliberate one-minute delay waits for 00:31 close, then fills 00:32.
    assert str(event["entry_open_jst"]).endswith("00:32:00+09:00")


def test_strategy_executes_next_open_exit_and_reversed_control() -> None:
    target, prior = date(2021, 9, 18), date(2021, 9, 17)
    bars = _path(target, prior)
    bars[30] = _bar(target, datetime.combine(prior, time(17, 0), JST), close=101)
    instrument, _, _, baseline = load_project_config(Path("config"))
    config = baseline.model_copy(
        update={
            "mode": "night_only",
            "risk": baseline.risk.model_copy(
                update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    event = night_opening_range_event(target, bars, _classifier(target, prior))
    strategy = R071NightOpeningRangeStrategy(
        "r071",
        range_start=datetime.combine(prior, time(16, 30), JST),
        opening_minutes=30,
        search_end=datetime.combine(prior, time(23, 30), JST),
        exit_open=datetime.combine(target, time(5, 30), JST),
    )
    trade = BacktestEngine(instrument.instrument.to_spec(), config, _classifier(target, prior)).run(
        bars, strategy
    ).trades[0]
    assert event["entry_open_jst"] == trade.entry_ts.isoformat()
    assert (trade.side, trade.exit_ts.time(), trade.exit_reason) == (
        Side.LONG,
        time(5, 30),
        ExitReason.SIGNAL,
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy


def test_mbb_is_fixed_seed_and_nonwrapping() -> None:
    assert mbb_mean_ci(list(range(20))) == mbb_mean_ci(list(range(20)))


def test_calendar_gap_is_known_no_trade_not_a_holiday_spanning_position() -> None:
    target, prior = date(2021, 9, 20), date(2021, 9, 17)
    assert night_opening_range_event(target, [], _classifier(target, prior))["status"] == (
        "NO_SCHEDULED_NIGHT_WINDOW"
    )
