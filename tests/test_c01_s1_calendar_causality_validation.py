from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from scripts.validate_c01_s1_calendar_causality import (
    CONTROL,
    EXCLUDED,
    TREATMENT,
    expected_labels,
    validate,
)


def _write_fixture(root: Path) -> tuple[Path, Path]:
    calendar_path = root / "calendar.json"
    calendar = {
        "days": [
            {
                "calendar_date": "2022-09-23",
                "cash_open": False,
                "ose_holiday_trading": True,
                "ose_trade_date": "2022-09-26",
            },
            {
                "calendar_date": "2022-09-24",
                "cash_open": False,
                "ose_holiday_trading": False,
                "ose_trade_date": None,
            },
            {
                "calendar_date": "2022-09-25",
                "cash_open": False,
                "ose_holiday_trading": False,
                "ose_trade_date": None,
            },
            {
                "calendar_date": "2022-09-26",
                "cash_open": True,
                "ose_holiday_trading": False,
                "ose_trade_date": "2022-09-26",
            },
            {
                "calendar_date": "2022-09-27",
                "cash_open": True,
                "ose_holiday_trading": False,
                "ose_trade_date": "2022-09-27",
            },
        ]
    }
    calendar_path.write_text(json.dumps(calendar), encoding="utf-8")
    artifacts = root / "artifacts"
    artifacts.mkdir()
    for name in ("labeling_spec.md", "fixture_results.md", "review.md"):
        (artifacts / name).write_text("Synthetic S1 fixture", encoding="utf-8")
    labels = expected_labels(calendar)
    for row in labels:
        row.update(
            reason="Synthetic fixture",
            availability={
                "decision_date": row["calendar_date"],
                "basis": "Synthetic publication-time fixture",
                "source_urls": ["https://example.invalid/synthetic"],
            },
        )
    (artifacts / "label_audit.json").write_text(
        json.dumps(
            {
                "calendar_contract_sha256": hashlib.sha256(calendar_path.read_bytes()).hexdigest(),
                "labels": labels,
                "counts": {TREATMENT: 1, CONTROL: 1},
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "synthetic_cases.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "holiday_weekend_reopen",
                        "expected_label": TREATMENT,
                        "reason": "fixture",
                    },
                    {"id": "weekend_only_reopen", "expected_label": EXCLUDED, "reason": "fixture"},
                    {"id": "holiday_nonconducted", "expected_label": EXCLUDED, "reason": "fixture"},
                    {"id": "year_end_reopen", "expected_label": EXCLUDED, "reason": "fixture"},
                    {
                        "id": "missing_or_ambiguous_calendar",
                        "expected_label": EXCLUDED,
                        "reason": "fixture",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return artifacts, calendar_path


def test_expected_labels_prioritize_holiday_over_weekend() -> None:
    calendar = {
        "days": [
            {"calendar_date": "2022-09-23", "cash_open": False, "ose_holiday_trading": True},
            {"calendar_date": "2022-09-24", "cash_open": False, "ose_holiday_trading": False},
            {"calendar_date": "2022-09-25", "cash_open": False, "ose_holiday_trading": False},
            {"calendar_date": "2022-09-26", "cash_open": True, "ose_holiday_trading": False},
            {"calendar_date": "2022-09-27", "cash_open": True, "ose_holiday_trading": False},
        ]
    }
    assert [row["label"] for row in expected_labels(calendar)] == [TREATMENT, CONTROL]


def test_validator_rejects_changed_label(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    artifacts, calendar = _write_fixture(root)
    assert validate(artifacts, calendar) == []
    audit_path = artifacts / "label_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["labels"][0]["label"] = CONTROL
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    assert any("Incorrect label" in issue for issue in validate(artifacts, calendar))
