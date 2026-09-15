from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r087_multiday_day_trend_acceptance import (
    _acceptance_observation,
    build_events,
    nearest_rank,
    required_valid_reference_count,
    state_ledger,
)


def _classifier(axis: list[date]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    days = [
        TradingDay(target, axis[index - 1] if index else None, axis[index + 1] if index + 1 < len(axis) else None, target, False, "test")
        for index, target in enumerate(axis)
    ]
    return CalendarClassifier(sessions, ExchangeCalendar(days))


def _bar(target: date, stamp: datetime, value: int, *, close: int | None = None) -> Bar:
    return Bar(stamp, target, target, Session.DAY, "test", value, value, value, value if close is None else close)


def _day_bars(target: date, *, invalid_open: bool = False) -> list[Bar]:
    base = datetime.combine(target, time(8, 45), JST)
    return [
        _bar(target, base, 0 if invalid_open else 100),
        _bar(target, datetime.combine(target, time(9), JST), 100),
        _bar(target, datetime.combine(target, time(9, 14), JST), 100, close=101),
        _bar(target, datetime.combine(target, time(9, 15), JST), 101),
        _bar(target, datetime.combine(target, time(14, 54), JST), 101),
        _bar(target, datetime.combine(target, time(14, 55), JST), 102),
        _bar(target, datetime.combine(target, time(15, 15), JST), 102, close=102),
    ]


def test_nearest_rank_and_fixed_reference_coverage() -> None:
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 30) == 2.0
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 70) == 3.0
    assert (required_valid_reference_count(80), required_valid_reference_count(120), required_valid_reference_count(160)) == (67, 100, 134)


def test_t_uses_exact_prior_scheduled_dates_and_never_backfills() -> None:
    axis = [date(2024, 1, 2) + timedelta(days=index) for index in range(12)]
    classifier = _classifier(axis)
    bars = {
        (target, Session.DAY): [] if target == axis[3] else _day_bars(target)
        for target in axis
    }
    ledger = state_ledger(classifier, axis, bars, set())
    row = ledger[10]
    assert row["prior_scheduled_day_return_trade_dates"] == [target.isoformat() for target in axis[:10]]
    assert row["t_valid"] is False
    assert row["reason"] == "T_REQUIRES_ALL_PRIOR_SCHEDULED_DAY_RETURNS_VALID_NO_BACKFILL"


def test_acceptance_uses_0900_open_and_0914_close_only() -> None:
    target = date(2024, 1, 4)
    observation = _acceptance_observation(target, {(target, Session.DAY): _day_bars(target)}, 15)
    assert observation["p_valid"] is True
    assert observation["p_bps"] == 100.0
    assert str(observation["b_confirmation_close_jst"]).endswith("09:14:00+09:00")


def test_execution_enters_next_eligible_open_and_exits_fixed_clock() -> None:
    target = date(2024, 1, 4)
    classifier = _classifier([target])
    event = {
        "trade_date": target.isoformat(),
        "condition": "EA",
        "p_bps": 1.0,
        "b_confirmation_close_jst": datetime.combine(target, time(9, 14), JST).isoformat(),
    }
    events = build_events(classifier, [target], {(target, Session.DAY): _day_bars(target)}, set())
    assert events[0]["status"] == "SKIPPED"  # no ten-day state may create a trade
    from n225m_bt.research.r087_multiday_day_trend_acceptance import r087_event

    executable = r087_event(event, {(target, Session.DAY): _day_bars(target)})
    assert executable["status"] == "EXECUTABLE"
    assert str(executable["entry_open_jst"]).endswith("09:15:00+09:00")
    assert str(executable["exit_open_jst"]).endswith("14:55:00+09:00")
