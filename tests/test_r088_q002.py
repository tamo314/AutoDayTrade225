from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r088_q002_conditional_path_efficiency import state_ledger


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


def _bars(day: date, changes: list[int]) -> list[Bar]:
    base, price = datetime.combine(day, time(9), JST), 1000
    rows: list[Bar] = []
    for index, change in enumerate(changes):
        close = price + change
        rows.append(
            Bar(
                base + timedelta(minutes=index),
                day,
                day,
                Session.DAY,
                "test",
                price,
                max(price, close),
                min(price, close),
                close,
            )
        )
        price = close
    rows.extend(
        [
            Bar(
                base + timedelta(minutes=60),
                day,
                day,
                Session.DAY,
                "test",
                price,
                price,
                price,
                price,
            ),
            Bar(
                datetime.combine(day, time(14, 54), JST),
                day,
                day,
                Session.DAY,
                "test",
                price,
                price,
                price,
                price,
            ),
            Bar(
                datetime.combine(day, time(14, 55), JST),
                day,
                day,
                Session.DAY,
                "test",
                price,
                price,
                price,
                price,
            ),
        ]
    )
    return rows


def test_conditional_set_is_prior_nearest_m_with_newest_tie_break() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=index) for index in range(122)]
    classifier = _classifier(axis)
    bars = {
        (day, Session.DAY): _bars(day, [1] * 60 if index % 2 else [1, -1] * 30)
        for index, day in enumerate(axis)
    }
    ledger = state_ledger(classifier, axis, bars, set())
    row = ledger[121]
    assert row["m_band"] in {"B1", "B2"}
    assert len(row["comparison_trade_dates"]) == 60
    assert axis[121].isoformat() not in row["comparison_trade_dates"]
    assert row["comparison_trade_dates"][0] == axis[119].isoformat()


def test_current_e_cannot_change_conditional_threshold_or_comparison_set() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=index) for index in range(122)]
    classifier = _classifier(axis)
    base = {(day, Session.DAY): _bars(day, [1] * 60) for day in axis}
    changed = dict(base)
    changed[(axis[-1], Session.DAY)] = _bars(axis[-1], [3, -1] * 30)
    first = state_ledger(classifier, axis, base, set())[-1]
    second = state_ledger(classifier, axis, changed, set())[-1]
    assert first["comparison_trade_dates"] == second["comparison_trade_dates"]
    assert first["q_conditional_e_low"] == second["q_conditional_e_low"]
    assert first["q_conditional_e_high"] == second["q_conditional_e_high"]
