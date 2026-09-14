"""Precommitted non-circular rolling-U construction for R062-Q002.

U membership is deliberately a night-only property.  In particular, neither
the following TSE path nor the availability of a rolling threshold can alter
whether a completed TSE--OSE-night--TSE triplet belongs to U.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from math import ceil
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r055 import DEVELOPMENT_END, DEVELOPMENT_START, night_observation

LOOKBACK = 120
MIN_REFERENCES = 100
TICK = 5
COMMON_DAY_BARS = 66


def nearest_rank(values: list[float], percentile: int) -> float:
    """Precommitted nearest-rank threshold; equality is on the upper side."""
    if not values or percentile not in {50, 70, 75, 80}:
        raise ValueError("R062-Q002 nearest-rank inputs are invalid")
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def _valid_day(bar: Bar | None, target: date) -> bool:
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and min(bar.open, bar.high, bar.low, bar.close) > 0
    )


def u_observation(
    classifier: CalendarClassifier,
    target: date,
    night_rows: list[Bar] | None,
    *,
    night_quarantined: bool = False,
) -> dict[str, object]:
    """Return the night-only U membership record for one scheduled triplet."""
    record = classifier.exchange_calendar.get(target)
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "u_member": False,
        "u_reason": "TRIPLET_MAPPING_UNAVAILABLE",
    }
    if record is None or record.previous_trade_date is None or record.night_calendar_start_date is None:
        return result
    previous = classifier.exchange_calendar.get(record.previous_trade_date)
    if previous is None or previous.next_trade_date != target:
        return result
    result.update(
        prior_tse_trade_date=record.previous_trade_date.isoformat(),
        ose_night_calendar_start_date=record.night_calendar_start_date.isoformat(),
    )
    if night_quarantined:
        result["u_reason"] = "R004_NIGHT_QUARANTINED"
        return result
    observation = night_observation(classifier, target, night_rows)
    result.update({f"u_{key}": value for key, value in observation.items()})
    if observation.get("status") != "valid":
        result["u_reason"] = "NIGHT_ENDPOINT_OR_PATH_INVALID"
        return result
    result.update(
        u_member=True,
        u_reason="U_VALID",
        x=float(cast(float, observation["x"])),
        rN=float(cast(float, observation["rN"])),
        oN_points=int(cast(int, observation["oN_points"])),
        cN_points=int(cast(int, observation["cN_points"])),
    )
    return result


def rolling_u_ledger(candidates: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Attach only prior U observations, without filling older non-U dates."""
    history: list[dict[str, object]] = []
    output: list[dict[str, object]] = []
    for source in candidates:
        row = dict(source)
        target = str(row["trade_date"])
        references = history[-LOOKBACK:]
        if any(str(item["trade_date"]) >= target for item in references):
            raise ValueError("R062-Q002 rolling-U causality violation")
        row.update(
            prior_u_count=len(history),
            rolling_u_reference_count=len(references),
            rolling_u_reference_trade_dates=[str(item["trade_date"]) for item in references],
            rolling_u_valid=bool(len(references) >= MIN_REFERENCES),
        )
        if len(references) >= MIN_REFERENCES:
            values = [float(cast(float, item["x"])) for item in references]
            row.update({f"q{q}": nearest_rank(values, q) for q in (50, 70, 75, 80)})
        output.append(row)
        if row.get("u_member"):
            history.append(row)
    return output


def r062_q002_event(
    classifier: CalendarClassifier,
    target: date,
    target_day: list[Bar] | None,
    u_row: dict[str, object],
    *,
    day_quarantined: bool = False,
    reaction_minutes: int = 15,
    rejection_ticks: int = 1,
) -> dict[str, object]:
    """Build common E after U and its strictly-prior threshold are frozen."""
    if reaction_minutes not in {10, 15, 20} or rejection_ticks not in {1, 2}:
        raise ValueError("R062-Q002 reaction specification is not preregistered")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "reaction_minutes": reaction_minutes,
        "rejection_ticks": rejection_ticks,
        "common_day_bars": COMMON_DAY_BARS,
        "rolling_u_reference_count": u_row.get("rolling_u_reference_count", 0),
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if day_quarantined:
        event["reason"] = "TARGET_DAY_SESSION_QUARANTINED"
        return event
    if not bool(u_row.get("u_member")):
        event["reason"] = "TARGET_U_INVALID"
        return event
    if not bool(u_row.get("rolling_u_valid")):
        event["reason"] = "INSUFFICIENT_PRIOR_U_REFERENCES"
        return event
    start = classifier.session_open(target, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in target_day or []}
    required = [by_time.get(start + timedelta(minutes=index)) for index in range(COMMON_DAY_BARS)]
    if not all(_valid_day(bar, target) for bar in required):
        event["reason"] = "TSE_COMMON_EXECUTION_PATH_INVALID"
        return event
    rows = [cast(Bar, bar) for bar in required]
    r_n = float(cast(float, u_row["rN"]))
    sign = 1 if r_n > 0 else -1 if r_n < 0 else 0
    close = rows[reaction_minutes - 1].close
    h_points = sign * (close - rows[0].open) if sign else 0
    cutoff = rejection_ticks * TICK
    response = "rejection" if h_points <= -cutoff else "confirmation" if h_points >= cutoff else "neutral"
    x = float(cast(float, u_row["x"]))
    q50, q70, q75, q80 = (float(cast(float, u_row[f"q{q}"])) for q in (50, 70, 75, 80))
    extreme, medium = x >= q75, q50 <= x < q75
    cell = (
        "A" if sign and extreme and response == "rejection" else
        "C" if sign and medium and response == "rejection" else
        "D" if sign and extreme and response == "confirmation" else
        "M" if sign and medium and response == "confirmation" else "NONE"
    )
    entry_ordinal = reaction_minutes
    c_n = float(cast(float, u_row["cN_points"]))
    event.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        u_trade_date=u_row["trade_date"],
        u_prior_tse_trade_date=u_row.get("prior_tse_trade_date"),
        u_ose_night_calendar_start_date=u_row.get("ose_night_calendar_start_date"),
        rolling_u_reference_trade_dates=u_row["rolling_u_reference_trade_dates"],
        q50=q50, q70=q70, q75=q75, q80=q80,
        x=x, rN=r_n, cN_points=c_n, oN_points=u_row["oN_points"],
        night_sign=sign, direction_eligible=sign != 0,
        opening_response_points=h_points, opening_response=response, cell=cell,
        planned_signal_jst=(start + timedelta(minutes=reaction_minutes - 1)).isoformat(),
        planned_entry_jst=(start + timedelta(minutes=entry_ordinal)).isoformat(),
        planned_exit15_jst=(start + timedelta(minutes=entry_ordinal + 15)).isoformat(),
        planned_exit30_jst=(start + timedelta(minutes=entry_ordinal + 30)).isoformat(),
        planned_exit45_jst=(start + timedelta(minutes=entry_ordinal + 45)).isoformat(),
        tse_open_points=rows[0].open, tse_reaction_close_points=close,
        tse_opening_gap_s_adjusted_bps=(sign * (rows[0].open - c_n) / c_n * 10_000 if sign else 0.0),
        tse_opening_range_bps=(max(row.high for row in rows[:reaction_minutes]) - min(row.low for row in rows[:reaction_minutes])) / rows[0].open * 10_000,
    )
    return event
