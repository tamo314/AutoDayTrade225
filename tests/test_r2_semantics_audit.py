"""R2 semantics audit tests; no market data is opened."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from n225m_bt.cli import app


def test_r2_audit_records_config_claims_as_blocked_not_provider_evidence(workspace_tmp: Path) -> None:
    output = workspace_tmp / f"r2-audit-{uuid4().hex}"
    result = CliRunner().invoke(
        app,
        [
            "research", "audit-data-semantics", "--audit-id", "R2-SYNTH", "--output", str(output),
            "--config", "config/data.yaml", "--evidence", "config/source_semantics_evidence.yaml",
        ],
    )
    assert result.exit_code == 0, result.stdout
    evidence = json.loads((output / "source_semantics_evidence.json").read_text(encoding="utf-8"))
    gate = json.loads((output / "quality_gate.json").read_text(encoding="utf-8"))
    assert evidence["status"] == gate["data_quality"] == "BLOCKED"
    assert "contract_identity_and_roll" in evidence["required_provider_evidence_missing"]
    assert len(evidence["provider_public_evidence"]) == 1
    assert gate["blocked_dependency"] == "supplier_or_feed_semantics_evidence"
    causal = json.loads((output / "causal_quality_audit.json").read_text(encoding="utf-8"))
    missingness = json.loads((output / "outcome_missingness.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "audit_manifest.json").read_text(encoding="utf-8"))
    access = json.loads((output / "access_ledger.json").read_text(encoding="utf-8"))
    assert causal["status"] == "BLOCKED"
    assert missingness["status"] == "NOT_RUN"
    assert "src/n225m_bt/cli.py" in manifest["code_sha256"]
    assert access["status"] == "PASS"


def test_r2_owner_acceptance_releases_limited_development_without_promoting_evidence(
    workspace_tmp: Path,
) -> None:
    output = workspace_tmp / f"r2-accepted-{uuid4().hex}"
    result = CliRunner().invoke(
        app,
        [
            "research", "audit-data-semantics", "--audit-id", "R2-ACCEPTED", "--output", str(output),
            "--config", "config/data.yaml", "--evidence", "config/source_semantics_evidence.yaml",
            "--accept-unresolved-source-semantics",
        ],
    )
    assert result.exit_code == 0, result.stdout
    evidence = json.loads((output / "source_semantics_evidence.json").read_text(encoding="utf-8"))
    gate = json.loads((output / "quality_gate.json").read_text(encoding="utf-8"))
    assert evidence["status"] == gate["data_quality"] == "PASS_LIMITED"
    assert "contract_identity_and_roll" in evidence["required_provider_evidence_missing"]
    assert gate["blocked_dependency"] is None
    assert "OOS" in gate["prohibited_use"]
