from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r065 import (
    CrossSessionProfile,
    HoldingPolicy,
    assess_shared_engine_for_cross_session,
    boundary_event,
    boundary_order_plan,
    validate_cross_session_profile,
)


def bar(ts: datetime, target: date, session: Session, price: int = 40_000) -> Bar:
    return Bar(ts, target, ts.date(), session, "test", price, price, price, price)


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def rows(c: CalendarClassifier) -> dict[tuple[date, Session], list[Bar]]:
    target = date(2024, 11, 5)
    record = c.exchange_calendar.get(target)
    assert record is not None and record.next_trade_date is not None
    next_target = record.next_trade_date
    n0, d0, n1 = (
        c.session_open(target, Session.NIGHT),
        c.session_open(target, Session.DAY),
        c.session_open(next_target, Session.NIGHT),
    )
    return {
        (target, Session.NIGHT): [bar(n0 + timedelta(minutes=i), target, Session.NIGHT) for i in range(6)],
        (target, Session.DAY): [bar(d0 + timedelta(minutes=i), target, Session.DAY) for i in range(2)],
        (next_target, Session.NIGHT): [bar(n1, next_target, Session.NIGHT)],
    }


def test_r065_schedule_chain_and_common_path() -> None:
    c = classifier()
    event = boundary_event(c, date(2024, 11, 5), rows(c), set())
    assert event["status"] == "E"
    assert event["n0_jst"] < event["d0_jst"] < event["n1_jst"]  # type: ignore[operator]
    assert event["n1_trade_date"] == "2024-11-06"


def test_r065_requires_delayed_path_and_next_night() -> None:
    c = classifier()
    grouped = rows(c)
    grouped[(date(2024, 11, 5), Session.NIGHT)] = grouped[(date(2024, 11, 5), Session.NIGHT)][:5]
    event = boundary_event(c, date(2024, 11, 5), grouped, set())
    assert event["status"] == "skipped"
    assert "N0_DELAY5" in str(event["reason"])


def test_r1_cross_session_profile_requires_calendar_only_precreated_orders() -> None:
    c, target = classifier(), date(2024, 11, 5)
    n0, d0 = c.session_open(target, Session.NIGHT), c.session_open(target, Session.DAY)
    profile = CrossSessionProfile(
        HoldingPolicy.EXPLICIT_CROSS_SESSION,
        10_000,
        1,
        "calendar_scheduled_boundary_only",
        "open_position_null_pnl",
        "margin and forced liquidation are outside the research model",
    )
    plan = boundary_order_plan(
        c, target, profile, a_order_created_at=n0 - timedelta(minutes=1), d_order_created_at=d0 - timedelta(minutes=1)
    )
    assert plan["price_access"] == "none"
    conditions = plan["conditions"]
    assert isinstance(conditions, dict)
    assert conditions["A_n0_to_d0"]["status"] == "E_EXEC"  # type: ignore[index]
    assert conditions["D_d0_to_n1"]["status"] == "E_EXEC"  # type: ignore[index]


def test_r1_cross_session_profile_excludes_development_terminal_n1_without_prices() -> None:
    c, target = classifier(), date(2025, 6, 30)
    n0, d0 = c.session_open(target, Session.NIGHT), c.session_open(target, Session.DAY)
    profile = CrossSessionProfile(
        HoldingPolicy.EXPLICIT_CROSS_SESSION,
        10_000,
        1,
        "calendar_scheduled_boundary_only",
        "open_position_null_pnl",
        "margin and forced liquidation are outside the research model",
    )
    plan = boundary_order_plan(
        c, target, profile, a_order_created_at=n0 - timedelta(minutes=1), d_order_created_at=d0 - timedelta(minutes=1)
    )
    conditions = plan["conditions"]
    assert isinstance(conditions, dict)
    assert conditions["D_d0_to_n1"]["reason"] == "PLANNED_N1_OUTSIDE_DEVELOPMENT"  # type: ignore[index]


def test_r1_cross_session_profile_rejects_the_default_session_flat_contract() -> None:
    profile = CrossSessionProfile(
        HoldingPolicy.SESSION_FLAT,
        60,
        1,
        "calendar_scheduled_boundary_only",
        "open_position_null_pnl",
        "test only",
    )
    with pytest.raises(ValueError, match="explicit_cross_session"):
        validate_cross_session_profile(profile)


def test_r1_shared_engine_is_blocked_until_a_separate_cross_session_executor_exists() -> None:
    _, _, _, config = load_project_config(Path("config"))
    capability = assess_shared_engine_for_cross_session(config)
    assert capability.status == "BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED"
    assert any("calendar-scheduled" in reason for reason in capability.reasons)
    assert any("force-flats" in reason for reason in capability.reasons)
