from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r076_tse_opening_failed_auction import failed_auction_event, feasibility


def _bar(target: date, clock: time, value: int, *, close: int | None = None) -> Bar:
    stamp = datetime.combine(target, clock, JST)
    final_close = value if close is None else close
    return Bar(stamp, target, target, Session.DAY, "synthetic", value, value, value, final_close)


def _day(target: date, *, return_close: int = 105) -> list[Bar]:
    rows = [
        _bar(target, (datetime.combine(target, time(9)) + timedelta(minutes=index)).time(), 100)
        for index in range(31)
    ]
    rows[0] = Bar(rows[0].ts_jst, target, target, Session.DAY, "synthetic", 100, 110, 100, 100)
    rows[30] = _bar(target, time(9, 30), 100, close=115)
    rows.extend([_bar(target, time(9, 31), 100, close=return_close), _bar(target, time(9, 32), 100)])
    rows.extend([_bar(target, clock, 100) for clock in (time(14, 14), time(14, 15), time(14, 29), time(14, 30), time(14, 44), time(14, 45))])
    return rows


def test_failed_breakout_uses_post_breakout_strict_internal_confirmation() -> None:
    target = date(2024, 1, 4)
    event = failed_auction_event(target, _day(target))
    assert event["status"] == "EXECUTABLE"
    assert event["initial_breakout_direction"] == "long"
    assert event["failed_fade_direction"] == "short"
    assert event["breakout_bar_jst"].endswith("09:30:00+09:00")
    assert event["confirmation_bar_jst"].endswith("09:31:00+09:00")
    assert event["entry_open_jst"].endswith("09:32:00+09:00")


def test_equal_close_is_not_a_return_and_two_close_profile_waits_for_second_close() -> None:
    target = date(2024, 1, 4)
    equality_rows = _day(target, return_close=110)
    equality_rows.extend(
        _bar(target, (datetime.combine(target, time(9, 33)) + timedelta(minutes=index)).time(), 100, close=110)
        for index in range(28)
    )
    equality = failed_auction_event(target, equality_rows)
    assert equality["reason"] == "NO_STRICT_INTERNAL_CLOSE_BY_RETURN_DEADLINE"
    rows = _day(target)
    rows.append(_bar(target, time(9, 32), 100, close=105))
    rows.append(_bar(target, time(9, 33), 100))
    event = failed_auction_event(target, rows, consecutive_internal_closes=2)
    assert event["confirmation_bar_jst"].endswith("09:32:00+09:00")


def test_s2_gate_requires_the_frozen_count_and_direction_minima() -> None:
    events: dict[date, dict[str, object]] = {}
    cursor = date(2021, 1, 1)
    for index in range(212):
        target = cursor + timedelta(days=index)
        events[target] = {
            "status": "EXECUTABLE",
            "reason": "FAILED_BREAKOUT_CONFIRMED_STRICT_INTERNAL_CLOSE",
            "initial_breakout_direction": "long" if index % 2 else "short",
        }
    result = feasibility(events)
    assert result["gate"]["executable_at_least_200"] is True
    assert result["gate"]["initial_direction_minima_passed"] is True
