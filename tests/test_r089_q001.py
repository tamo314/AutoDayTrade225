from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r089_late_concentration import state_ledger


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
    base, price, rows = datetime.combine(day, time(9), JST), 1000, []
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
    for stamp in (
        base + timedelta(minutes=60),
        datetime.combine(day, time(14, 54), JST),
        datetime.combine(day, time(14, 55), JST),
    ):
        rows.append(Bar(stamp, day, day, Session.DAY, "test", price, price, price, price))
    return rows


def test_f_uses_signed_terminal_change_over_absolute_full_displacement() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=index) for index in range(162)]
    bars = {(day, Session.DAY): _bars(day, [2] * 45 + [-1] * 15) for day in axis}
    row = state_ledger(_classifier(axis), axis, bars, set())[-1]
    assert row["p_recent_close_jst"].endswith("09:44:00+09:00")
    assert row["pN_close_jst"].endswith("09:59:00+09:00")
    assert row["displacement_points"] == 75
    assert row["f_late_concentration"] == -0.2


def test_current_f_cannot_change_prior_f_threshold_or_current_band_reference() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=index) for index in range(162)]
    base = {
        (day, Session.DAY): _bars(day, [1 + index % 3] * 45 + [1] * 15)
        for index, day in enumerate(axis)
    }
    changed = dict(base)
    changed[(axis[-1], Session.DAY)] = _bars(axis[-1], [2] * 45 + [4] * 15)
    first, second = (
        state_ledger(_classifier(axis), axis, candidate, set())[-1] for candidate in (base, changed)
    )
    assert axis[-1].isoformat() not in first["reference_valid_trade_dates"]
    assert first["band_reference_trade_dates"] == second["band_reference_trade_dates"]
    assert first["q_conditional_f_low"] == second["q_conditional_f_low"]
    assert first["q_conditional_f_high"] == second["q_conditional_f_high"]
