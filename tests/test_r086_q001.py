from datetime import date, datetime, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r086_intraday_same_clock_shock_fade import (
    _blocks,
    _execution_event,
    bootstrap,
    build_events,
    pre_shock_control_events,
)


def _classifier() -> CalendarClassifier:
    target = date(2024, 1, 4)
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar([TradingDay(target, None, None, target, False, "test")]))


def _bar(target: date, stamp: datetime, value: int, *, close: int | None = None) -> Bar:
    return Bar(stamp, target, stamp.date(), Session.DAY, "test", value, value, value, value if close is None else close)


def test_blocks_are_non_overlapping_and_respect_30_minute_edges() -> None:
    target, classifier = date(2024, 1, 4), _classifier()
    blocks = _blocks(target, classifier, 5)
    opening, closing = classifier.session_open(target, Session.DAY), classifier.session_close(target, Session.DAY)
    assert blocks[0][1] == opening + timedelta(minutes=30)
    assert all(blocks[index][2] < blocks[index + 1][1] for index in range(len(blocks) - 1))
    assert blocks[-1][2] <= closing - timedelta(minutes=30)


def test_same_clock_history_is_current_excluded_and_first_shock_only() -> None:
    target = date(2024, 1, 4)
    classifier = _classifier()
    begin = classifier.session_open(target, Session.DAY) + timedelta(minutes=30)
    rows = [_bar(target, begin + timedelta(minutes=index), 100, close=110 if index == 4 else 100) for index in range(5)]
    # A short synthetic history is deliberately used by a permitted lookback=40 only after 40 dates;
    # here the selected date cannot use its own X and remains history-insufficient.
    events, blocks = build_events(classifier, [target], {(target, Session.DAY): rows}, set(), lookback=40)
    assert events[0]["reason"] == "NO_SHOCK_AFTER_HISTORY"
    assert blocks[0]["reference_valid_count"] == 0
    assert blocks[0]["trade_date"] not in blocks[0]["reference_valid_trade_dates_oldest_to_newest"]


def test_execution_enters_next_open_and_exits_exact_hold_open_in_same_interval() -> None:
    target = date(2024, 1, 4)
    endpoint = datetime.combine(target, datetime.min.time(), JST).replace(hour=10)
    candidate = {"trade_date": target.isoformat(), "block_end_jst": endpoint.isoformat(), "interval_close_jst": endpoint.replace(hour=15).isoformat(), "x_bps": 10.0}
    bars = {(target, Session.DAY): [_bar(target, endpoint + timedelta(minutes=index), 100) for index in (0, 1, 19, 20, 21)]}
    event = _execution_event(candidate, bars)
    assert event["status"] == "EXECUTABLE"
    assert str(event["entry_open_jst"]).endswith("10:01:00+09:00")
    assert str(event["exit_open_jst"]).endswith("10:21:00+09:00")


def test_pre_shock_control_requires_prior_nonzero_block_and_bootstrap_is_seeded() -> None:
    events = [{"trade_date": "2024-01-04", "status": "EXECUTABLE", "block_start_offset_minutes": 35, "shock_minutes": 5}]
    controls = pre_shock_control_events(events, [{"trade_date": "2024-01-04", "block_start_offset_minutes": 30, "x_valid": True, "x_bps": -1.0}])
    assert controls[0]["pre_shock_direction"] == "long"
    first = bootstrap([0] * 20, [0] * 20, [0] * 20, [0] * 20)[1]
    second = bootstrap([0] * 20, [0] * 20, [0] * 20, [0] * 20)[1]
    assert (first == second).all()
