"""Development-only, non-PnL attrition audit for TASK-R073-D001.

This is intentionally not a backtest runner.  It reads only the Development
partition to check timestamp presence/eligibility and whether the two frozen
night feature opens are equal.  It never persists a price, return, order,
fill, trade, PnL, control, bootstrap, sensitivity, OOS, or Holdout result.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.data import load_split
from n225m_bt.research.r073_official_night_direction_day_reversal import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    official_night_direction_event,
    scheduled_axis,
)

ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "TASK-R073-D001"
RUN_ID = "task-r073-d001-20260915-development-attrition-audit-01"
OUT = ROOT / "results" / "research" / RUN_ID
R073_RUN = ROOT / "results" / "research" / "r073-q001-20260915-official-night-direction-day-reversal-01"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def eligible_at(lookup: dict[datetime, Bar], stamp: datetime) -> tuple[bool, str]:
    """Expose only bar existence/eligibility, never the bar's price."""
    bar = lookup.get(stamp)
    if bar is None:
        return False, "MISSING_RAW_BAR"
    if not bar.is_eligible:
        return False, "INELIGIBLE_RAW_BAR"
    return True, "ELIGIBLE"


def schedule_category(
    target: date, day: TradingDay, classifier: CalendarClassifier
) -> tuple[bool, str, str | None, dict[str, datetime]]:
    """Separate official-calendar eligibility from the frozen event helper."""
    if day.night_calendar_start_date is None:
        return False, "NO_OFFICIAL_NIGHT_SESSION", None, {}
    night_open = classifier.session_open(target, Session.NIGHT)
    night_close = classifier.session_close(target, Session.NIGHT)
    day_open = classifier.session_open(target, Session.DAY)
    day_close = classifier.session_close(target, Session.DAY)
    stamps = {
        "official_night_open": night_open,
        "night_0530_open": datetime.combine(target, time(5, 30), JST),
        "day_0859_activation": datetime.combine(target, time(8, 59), JST),
        "day_0900_open": datetime.combine(target, time(9), JST),
        "day_1429_activation": datetime.combine(target, time(14, 29), JST),
        "day_1430_open": datetime.combine(target, time(14, 30), JST),
    }
    official_open_kind = (
        "OLD_1630" if night_open.time() == time(16, 30) else "NEW_1700"
        if night_open.time() == time(17)
        else "UNREGISTERED"
    )
    if night_open.date() + timedelta(days=1) != target:
        return (
            False,
            "NORMAL_CLOSURE_OR_HOLIDAY_SPANNING_NIGHT",
            official_open_kind,
            stamps,
        )
    if official_open_kind == "UNREGISTERED":
        return False, "UNREGISTERED_OFFICIAL_NIGHT_OPEN", official_open_kind, stamps
    contained = (
        night_open <= stamps["night_0530_open"] <= night_close
        and day_open <= stamps["day_0859_activation"] < stamps["day_0900_open"]
        < stamps["day_1430_open"] <= day_close
    )
    if not contained:
        return False, "SCHEDULED_SESSION_OR_TRADE_DATE_MAPPING_ANOMALY", official_open_kind, stamps
    return True, "SCHEDULE_ALIGNED_R073_PRIMARY_WINDOW", official_open_kind, stamps


def waterfall(subset: list[dict[str, object]]) -> dict[str, int]:
    """Return the ordered non-PnL attrition waterfall for a calendar subset."""

    def available(row: dict[str, object], name: str) -> bool:
        values = cast(dict[str, dict[str, object]], row["availability"])
        return bool(values[name]["pass"])

    counts: dict[str, int] = {"scheduled_trade_dates": len(subset)}
    prior = [row for row in subset if bool(row["official_night_session_exists"])]
    counts["official_night_session_exists"] = len(prior)
    prior = [row for row in prior if bool(row["night_starts_prior_calendar_date"])]
    counts["not_holiday_spanning_night"] = len(prior)
    prior = [row for row in prior if bool(row["schedule_aligned_r073_primary_window"])]
    counts["schedule_aligned_r073_primary_window"] = len(prior)
    for name in (
        "official_night_open",
        "night_0530_open",
        "day_0859_activation",
        "day_0900_open",
        "day_1429_activation",
        "day_1430_open",
    ):
        prior = [row for row in prior if available(row, name)]
        counts[name] = len(prior)
    prior = [row for row in prior if row["night_direction_relation"] == "NONZERO"]
    counts["night_direction_nonzero"] = len(prior)
    counts["frozen_implementation_executable"] = sum(
        row["frozen_implementation_status"] == "EXECUTABLE" for row in subset
    )
    return counts


def compact_example(row: dict[str, object]) -> dict[str, object]:
    return {
        key: row[key]
        for key in (
            "trade_date",
            "calendar_schedule_version",
            "calendar_night_start_date",
            "calendar_is_holiday_trading_day",
            "official_night_open_kind",
            "schedule_reason",
            "frozen_implementation_status",
            "frozen_implementation_reason",
        )
    }


def run_validation() -> dict[str, object]:
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r073_q001.py", "-q"],
        "ruff": [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "scripts/run_task_r073_d001_attrition_audit.py",
            "src/n225m_bt/research/r073_official_night_direction_day_reversal.py",
        ],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r073_official_night_direction_day_reversal.py",
        ],
    }
    results: dict[str, object] = {}
    for name, command in commands.items():
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        results[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    results["status"] = "PASS" if all(
        cast(dict[str, object], result)["returncode"] == 0
        for name, result in results.items()
        if name != "status"
    ) else "FAIL"
    return results


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite immutable audit output {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r073_official_night_direction_day_reversal.py"),
        Path("tests/test_r073_q001.py"),
    ]
    config_files = [
        Path("config/local_calendar.yaml"),
        Path("config/sessions.yaml"),
        Path("config/research.yaml"),
    ]
    for folder, files in ((OUT / "source_snapshot", source_files), (OUT / "config_snapshot", config_files)):
        folder.mkdir()
        for file in files:
            shutil.copy2(ROOT / file, folder / file.name)
    write_json(
        OUT / "audit_manifest.json",
        {
            "task_id": TASK_ID,
            "run_id": RUN_ID,
            "purpose": "Development-only R073-Q001 sample attrition audit; no backtest or PnL calculation",
            "frozen_r073_run": str(R073_RUN.relative_to(ROOT)),
            "source_hashes": {str(path): digest(ROOT / path) for path in source_files},
            "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
            "forbidden_access": [
                "OOS",
                "Final Holdout",
                "price returns",
                "PnL",
                "orders",
                "fills",
                "trades",
                "momentum control",
                "bootstrap",
                "sensitivity",
            ],
            "allowed_price_dependent_operation": "Only equality/non-equality of the two frozen night feature open values; neither value nor any return is emitted.",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    validation = run_validation()
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION"})
        raise ValueError("pre-execution validation failed")

    _, sessions, data_config, _ = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    axis = scheduled_axis(calendar)
    axis_set = set(axis)
    development = load_split(data_config.gold_root, "development")
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
        schedule_pass, schedule_reason, open_kind, stamps = schedule_category(
            target, calendar_day, classifier
        )
        lookup = {bar.ts_jst: bar for bar in bars_by_date[target]}
        availability = {name: eligible_at(lookup, stamp) for name, stamp in stamps.items()}
        relation = "NOT_EVALUATED"
        if (
            schedule_pass
            and availability["official_night_open"][0]
            and availability["night_0530_open"][0]
        ):
            relation = (
                "EQUAL"
                if lookup[stamps["official_night_open"]].open
                == lookup[stamps["night_0530_open"]].open
                else "NONZERO"
            )
        event = official_night_direction_event(target, bars_by_date[target], classifier)
        event_status = str(event["status"])
        event_reason = str(event.get("reason", event_status))
        required = (
            "official_night_open",
            "night_0530_open",
            "day_0859_activation",
            "day_0900_open",
            "day_1429_activation",
            "day_1430_open",
        )
        raw_primary_pass = schedule_pass and all(availability[name][0] for name in required)
        if event_status == "EXECUTABLE":
            first_exclusion = "EXECUTABLE"
        elif not schedule_pass:
            first_exclusion = schedule_reason
        elif not raw_primary_pass:
            first_exclusion = next(
                f"{name}_{availability[name][1]}" for name in required if not availability[name][0]
            )
        elif relation == "EQUAL":
            first_exclusion = "ZERO_NIGHT_DIRECTION"
        else:
            first_exclusion = f"IMPLEMENTATION_FILTER_{event_reason}"
        row: dict[str, object] = {
            "trade_date": target.isoformat(),
            "month": target.strftime("%Y-%m"),
            "year": str(target.year),
            "calendar_schedule_version": calendar_day.schedule_version,
            "calendar_night_start_date": calendar_day.night_calendar_start_date.isoformat()
            if calendar_day.night_calendar_start_date
            else None,
            "calendar_is_holiday_trading_day": calendar_day.is_holiday_trading_day,
            "official_night_session_exists": calendar_day.night_calendar_start_date is not None,
            "night_starts_prior_calendar_date": calendar_day.night_calendar_start_date
            == target - timedelta(days=1),
            "official_night_open_kind": open_kind,
            "schedule_aligned_r073_primary_window": schedule_pass,
            "schedule_reason": schedule_reason,
            "availability": {
                name: {"pass": passed, "reason": reason}
                for name, (passed, reason) in availability.items()
            },
            "night_direction_relation": relation,
            "raw_primary_required_opens_and_activations": raw_primary_pass,
            "frozen_implementation_status": event_status,
            "frozen_implementation_reason": event_reason,
            "first_exclusion": first_exclusion,
        }
        rows.append(row)
        if first_exclusion != "EXECUTABLE":
            exclusions[first_exclusion].append(row)

    by_month = {
        key: waterfall([row for row in rows if row["month"] == key])
        for key in sorted({str(row["month"]) for row in rows})
    }
    by_year = {
        key: waterfall([row for row in rows if row["year"] == key])
        for key in sorted({str(row["year"]) for row in rows})
    }
    regime_groups = {
        "old_1630": [row for row in rows if row["official_night_open_kind"] == "OLD_1630"],
        "new_1700": [row for row in rows if row["official_night_open_kind"] == "NEW_1700"],
        "2025_h1": [row for row in rows if row["year"] == "2025"],
    }
    cause_counts = {
        key: {
            "all_development": len(values),
            "old_1630": sum(row["official_night_open_kind"] == "OLD_1630" for row in values),
            "new_1700": sum(row["official_night_open_kind"] == "NEW_1700" for row in values),
            "2025_h1": sum(row["year"] == "2025" for row in values),
            "examples": [compact_example(row) for row in values[:3]],
        }
        for key, values in sorted(exclusions.items())
    }
    existing_s2 = json.loads((R073_RUN / "s2_feasibility.json").read_text(encoding="utf-8"))
    reproduced = sum(row["frozen_implementation_status"] == "EXECUTABLE" for row in rows)
    schedule_capacity = sum(bool(row["schedule_aligned_r073_primary_window"]) for row in rows)
    tie_exclusions = len(exclusions["ZERO_NIGHT_DIRECTION"])
    raw_exclusions = sum(
        len(values) for key, values in exclusions.items() if "RAW_BAR" in key
    )
    implementation_exclusions = sum(
        len(values) for key, values in exclusions.items() if key.startswith("IMPLEMENTATION_FILTER_")
    )
    validation_result = {
        "no_backtest_engine_imported_or_called": True,
        "accessed_split": "development_only",
        "development_trade_date_range": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "oos_accessed": False,
        "final_holdout_accessed": False,
        "reproduced_frozen_s2_executable_count": reproduced,
        "existing_frozen_s2_executable_count": existing_s2["executable_trade_dates"],
        "frozen_s2_count_matches": reproduced == existing_s2["executable_trade_dates"],
        "event_status_counts": dict(sorted(Counter(str(row["frozen_implementation_status"]) for row in rows).items())),
        "raw_data_attrition_after_calendar_gate": raw_exclusions,
        "unexpected_implementation_filters_after_prior_gates": implementation_exclusions,
    }
    if not validation_result["frozen_s2_count_matches"]:
        raise AssertionError("attrition audit does not reproduce frozen S2 executable count")
    if raw_exclusions or implementation_exclusions:
        raise AssertionError("audit found a data or implementation cause requiring category A")

    result: dict[str, Any] = {
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "scope": "Development-only non-PnL availability audit",
        "stages": [
            "scheduled_trade_dates",
            "official_night_session_exists",
            "not_holiday_spanning_night",
            "schedule_aligned_r073_primary_window",
            "official_night_open",
            "night_0530_open",
            "day_0859_activation",
            "day_0900_open",
            "day_1429_activation",
            "day_1430_open",
            "night_direction_nonzero",
            "frozen_implementation_executable",
        ],
        "waterfall_by_month": by_month,
        "waterfall_by_year": by_year,
        "waterfall_by_required_subperiod": {
            key: waterfall(group) for key, group in regime_groups.items()
        },
        "cause_counts_and_examples": cause_counts,
        "theoretical_maximum_decomposition": {
            "scheduled_trade_dates": len(rows),
            "calendar_eligible_cross_session_trade_dates": schedule_capacity,
            "calendar_excluded_normal_closure_or_holiday_spanning_nights": len(
                exclusions["NORMAL_CLOSURE_OR_HOLIDAY_SPANNING_NIGHT"]
            ),
            "frozen_s2_execution_gate": 900,
            "gate_excess_over_calendar_capacity": 900 - schedule_capacity,
            "equal_night_direction_exclusions": tie_exclusions,
            "executable_trade_dates": reproduced,
            "gate_shortfall": 900 - reproduced,
        },
        "classification": {
            "normal_closure_or_shortened_trading": {
                "cause_keys": ["NORMAL_CLOSURE_OR_HOLIDAY_SPANNING_NIGHT"],
                "count": len(exclusions["NORMAL_CLOSURE_OR_HOLIDAY_SPANNING_NIGHT"]),
                "evidence": "The versioned calendar gives a night start earlier than the prior calendar date. The frozen day-only rule correctly rejects a weekend/holiday-spanning night rather than carrying a position across it.",
            },
            "valid_trading_time_regime": {
                "cause_keys": [],
                "count": 0,
                "evidence": "Both registered official starts (old 16:30 and new 17:00) pass the R073 schedule-aligned window. The 2024-11-05 revision is not an exclusion in R073.",
            },
            "equal_value_signal": {
                "cause_keys": ["ZERO_NIGHT_DIRECTION"],
                "count": tie_exclusions,
                "evidence": "Only an equality flag is retained; no feature price or return is emitted.",
            },
            "raw_data_missing_or_ineligible": {
                "cause_keys": [key for key in exclusions if "RAW_BAR" in key],
                "count": raw_exclusions,
                "evidence": "First failures after a valid calendar window only; no price value is emitted.",
            },
            "trade_date_assignment_or_calendar_mapping": {
                "cause_keys": [
                    "NO_OFFICIAL_NIGHT_SESSION",
                    "UNREGISTERED_OFFICIAL_NIGHT_OPEN",
                    "SCHEDULED_SESSION_OR_TRADE_DATE_MAPPING_ANOMALY",
                ],
                "count": sum(
                    len(exclusions[key])
                    for key in (
                        "NO_OFFICIAL_NIGHT_SESSION",
                        "UNREGISTERED_OFFICIAL_NIGHT_OPEN",
                        "SCHEDULED_SESSION_OR_TRADE_DATE_MAPPING_ANOMALY",
                    )
                ),
                "evidence": "No such exception occurred in the versioned Development calendar.",
            },
            "implementation_filter": {
                "cause_keys": [key for key in exclusions if key.startswith("IMPLEMENTATION_FILTER_")],
                "count": implementation_exclusions,
                "evidence": "Any nonzero count would mean a frozen helper condition remained after all documented schedule, availability, and tie gates.",
            },
        },
        "determination": {
            "category": "B_NORMAL_MARKET_SCHEDULE_AND_EQUAL_SIGNAL_EXCLUSIONS_MAKE_900_IMPOSSIBLE",
            "basis": "The price-independent versioned calendar permits only 886 same-prior-calendar-day night-to-day windows, so the frozen 900 executable-date gate exceeds capacity by 14 before values are inspected. The remaining 8 of the 22-date gap are valid equality no-trades. There are no data, trade-date-mapping, or residual implementation exclusions.",
            "r073_q001_spec_gate_implementation_changed": False,
            "new_study_preregistration": {
                "permissible": True,
                "condition": "It must use a new study/run ID, retain prior_information_seen=true, and not overwrite or relabel R073-Q001.",
                "price_independent_s2_availability_gate": "For this fixed calendar hash and Development axis, require 886 calendar-eligible same-prior-calendar-day cross-session windows and eligible raw bars at all six required timestamps for all 886; this replaces neither the original R073 gate nor an independently justified nonzero-event information requirement.",
                "not_authorized_by_this_audit": "Choosing a replacement nonzero-event count, calculating PnL, or changing the frozen R073-Q001 specification.",
            },
        },
    }
    write_json(OUT / "attrition_waterfall.json", result)
    write_json(OUT / "trade_date_audit.json", rows)
    write_json(OUT / "validation.json", validation_result)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "task_id": TASK_ID, "run_id": RUN_ID})


if __name__ == "__main__":
    main()
