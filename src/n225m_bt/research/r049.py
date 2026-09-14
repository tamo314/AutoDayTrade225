"""Causal same-clock five-minute shock ledger for R049-Q001."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar

TSE_NORMAL_SCHEDULE_ID = "R049-TSE-NORMAL-1"


# This is a versioned cash-market schedule, not an inference from OSE bars.
@dataclass(frozen=True)
class TSECashNormalSchedule:
    effective_start: date
    effective_end: date
    morning_open: time
    afternoon_open: time
    source: str


TSE_NORMAL_SCHEDULE = TSECashNormalSchedule(
    date(2021, 1, 1),
    date(2025, 6, 30),
    time(9, 0),
    time(12, 30),
    "JPX/TSE cash-equity trading-hours evidence frozen by R045; normal-session record R049-TSE-NORMAL-1",
)
ANCHORS = (
    ("mS+30", "morning_open", 30),
    ("mS+60", "morning_open", 60),
    ("mS+90", "morning_open", 90),
    ("aS+15", "afternoon_open", 15),
    ("aS+45", "afternoon_open", 45),
    ("aS+75", "afternoon_open", 75),
)


def _rank(values: list[float], percentile: int) -> float:
    return sorted(values)[(len(values) * percentile + 99) // 100 - 1]


def anchor_times(day: date) -> dict[str, datetime]:
    if not TSE_NORMAL_SCHEDULE.effective_start <= day <= TSE_NORMAL_SCHEDULE.effective_end:
        raise ValueError("R049 TSE schedule does not cover trade_date")
    morning = datetime.combine(day, TSE_NORMAL_SCHEDULE.morning_open, JST)
    afternoon = datetime.combine(day, TSE_NORMAL_SCHEDULE.afternoon_open, JST)
    bases = {"morning_open": morning, "afternoon_open": afternoon}
    return {name: bases[base] + timedelta(minutes=offset) for name, base, offset in ANCHORS}


def _observation(day: date, anchor: str, rows: list[Bar] | None) -> dict[str, object]:
    result: dict[str, object] = {
        "trade_date": day.isoformat(),
        "anchor": anchor,
        "status": "invalid",
    }
    if rows is None:
        result["reason"] = "DAY_SESSION_MISSING"
        return result
    times = anchor_times(day)
    at = times[anchor]
    by_time = {row.ts_jst: row for row in rows}
    observation_window = [at + timedelta(minutes=i) for i in range(-5, 0)]
    entry_to_h20_path = [at + timedelta(minutes=i) for i in range(21)]
    window_rows = [by_time.get(stamp) for stamp in observation_window]
    path_rows = [by_time.get(stamp) for stamp in entry_to_h20_path]
    if any(row is None for row in window_rows):
        result["reason"] = "OBSERVATION_WINDOW_MISSING"
        return result
    if any(row is None for row in (path_rows[0], path_rows[-1])):
        result["reason"] = "ENTRY_OR_H20_MISSING"
        return result
    if any(row is None for row in path_rows):
        result["reason"] = "ENTRY_TO_H20_PATH_MISSING"
        return result
    concrete = [row for row in (*window_rows, *path_rows) if row is not None]
    if any(
        not row.is_eligible or row.trade_date != day or row.session is not Session.DAY
        for row in [row for row in window_rows if row is not None]
    ):
        result["reason"] = "OBSERVATION_WINDOW_INELIGIBLE"
        return result
    if any(
        not row.is_eligible or row.trade_date != day or row.session is not Session.DAY
        for row in (path_rows[0], path_rows[-1])
        if row is not None
    ):
        result["reason"] = "ENTRY_OR_H20_INELIGIBLE"
        return result
    if any(
        not row.is_eligible or row.trade_date != day or row.session is not Session.DAY
        for row in [row for row in path_rows if row is not None]
    ):
        result["reason"] = "ENTRY_TO_H20_PATH_INELIGIBLE"
        return result
    p0, p5 = concrete[0].open, concrete[4].close
    r = p5 - p0
    if p0 <= 0:
        result.update(p0_points=p0, p5_points=p5, r_points=r, reason="NONPOSITIVE_P0")
        return result
    if r == 0:
        result.update(p0_points=p0, p5_points=p5, r_points=r, reason="ZERO_R")
        return result
    result.update(
        status="valid",
        window_start_jst=concrete[0].ts_jst.isoformat(),
        window_end_jst=concrete[4].ts_jst.isoformat(),
        entry_jst=at.isoformat(),
        exit_h10_jst=(at + timedelta(minutes=10)).isoformat(),
        exit_h15_jst=(at + timedelta(minutes=15)).isoformat(),
        exit_h20_jst=(at + timedelta(minutes=20)).isoformat(),
        p0_points=p0,
        p5_points=p5,
        r_points=r,
        x=abs(r) / p0,
        r_sign=1 if r > 0 else -1,
    )
    return result


def r049_event(
    target: date,
    target_bars: list[Bar] | None,
    history: Iterable[tuple[date, list[Bar] | None, bool]],
    cash_calendar: TSECashMarketCalendar,
    *,
    target_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Build the all-six-anchor common-E ledger without reading future prices/dates."""
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "schedule_id": TSE_NORMAL_SCHEDULE_ID,
    }
    if not development_start <= target <= development_end:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if not cash_calendar.is_open(target):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if target_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    prior = list(history)
    if len(prior) != 120:
        event["reason"] = "HISTORY_NOT_EXACTLY_120_TSE_DAYS"
        return event
    observations = [_observation(target, name, target_bars) for name, _, _ in ANCHORS]
    if any(row["status"] != "valid" for row in observations):
        event.update(observations=observations, reason="TARGET_ANCHOR_INVALID")
        return event
    references: dict[str, list[float]] = {name: [] for name, _, _ in ANCHORS}
    reference_audit: list[dict[str, object]] = []
    for day, bars, quarantined in prior:
        audit: dict[str, object] = {"trade_date": day.isoformat()}
        if quarantined or bars is None:
            audit["reason"] = "QUARANTINED_OR_MISSING"
        else:
            valid = True
            for name, _, _ in ANCHORS:
                row = _observation(day, name, bars)
                if row["status"] == "valid":
                    references[name].append(float(str(row["x"])))
                else:
                    valid = False
            audit["reason"] = "VALID_ALL_ANCHORS" if valid else "ANCHOR_INVALID"
        reference_audit.append(audit)
    if any(len(values) < 100 for values in references.values()):
        event.update(
            observations=observations,
            reference_audit=reference_audit,
            reference_counts={k: len(v) for k, v in references.items()},
            reason="INSUFFICIENT_VALID_REFERENCES",
        )
        return event
    for row in observations:
        name = str(row["anchor"])
        x = float(str(row["x"]))
        row.update({f"q{q}": _rank(references[name], q) for q in (75, 85, 90, 95)})
        row.update(
            q75_le_x=x >= float(str(row["q75"])),
            q90_le_x=x >= float(str(row["q90"])),
            moderate_q75_q90=float(str(row["q75"])) <= x < float(str(row["q90"])),
        )
    event.update(
        status="E",
        reason="COMMON_SIX_ANCHOR_CAUSAL_ELIGIBLE",
        observations=observations,
        reference_audit=reference_audit,
        reference_counts={k: len(v) for k, v in references.items()},
    )
    return event
