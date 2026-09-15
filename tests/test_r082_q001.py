from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r082_us_open_continuation import (
    _execution_event,
    assign_abs_u_quintiles,
    nyse_open_jst,
    pre_open_sign_events,
)


def _bar(target: date, stamp: datetime, value: int) -> Bar:
    return Bar(stamp, target, stamp.date(), Session.NIGHT, "test", value, value, value, value)


def test_r082_uses_real_new_york_dst_conversion() -> None:
    assert nyse_open_jst(date(2024, 3, 8)).time() == time(23, 30)
    assert nyse_open_jst(date(2024, 3, 11)).time() == time(22, 30)
    assert nyse_open_jst(date(2024, 11, 1)).time() == time(22, 30)
    assert nyse_open_jst(date(2024, 11, 4)).time() == time(23, 30)


def test_r082_enters_after_u_endpoint_and_exits_at_s_plus_180_open() -> None:
    target, s = date(2024, 3, 12), datetime(2024, 3, 11, 22, 30, tzinfo=JST)
    base = {
        "trade_date": target.isoformat(),
        "u_valid": True,
        "u_points": 5,
        "s_jst": s.isoformat(),
    }
    bars = {
        (target, Session.NIGHT): [
            _bar(target, s + timedelta(minutes=offset), 100) for offset in (29, 30, 179, 180)
        ]
    }
    event = _execution_event(base, bars)
    assert event["status"] == "EXECUTABLE"
    assert str(event["entry_open_jst"]).endswith("23:00:00+09:00")
    assert datetime.fromisoformat(str(event["entry_open_jst"])) == s + timedelta(minutes=30)
    assert datetime.fromisoformat(str(event["exit_open_jst"])) == s + timedelta(minutes=180)


def test_r082_pre_open_control_is_only_same_date_nonzero_intersection() -> None:
    events = pre_open_sign_events(
        [
            {
                "trade_date": "2024-03-12",
                "status": "EXECUTABLE",
                "u_points": 5,
                "p_valid": True,
                "p_points": -5,
            },
            {"trade_date": "2024-03-13", "status": "EXECUTABLE", "u_points": 5, "p_valid": False},
        ]
    )
    assert events[0]["pre_open_direction"] == "short"
    assert events[0]["pre_open_sign_control_status"] == "EXECUTABLE_COMMON_U_AND_P_NONZERO"
    assert events[1]["status"] == "SKIPPED"


def test_r082_quintiles_are_descriptive_with_trade_date_tiebreak() -> None:
    events = assign_abs_u_quintiles(
        [
            {"trade_date": f"2024-03-{day:02d}", "status": "EXECUTABLE", "abs_u_points": 5}
            for day in range(1, 11)
        ]
    )
    assert [event["abs_u_quintile"] for event in events] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
