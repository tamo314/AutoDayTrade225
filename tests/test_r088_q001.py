from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r088_cash_open_path_efficiency import (
    _observation,
    nearest_rank,
    r088_event,
    state_ledger,
)


def _classifier(axis: list[date]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    days = [
        TradingDay(
            day,
            axis[index - 1] if index else None,
            axis[index + 1] if index + 1 < len(axis) else None,
            day,
            False,
            "test",
        )
        for index, day in enumerate(axis)
    ]
    return CalendarClassifier(sessions, ExchangeCalendar(days))


def _bar(day: date, stamp: datetime, opening: int, closing: int) -> Bar:
    return Bar(
        stamp,
        day,
        day,
        Session.DAY,
        "test",
        opening,
        max(opening, closing),
        min(opening, closing),
        closing,
    )


def _bars(day: date, *, flat: bool = False) -> list[Bar]:
    base = datetime.combine(day, time(9), JST)
    values = [100] * 61 if flat else [100 + (index % 3) + index // 3 for index in range(61)]
    result = [
        _bar(day, base + timedelta(minutes=index), values[index], values[index + 1])
        for index in range(60)
    ]
    result.extend(
        [
            _bar(day, base + timedelta(minutes=60), values[-1], values[-1]),
            _bar(day, datetime.combine(day, time(14, 54), JST), values[-1], values[-1]),
            _bar(day, datetime.combine(day, time(14, 55), JST), values[-1], values[-1]),
        ]
    )
    return result


def test_path_uses_exactly_sixty_close_terms_and_rejects_zero_length() -> None:
    day = date(2024, 1, 4)
    classifier = _classifier([day])
    result = _observation(classifier, day, {(day, Session.DAY): _bars(day)}, set(), 60)
    assert result["observation_valid"] is True
    assert len(result["p_close_points"]) == len(result["l_terms_points"]) == 60
    assert sum(result["l_terms_points"]) == result["l_points"]
    flat = _observation(classifier, day, {(day, Session.DAY): _bars(day, flat=True)}, set(), 60)
    assert flat["reason"] == "ZERO_PATH_LENGTH"


def test_exact_prior_reference_excludes_current_and_fixed_execution_is_next_open() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=index) for index in range(122)]
    classifier = _classifier(axis)
    ledger = state_ledger(classifier, axis, {(day, Session.DAY): _bars(day) for day in axis}, set())
    row = ledger[121]
    assert row["reference_scheduled_trade_dates"] == [day.isoformat() for day in axis[1:121]]
    assert axis[121].isoformat() not in row["reference_valid_trade_dates"]
    event = r088_event(
        {**row, "high_efficiency_condition": True, "displacement_points": 1.0},
        {(axis[121], Session.DAY): _bars(axis[121])},
    )
    assert event["status"] == "EXECUTABLE"
    assert str(event["entry_open_jst"]).endswith("10:00:00+09:00")
    assert str(event["exit_open_jst"]).endswith("14:55:00+09:00")


def test_nearest_rank_is_uninterpolated() -> None:
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 60) == 3.0
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 70) == 3.0
