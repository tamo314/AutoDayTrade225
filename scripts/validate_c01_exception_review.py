"""Validate the C01 exception-review design without market access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REQUIRED_BLOCKERS = {
    "f05_f06_closed",
    "no_new_direct_economic_evidence",
    "no_finite_s2_attempt_or_budget",
    "no_review_receipt",
    "no_matching_grant",
}
REQUIRED_RECORDS = {"R087/Q002", "R101/Q001"}
REQUIRED_RECONSIDERATION = {
    "new_direct_economic_evidence",
    "bounded_f05_f06_human_exception",
    "finite_s2_attempt_and_budget",
    "non_pnl_manifest_and_review_receipt",
    "matching_grant",
    "s1_hash_and_development_range_match",
}
REQUIRED_CASES = {
    "calendar_novelty_only": "DENY",
    "small_route_variation": "DENY",
    "future_unverified_economic_claim": "REQUIRE_SEPARATE_REGISTERED_REVIEW",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(artifacts: Path, registry_path: Path, s2_matrix_path: Path) -> list[str]:
    issues: list[str] = []
    required_files = (
        "exception_review.md",
        "comparability_matrix.json",
        "synthetic_exception_cases.json",
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
        matrix = json.loads(
            (artifacts / "comparability_matrix.json").read_text(encoding="utf-8-sig")
        )
        cases = json.loads(
            (artifacts / "synthetic_exception_cases.json").read_text(encoding="utf-8-sig")
        )
        require(matrix["task_id"] == "TASK-C01-EXCEPTION-REVIEW-05", "Incorrect task id")
        require(matrix["registry_sha256"] == digest(registry_path), "Registry hash mismatch")
        require(
            matrix["s2_admission_matrix_sha256"] == digest(s2_matrix_path),
            "S2 admission matrix hash mismatch",
        )
        require(
            matrix["current_decision"] == "NO_EXCEPTION_RECOMMENDED",
            "Current decision must not issue an exception",
        )
        records = matrix["prior_records"]
        require(isinstance(records, list), "prior_records must be an array")
        record_ids = {row.get("id") for row in records if isinstance(row, dict)}
        require(record_ids >= REQUIRED_RECORDS, "Missing required prior record")
        for row in records:
            if row.get("id") in REQUIRED_RECORDS:
                require(
                    isinstance(row.get("comparison"), str) and row["comparison"].strip(),
                    f"Missing comparison for {row['id']}",
                )
                require(
                    isinstance(row.get("outcome_class"), str) and row["outcome_class"].strip(),
                    f"Missing outcome class for {row['id']}",
                )
        blockers = matrix["blocking_conditions"]
        reconsideration = matrix["reconsideration_requirements"]
        require(
            isinstance(blockers, list) and set(blockers) == REQUIRED_BLOCKERS,
            "Incorrect blocking conditions",
        )
        require(
            isinstance(reconsideration, list) and set(reconsideration) == REQUIRED_RECONSIDERATION,
            "Incorrect reconsideration requirements",
        )
        rows = cases["cases"] if isinstance(cases, dict) else cases
        require(isinstance(rows, list), "Synthetic cases must be an array")
        found = {row.get("id"): row for row in rows if isinstance(row, dict)}
        require(set(found) == set(REQUIRED_CASES), "Unexpected or missing synthetic case")
        for case_id, expected in REQUIRED_CASES.items():
            row = found[case_id]
            require(row.get("expected_decision") == expected, f"Unexpected decision: {case_id}")
            require(
                isinstance(row.get("reason"), str) and row["reason"].strip(),
                f"Missing reason: {case_id}",
            )
            require(
                row.get("authorises_real_access") is False,
                f"Synthetic case must not authorise real access: {case_id}",
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"Invalid C01 exception-review contract: {type(exc).__name__}: {exc}")
    return issues


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--registry",
        type=Path,
        default=root / "docs/strategy/registry/20260916_review.json",
    )
    parser.add_argument(
        "--s2-matrix",
        type=Path,
        default=root / "docs/strategy/plans/TASK-C01-S2-ADMISSION-04/admission_matrix.json",
    )
    args = parser.parse_args()
    issues = validate(args.artifacts, args.registry, args.s2_matrix)
    print(
        json.dumps(
            {
                "passed": not issues,
                "issues": issues,
                "limitations": "Exception-review structure only; no exception, grant, market access, PnL, economics, or feasibility is approved.",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
