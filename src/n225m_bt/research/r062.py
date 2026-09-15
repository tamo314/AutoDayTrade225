"""Causal overnight-inventory rejection event construction for R062-Q001."""

from __future__ import annotations

from datetime import date, timedelta
from math import ceil
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r055 import DEVELOPMENT_END, DEVELOPMENT_START, night_observation

LOOKBACK = 120
MIN_REFERENCES = 100
BASE_WINDOW = 15


def nearest_rank(values: list[float], percentile: int) -> float:
    """Nearest-rank percentile; equality is deliberately assigned to the upper cell."""
    if not values or percentile not in {50, 70, 75, 80}:
        raise ValueError("R062 nearest-rank inputs are invalid")
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def _valid(row: Bar | None, target: date) -> bool:
    return bool(
        row is not None
        and row.is_eligible
        and row.trade_date == target
        and row.session is Session.DAY
        and row.open > 0
        and row.close > 0
    )


def r062_exec_event(
    classifier: CalendarClassifier,
    target: date,
    target_night: list[Bar] | None,
    target_day: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, list[Bar] | None, bool]],
    *,
    night_quarantined: bool = False,
    day_quarantined: bool = False,
    reaction_minutes: int = BASE_WINDOW,
) -> dict[str, object]:
    """Resolve R062 selection at the reaction close without common-E exits.

    The original :func:`r062_event` is intentionally left as the historical
    common-E report.  This adapter reads only the night observation, its
    rolling references, and the selected day reaction prefix.
    """
    if reaction_minutes not in {10, 15, 20}:
        raise ValueError("R062 reaction window is not preregistered")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "selection_status": "not_selected",
        "execution_status": "not_scheduled",
        "lookback_scheduled_triplets": LOOKBACK,
        "minimum_valid_x": MIN_REFERENCES,
        "reaction_minutes": reaction_minutes,
        "common_e_contract": "legacy_r062_event",
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if night_quarantined or day_quarantined:
        event["reason"] = "TARGET_SESSION_QUARANTINED"
        return event
    if len(history) != LOOKBACK:
        event["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TRIPLETS"
        return event
    night = night_observation(classifier, target, target_night)
    event.update(night, reference_trade_dates=[item[0].isoformat() for item in history])
    if night["status"] != "valid":
        event["reason"] = "TARGET_NIGHT_INVALID"
        return event
    values = [
        float(cast(float, observation["x"]))
        for prior, night_rows, _day_rows, isolated in history
        if not isolated
        and (observation := night_observation(classifier, prior, night_rows))["status"] == "valid"
    ]
    event["reference_valid_x_count"] = len(values)
    if len(values) < MIN_REFERENCES:
        event["reason"] = "INSUFFICIENT_VALID_X_REFERENCES"
        return event
    start = classifier.session_open(target, Session.DAY)
    by_time = {row.ts_jst: row for row in target_day or []}
    required = [by_time.get(start + timedelta(minutes=index)) for index in range(reaction_minutes)]
    if not all(_valid(row, target) for row in required):
        event["reason"] = "REACTION_DECISION_PATH_INVALID"
        return event
    rows = [cast(Bar, row) for row in required]
    r_n = float(cast(float, event["rN"]))
    sign = 1 if r_n > 0 else -1 if r_n < 0 else 0
    close = rows[-1].close
    h_points = sign * (close - rows[0].open) if sign else 0
    response = "rejection" if h_points <= -5 else "confirmation" if h_points >= 5 else "neutral"
    q50, q70, q75, q80 = (nearest_rank(values, percentile) for percentile in (50, 70, 75, 80))
    x = float(cast(float, event["x"]))
    extreme, medium = x >= q75, q50 <= x < q75
    cell = (
        "A"
        if sign and extreme and response == "rejection"
        else "C"
        if sign and medium and response == "rejection"
        else "D"
        if sign and extreme and response == "confirmation"
        else "M"
        if sign and medium and response == "confirmation"
        else "NONE"
    )
    selected = cell in {"A", "C", "D", "M"}
    event.update(
        status="E_EXEC",
        selection_status=cell if selected else "not_selected",
        execution_status="scheduled" if selected else "not_scheduled",
        reason="REACTION_CLASSIFIED",
        q50=q50,
        q70=q70,
        q75=q75,
        q80=q80,
        night_sign=sign,
        direction_eligible=sign != 0,
        opening_response_points=h_points,
        opening_response=response,
        cell=cell,
        planned_signal_jst=rows[-1].ts_jst.isoformat(),
        planned_entry_jst=(rows[-1].ts_jst + timedelta(minutes=1)).isoformat(),
        planned_exit_jst=(rows[-1].ts_jst + timedelta(minutes=16)).isoformat(),
        tse_open_points=rows[0].open,
        tse_reaction_close_points=close,
    )
    return event


def r062_event(
    classifier: CalendarClassifier,
    target: date,
    target_night: list[Bar] | None,
    target_day: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, list[Bar] | None, bool]],
    *,
    night_quarantined: bool = False,
    day_quarantined: bool = False,
    reaction_minutes: int = BASE_WINDOW,
) -> dict[str, object]:
    """Build one common-E event using only the target's prior 120 scheduled triplets."""
    if reaction_minutes not in {10, 15, 20}:
        raise ValueError("R062 reaction window is not preregistered")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "lookback_scheduled_triplets": LOOKBACK,
        "minimum_valid_x": MIN_REFERENCES,
        "reaction_minutes": reaction_minutes,
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if night_quarantined or day_quarantined:
        event["reason"] = "TARGET_SESSION_QUARANTINED"
        return event
    if len(history) != LOOKBACK:
        event["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TRIPLETS"
        return event
    night = night_observation(classifier, target, target_night)
    event.update(night, reference_trade_dates=[item[0].isoformat() for item in history])
    if night["status"] != "valid":
        event["reason"] = "TARGET_NIGHT_INVALID"
        return event
    values: list[float] = []
    for prior, night_rows, day_rows, isolated in history:
        observation = night_observation(classifier, prior, night_rows)
        prior_start = classifier.session_open(prior, Session.DAY)
        prior_by_time = {row.ts_jst: row for row in day_rows or []}
        prior_path = [prior_by_time.get(prior_start + timedelta(minutes=index)) for index in range(66)]
        if (
            not isolated
            and observation["status"] == "valid"
            and all(_valid(row, prior) for row in prior_path)
        ):
            values.append(float(cast(float, observation["x"])))
    event["reference_valid_x_count"] = len(values)
    if len(values) < MIN_REFERENCES:
        event["reason"] = "INSUFFICIENT_VALID_X_REFERENCES"
        return event
    start = classifier.session_open(target, Session.DAY)
    by_time = {row.ts_jst: row for row in target_day or []}
    # Common E intentionally includes every predeclared reaction/entry/exit path.
    required = [by_time.get(start + timedelta(minutes=index)) for index in range(66)]
    if not all(_valid(row, target) for row in required):
        event["reason"] = "TSE_COMMON_EXECUTION_PATH_INVALID"
        return event
    rows = [cast(Bar, row) for row in required]
    r_n = float(cast(float, event["rN"]))
    sign = 1 if r_n > 0 else -1 if r_n < 0 else 0
    close = rows[reaction_minutes - 1].close
    h_points = sign * (close - rows[0].open) if sign else 0
    response = "rejection" if h_points <= -5 else "confirmation" if h_points >= 5 else "neutral"
    q50, q70, q75, q80 = (nearest_rank(values, percentile) for percentile in (50, 70, 75, 80))
    x = float(cast(float, event["x"]))
    extreme = x >= q75
    medium = q50 <= x < q75
    cell = (
        "A"
        if sign and extreme and response == "rejection"
        else "C"
        if sign and medium and response == "rejection"
        else "D"
        if sign and extreme and response == "confirmation"
        else "M"
        if sign and medium and response == "confirmation"
        else "NONE"
    )
    entry_ordinal = reaction_minutes
    event.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        q50=q50,
        q70=q70,
        q75=q75,
        q80=q80,
        night_sign=sign,
        direction_eligible=sign != 0,
        opening_response_points=h_points,
        opening_response=response,
        cell=cell,
        planned_signal_jst=(start + timedelta(minutes=reaction_minutes - 1)).isoformat(),
        planned_entry_jst=(start + timedelta(minutes=entry_ordinal)).isoformat(),
        planned_exit15_jst=(start + timedelta(minutes=entry_ordinal + 15)).isoformat(),
        planned_exit30_jst=(start + timedelta(minutes=entry_ordinal + 30)).isoformat(),
        planned_exit45_jst=(start + timedelta(minutes=entry_ordinal + 45)).isoformat(),
        tse_open_points=rows[0].open,
        tse_reaction_close_points=close,
        tse_opening_range_bps=(max(row.high for row in rows[:reaction_minutes]) - min(row.low for row in rows[:reaction_minutes])) / rows[0].open * 10_000,
    )
    return event
