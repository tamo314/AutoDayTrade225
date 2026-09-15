from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r096_opening_range_acceptance import state_ledger


def _classifier(axis: list[date]) -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions,
        ExchangeCalendar(
            [
                TradingDay(
                    day,
                    axis[i - 1] if i else None,
                    axis[i + 1] if i + 1 < len(axis) else None,
                    day,
                    False,
                    "test",
                )
                for i, day in enumerate(axis)
            ]
        ),
    )


def _bars(day: date, changes: list[int]) -> list[Bar]:
    stamp, price, result = datetime.combine(day, time(9), JST), 1000, []
    for i, change in enumerate(changes):
        close = price + change
        result.append(
            Bar(
                stamp + timedelta(minutes=i),
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
    return result


def test_directional_range_position_uses_low_for_up_and_high_for_down() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=i) for i in range(121)]
    up = _bars(axis[-1], [2] * 60)
    down = _bars(axis[-1], [-2] * 60)
    first = state_ledger(
        _classifier(axis),
        axis,
        {(day, Session.DAY): _bars(day, [1] * 60) for day in axis[:-1]}
        | {(axis[-1], Session.DAY): up},
        set(),
    )[-1]
    second = state_ledger(
        _classifier(axis),
        axis,
        {(day, Session.DAY): _bars(day, [1] * 60) for day in axis[:-1]}
        | {(axis[-1], Session.DAY): down},
        set(),
    )[-1]
    assert first["z_directional_close_position"] == 1
    assert second["z_directional_close_position"] == 1


def test_current_x_z_never_change_prior_thresholds() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=i) for i in range(121)]
    base = {(day, Session.DAY): _bars(day, [1 + i % 3] * 60) for i, day in enumerate(axis)}
    changed = dict(base)
    changed[(axis[-1], Session.DAY)] = _bars(axis[-1], [5] * 60)
    first, second = (
        state_ledger(_classifier(axis), axis, candidate, set())[-1] for candidate in (base, changed)
    )
    assert axis[-1].isoformat() not in first["reference_valid_trade_dates"]
    assert first["qx_cutoff"] == second["qx_cutoff"]
    assert first["qz_high"] == second["qz_high"]
