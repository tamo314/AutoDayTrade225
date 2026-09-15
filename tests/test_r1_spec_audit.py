"""S0/S1 specification-audit tests using JSON only, never market data."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from n225m_bt.cli import app
from n225m_bt.research.spec_audit import SpecificationAuditError, parse_frozen_specification


def valid_specification() -> dict[str, object]:
    return {
        "identity": {
            "family_id": "synthetic_family",
            "study_id": "SYNTH-Q001",
            "spec_version": "v1",
            "run_id": "synth-run-001",
            "protocol_revision": "RG-20260915-01",
        },
        "stage": "S0",
        "preregistration_hash": "pre-hash",
        "source_snapshot_hash": "source-hash",
        "seed": 20260915,
        "nuisance_ids": [],
        "decision_criteria": ["main_net_positive"],
        "conditions": [
            {
                "condition_id": "A",
                "side": "long",
                "quantile": 75,
                "relation": "root",
                "parent_condition_id": None,
                "first_event_only": True,
            },
            {
                "condition_id": "A_fade",
                "side": "short",
                "quantile": None,
                "relation": "equal",
                "parent_condition_id": "A",
                "first_event_only": True,
            },
        ],
        "gates": [
            {
                "requirement_id": "main-positive",
                "path_id": "A-path",
                "event_set_id": "A",
                "weighting_id": "scheduled-axis",
                "side": "long",
                "metric_id": "net_mean_jpy",
                "slippage_ticks_per_side": 1,
                "fee_jpy_per_side": 30,
                "operator": ">",
                "threshold": 0,
            }
        ],
    }


def test_spec_audit_cli_writes_exclusive_non_market_artifacts(workspace_tmp: Path) -> None:
    source = workspace_tmp / "spec.json"
    source.write_text(json.dumps(valid_specification()), encoding="utf-8")
    output = workspace_tmp / f"spec-audit-{uuid4().hex}"
    result = CliRunner().invoke(
        app, ["research", "audit-spec", str(source), "--output", str(output)]
    )
    assert result.exit_code == 0, result.stdout
    spec_audit = json.loads((output / "spec_audit.json").read_text())
    access = json.loads((output / "access_ledger.json").read_text())
    assert spec_audit["status"] == "VALID_SPECIFICATION"
    assert spec_audit["market_data_access"] is False
    assert access["physical_market_data_read"] is False
    assert json.loads((output / "gate_witnesses.json").read_text())["status"] == "NOT_RUN"
    duplicate = CliRunner().invoke(
        app, ["research", "audit-spec", str(source), "--output", str(output)]
    )
    assert duplicate.exit_code != 0


def test_spec_audit_rejects_missing_m09_fields_and_r031_gate() -> None:
    missing_seed = valid_specification()
    del missing_seed["seed"]
    try:
        parse_frozen_specification(missing_seed)
    except SpecificationAuditError as exc:
        assert "keys must be exactly" in str(exc)
    else:
        raise AssertionError("missing seed accepted")

    impossible = valid_specification()
    gates = impossible["gates"]
    assert isinstance(gates, list)
    base = {
        "path_id": "same-path",
        "event_set_id": "B",
        "weighting_id": "uniform",
        "metric_id": "gross_pre_fee_mean_jpy",
        "slippage_ticks_per_side": 0,
        "fee_jpy_per_side": 0,
        "operator": "<",
        "threshold": 0,
    }
    impossible["gates"] = [
        {"requirement_id": "long", "side": "long", **base},
        {"requirement_id": "short", "side": "short", **base},
    ]
    try:
        parse_frozen_specification(impossible)
    except SpecificationAuditError as exc:
        assert "unsatisfiable" in str(exc)
    else:
        raise AssertionError("R031 contradiction accepted")
