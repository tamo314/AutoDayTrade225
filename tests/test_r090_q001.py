from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r090_lunch_rejection import feasibility, state_ledger


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
    base, price, rows = datetime.combine(day, time(10), JST), 1000, []
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
        datetime.combine(day, time(13, 24), JST),
        datetime.combine(day, time(13, 25), JST),
        datetime.combine(day, time(14, 54), JST),
        datetime.combine(day, time(14, 55), JST),
    ):
        rows.append(Bar(stamp, day, day, Session.DAY, "test", price, price, price, price))
    return rows


def test_lunch_j_has_registered_times_signed_reversal_and_absolute_denom() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=i) for i in range(162)]
    bars = {(day, Session.DAY): _bars(day, [1] * 150 + [-1] * 10 + [0] * 190) for day in axis}
    row = state_ledger(_classifier(axis), axis, bars, set())[-1]
    assert row["p0_open_jst"].endswith("11:30:00+09:00")
    assert row["p1_close_jst"].endswith("12:29:00+09:00")
    assert row["p2_close_jst"].endswith("12:39:00+09:00")
    assert row["displacement_points"] == 60
    assert row["j_rejection"] == 1 / 6


def test_current_j_cannot_change_prior_threshold_and_placebo_is_separate() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=i) for i in range(162)]
    base = {(day, Session.DAY): _bars(day, [1 + i % 3] * 330) for i, day in enumerate(axis)}
    changed = dict(base)
    changed[(axis[-1], Session.DAY)] = _bars(axis[-1], [3] * 150 + [-3] * 10 + [1] * 170)
    first, second = (
        state_ledger(_classifier(axis), axis, candidate, set())[-1] for candidate in (base, changed)
    )
    assert axis[-1].isoformat() not in first["reference_valid_trade_dates"]
    assert first["band_reference_trade_dates"] == second["band_reference_trade_dates"]
    assert first["q_conditional_j_high"] == second["q_conditional_j_high"]
    placebo = state_ledger(_classifier(axis), axis, base, set(), interval="placebo")[-1]
    assert placebo["interval"] == "placebo"
    assert placebo["p0_open_jst"].endswith("10:00:00+09:00")


def test_lr_la_reason_codes_are_explained_in_pnl_free_audit() -> None:
    rows = [
        {
            "trade_date": "2023-01-02",
            "reason": "LR_AND_LA_AT_EQUAL_J_QUANTILES",
            "status": "SKIPPED",
        }
    ]
    audit = feasibility(rows, rows)
    assert audit["gate"]["unexplained_exclusions_equal_zero"] is True
