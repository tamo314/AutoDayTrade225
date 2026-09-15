"""Development-only availability audit for TASK-R072-D001.

This program deliberately does not create orders, fills, returns, PnL, controls,
bootstrap samples, sensitivities, OOS, or Final Holdout outputs.  It diagnoses
only the frozen R072-Q001 primary-profile sample attrition.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.data import load_split
from n225m_bt.research.r072_night_direction_day_reversal import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    night_direction_day_reversal_event,
    scheduled_axis,
)

ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "TASK-R072-D001"
RUN_ID = "task-r072-d001-20260915-development-attrition-audit-02"
OUT = ROOT / "results" / "research" / RUN_ID
R072_RUN = ROOT / "results" / "research" / "r072-q001-20260915-night-direction-day-reversal-01"
PRIMARY_TIMES = {
    "night_1630_open": time(16, 30),
    "night_0500_open": time(5, 0),
    "night_0530_open": time(5, 30),
    "day_0900_open": time(9, 0),
    "day_0901_open": time(9, 1),
    "day_1415_open": time(14, 15),
    "day_1430_open": time(14, 30),
    "day_1445_open": time(14, 45),
}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def eligible_at(lookup: dict[datetime, Bar], stamp: datetime) -> tuple[bool, str]:
    """Return presence/eligibility without exposing a price value."""
    bar = lookup.get(stamp)
    if bar is None:
        return False, "MISSING_RAW_BAR"
    if not bar.is_eligible:
        return False, "INELIGIBLE_RAW_BAR"
    return True, "ELIGIBLE"


def schedule_category(
    target: date, day: TradingDay, classifier: CalendarClassifier
) -> tuple[bool, str, dict[str, datetime]]:
    """Reproduce the frozen implementation's calendar portion, with causes separated."""
    night_open = classifier.session_open(target, Session.NIGHT)
    night_close = classifier.session_close(target, Session.NIGHT)
    day_open = classifier.session_open(target, Session.DAY)
    day_close = classifier.session_close(target, Session.DAY)
    stamps = {
        "night_1630_open": datetime.combine(night_open.date(), time(16, 30), JST),
        "night_0500_open": datetime.combine(target, time(5, 0), JST),
        "night_0530_open": datetime.combine(target, time(5, 30), JST),
        "day_0900_open": datetime.combine(target, time(9, 0), JST),
        "day_0901_open": datetime.combine(target, time(9, 1), JST),
        "day_1415_open": datetime.combine(target, time(14, 15), JST),
        "day_1430_open": datetime.combine(target, time(14, 30), JST),
        "day_1445_open": datetime.combine(target, time(14, 45), JST),
        "entry_activation": datetime.combine(target, time(8, 59), JST),
        "exit_activation": datetime.combine(target, time(14, 29), JST),
    }
    required_inside = (
        night_open <= stamps["night_1630_open"] <= stamps["night_0500_open"]
        <= stamps["night_0530_open"] <= night_close
        and day_open <= stamps["entry_activation"] < stamps["day_0900_open"]
        < stamps["day_1430_open"] <= day_close
    )
    if night_open.time() != time(16, 30):
        return False, "MARKET_SCHEDULE_REVISION_NIGHT_OPEN_NOT_1630", stamps
    if night_open.date() + timedelta(days=1) != target:
        return False, "NORMAL_CLOSURE_OR_HOLIDAY_CARRY_NIGHT_NOT_PRIOR_CALENDAR_DAY", stamps
    if not required_inside:
        return False, "SCHEDULED_SESSION_OR_CALENDAR_MAPPING_ANOMALY", stamps
    return True, "SCHEDULED_R072_PRIMARY_WINDOW", stamps


def month_key(target: date) -> str:
    return target.strftime("%Y-%m")


def sample(rows: list[dict[str, object]], limit: int = 3) -> list[dict[str, object]]:
    return rows[:limit]


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite immutable audit output {OUT}")
    OUT.mkdir(parents=True)
    source_files = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r072_night_direction_day_reversal.py")]
    config_files = [Path("config/local_calendar.yaml"), Path("config/sessions.yaml"), Path("config/research.yaml")]
    for folder, files in ((OUT / "source_snapshot", source_files), (OUT / "config_snapshot", config_files)):
        folder.mkdir()
        for file in files:
            shutil.copy2(ROOT / file, folder / file.name)
    write_json(
        OUT / "audit_manifest.json",
        {
            "task_id": TASK_ID,
            "run_id": RUN_ID,
            "purpose": "Development-only R072-Q001 sample attrition audit; no backtest or PnL calculation",
            "frozen_r072_run": str(R072_RUN.relative_to(ROOT)),
            "source_hashes": {str(path): digest(ROOT / path) for path in source_files},
            "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
            "forbidden_access": ["OOS", "Final Holdout", "PnL", "returns", "trades", "bootstrap", "controls", "sensitivity"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )

    _, sessions, data_config, _ = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    axis = scheduled_axis(calendar)
    development = load_split(data_config.gold_root, "development")
    axis_set = set(axis)
    bars_by_date: dict[date, list[Bar]] = defaultdict(list)
    for bar in development.bars:
        if bar.trade_date in axis_set:
            bars_by_date[bar.trade_date].append(bar)

    rows: list[dict[str, object]] = []
    exclusions: dict[str, list[dict[str, object]]] = defaultdict(list)
    for target in axis:
        calendar_day = calendar.get(target)
        if calendar_day is None:
            raise ValueError(f"scheduled axis date missing calendar row: {target}")
        schedule_pass, schedule_reason, stamps = schedule_category(target, calendar_day, classifier)
        lookup = {bar.ts_jst: bar for bar in bars_by_date[target]}
        availability = {name: eligible_at(lookup, stamp) for name, stamp in stamps.items()}
        event = night_direction_day_reversal_event(target, bars_by_date[target], classifier)
        night_relation = "NOT_EVALUATED"
        if schedule_pass and availability["night_1630_open"][0] and availability["night_0530_open"][0]:
            first = lookup[stamps["night_1630_open"]]
            last = lookup[stamps["night_0530_open"]]
            night_relation = "EQUAL" if first.open == last.open else "NONZERO"
        primary_required = (
            "night_1630_open",
            "night_0530_open",
            "entry_activation",
            "day_0900_open",
            "exit_activation",
            "day_1430_open",
        )
        raw_primary_pass = schedule_pass and all(availability[name][0] for name in primary_required)
        event_status = str(event["status"])
        event_reason = str(event.get("reason", event_status))
        if event_status == "EXECUTABLE":
            first_exclusion = "EXECUTABLE"
        elif not schedule_pass:
            first_exclusion = schedule_reason
        elif not raw_primary_pass:
            first_exclusion = next(name + "_" + availability[name][1] for name in primary_required if not availability[name][0])
        elif night_relation == "EQUAL":
            first_exclusion = "ZERO_NIGHT_DIRECTION"
        else:
            first_exclusion = "IMPLEMENTATION_FILTER_" + event_reason
        row: dict[str, object] = {
            "trade_date": target.isoformat(),
            "month": month_key(target),
            "year": str(target.year),
            "calendar_schedule_version": calendar_day.schedule_version,
            "calendar_night_start_date": calendar_day.night_calendar_start_date.isoformat()
            if calendar_day.night_calendar_start_date
            else None,
            "calendar_is_holiday_trading_day": calendar_day.is_holiday_trading_day,
            "schedule_r072_primary_window": schedule_pass,
            "schedule_reason": schedule_reason,
            "availability": {name: {"pass": passed, "reason": reason} for name, (passed, reason) in availability.items()},
            "night_direction_relation": night_relation,
            "raw_primary_required_opens_and_activations": raw_primary_pass,
            "frozen_implementation_status": event_status,
            "frozen_implementation_reason": event_reason,
            "first_exclusion": first_exclusion,
        }
        rows.append(row)
        if first_exclusion != "EXECUTABLE":
            exclusions[first_exclusion].append(row)

    stages = [
        "scheduled_target",
        "schedule_r072_primary_window",
        "night_1630_open",
        "night_0500_open",
        "night_0530_open",
        "day_0900_open",
        "day_0901_open",
        "day_1415_open",
        "day_1430_open",
        "day_1445_open",
        "night_direction_nonzero",
        "raw_primary_required_opens_and_activations",
        "frozen_implementation_executable",
    ]

    def waterfall(subset: list[dict[str, object]]) -> dict[str, int]:
        def availability_pass(row: dict[str, object], name: str) -> bool:
            availability = cast(dict[str, dict[str, object]], row["availability"])
            return bool(availability[name]["pass"])

        counts: dict[str, int] = {"scheduled_target": len(subset)}
        prior = [row for row in subset if bool(row["schedule_r072_primary_window"])]
        counts["schedule_r072_primary_window"] = len(prior)
        for name in (
            "night_1630_open", "night_0500_open", "night_0530_open", "day_0900_open", "day_0901_open",
            "day_1415_open", "day_1430_open", "day_1445_open",
        ):
            prior = [row for row in prior if availability_pass(row, name)]
            counts[name] = len(prior)
        prior = [row for row in prior if row["night_direction_relation"] == "NONZERO"]
        counts["night_direction_nonzero"] = len(prior)
        primary = [row for row in subset if bool(row["raw_primary_required_opens_and_activations"])]
        counts["raw_primary_required_opens_and_activations"] = len(primary)
        counts["frozen_implementation_executable"] = sum(
            row["frozen_implementation_status"] == "EXECUTABLE" for row in subset
        )
        return counts

    by_month: dict[str, dict[str, int]] = {}
    by_year: dict[str, dict[str, int]] = {}
    for key in sorted({str(row["month"]) for row in rows}):
        by_month[key] = waterfall([row for row in rows if row["month"] == key])
    for key in sorted({str(row["year"]) for row in rows}):
        by_year[key] = waterfall([row for row in rows if row["year"] == key])
    cause_counts = {
        key: {
            "all_development": len(values),
            "year_2024": sum(row["year"] == "2024" for row in values),
            "year_2025_h1": sum(row["year"] == "2025" for row in values),
            "examples": [
                {
                    field: row[field]
                    for field in (
                        "trade_date", "calendar_schedule_version", "calendar_night_start_date",
                        "calendar_is_holiday_trading_day", "schedule_reason", "frozen_implementation_status",
                        "frozen_implementation_reason",
                    )
                }
                for row in sample(values)
            ],
        }
        for key, values in sorted(exclusions.items())
    }
    classifications = {
        "normal_closure_or_shortened_trading": {
            "cause_keys": ["NORMAL_CLOSURE_OR_HOLIDAY_CARRY_NIGHT_NOT_PRIOR_CALENDAR_DAY"],
            "count": len(exclusions["NORMAL_CLOSURE_OR_HOLIDAY_CARRY_NIGHT_NOT_PRIOR_CALENDAR_DAY"]),
            "evidence": "calendar_night_start_date is explicitly an earlier trading calendar date, so the frozen implementation's next-calendar-day predicate is false.",
        },
        "market_schedule_revision": {
            "cause_keys": ["MARKET_SCHEDULE_REVISION_NIGHT_OPEN_NOT_1630"],
            "count": len(exclusions["MARKET_SCHEDULE_REVISION_NIGHT_OPEN_NOT_1630"]),
            "evidence": "sessions.yaml regime ose_n225m_from_20241105 fixes night session_open at 17:00; the frozen implementation requires night_open == 16:30.",
        },
        "raw_data_missing_or_ineligible": {
            "cause_keys": [key for key in exclusions if "RAW_BAR" in key],
            "count": sum(len(values) for key, values in exclusions.items() if "RAW_BAR" in key),
            "evidence": "Only first failures after a valid frozen schedule window are counted here; no price value is emitted.",
        },
        "trade_date_assignment": {
            "cause_keys": ["SCHEDULED_SESSION_OR_CALENDAR_MAPPING_ANOMALY"],
            "count": len(exclusions["SCHEDULED_SESSION_OR_CALENDAR_MAPPING_ANOMALY"]),
            "evidence": "Anomaly means the explicit calendar mapping or session containment could not satisfy the frozen time contract. Normal weekends/holidays are classified separately.",
        },
        "implementation_filter_after_prior_gates": {
            "cause_keys": [key for key in exclusions if key.startswith("IMPLEMENTATION_FILTER_")],
            "count": sum(len(values) for key, values in exclusions.items() if key.startswith("IMPLEMENTATION_FILTER_")),
            "evidence": "A disagreement after schedule, required raw-bar availability, and nonzero direction would indicate an additional frozen implementation filter.",
        },
    }
    existing_s2 = json.loads((R072_RUN / "s2_feasibility.json").read_text(encoding="utf-8"))
    reproduced = sum(row["frozen_implementation_status"] == "EXECUTABLE" for row in rows)
    validation = {
        "no_backtest_engine_imported_or_called": True,
        "accessed_split": "development_only",
        "development_trade_date_range": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "oos_accessed": False,
        "final_holdout_accessed": False,
        "reproduced_frozen_s2_executable_count": reproduced,
        "existing_frozen_s2_executable_count": existing_s2["executable_trade_dates"],
        "frozen_s2_count_matches": reproduced == existing_s2["executable_trade_dates"],
        "event_status_counts": dict(sorted(Counter(str(row["frozen_implementation_status"]) for row in rows).items())),
    }
    if not validation["frozen_s2_count_matches"]:
        raise AssertionError("attrition audit does not reproduce frozen S2 executable count")
    result = {
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "stages": stages,
        "waterfall_by_month": by_month,
        "waterfall_by_year": by_year,
        "cause_counts_and_examples": cause_counts,
        "classification": classifications,
        "determination": {
            "category": "B_VALID_MARKET_SCHEDULE_WITH_OVERBROAD_FROZEN_PRE_GATE",
            "basis": "All 2025H1 scheduled target days first fail the frozen 16:30 night-open schedule predicate after the 2024-11-05 17:00 market-session revision; this is neither a raw-data failure nor a trade-date-assignment anomaly.",
            "r072_q001_spec_gate_implementation_changed": False,
            "rerun_scientifically_permissible": "Only as a new, separately preregistered study/spec that records the schedule-aligned feature time and does not relabel or overwrite frozen R072-Q001. Replaying R072-Q001 with altered times is not permissible.",
        },
    }
    write_json(OUT / "attrition_waterfall.json", result)
    write_json(OUT / "trade_date_audit.json", rows)
    write_json(OUT / "validation.json", validation)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "task_id": TASK_ID, "run_id": RUN_ID})


if __name__ == "__main__":
    main()
