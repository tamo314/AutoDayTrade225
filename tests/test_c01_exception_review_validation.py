from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from scripts.validate_c01_exception_review import (
    REQUIRED_BLOCKERS,
    REQUIRED_CASES,
    REQUIRED_RECONSIDERATION,
    validate,
)


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    registry = root / "registry.json"
    s2_matrix = root / "s2_matrix.json"
    registry.write_text('{"families": []}', encoding="utf-8")
    s2_matrix.write_text('{"current_authorisation": "NOT_GRANTED"}', encoding="utf-8")
    artifacts = root / "artifacts"
    artifacts.mkdir()
    for name in ("exception_review.md", "verification.md", "review.md"):
        (artifacts / name).write_text("Synthetic exception fixture", encoding="utf-8")
    (artifacts / "comparability_matrix.json").write_text(
        json.dumps(
            {
                "task_id": "TASK-C01-EXCEPTION-REVIEW-05",
                "registry_sha256": hashlib.sha256(registry.read_bytes()).hexdigest(),
                "s2_admission_matrix_sha256": hashlib.sha256(s2_matrix.read_bytes()).hexdigest(),
                "current_decision": "NO_EXCEPTION_RECOMMENDED",
                "prior_records": [
                    {
                        "id": "R087/Q002",
                        "comparison": "fixture",
                        "outcome_class": "INFORMATION_INSUFFICIENCY",
                    },
                    {
                        "id": "R101/Q001",
                        "comparison": "fixture",
                        "outcome_class": "ECONOMIC_REJECT",
                    },
                ],
                "blocking_conditions": sorted(REQUIRED_BLOCKERS),
                "reconsideration_requirements": sorted(REQUIRED_RECONSIDERATION),
            }
        ),
        encoding="utf-8",
    )
    (artifacts / "synthetic_exception_cases.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": case_id,
                        "expected_decision": decision,
                        "reason": "fixture",
                        "authorises_real_access": False,
                    }
                    for case_id, decision in REQUIRED_CASES.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    return artifacts, registry, s2_matrix


def test_validator_accepts_complete_synthetic_exception_review(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    artifacts, registry, s2_matrix = _write_fixture(root)
    assert validate(artifacts, registry, s2_matrix) == []


def test_validator_rejects_synthetic_access_authorisation(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    artifacts, registry, s2_matrix = _write_fixture(root)
    path = artifacts / "synthetic_exception_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    cases["cases"][0]["authorises_real_access"] = True
    path.write_text(json.dumps(cases), encoding="utf-8")
    assert any("must not authorise" in issue for issue in validate(artifacts, registry, s2_matrix))
