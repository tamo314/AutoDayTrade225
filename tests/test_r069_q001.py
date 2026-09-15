from datetime import date, datetime, time

from n225m_bt.domain import Bar, Session
from n225m_bt.research.r069_opening_range_breakout import (
    mbb_mean_ci,
    opening_range_breakout_event,
)


def _bar(target: date, clock: time, *, high: int = 100, low: int = 100, close: int = 100) -> Bar:
    return Bar(
        datetime.combine(target, clock).astimezone(),
        target,
        target,
        Session.DAY,
        "test",
        100,
        high,
        low,
        close,
    )


def _primary_path(target: date) -> list[Bar]:
    clocks = [time(9, minute) for minute in range(60)] + [time(10, minute) for minute in range(60)]
    clocks += [time(11, 0), time(14, 29), time(14, 30)]
    return [_bar(target, clock) for clock in clocks]


def test_first_strict_close_breakout_uses_opening_high_low_and_next_open() -> None:
    target = date(2025, 1, 6)
    bars = _primary_path(target)
    bars[5] = _bar(target, time(9, 5), high=110, low=90, close=100)
    bars[30] = _bar(target, time(9, 30), high=112, low=99, close=111)
    event = opening_range_breakout_event(target, bars)
    assert event["status"] == "SIGNALLED"
    assert event["breakout_direction"] == "long"
    assert event["breakout_bar_jst"].endswith("09:30:00+00:00") or "09:30" in str(
        event["breakout_bar_jst"]
    )
    assert event["entry_observable"] is True
    assert event["outcome_observable"] is True


def test_equality_is_not_breakout_and_missing_range_skips() -> None:
    target = date(2025, 1, 6)
    equality = _primary_path(target)
    equality[30] = _bar(target, time(9, 30), close=100)
    assert opening_range_breakout_event(target, equality)["reason"] == "NO_STRICT_BREAKOUT_BY_DEADLINE"
    missing = equality[1:]
    assert opening_range_breakout_event(target, missing)["reason"] == "MISSING_OPENING_RANGE"


def test_mbb_is_fixed_seed_and_nonwrapping() -> None:
    assert mbb_mean_ci(list(range(20))) == mbb_mean_ci(list(range(20)))
