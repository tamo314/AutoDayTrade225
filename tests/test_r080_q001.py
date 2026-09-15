from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r080_cash_first_hour_all_continuation import (
    attach_lagged_sign,
    primary_events,
)


def _bar(target: date, stamp: datetime, value: int) -> Bar:
    return Bar(stamp, target, stamp.date(), Session.DAY, "test", value, value, value, value)


def test_r080_executes_nonzero_r_without_extreme_selection() -> None:
    target = date(2024, 1, 4)
    endpoint = datetime.combine(target, time(9, 59), JST)
    source = [{"trade_date": target.isoformat(), "r_valid": True, "r_points": 5, "abs_r_points": 5, "signal_endpoint_jst": endpoint.isoformat(), "state": "NONE"}]
    bars = {(target, Session.DAY): [_bar(target, endpoint + timedelta(minutes=offset), 100) for offset in (0, 1, 270, 271)]}
    event = primary_events(source, bars)[0]
    assert event["status"] == "EXECUTABLE"
    assert event["continuation_direction"] == "long"
    assert str(event["entry_open_jst"]).endswith("10:00:00+09:00")


def test_r080_lagged_sign_is_strictly_prior_valid_r() -> None:
    events = attach_lagged_sign([
        {"trade_date": "2024-01-04", "r_valid": True, "r_points": -5},
        {"trade_date": "2024-01-05", "r_valid": False},
        {"trade_date": "2024-01-09", "r_valid": True, "r_points": 5},
    ])
    assert [event["lagged_r_direction"] for event in events] == [None, "short", "short"]
