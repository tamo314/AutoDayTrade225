from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r078_cash_first_hour_extreme_fade import r078_event


def _bar(target: date, stamp: datetime, value: int) -> Bar:
    return Bar(stamp, target, stamp.date(), Session.DAY, "test", value, value, value, value)


def test_r079_uses_the_reused_event_continuation_direction() -> None:
    target = date(2024, 1, 4)
    endpoint = datetime.combine(target, time(9, 59), JST)
    state = {
        "trade_date": target.isoformat(),
        "state": "E",
        "r_points": -10,
        "signal_endpoint_jst": endpoint.isoformat(),
    }
    bars = {
        (target, Session.DAY): [
            _bar(target, endpoint + timedelta(minutes=offset), 100)
            for offset in (0, 1, 270, 271)
        ]
    }
    event = r078_event(state, bars)
    assert event["status"] == "EXECUTABLE"
    assert event["continuation_direction"] == "short"
    assert event["fade_direction"] == "long"
