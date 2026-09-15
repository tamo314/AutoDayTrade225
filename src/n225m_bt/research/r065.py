"""Fixed, schedule-derived overnight boundary events for R065-Q001."""

from __future__ import annotations

from datetime import date, timedelta
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)


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
