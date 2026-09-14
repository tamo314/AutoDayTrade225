"""Causal OSE-night return events for R055-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import ceil
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.session_rules import regime_for_trade_date
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START, DEVELOPMENT_END = date(2021, 1, 1), date(2025, 6, 30)
LOOKBACK, MIN_REFERENCES = 120, 100


def _rank(values: list[float], percentile: int) -> float:
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def _valid(row: Bar | None, target: date, session: Session) -> bool:
    return bool(
        row is not None
        and row.is_eligible
        and row.trade_date == target
        and row.session is session
        and row.open > 0
        and row.close > 0
    )


def normal_night_end(classifier: CalendarClassifier, target: date) -> tuple[datetime, datetime, str, str]:
    """Derive the normal endpoint from the explicitly assigned night start."""
    start = classifier.session_open(target, Session.NIGHT)
    regime = regime_for_trade_date(classifier.sessions, start.date())
    endpoint = regime.night.regular_end_next_day or regime.night.session_close_next_day
    if endpoint is None:
        raise ValueError("NO_SCHEDULED_NORMAL_NIGHT_ENDPOINT")
    known = datetime.combine(start.date() + timedelta(days=1), endpoint, JST)
    basis = (
        "regular_end_before_separate_closing_auction"
        if regime.night.regular_end_next_day is not None
        else "scheduled_session_close_no_finer_auction_metadata"
    )
    return known - timedelta(minutes=1), known, regime.id, basis


def night_observation(
    classifier: CalendarClassifier, target: date, rows: list[Bar] | None
) -> dict[str, object]:
    """Use every scheduled normal night minute, never observed endpoints."""
    result: dict[str, object] = {"status": "invalid"}
    try:
        start = classifier.session_open(target, Session.NIGHT)
        final, known, version, basis = normal_night_end(classifier, target)
    except ValueError as exc:
        result["reason"] = str(exc)
        return result
    result.update(
        nS_jst=start.isoformat(), cN_bar_start_jst=final.isoformat(), cN_known_jst=known.isoformat(),
        night_schedule_version=version, cN_selection_basis=basis,
    )
    count = int((final - start).total_seconds() // 60) + 1
    by_time = {bar.ts_jst: bar for bar in rows or []}
    segment = [by_time.get(start + timedelta(minutes=index)) for index in range(count)]
    if not all(_valid(row, target, Session.NIGHT) for row in segment):
        result["reason"] = "INCOMPLETE_OR_INELIGIBLE_SCHEDULED_NORMAL_NIGHT"
        return result
    concrete = [row for row in segment if row is not None]
    o_n, c_n = concrete[0].open, concrete[-1].close
    result.update(status="valid", oN_points=o_n, cN_points=c_n, rN=(c_n-o_n)/o_n, x=abs((c_n-o_n)/o_n), night_range_bps=(max(row.high for row in concrete)-min(row.low for row in concrete))/o_n*10_000, scheduled_night_minutes=count)
    return result


def r055_event(
    classifier: CalendarClassifier,
    target: date,
    target_night: list[Bar] | None,
    target_day: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, bool]],
    *,
    night_quarantined: bool = False,
    day_quarantined: bool = False,
) -> dict[str, object]:
    """Build common-E using exactly the preceding 120 explicit trade dates."""
    event: dict[str, object] = {"trade_date": target.isoformat(), "status": "skipped", "lookback_scheduled_trade_dates": LOOKBACK, "minimum_valid_x": MIN_REFERENCES}
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if night_quarantined or day_quarantined:
        event["reason"] = "TARGET_SESSION_QUARANTINED"
        return event
    if len(history) != LOOKBACK:
        event["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TRADE_DATES"
        return event
    current = night_observation(classifier, target, target_night)
    event.update(current, reference_trade_dates=[day.isoformat() for day, _, _ in history])
    if current["status"] != "valid":
        event["reason"] = "TARGET_NIGHT_INVALID"
        return event
    values = [
        float(cast(float, obs["x"]))
        for prior, rows, isolated in history
        if not isolated and (obs := night_observation(classifier, prior, rows))["status"] == "valid"
    ]
    event["reference_valid_x_count"] = len(values)
    if len(values) < MIN_REFERENCES:
        event["reason"] = "INSUFFICIENT_VALID_X_REFERENCES"
        return event
    start = classifier.session_open(target, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in target_day or []}
    path = [by_time.get(start + timedelta(minutes=i)) for i in (0, 30, 15, 45)]
    if not all(_valid(row, target, Session.DAY) for row in path):
        event["reason"] = "TSE_ENTRY_OR_FIXED_EXIT_PATH_INVALID"
        return event
    r_n = float(cast(float, event["rN"]))
    event.update(status="E", reason="COMMON_ELIGIBLE", q75=_rank(values,75), q85=_rank(values,85), q90=_rank(values,90), q95=_rank(values,95), night_sign=1 if r_n>0 else -1 if r_n<0 else 0, direction_eligible=r_n != 0, planned_entry_jst=start.isoformat(), planned_exit15_jst=(start+timedelta(minutes=15)).isoformat(), planned_exit30_jst=(start+timedelta(minutes=30)).isoformat(), planned_exit45_jst=(start+timedelta(minutes=45)).isoformat())
    return event
