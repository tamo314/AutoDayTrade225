from __future__ import annotations

import json
import uuid
from pathlib import Path

from scripts.validate_c02_volume_input_audit import (
    ATTRIBUTE_IDS,
    REQUIRED_CASES,
    validate,
)


def _write_fixture(root: Path) -> Path:
    artifacts = root / "artifacts"
    artifacts.mkdir()
    for name in ("source_contract.md", "verification.md", "review.md"):
        (artifacts / name).write_text("Synthetic C02 fixture", encoding="utf-8")
    (artifacts / "input_semantics.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-C02-VOLUME-INPUT-AUDIT-06",
                "current_input_decision": "NOT_VERIFIED",
                "real_data_accessed": False,
                "authorises_f11_reopen": False,
                "attributes": [
                    {"id": attribute_id, "status": "UNVERIFIED", "basis": "fixture"}
                    for attribute_id in sorted(ATTRIBUTE_IDS)
                ],
                "research_ledger": {"search_queries": 0, "primary_source_bodies": 0},
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "synthetic_volume_cases.json").write_text(
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


def test_validator_accepts_bounded_synthetic_volume_audit(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    assert validate(_write_fixture(root)) == []


def test_validator_rejects_f11_reopening(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    artifacts = _write_fixture(root)
    path = artifacts / "input_semantics.json"
    semantics = json.loads(path.read_text(encoding="utf-8"))
    semantics["authorises_f11_reopen"] = True
    path.write_text(json.dumps(semantics), encoding="utf-8")
    assert any("F11 must remain closed" in issue for issue in validate(artifacts))
