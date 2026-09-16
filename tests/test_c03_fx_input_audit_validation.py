from __future__ import annotations

import json
import uuid
from pathlib import Path

from scripts.validate_c03_fx_input_audit import ATTRIBUTE_IDS, REQUIRED_CASES, validate


def _write_fixture(root: Path) -> Path:
    artifacts = root / "artifacts"
    artifacts.mkdir()
    for name in ("source_contract.md", "verification.md", "review.md"):
        (artifacts / name).write_text("Synthetic C03 fixture", encoding="utf-8")
    (artifacts / "input_admission.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-C03-FX-INPUT-AUDIT-07",
                "current_input_decision": "NOT_VERIFIED",
                "real_data_accessed": False,
                "external_time_series_accessed": False,
                "authorises_c03_family": False,
                "authorises_market_data_access": False,
                "attributes": [
                    {"id": attribute_id, "status": "UNVERIFIED", "basis": "fixture"}
                    for attribute_id in sorted(ATTRIBUTE_IDS)
                ],
                "research_ledger": {"search_queries": 0, "primary_source_bodies": 0},
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "synthetic_timing_cases.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": case_id,
                        "expected_handling": handling,
                        "reason": "fixture",
                        "authorises_real_access": False,
                    }
                    for case_id, handling in REQUIRED_CASES.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    return artifacts


def test_validator_accepts_bounded_synthetic_fx_audit(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    assert validate(_write_fixture(root)) == []


def test_validator_rejects_c03_family_authorisation(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    artifacts = _write_fixture(root)
    path = artifacts / "input_admission.json"
    admission = json.loads(path.read_text(encoding="utf-8"))
    admission["authorises_c03_family"] = True
    path.write_text(json.dumps(admission), encoding="utf-8")
    assert any("authorises_c03_family must remain false" in issue for issue in validate(artifacts))
