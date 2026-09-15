from datetime import date, datetime, time

from n225m_bt.domain import Bar, Session
from n225m_bt.research.r066_tse_drift import (
    aligned_daily_net,
    feasibility,
    mbb_mean_ci,
    path_status,
)


def _bar(stamp: datetime, *, eligible: bool = True) -> Bar:
    return Bar(
        ts_jst=stamp,
        trade_date=stamp.date(),
        calendar_date=stamp.date(),
        session=Session.DAY,
        schedule_version="test",
        open=100,
        high=100,
        low=100,
        close=100,
        is_eligible=eligible,
    )


def test_path_status_requires_only_the_preregistered_execution_clocks() -> None:
    target = date(2025, 1, 6)
    tz = datetime.now().astimezone().tzinfo
    assert tz is not None
    bars = [_bar(datetime.combine(target, value, tz)) for value in (time(8, 59), time(9), time(14, 29), time(14, 30))]
    assert path_status(target, bars) == "EXECUTABLE"
    assert path_status(target, bars[:-1]) == "MISSING_EXIT"


def test_feasibility_gate_and_daily_null_contract() -> None:
    axis = [date(2021, 1, 4), date(2022, 1, 4), date(2023, 1, 4), date(2024, 1, 4)]
    assert feasibility(axis, {})["gate"]["passed"] is False
    daily = aligned_daily_net(axis, (), {axis[-1]})
    assert list(daily.values()) == [0, 0, 0, None]


def test_mbb_is_deterministic_and_nonempty() -> None:
    first = mbb_mean_ci(list(range(20)))
    second = mbb_mean_ci(list(range(20)))
    assert first == second
    assert first["estimate"] == 9.5
