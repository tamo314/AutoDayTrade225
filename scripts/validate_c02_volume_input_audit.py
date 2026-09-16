"""Validate the C02 volume-input audit without inspecting market data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ATTRIBUTE_IDS = {
    "per_minute_quantity",
    "bar_finality_and_corrections",
    "timestamp_timezone",
    "trade_date_and_session_boundary",
    "missing_zero_semantics",
}
ATTRIBUTE_STATUS = {"VERIFIED", "UNVERIFIED", "CONTRADICTED"}
INPUT_DECISIONS = {"NOT_VERIFIED", "NOT_USABLE_FOR_C02", "CONDITIONALLY_DOCUMENTED"}
REQUIRED_CASES = {
    "per_minute_quantity": "REQUIRES_DOCUMENTED_SEMANTICS",
    "cumulative_quantity": "FAIL_CLOSED",
    "unfinalized_bar": "FAIL_CLOSED",
    "corrected_bar": "REQUIRES_DOCUMENTED_REVISION_POLICY",
    "session_boundary": "REQUIRES_DOCUMENTED_SESSION_MAPPING",
    "missing_or_zero": "FAIL_CLOSED",
}


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(artifacts: Path) -> list[str]:
    issues: list[str] = []
    required_files = (
        "source_contract.md",
        "input_semantics.json",
        "synthetic_volume_cases.json",
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
        semantics = json.loads((artifacts / "input_semantics.json").read_text(encoding="utf-8-sig"))
        cases = json.loads(
            (artifacts / "synthetic_volume_cases.json").read_text(encoding="utf-8-sig")
        )
        require(semantics["task_id"] == "TASK-C02-VOLUME-INPUT-AUDIT-06", "Incorrect task id")
        require(
            semantics["current_input_decision"] in INPUT_DECISIONS,
            "Invalid input decision",
        )
        require(semantics["real_data_accessed"] is False, "Real data access is prohibited")
        require(semantics["authorises_f11_reopen"] is False, "F11 must remain closed")
        attributes = semantics["attributes"]
        require(isinstance(attributes, list), "attributes must be an array")
        found_attributes = {row.get("id"): row for row in attributes if isinstance(row, dict)}
        require(set(found_attributes) == ATTRIBUTE_IDS, "Missing or unexpected input attribute")
        for attribute_id, row in found_attributes.items():
            require(
                row.get("status") in ATTRIBUTE_STATUS,
                f"Invalid status for {attribute_id}",
            )
            require(
                isinstance(row.get("basis"), str) and row["basis"].strip(),
                f"Missing basis for {attribute_id}",
            )
        ledger = semantics["research_ledger"]
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
                row.get("expected_handling") == expected, f"Unexpected case handling: {case_id}"
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
        issues.append(f"Invalid C02 volume-input audit: {type(exc).__name__}: {exc}")
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
                "limitations": "Structure only; no volume value, market data, C02 execution, F11 reopening, or economics is validated.",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
