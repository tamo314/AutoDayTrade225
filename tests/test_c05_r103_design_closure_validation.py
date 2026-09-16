from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_c05_r103_design_closure import validate


def write_artifacts(root: Path) -> None:
    for name in ("prior_contract.md", "verification.md", "review.md"):
        (root / name).write_text("evidence\n", encoding="utf-8")
    design = {
        "task_id": "TASK-C05-R103-DESIGN-CLOSURE-09",
        "study_id": "R103/Q001",
        "family_id": "F07",
        "current_design_decision": "CLOSE_CURRENT_DESIGN",
        "frozen_2021_initialization_minimum": 8,
        "observed_2021_initialization_count": 4,
        "pnl_evidence": "NOT_OBTAINED",
        "economics_status": "NOT_EVALUATED",
        "family_closure": {
            "remaining_economic_specs": 0,
            "remaining_parameter_variants": 0,
            "automatic_reopen": False,
        },
        "market_data_accessed": False,
        "pnl_accessed": False,
        "authorises_family_reopen": False,
        "authorises_market_data_access": False,
        "authorises_execution": False,
        "authorises_exception": False,
        "exception_path": {
            "status": "SEPARATE_HUMAN_AUTHORIZATION_REQUIRED",
            "exception_created": False,
        },
    }
    (root / "sample_design.json").write_text(json.dumps(design), encoding="utf-8")
    cases = {
        "observed_four_vs_min_eight": "FAIL_INFORMATION_GATE",
        "lower_minimum_after_shortfall": "PROHIBITED_POST_HOC",
        "new_full_period_design": "SEPARATE_HUMAN_EXCEPTION_REQUIRED",
        "economic_reject_neighbour": "PRESERVE_ECONOMIC_REJECT",
        "pnl_before_shortfall": "NOT_EVALUATED",
        "same_family_variant": "CLOSED_NO_AUTOMATIC_REOPEN",
    }
    rows = [
        {
            "id": key,
            "expected_handling": value,
            "reason": "fixed boundary",
            "authorises_real_access": False,
        }
        for key, value in cases.items()
    ]
    (root / "synthetic_count_cases.json").write_text(json.dumps({"cases": rows}), encoding="utf-8")


def test_validate_accepts_complete_boundary_artifacts(tmp_path: Path) -> None:
    write_artifacts(tmp_path)
    assert validate(tmp_path) == []


def test_validate_rejects_post_hoc_lowered_minimum(tmp_path: Path) -> None:
    write_artifacts(tmp_path)
    path = tmp_path / "sample_design.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["frozen_2021_initialization_minimum"] = 4
    path.write_text(json.dumps(document), encoding="utf-8")
    assert validate(tmp_path)
