"""Fixed, schedule-derived overnight boundary events for R065-Q001."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import BacktestConfig
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)


class HoldingPolicy(StrEnum):
    SESSION_FLAT = "session_flat"
    EXPLICIT_CROSS_SESSION = "explicit_cross_session"


@dataclass(frozen=True, slots=True)
class CrossSessionProfile:
    """Explicit, non-default risk contract required before boundary execution."""

    holding_policy: HoldingPolicy
    max_wall_clock_hold_minutes: int | None
    max_concurrent_positions: int | None
    pending_order_policy: str
    end_of_data_policy: str
    model_limit_note: str


@dataclass(frozen=True, slots=True)
class ScheduledBoundaryOrder:
    """Calendar-only order plan; it contains no observed market price."""

    condition: str
    direction: str
    order_created_at: datetime
    submit_at: datetime
    scheduled_fill_at: datetime
    scheduled_exit_at: datetime
    decision_basis: str


@dataclass(frozen=True, slots=True)
class CrossSessionEngineCapability:
    """Read-only capability assessment, not an authorization to execute."""

    status: str
    reasons: tuple[str, ...]


def assess_shared_engine_for_cross_session(config: BacktestConfig) -> CrossSessionEngineCapability:
    """Fail closed: the shared bar-close engine is not an R065 execution profile.

    This inspection intentionally does not alter ``config``.  Even a caller
    that enables cross-session pending orders still lacks a calendar-scheduled
    open-order type and an unresolved-exit/null-PnL state in the shared engine.
    """
    reasons = [
        "shared engine creates signals only at bar close; it has no calendar-scheduled open-order type",
        "shared engine force-closes any remaining position at end of supplied data instead of recording null PnL",
    ]
    if not config.execution.allow_cross_session_pending_order:
        reasons.append("baseline configuration cancels pending orders across a session boundary")
    if config.risk.force_flat:
        reasons.append("baseline configuration force-flats held positions before each session close")
    return CrossSessionEngineCapability("BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED", tuple(reasons))


def validate_cross_session_profile(profile: CrossSessionProfile) -> None:
    """Fail closed unless the special holding/risk profile is fully explicit."""
    if profile.holding_policy is not HoldingPolicy.EXPLICIT_CROSS_SESSION:
        raise ValueError("cross-session boundary orders require explicit_cross_session")
    if profile.max_wall_clock_hold_minutes is None or profile.max_wall_clock_hold_minutes <= 0:
        raise ValueError("maximum wall-clock holding time must be explicit and positive")
    if profile.max_concurrent_positions is None or profile.max_concurrent_positions != 1:
        raise ValueError("cross-session maximum concurrent positions must explicitly be one")
    if profile.pending_order_policy != "calendar_scheduled_boundary_only":
        raise ValueError("cross-session pending-order policy is not explicit")
    if profile.end_of_data_policy != "open_position_null_pnl":
        raise ValueError("cross-session end-of-data policy must retain null PnL")
    if not profile.model_limit_note.strip():
        raise ValueError("cross-session model-limit note is required")


def boundary_order_plan(
    classifier: CalendarClassifier,
    target: date,
    profile: CrossSessionProfile,
    *,
    a_order_created_at: datetime,
    d_order_created_at: datetime,
) -> dict[str, object]:
    """Plan R065 A/D calendar orders without inspecting any Bar or result ledger.

    A is n0-to-d0 and D is d0-to-n1.  The function intentionally rejects a
    condition whose scheduled boundary leaves Development before any market
    input could be selected.  It does not change the shared session-flat
    engine configuration or claim that the engine can execute this profile.
    """
    validate_cross_session_profile(profile)
    maximum_hold = profile.max_wall_clock_hold_minutes
    if maximum_hold is None:  # Kept for static type narrowing after validation.
        raise ValueError("maximum wall-clock holding time must be explicit")
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "holding_policy": profile.holding_policy.value,
        "status": "skipped",
        "price_access": "none",
        "conditions": {},
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        result["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return result
    record = classifier.exchange_calendar.get(target)
    if record is None or record.next_trade_date is None:
        result["reason"] = "NO_UNIQUE_NEXT_TRADE_DATE"
        return result
    next_target = record.next_trade_date
    next_record = classifier.exchange_calendar.get(next_target)
    if next_record is None or next_record.previous_trade_date != target:
        result["reason"] = "NONRECIPROCAL_SCHEDULE_CHAIN"
        return result
    try:
        n0 = classifier.session_open(target, Session.NIGHT)
        d0 = classifier.session_open(target, Session.DAY)
        n1 = classifier.session_open(next_target, Session.NIGHT)
    except ValueError as exc:
        result["reason"] = str(exc)
        return result
    if not n0 < d0 < n1:
        result["reason"] = "NONMONOTONIC_SCHEDULE_BOUNDARIES"
        return result

    def schedule(
        condition: str, created: datetime, entry: datetime, exit_: datetime
    ) -> dict[str, object]:
        if created >= entry:
            return {"status": "blocked", "reason": "ORDER_NOT_CREATED_BEFORE_SCHEDULED_OPEN"}
        held_minutes = int((exit_ - entry).total_seconds() // 60)
        if held_minutes > maximum_hold:
            return {"status": "blocked", "reason": "MAX_WALL_CLOCK_HOLD_EXCEEDED"}
        order = ScheduledBoundaryOrder(
            condition,
            "long",
            created,
            created,
            entry,
            exit_,
            "versioned_calendar_only",
        )
        return {
            "status": "E_EXEC",
            "selection_status": "scheduled_boundary",
            "execution_status": "scheduled",
            "order": order,
            "wall_clock_hold_minutes": held_minutes,
        }

    conditions: dict[str, object] = {"A_n0_to_d0": schedule("A", a_order_created_at, n0, d0)}
    if not DEVELOPMENT_START <= next_target <= DEVELOPMENT_END:
        conditions["D_d0_to_n1"] = {
            "status": "skipped",
            "selection_status": "not_selected",
            "execution_status": "not_scheduled",
            "reason": "PLANNED_N1_OUTSIDE_DEVELOPMENT",
            "n1_trade_date": next_target.isoformat(),
        }
    else:
        conditions["D_d0_to_n1"] = schedule("D", d_order_created_at, d0, n1)
    result.update(
        status="planned",
        reason="CALENDAR_ONLY_BOUNDARY_PLAN",
        n0_jst=n0.isoformat(),
        d0_jst=d0.isoformat(),
        n1_jst=n1.isoformat(),
        n1_trade_date=next_target.isoformat(),
        conditions=conditions,
    )
    return result


def _valid(bar: Bar | None, target: date, session: Session) -> bool:
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is session
        and bar.open > 0
    )


def boundary_event(
    classifier: CalendarClassifier,
    target: date,
    grouped: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Return common E without inferring any calendar linkage from observed bars."""
    event: dict[str, object] = {"trade_date": target.isoformat(), "status": "skipped"}
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    record = classifier.exchange_calendar.get(target)
    if record is None or record.next_trade_date is None:
        event["reason"] = "NO_UNIQUE_NEXT_TRADE_DATE"
        return event
    next_target = record.next_trade_date
    next_record = classifier.exchange_calendar.get(next_target)
    if next_record is None or next_record.previous_trade_date != target:
        event["reason"] = "NONRECIPROCAL_SCHEDULE_CHAIN"
        return event
    try:
        n0 = classifier.session_open(target, Session.NIGHT)
        d0 = classifier.session_open(target, Session.DAY)
        n1 = classifier.session_open(next_target, Session.NIGHT)
    except ValueError as exc:
        event["reason"] = str(exc)
        return event
    event.update(
        {
            "n0_jst": n0.isoformat(),
            "n0_delay1_jst": (n0 + timedelta(minutes=1)).isoformat(),
            "n0_delay5_jst": (n0 + timedelta(minutes=5)).isoformat(),
            "d0_jst": d0.isoformat(),
            "d0_delay1_jst": (d0 + timedelta(minutes=1)).isoformat(),
            "n1_jst": n1.isoformat(),
            "n1_trade_date": next_target.isoformat(),
            "schedule_version_t": record.schedule_version,
            "schedule_version_n1": next_record.schedule_version,
        }
    )
    if not n0 < d0 < n1:
        event["reason"] = "NONMONOTONIC_SCHEDULE_BOUNDARIES"
        return event
    sessions = ((target, Session.NIGHT), (target, Session.DAY), (next_target, Session.NIGHT))
    if any(key in isolated for key in sessions):
        event["reason"] = "R004_SESSION_QUARANTINED"
        return event
    lookup = {
        key: {bar.ts_jst: bar for bar in grouped.get(key, [])}
        for key in sessions
    }
    required = {
        "n0": (lookup[(target, Session.NIGHT)].get(n0), target, Session.NIGHT),
        "n0_delay1": (
            lookup[(target, Session.NIGHT)].get(n0 + timedelta(minutes=1)),
            target,
            Session.NIGHT,
        ),
        "n0_delay5": (
            lookup[(target, Session.NIGHT)].get(n0 + timedelta(minutes=5)),
            target,
            Session.NIGHT,
        ),
        "d0": (lookup[(target, Session.DAY)].get(d0), target, Session.DAY),
        "d0_delay1": (
            lookup[(target, Session.DAY)].get(d0 + timedelta(minutes=1)),
            target,
            Session.DAY,
        ),
        "n1": (lookup[(next_target, Session.NIGHT)].get(n1), next_target, Session.NIGHT),
    }
    invalid = [name for name, (bar, day, session) in required.items() if not _valid(bar, day, session)]
    if invalid:
        event["reason"] = "MISSING_INELIGIBLE_OR_NONPOSITIVE_" + "_".join(invalid).upper()
        return event
    concrete = {name: cast(Bar, value[0]) for name, value in required.items()}
    event.update(
        {
            "status": "E",
            "reason": "COMMON_ELIGIBLE",
            "n0_open": concrete["n0"].open,
            "n0_delay1_open": concrete["n0_delay1"].open,
            "n0_delay5_open": concrete["n0_delay5"].open,
            "d0_open": concrete["d0"].open,
            "d0_delay1_open": concrete["d0_delay1"].open,
            "n1_open": concrete["n1"].open,
        }
    )
    return event
