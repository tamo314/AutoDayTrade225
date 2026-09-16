from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from scripts.validate_c01_s2_admission import (
    REQUIRED_ALLOWED,
    REQUIRED_CASES,
    REQUIRED_GATES,
    REQUIRED_PROHIBITED,
    validate,
)


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    calendar = root / "calendar.json"
    s1_audit = root / "s1_audit.json"
    calendar.write_text('{"days": []}', encoding="utf-8")
    s1_audit.write_text('{"labels": []}', encoding="utf-8")
    artifacts = root / "artifacts"
    artifacts.mkdir()
    for name in ("s2_admission_spec.md", "verification.md", "review.md"):
        (artifacts / name).write_text("Synthetic S2 fixture", encoding="utf-8")
    (artifacts / "admission_matrix.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-C01-S2-ADMISSION-04",
                "calendar_contract_sha256": hashlib.sha256(calendar.read_bytes()).hexdigest(),
                "s1_label_audit_sha256": hashlib.sha256(s1_audit.read_bytes()).hexdigest(),
                "current_authorisation": {
                    "real_market_input_access": "NOT_GRANTED",
                    "grant_count": 0,
                    "family_exception": "NOT_GRANTED",
                    "real_data_pnl_attempts": 0,
                },
                "required_admission_gates": sorted(REQUIRED_GATES),
                "allowed_output_fields": sorted(REQUIRED_ALLOWED),
                "prohibited_output_fields": sorted(REQUIRED_PROHIBITED),
            }
        ),
        encoding="utf-8",
    )
    cases = [
        {"id": case_id, "expected_decision": decision, "reason": "fixture"}
        for case_id, decision in REQUIRED_CASES.items()
    ]
    for row in cases:
        if row["id"] == "hypothetical_complete_metadata_only":
            row["authorises_real_access"] = False
    (artifacts / "synthetic_admission_cases.json").write_text(
        json.dumps({"cases": cases}), encoding="utf-8"
    )
    return artifacts, calendar, s1_audit


def test_validator_accepts_complete_synthetic_admission_contract(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    artifacts, calendar, s1_audit = _write_fixture(root)
    assert validate(artifacts, calendar, s1_audit) == []


def test_validator_rejects_real_access_authorised_by_synthetic_case(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    artifacts, calendar, s1_audit = _write_fixture(root)
    path = artifacts / "synthetic_admission_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    for row in cases["cases"]:
        if row["id"] == "hypothetical_complete_metadata_only":
            row["authorises_real_access"] = True
    path.write_text(json.dumps(cases), encoding="utf-8")
    assert any("must not authorise" in issue for issue in validate(artifacts, calendar, s1_audit))
