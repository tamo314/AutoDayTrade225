"""Validate the C04 event-calendar audit without inspecting values or market data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ATTRIBUTE_IDS = {
    "event_identity_and_scope",
    "historical_release_timestamp",
    "scheduled_actual_and_change_status",
    "timezone_dst_to_jst",
    "revision_and_republication_semantics",
    "ose_session_trade_date_alignment",
}
ATTRIBUTE_STATUS = {"VERIFIED", "UNVERIFIED", "CONTRADICTED"}
INPUT_DECISIONS = {"NOT_VERIFIED", "NOT_USABLE_FOR_C04", "CONDITIONALLY_DOCUMENTED"}
REQUIRED_CASES = {
    "scheduled_vs_actual_release": "REQUIRES_DOCUMENTED_ACTUAL_TIMESTAMP",
    "dst_boundary": "REQUIRES_DOCUMENTED_TIMEZONE_CONVERSION",
    "holiday_or_emergency_change": "NO_EVENT",
    "revised_value": "IGNORE_VALUE_USE_ORIGINALLY_RELEASED_TIMESTAMP_ONLY",
    "outside_ose_session": "NO_SIGNAL",
    "unknown_release_time": "FAIL_CLOSED",
}


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(artifacts: Path) -> list[str]:
    issues: list[str] = []
    required_files = (
        "source_contract.md",
        "event_admission.json",
        "synthetic_calendar_cases.json",
        "verification.md",
        "review.md",
    )
    for name in required_files:
        path = artifacts / name
        if not path.is_file() or not path.read_text(encoding="utf-8-sig").strip():
            issues.append(f"Missing artifact: {name}")
    if issues:
        return issues
    try:
        admission = json.loads((artifacts / "event_admission.json").read_text(encoding="utf-8-sig"))
        cases = json.loads(
            (artifacts / "synthetic_calendar_cases.json").read_text(encoding="utf-8-sig")
        )
        require(admission["task_id"] == "TASK-C04-EVENT-CALENDAR-AUDIT-08", "Incorrect task id")
        require(admission["event_type"] == "BLS_EMPLOYMENT_SITUATION", "Unexpected event type")
        require(
            admission["current_input_decision"] in INPUT_DECISIONS,
            "Invalid input decision",
        )
        for field in (
            "market_data_accessed",
            "economic_values_accessed",
            "authorises_c04_family",
            "authorises_market_data_access",
        ):
            require(admission[field] is False, f"{field} must remain false")
        attributes = admission["attributes"]
        require(isinstance(attributes, list), "attributes must be an array")
        found_attributes = {row.get("id"): row for row in attributes if isinstance(row, dict)}
        require(set(found_attributes) == ATTRIBUTE_IDS, "Missing or unexpected event attribute")
        for attribute_id, row in found_attributes.items():
            require(
                row.get("status") in ATTRIBUTE_STATUS,
                f"Invalid status for {attribute_id}",
            )
            require(
                isinstance(row.get("basis"), str) and row["basis"].strip(),
                f"Missing basis for {attribute_id}",
            )
        ledger = admission["research_ledger"]
        require(
            isinstance(ledger, dict)
            and isinstance(ledger.get("search_queries"), int)
            and 0 <= ledger["search_queries"] <= 8
            and isinstance(ledger.get("primary_source_bodies"), int)
            and 0 <= ledger["primary_source_bodies"] <= 8,
            "Invalid bounded research ledger",
        )
        rows = cases["cases"] if isinstance(cases, dict) else cases
        require(isinstance(rows, list), "Synthetic cases must be an array")
        found_cases = {row.get("id"): row for row in rows if isinstance(row, dict)}
        require(set(found_cases) == set(REQUIRED_CASES), "Unexpected or missing synthetic case")
        for case_id, expected in REQUIRED_CASES.items():
            row = found_cases[case_id]
            require(
                row.get("expected_handling") == expected,
                f"Unexpected case handling: {case_id}",
            )
            require(
                isinstance(row.get("reason"), str) and row["reason"].strip(),
                f"Missing case reason: {case_id}",
            )
            require(
                row.get("authorises_real_access") is False,
                f"Case must not authorise real access: {case_id}",
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"Invalid C04 event-calendar audit: {type(exc).__name__}: {exc}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    issues = validate(args.artifacts)
    print(
        json.dumps(
            {
                "passed": not issues,
                "issues": issues,
                "limitations": "Structure only; no economic value, price, market data, C04 execution, or economics is validated.",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
