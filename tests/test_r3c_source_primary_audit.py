"""Tests for the strict no-price R062 source-primary audit."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from n225m_bt.research.r3c_source_primary_audit import run_source_primary_audit


def test_r3c_audit_is_exclusive_and_blocks_without_primary_closure(workspace_tmp: Path) -> None:
    root = workspace_tmp / "repo"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "src" / "n225m_bt" / "research").mkdir(parents=True, exist_ok=True)
    (root / "data" / "raw" / "225labo" / "center").mkdir(parents=True, exist_ok=True)
    (root / "config" / "data.yaml").write_text(
        "source: 225labo\nsource_format: {}\n", encoding="utf-8"
    )
    (root / "config" / "source_semantics_evidence.yaml").write_text(
        "provider_public_claims: []\n", encoding="utf-8"
    )
    (root / "config" / "r3c_source_primary_evidence.yaml").write_text(
        "sources: []\n", encoding="utf-8"
    )
    (root / "src" / "n225m_bt" / "research" / "r062.py").write_text(
        "# frozen rule\n", encoding="utf-8"
    )
    (root / "data" / "raw" / "225labo" / "center" / "provider.zip").write_bytes(b"not opened")
    output = root / "results" / f"AUDIT-R3C-TEST-{uuid4().hex}"

    run_source_primary_audit(repository_root=root, output=output, audit_id="AUDIT-R3C-TEST")

    mapping = json.loads((output / "evidence_mapping.json").read_text(encoding="utf-8"))
    access = json.loads((output / "access_ledger.json").read_text(encoding="utf-8"))
    decision = json.loads((output / "decision.json").read_text(encoding="utf-8"))
    assert len(mapping["roles"]) == 15
    assert mapping["exclusive_classification_counts"] == {
        "CONFLICTING": 0,
        "MISSING": 15,
        "RESOLVED_PRIMARY": 0,
        "UNKNOWN": 0,
    }
    assert decision["status"] == "BLOCKED_EXTERNAL_EVIDENCE"
    assert access["market_data_values_read"] is False
    assert access["market_data_metadata_read"] is False
    assert access["price_derived_qc_read"] is False
    source = json.loads((output / "source_evidence.json").read_text(encoding="utf-8"))
    assert source["original_distribution_filenames_only"]["directory_enumerated"] is False
