"""Isolated authorities for synthetic execution tests; never alter the workspace policy."""

import json
from pathlib import Path

from n225m_bt.research.execution import (
    FileSeal,
    ResearchController,
    RunManifest,
    development_files,
    digest_file,
    manifest_digest,
    source_tree_digest,
)


def seal(root: Path, path: Path) -> FileSeal:
    return FileSeal(
        path=path.resolve().relative_to(root.resolve()).as_posix(), sha256=digest_file(path)
    )


def grant(root: Path, manifest: RunManifest, *, reopened: bool = True) -> None:
    review = root / f"review-{manifest.run_id}.json"
    review.write_text(
        json.dumps(
            {
                "manifest_sha256": manifest_digest(manifest),
                "decision": "APPROVED",
                "reason": "synthetic test fixture only",
                "specification": "PASS",
                "input_contract": "PASS",
                "causality": "PASS",
                "unknown_exit_null": "PASS",
                "synthetic_calibration": "PASS",
                "reopened_family": reopened,
            }
        ),
        encoding="utf-8",
    )
    path = root / "config/research_execution.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["grants"].append(
        {
            "manifest_sha256": manifest_digest(manifest),
            "review": seal(root, review).model_dump(),
            "entrypoints": [manifest.entrypoint],
            "reopened_family": reopened,
        }
    )
    path.write_text(json.dumps(policy), encoding="utf-8")


def provision(
    root: Path,
    *,
    input_root: Path | None = None,
    entry: str = "fixture",
    conditions: tuple[str, ...] = ("A",),
    seed: int = 42,
) -> tuple[ResearchController, RunManifest]:
    root = root.resolve()
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src/fixture.py").write_text("# Synthetic isolated workspace\n", encoding="utf-8")
    (root / "config").mkdir(exist_ok=True)
    (root / "config/fixture.yaml").write_text("synthetic: true\n", encoding="utf-8")
    (root / "spec.md").write_text("Synthetic frozen study; no market evidence.\n", encoding="utf-8")
    (root / "evidence.json").write_text('{"synthetic": true}', encoding="utf-8")
    registry = root / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "records": [{"key": "TEST/Q001", "family_id": "F01"}],
                "families": [
                    {"family_id": "F01", "status": "CLOSED_FOR_CURRENT_DEVELOPMENT_SEARCH"}
                ],
            }
        ),
        encoding="utf-8",
    )
    policy = {
        "schema_version": 1,
        "policy_id": "TEST-ONLY",
        "market_execution_enabled": True,
        "registry": seal(root, registry).model_dump(),
        "families": {"F01": {"max_attempts": 3, "max_specs": 2, "max_conditions": 100}},
        "batches": {"B1": {"max_attempts": 3, "max_specs": 2, "max_conditions": 100}},
        "grants": [],
    }
    (root / "config/research_execution.json").write_text(json.dumps(policy), encoding="utf-8")
    if input_root is None:
        input_root = root / "gold"
        fixture = input_root / "year=2024/month=11/bars.parquet"
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(b"synthetic-only fixture; unit tests do not parse this file")
    input_root = input_root.resolve()
    m = RunManifest(
        batch_id="B1",
        family_id="F01",
        study_id="TEST",
        spec_version="v1",
        run_id="test-001",
        stage="S3",
        entrypoint=entry,
        conditions=conditions,
        seed=seed,
        stop_rule="One attempt",
        known_record_keys=("TEST/Q001",),
        specification=seal(root, root / "spec.md"),
        evidence=(seal(root, root / "evidence.json"),),
        source_tree_sha256=source_tree_digest(root),
        config_files=tuple(seal(root, p) for p in root.rglob("*.yaml")),
        input_root=input_root.relative_to(root).as_posix(),
        input_files=tuple(seal(root, p) for p in development_files(input_root)),
        output="results/research/test-001",
    )
    grant(root, m)
    return ResearchController(root), m
