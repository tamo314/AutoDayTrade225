"""Development-ledger-only attrition audit for TASK-R074-D001.

This deliberately does not load bars, a backtest engine, or any performance
artifact.  It derives only availability and event-selection counts from the
already-frozen R074-Q001 S2 primary event ledger.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "TASK-R074-D001"
RUN_ID = "task-r074-d001-20260915-development-s2-ledger-attrition-audit-04"
OUT = ROOT / "results" / "research" / RUN_ID
R074_RUN = ROOT / "results" / "research" / "r074-q001-20260915-low-night-range-opening-breakout-01"
LEDGER = R074_RUN / "s2_events_primary.json"
YEARS = (2021, 2022, 2023, 2024)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def state(event: dict[str, object]) -> str | None:
    """Classify the frozen q25/q75 values without retaining their values."""
    required = ("current_night_range_points", "q25_points", "q75_points")
    if not all(key in event for key in required):
        return None
    current = int(cast(int, event["current_night_range_points"]))
    q25 = int(cast(int, event["q25_points"]))
    q75 = int(cast(int, event["q75_points"]))
    if current <= q25:
        return "A"
    if current <= q75:
        return "D"
    return "HIGH"


def category(event: dict[str, object]) -> str:
    """Return one mutually exclusive terminal branch of the frozen event tree."""
    reason = str(event.get("reason", ""))
    status = str(event.get("status", ""))
    direct = {
        "CURRENT_NO_SCHEDULED_OVERNIGHT_WINDOW": "night_window_not_formed",
        "CURRENT_R004_NIGHT_SESSION_QUARANTINED": "current_night_r004_quarantined",
        "REFERENCE_OUTSIDE_DEVELOPMENT": "reference_outside_development",
        "REFERENCE_R004_NIGHT_SESSION_QUARANTINED": "reference_night_r004_quarantined",
    }
    if reason in direct:
        return direct[reason]
    event_state = state(event)
    if event_state == "D":
        return "state_D"
    if event_state == "HIGH":
        return "state_high"
    if event_state != "A":
        return "other_pre_state_or_state_classification_anomaly"
    if reason == "OPENING_RANGE_MISSING_OR_INELIGIBLE":
        return "A_opening_range_not_formed"
    if reason == "NO_STRICT_CLOSE_BREAKOUT_BY_1100":
        return "A_no_breakout_by_1100"
    if status in {"SIGNALLED", "ENTRY_CANCELLED", "ENTRY_FILLED_EXIT_UNKNOWN"}:
        return "A_entry_or_exit_unobserved"
    if status == "EXECUTABLE":
        direction = str(event.get("breakout_direction", ""))
        if direction in {"long", "short"}:
            return f"executable_{direction}"
    return "other_implementation_or_classification_anomaly"


BRANCHES = (
    "night_window_not_formed",
    "current_night_r004_quarantined",
    "reference_outside_development",
    "reference_night_r004_quarantined",
    "other_pre_state_or_state_classification_anomaly",
    "state_D",
    "state_high",
    "A_opening_range_not_formed",
    "A_no_breakout_by_1100",
    "A_entry_or_exit_unobserved",
    "executable_long",
    "executable_short",
    "other_implementation_or_classification_anomaly",
)


def count_rows(rows: list[dict[str, object]]) -> dict[str, int | float]:
    counts = Counter(str(row["category"]) for row in rows)
    scheduled = len(rows)
    reference_set = sum(
        counts[key]
        for key in (
            "state_D",
            "state_high",
            "A_opening_range_not_formed",
            "A_no_breakout_by_1100",
            "A_entry_or_exit_unobserved",
            "executable_long",
            "executable_short",
            "other_implementation_or_classification_anomaly",
        )
    )
    a_total = sum(
        counts[key]
        for key in (
            "A_opening_range_not_formed",
            "A_no_breakout_by_1100",
            "A_entry_or_exit_unobserved",
            "executable_long",
            "executable_short",
            "other_implementation_or_classification_anomaly",
        )
    )
    breakout = sum(
        counts[key]
        for key in (
            "A_entry_or_exit_unobserved",
            "executable_long",
            "executable_short",
            "other_implementation_or_classification_anomaly",
        )
    )
    executable = counts["executable_long"] + counts["executable_short"]
    result: dict[str, int | float] = {"scheduled_trade_dates": scheduled}
    result.update({key: counts[key] for key in BRANCHES})
    result.update(
        reference_set_established=reference_set,
        state_A=a_total,
        strict_breakout_after_A=breakout,
        final_executable=executable,
        A_incidence_after_reference_set=(a_total / reference_set if reference_set else 0.0),
        breakout_rate_after_A=(breakout / a_total if a_total else 0.0),
        final_executable_rate_after_A=(executable / a_total if a_total else 0.0),
        final_executable_rate_after_breakout=(executable / breakout if breakout else 0.0),
    )
    if sum(counts[key] for key in BRANCHES) != scheduled:
        raise AssertionError("exclusive branches do not cover the scheduled axis")
    return result


def percentage(value: int | float, denominator: int | float) -> float | None:
    return float(value / denominator) if denominator else None


def report_with_rates(rows: list[dict[str, object]]) -> dict[str, object]:
    counts = count_rows(rows)
    scheduled = int(counts["scheduled_trade_dates"])
    return {
        "counts": counts,
        "rate_of_scheduled_trade_dates": {
            key: percentage(value, scheduled)
            for key, value in counts.items()
            if key not in {"scheduled_trade_dates"}
        },
    }


def r004_impact(rows: list[dict[str, object]]) -> dict[str, object]:
    isolated: set[str] = {
        str(row["trade_date"])
        for row in rows
        if row["category"] == "current_night_r004_quarantined"
    }
    for row in rows:
        event = cast(dict[str, object], row["event"])
        if row["category"] != "reference_night_r004_quarantined":
            continue
        references = cast(list[str], event["reference_trade_dates_p1_to_p20"])
        index = int(cast(int, event["reference_index"])) - 1
        isolated.add(references[index])

    per_night: dict[str, object] = {}
    direct_month: Counter[str] = Counter()
    affected_month: Counter[str] = Counter()
    any_affected_2021: set[str] = set()
    for night in sorted(isolated):
        affected = [
            row
            for row in rows
            if night
            in cast(
                list[str], cast(dict[str, object], row["event"]).get("reference_trade_dates_p1_to_p20", [])
            )
        ]
        direct = [
            row
            for row in affected
            if row["category"] == "reference_night_r004_quarantined"
            and cast(list[str], cast(dict[str, object], row["event"])["reference_trade_dates_p1_to_p20"])[
                int(cast(int, cast(dict[str, object], row["event"])["reference_index"])) - 1
            ]
            == night
        ]
        affected_2021 = [row for row in affected if row["year"] == 2021]
        direct_2021 = [row for row in direct if row["year"] == 2021]
        direct_month.update(str(row["month"]) for row in direct_2021)
        affected_month.update(str(row["month"]) for row in affected_2021)
        any_affected_2021.update(str(row["trade_date"]) for row in affected_2021)
        per_night[night] = {
            "downstream_targets_with_night_in_frozen_20_reference_set": len(affected),
            "downstream_targets_directly_invalidated_as_first_r004_reference": len(direct),
            "downstream_targets_2021_with_night_in_reference_set": len(affected_2021),
            "downstream_targets_2021_directly_invalidated": len(direct_2021),
            "first_affected_target": min((str(row["trade_date"]) for row in affected), default=None),
            "last_affected_target": max((str(row["trade_date"]) for row in affected), default=None),
        }
    return {
        "isolated_nights_inferred_from_frozen_primary_ledger": per_night,
        "all_isolated_night_membership_occurrences_2021_by_target_month": dict(sorted(affected_month.items())),
        "direct_first_reference_invalidations_2021_by_target_month": dict(sorted(direct_month.items())),
        "unique_2021_targets_affected_by_at_least_one_isolated_night": len(any_affected_2021),
        "note": "Membership counts retain overlap. Direct counts use the frozen helper's first failing R004 reference and are mutually exclusive across target dates.",
    }


def run_validation() -> dict[str, object]:
    commands = {
        "ruff": [sys.executable, "-m", "ruff", "check", "scripts/run_task_r074_d001_attrition_audit.py"],
        "mypy": [sys.executable, "-m", "mypy", "scripts/run_task_r074_d001_attrition_audit.py"],
    }
    results: dict[str, object] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        results[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    results["status"] = "PASS" if all(
        cast(dict[str, object], value)["returncode"] == 0
        for key, value in results.items()
        if key != "status"
    ) else "FAIL"
    return results


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite immutable audit output {OUT}")
    OUT.mkdir(parents=True)
    source_files = [Path(__file__).relative_to(ROOT)]
    documentation = Path("docs/strategy/33_task_r074_d001_attrition_audit.md")
    for directory, files in ((OUT / "source_snapshot", source_files), (OUT / "documentation_snapshot", [documentation])):
        directory.mkdir()
        for file in files:
            shutil.copy2(ROOT / file, directory / file.name)
    write_json(
        OUT / "audit_manifest.json",
        {
            "task_id": TASK_ID,
            "run_id": RUN_ID,
            "input": str(LEDGER.relative_to(ROOT)),
            "input_sha256": digest(LEDGER),
            "purpose": "R074-Q001 2021 Development attrition audit using only the existing S2 primary event ledger",
            "forbidden_access": ["bars", "backtest", "PnL", "return", "win_loss", "profit_factor", "bootstrap", "sensitivity", "OOS", "Final Holdout"],
            "output_constraint": "No price level, range, return, performance, order, fill, or trade record is emitted.",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    validation = run_validation()
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION"})
        raise ValueError("pre-execution validation failed")

    ledger = cast(dict[str, dict[str, object]], json.loads(LEDGER.read_text(encoding="utf-8")))
    rows: list[dict[str, object]] = []
    for target, event in sorted(ledger.items()):
        year = int(target[:4])
        if year not in YEARS:
            continue
        rows.append(
            {
                "trade_date": target,
                "year": year,
                "month": target[:7],
                "category": category(event),
                "event": event,
            }
        )
    by_year = {str(year): report_with_rates([row for row in rows if row["year"] == year]) for year in YEARS}
    by_month_2021 = {
        month: report_with_rates([row for row in rows if row["month"] == month])
        for month in sorted({str(row["month"]) for row in rows if row["year"] == 2021})
    }
    benchmark_rows = [row for row in rows if row["year"] in {2022, 2023, 2024}]
    benchmark = report_with_rates(benchmark_rows)
    current_counts = cast(dict[str, int | float], by_year["2021"]["counts"])
    decomposition = {
        "scheduled_axis_normal_calendar_non_overnight_windows": current_counts["night_window_not_formed"],
        "a_development_start_edge_reference_outside_development": current_counts["reference_outside_development"],
        "b_fixed_r004_data_isolation_chain": current_counts["current_night_r004_quarantined"] + current_counts["reference_night_r004_quarantined"],
        "c_normal_non_A_market_state_D_or_high": current_counts["state_D"] + current_counts["state_high"],
        "d_normal_A_no_strict_breakout_by_1100": current_counts["A_no_breakout_by_1100"],
        "e_opening_entry_exit_or_other_implementation_classification_anomaly": current_counts["A_opening_range_not_formed"]
        + current_counts["A_entry_or_exit_unobserved"]
        + current_counts["other_pre_state_or_state_classification_anomaly"]
        + current_counts["other_implementation_or_classification_anomaly"],
        "final_executable_A_long_short": current_counts["final_executable"],
    }
    reproduced = sum(
        int(cast(dict[str, int | float], item["counts"])["final_executable"])
        for item in by_year.values()
    )
    expected = 12
    anomalies = decomposition["e_opening_entry_exit_or_other_implementation_classification_anomaly"]
    determination = {
        "frozen_2021_A_executable_count_reproduced": int(current_counts["final_executable"]),
        "frozen_2021_gate": 25,
        "shortfall": 25 - int(current_counts["final_executable"]),
        "normal_market_state_rarity_is_largest_exclusive_nonexecution_branch": decomposition["c_normal_non_A_market_state_D_or_high"]
        >= max(
            int(decomposition["a_development_start_edge_reference_outside_development"]),
            int(decomposition["b_fixed_r004_data_isolation_chain"]),
            int(decomposition["d_normal_A_no_strict_breakout_by_1100"]),
        ),
        "implementation_or_classification_anomaly_count": anomalies,
        "new_id_preregistration_based_only_on_nonperformance_eligible_opportunity_count": {
            "permissible": False,
            "reason": "The audit must not authorize a replacement gate if normal A-state rarity is the dominant exclusive branch; a new count would be selected after observing the same price-derived event scarcity.",
        },
        "r074_with_current_development_data": "UNTESTABLE_IF_NORMAL_A_STATE_RARITY_DOMINATES",
    }
    if anomalies:
        determination["required_repair"] = "Investigate the unexpected category before any new preregistration; add a synthetic regression for its exact frozen event path."
    if not bool(determination["normal_market_state_rarity_is_largest_exclusive_nonexecution_branch"]):
        determination["new_id_preregistration_based_only_on_nonperformance_eligible_opportunity_count"] = {
            "permissible": True,
            "condition": "A Planner must create a distinct study ID and freeze an availability-only threshold justified without PnL, while preserving the R074-Q001 record unchanged.",
        }
        determination["r074_with_current_development_data"] = "R074_Q001_REMAINS_INCONCLUSIVE;_NEW_ID_AVAILABILITY_ONLY_REPREREGISTRATION_PERMISSIBLE"
    result: dict[str, Any] = {
        "task_id": TASK_ID,
        "run_id": RUN_ID,
        "scope": "Existing R074-Q001 Development S2 primary event ledger only; no performance access",
        "exclusive_attrition_branch_order": list(BRANCHES),
        "waterfall_definition": "Reference-set establishment follows the four pre-reference exclusions. A/D/HIGH split is then exclusive. Opening, breakout, observability, and direction split apply only to A.",
        "waterfall_by_month_2021": by_month_2021,
        "waterfall_by_year_2021_to_2024": by_year,
        "benchmark_2022_to_2024_pooled": benchmark,
        "r004_isolation_impact": r004_impact(rows),
        "year_2021_exclusive_count_decomposition": decomposition,
        "determination": determination,
        "validation": {
            "input_event_count": len(ledger),
            "audited_2021_to_2024_event_count": len(rows),
            "reproduced_2021_executable_count_matches_task_stated_frozen_s2": int(current_counts["final_executable"]) == expected,
            "reproduced_2021_to_2024_executable_count": reproduced,
            "no_unclassified_exclusive_branches": anomalies == 0,
            "oos_accessed": False,
            "final_holdout_accessed": False,
        },
    }
    if not bool(result["validation"]["reproduced_2021_executable_count_matches_task_stated_frozen_s2"]):
        raise AssertionError("2021 executable count does not reproduce frozen S2")
    write_json(OUT / "attrition_waterfall.json", result)
    write_json(
        OUT / "trade_date_audit.json",
        [
            {key: value for key, value in row.items() if key != "event"}
            for row in rows
        ],
    )
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "task_id": TASK_ID, "run_id": RUN_ID})


if __name__ == "__main__":
    main()
