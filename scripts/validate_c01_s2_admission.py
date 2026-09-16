"""Validate the C01 S2 admission design without reading market data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REQUIRED_GATES = {
    "calendar_hash_match",
    "development_range_fixed",
    "family_exception_review",
    "finite_s2_attempt",
    "review_receipt",
    "matching_grant",
}
REQUIRED_ALLOWED = {
    "calendar_date",
    "trade_date",
    "s1_label",
    "open_0900_present",
    "close_0914_present",
    "entry_0915_present",
    "exit_1030_present",
    "entry_available",
    "exit_available",
    "missingness_code",
    "scheduled_date_count",
}
REQUIRED_PROHIBITED = {
    "open",
    "high",
    "low",
    "close",
    "price",
    "return",
    "direction",
    "order",
    "fill_price",
    "pnl",
    "gross",
    "net",
}
REQUIRED_CASES = {
    "no_grant": "REJECT",
    "no_family_exception": "REJECT",
    "calendar_hash_mismatch": "REJECT",
    "out_of_development_range": "REJECT",
    "prohibited_output_field": "REJECT",
    "hypothetical_complete_metadata_only": "ACCEPT_SYNTHETIC_ONLY",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(artifacts: Path, calendar_path: Path, s1_audit_path: Path) -> list[str]:
    issues: list[str] = []
    required_files = (
        "s2_admission_spec.md",
        "admission_matrix.json",
        "synthetic_admission_cases.json",
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
        matrix = json.loads((artifacts / "admission_matrix.json").read_text(encoding="utf-8-sig"))
        cases = json.loads(
            (artifacts / "synthetic_admission_cases.json").read_text(encoding="utf-8-sig")
        )
        require(matrix["task_id"] == "TASK-C01-S2-ADMISSION-04", "Incorrect task id")
        require(
            matrix["calendar_contract_sha256"] == digest(calendar_path),
            "Calendar contract hash mismatch",
        )
        require(
            matrix["s1_label_audit_sha256"] == digest(s1_audit_path),
            "S1 label-audit hash mismatch",
        )
        current = matrix["current_authorisation"]
        require(
            current
            == {
                "real_market_input_access": "NOT_GRANTED",
                "grant_count": 0,
                "family_exception": "NOT_GRANTED",
                "real_data_pnl_attempts": 0,
            },
            "Current authorisation must remain NOT_GRANTED with zero grants/attempts",
        )
        gates = matrix["required_admission_gates"]
        require(
            isinstance(gates, list) and set(gates) == REQUIRED_GATES, "Incorrect admission gates"
        )
        allowed = matrix["allowed_output_fields"]
        prohibited = matrix["prohibited_output_fields"]
        require(
            isinstance(allowed, list) and set(allowed) == REQUIRED_ALLOWED,
            "Incorrect allowed outputs",
        )
        require(
            isinstance(prohibited, list) and set(prohibited) >= REQUIRED_PROHIBITED,
            "Missing prohibited outputs",
        )
        require(not set(allowed) & set(prohibited), "Allowed and prohibited outputs overlap")
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
            if expected == "ACCEPT_SYNTHETIC_ONLY":
                require(
                    row.get("authorises_real_access") is False,
                    "Synthetic accept must not authorise real access",
                )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"Invalid S2 admission contract: {type(exc).__name__}: {exc}")
    return issues


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--calendar",
        type=Path,
        default=root / "docs/strategy/plans/TASK-C01-PREPARATION-02/calendar_contract.json",
    )
    parser.add_argument(
        "--s1-audit",
        type=Path,
        default=root / "docs/strategy/plans/TASK-C01-S1-CALENDAR-CAUSALITY-03/label_audit.json",
    )
    args = parser.parse_args()
    issues = validate(args.artifacts, args.calendar, args.s1_audit)
    print(
        json.dumps(
            {
                "passed": not issues,
                "issues": issues,
                "limitations": "Admission-design checks only; no grant, market data, PnL, economics, or feasibility is validated.",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
