from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r081_tse_lunch_continuation import (
    MAIN_WINDOW,
    TRIMMED_WINDOW,
    _execution_event,
    assign_abs_l_quintiles,
    morning_sign_events,
)


def _bar(target: date, stamp: datetime, value: int) -> Bar:
    return Bar(stamp, target, stamp.date(), Session.DAY, "test", value, value, value, value)


def test_r081_lunch_event_enters_only_after_1229_close() -> None:
    target = date(2024, 1, 4)
    endpoint = datetime.combine(target, time(12, 29), JST)
    base = {"trade_date": target.isoformat(), "l_valid": True, "l_points": 5, "signal_endpoint_jst": endpoint.isoformat()}
    bars = {(target, Session.DAY): [_bar(target, endpoint + timedelta(minutes=offset), 100) for offset in (0, 1, 120, 121)]}
    event = _execution_event(base, bars)
    assert event["status"] == "EXECUTABLE"
    assert event["continuation_direction"] == "long"
    assert str(event["entry_open_jst"]).endswith("12:30:00+09:00")
    assert str(event["exit_open_jst"]).endswith("14:30:00+09:00")


def test_r081_trimmed_window_and_only_registered_windows_are_fixed() -> None:
    assert (time(11, 30), time(12, 29)) == MAIN_WINDOW
    assert (time(11, 35), time(12, 24)) == TRIMMED_WINDOW


def test_r081_morning_control_is_only_nonzero_same_date_intersection() -> None:
    events = morning_sign_events([
        {"trade_date": "2024-01-04", "status": "EXECUTABLE", "l_points": 5, "m_valid": True, "m_points": -5},
        {"trade_date": "2024-01-05", "status": "EXECUTABLE", "l_points": 5, "m_valid": False},
        {"trade_date": "2024-01-09", "status": "SKIPPED", "l_points": -5, "m_valid": True, "m_points": -5},
    ])
    assert events[0]["morning_direction"] == "short"
    assert events[0]["morning_sign_control_status"] == "EXECUTABLE_COMMON_L_AND_M_NONZERO"
    assert events[1]["status"] == "SKIPPED"
    assert events[2]["morning_sign_control_status"] == "NOT_IN_COMMON_L_AND_M_NONZERO_SET"


def test_r081_quintiles_are_descriptive_and_use_trade_date_tiebreak() -> None:
    events = assign_abs_l_quintiles([
        {"trade_date": f"2024-01-{day:02d}", "status": "EXECUTABLE", "abs_l_points": 5}
        for day in range(2, 12)
    ])
    assert [event["abs_l_quintile"] for event in events] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
