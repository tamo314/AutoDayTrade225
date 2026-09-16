"""Validate C01 S1 calendar labels without loading any market data."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

BOUNDARY = date(2022, 9, 23)
TREATMENT = "TREATMENT_HOLIDAY_REOPEN"
CONTROL = "ORDINARY_CASH_OPEN_CONTROL"
EXCLUDED = "OUT_OF_REGIME_OR_EXCLUDED"
LABELS = {TREATMENT, CONTROL, EXCLUDED}
REQUIRED_CASES = {
    "holiday_weekend_reopen": TREATMENT,
    "weekend_only_reopen": EXCLUDED,
    "holiday_nonconducted": EXCLUDED,
    "year_end_reopen": EXCLUDED,
    "missing_or_ambiguous_calendar": EXCLUDED,
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def expected_labels(calendar: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive fixed C01 labels solely from the sealed calendar contract."""
    rows = calendar["days"]
    by_day = {date.fromisoformat(row["calendar_date"]): row for row in rows}
    expected: list[dict[str, Any]] = []
    for current in sorted(day for day, row in by_day.items() if row["cash_open"]):
        interval: list[date] = []
        prior = current - timedelta(days=1)
        while prior in by_day and not by_day[prior]["cash_open"]:
            interval.append(prior)
            prior -= timedelta(days=1)
        interval.reverse()
        has_holiday_session = any(by_day[day]["ose_holiday_trading"] for day in interval)
        if current >= BOUNDARY and has_holiday_session:
            label = TREATMENT
        elif current >= BOUNDARY and not interval:
            label = CONTROL
        else:
            label = EXCLUDED
        expected.append(
            {
                "calendar_date": current.isoformat(),
                "label": label,
                "closure_calendar_dates": [day.isoformat() for day in interval],
            }
        )
    return expected


def validate(artifacts: Path, calendar_path: Path) -> list[str]:
    issues: list[str] = []
    required = (
        "labeling_spec.md",
        "label_audit.json",
        "synthetic_cases.json",
        "fixture_results.md",
        "review.md",
    )
    for name in required:
        path = artifacts / name
        if not path.is_file() or not path.read_text(encoding="utf-8-sig").strip():
            issues.append(f"Missing artifact: {name}")
    if issues:
        return issues
    try:
        calendar = json.loads(calendar_path.read_text(encoding="utf-8-sig"))
        audit = json.loads((artifacts / "label_audit.json").read_text(encoding="utf-8-sig"))
        cases = json.loads((artifacts / "synthetic_cases.json").read_text(encoding="utf-8-sig"))
        require(
            audit["calendar_contract_sha256"] == digest(calendar_path), "Calendar hash mismatch"
        )
        expected = expected_labels(calendar)
        actual = audit["labels"]
        require(isinstance(actual, list), "labels must be an array")
        require(len(actual) == len(expected), "Each cash-open date needs exactly one label")
        expected_by_day = {row["calendar_date"]: row for row in expected}
        actual_days = [row["calendar_date"] for row in actual]
        require(len(actual_days) == len(set(actual_days)), "Duplicate labeled date")
        require(set(actual_days) == set(expected_by_day), "Missing or unexpected labeled date")
        for row in actual:
            expected_row = expected_by_day[row["calendar_date"]]
            require(row["label"] in LABELS, f"Unknown label: {row['calendar_date']}")
            require(
                row["label"] == expected_row["label"], f"Incorrect label: {row['calendar_date']}"
            )
            require(
                row["closure_calendar_dates"] == expected_row["closure_calendar_dates"],
                f"Incorrect closure interval: {row['calendar_date']}",
            )
            require(isinstance(row.get("reason"), str) and row["reason"].strip(), "Missing reason")
            availability = row.get("availability")
            require(isinstance(availability, dict), "Missing availability record")
            require(
                availability.get("decision_date") == row["calendar_date"], "Decision date mismatch"
            )
            require(
                isinstance(availability.get("basis"), str) and availability["basis"].strip(),
                "Missing availability basis",
            )
            urls = availability.get("source_urls")
            require(
                isinstance(urls, list)
                and urls
                and all(isinstance(url, str) and url.startswith("https://") for url in urls),
                "Availability requires official HTTPS source URLs",
            )
        counts = Counter(row["label"] for row in actual)
        require(audit["counts"] == dict(counts), "Label counts mismatch")
        case_rows = cases["cases"] if isinstance(cases, dict) else cases
        require(isinstance(case_rows, list), "cases must be an array")
        found = {row.get("id"): row for row in case_rows if isinstance(row, dict)}
        for case_id, label in REQUIRED_CASES.items():
            require(case_id in found, f"Missing synthetic case: {case_id}")
            require(found[case_id].get("expected_label") == label, f"Unexpected label: {case_id}")
            require(
                isinstance(found[case_id].get("reason"), str) and found[case_id]["reason"].strip(),
                f"Missing case reason: {case_id}",
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"Invalid S1 calendar-label contract: {type(exc).__name__}: {exc}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--calendar",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "docs/strategy/plans/TASK-C01-PREPARATION-02/calendar_contract.json",
    )
    args = parser.parse_args()
    issues = validate(args.artifacts, args.calendar)
    print(
        json.dumps(
            {
                "passed": not issues,
                "issues": issues,
                "limitations": "Calendar-only structural checks; no market data, economics, or causal effect is validated.",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
