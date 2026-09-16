"""Validate the C05 R103 design-closure audit without opening market data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_CASES = {
    "observed_four_vs_min_eight": "FAIL_INFORMATION_GATE",
    "lower_minimum_after_shortfall": "PROHIBITED_POST_HOC",
    "new_full_period_design": "SEPARATE_HUMAN_EXCEPTION_REQUIRED",
    "economic_reject_neighbour": "PRESERVE_ECONOMIC_REJECT",
    "pnl_before_shortfall": "NOT_EVALUATED",
    "same_family_variant": "CLOSED_NO_AUTOMATIC_REOPEN",
}


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(artifacts: Path) -> list[str]:
    issues: list[str] = []
    files = ("prior_contract.md", "sample_design.json", "synthetic_count_cases.json", "verification.md", "review.md")
    for name in files:
        path = artifacts / name
        if not path.is_file() or not path.read_text(encoding="utf-8-sig").strip():
            issues.append(f"Missing artifact: {name}")
    if issues:
        return issues
    try:
        design = json.loads((artifacts / "sample_design.json").read_text(encoding="utf-8-sig"))
        cases = json.loads((artifacts / "synthetic_count_cases.json").read_text(encoding="utf-8-sig"))
        require(design["task_id"] == "TASK-C05-R103-DESIGN-CLOSURE-09", "Incorrect task id")
        require(design["study_id"] == "R103/Q001", "Incorrect study id")
        require(design["family_id"] == "F07", "Incorrect family id")
        require(design["current_design_decision"] == "CLOSE_CURRENT_DESIGN", "Unexpected closure decision")
        require(design["frozen_2021_initialization_minimum"] == 8, "The frozen 2021 minimum must remain 8")
        require(design["observed_2021_initialization_count"] == 4, "The observed 2021 count must remain 4")
        require(design["pnl_evidence"] == "NOT_OBTAINED", "PnL evidence must remain unavailable")
        require(design["economics_status"] == "NOT_EVALUATED", "Economic status must remain not evaluated")
        closure = design["family_closure"]
        require(closure["remaining_economic_specs"] == 0, "F07 economic scope must remain closed")
        require(closure["remaining_parameter_variants"] == 0, "F07 parameter scope must remain closed")
        require(closure["automatic_reopen"] is False, "F07 must not automatically reopen")
        for field in ("market_data_accessed", "pnl_accessed", "authorises_family_reopen", "authorises_market_data_access", "authorises_execution", "authorises_exception"):
            require(design[field] is False, f"{field} must remain false")
        exception_path = design["exception_path"]
        require(exception_path["status"] == "SEPARATE_HUMAN_AUTHORIZATION_REQUIRED", "Exception path must require human authorization")
        require(exception_path["exception_created"] is False, "No exception may be created")
        rows = cases["cases"] if isinstance(cases, dict) else cases
        require(isinstance(rows, list), "Synthetic cases must be an array")
        found = {row.get("id"): row for row in rows if isinstance(row, dict)}
        require(set(found) == set(REQUIRED_CASES), "Unexpected or missing synthetic case")
        for case_id, expected in REQUIRED_CASES.items():
            row = found[case_id]
            require(row.get("expected_handling") == expected, f"Unexpected case handling: {case_id}")
            require(isinstance(row.get("reason"), str) and row["reason"].strip(), f"Missing case reason: {case_id}")
            require(row.get("authorises_real_access") is False, f"Case must not authorise access: {case_id}")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"Invalid C05 R103 design-closure audit: {type(exc).__name__}: {exc}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    issues = validate(args.artifacts)
    print(json.dumps({"passed": not issues, "issues": issues, "limitations": "Structure only; no market data, PnL, R103 economics, execution, or reopening is validated."}, ensure_ascii=True, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
