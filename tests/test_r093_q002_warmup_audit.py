from copy import deepcopy
from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r093_cash_night_reversal import (
    cash_state_ledger,
    nearest_rank,
    scheduled_reference_audit,
)


def _bar(day: date, stamp: datetime, value: int, *, close: int | None = None) -> Bar:
    return Bar(
        stamp,
        day,
        stamp.date(),
        Session.DAY,
        "synthetic",
        value,
        value,
        value,
        value if close is None else close,
    )


def _cash_bars(axis: list[date], missing: set[date] | None = None) -> dict[tuple[date, Session], list[Bar]]:
    result: dict[tuple[date, Session], list[Bar]] = {}
    missing = missing or set()
    for index, day in enumerate(axis):
        if day in missing:
            result[(day, Session.DAY)] = []
            continue
        result[(day, Session.DAY)] = [
            _bar(day, datetime.combine(day, time(9), JST), 10_000),
            _bar(day, datetime.combine(day, time(14, 59), JST), 10_000 + index, close=10_000 + index),
        ]
    return result


def test_r093_q002_warmup_windows_are_exact_through_and_after_120() -> None:
    first = date(2024, 1, 1)
    axis = [first + timedelta(days=index) for index in range(122)]
    ledger = cash_state_ledger(axis, _cash_bars(axis), set())

    for position in (0, 1, 99, 100, 119, 120, 121):
        expected = [item.isoformat() for item in axis[max(0, position - 120) : position]]
        assert ledger[position]["reference_trade_dates_oldest_to_newest"] == expected
    assert "q75" not in ledger[119]
    assert ledger[120]["reference_valid_x_count"] == 120
    assert "q75" in ledger[120]
    assert all(scheduled_reference_audit(ledger, axis).values())


def test_r093_q002_missing_reference_is_not_backfilled_and_requires_100_valid_x() -> None:
    first = date(2024, 1, 1)
    axis = [first + timedelta(days=index) for index in range(131)]
    missing = set(axis[10:30])
    ledger = cash_state_ledger(axis, _cash_bars(axis, missing), set())

    target = ledger[130]
    assert target["reference_trade_dates_oldest_to_newest"] == [item.isoformat() for item in axis[10:130]]
    assert target["reference_valid_x_count"] == 100
    assert "q75" in target
    expected_values = [index / 10_000 for index in range(30, 130)]
    assert target["q75"] == nearest_rank(expected_values, 75)

    too_many_missing = set(axis[10:31])
    insufficient = cash_state_ledger(axis, _cash_bars(axis, too_many_missing), set())[130]
    assert insufficient["reference_valid_x_count"] == 99
    assert "q75" not in insufficient
    assert insufficient["reason"] == "INSUFFICIENT_VALID_X_REFERENCES"


def test_r093_q002_audit_rejects_current_future_and_duplicate_references() -> None:
    first = date(2024, 1, 1)
    axis = [first + timedelta(days=index) for index in range(122)]
    ledger = cash_state_ledger(axis, _cash_bars(axis), set())
    current = deepcopy(ledger)
    current[120]["reference_trade_dates_oldest_to_newest"][-1] = axis[120].isoformat()
    assert not scheduled_reference_audit(current, axis)["reference_dates_exclude_current_and_future"]
    future = deepcopy(ledger)
    future[121]["reference_trade_dates_oldest_to_newest"][-1] = (axis[121] + timedelta(days=1)).isoformat()
    assert not scheduled_reference_audit(future, axis)["reference_dates_exclude_current_and_future"]
    duplicate = deepcopy(ledger)
    duplicate[120]["reference_trade_dates_oldest_to_newest"][-1] = duplicate[120]["reference_trade_dates_oldest_to_newest"][0]
    assert not scheduled_reference_audit(duplicate, axis)["reference_dates_preserve_order_and_uniqueness"]
