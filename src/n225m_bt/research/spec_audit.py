"""Read-only S0/S1 specification auditing for new research paths.

The audit consumes only a frozen JSON specification and writes a fresh audit
directory.  It never imports a loader or opens market-data artifacts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from n225m_bt.domain import Side
from n225m_bt.research.conditions import (
    ConditionDefinition,
    GateOperator,
    GateRequirement,
    SetRelation,
    assert_gate_satisfiable,
    validate_condition_matrix,
)
from n225m_bt.research.governance import ResearchStage, StudyIdentity, validate_identity


class SpecificationAuditError(ValueError):
    """A new specification is not sufficiently frozen for its declared stage."""


@dataclass(frozen=True, slots=True)
class FrozenSpecification:
    identity: StudyIdentity
    stage: ResearchStage
    preregistration_hash: str
    source_snapshot_hash: str
    seed: int
    nuisance_ids: tuple[str, ...]
    decision_criteria: tuple[str, ...]
    conditions: tuple[ConditionDefinition, ...]
    gates: tuple[GateRequirement, ...]


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SpecificationAuditError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _exact_keys(value: dict[str, object], field: str, keys: set[str]) -> None:
    if set(value) != keys:
        raise SpecificationAuditError(
            f"{field} keys must be exactly {sorted(keys)}; got {sorted(value)}"
        )


def _strings(value: object, field: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise SpecificationAuditError(f"{field} must be a list of nonempty strings")
    if not allow_empty and not value:
        raise SpecificationAuditError(f"{field} must not be empty")
    return tuple(cast(list[str], value))


def _condition(value: object) -> ConditionDefinition:
    item = _mapping(value, "condition")
    _exact_keys(
        item,
        "condition",
        {"condition_id", "side", "quantile", "relation", "parent_condition_id", "first_event_only"},
    )
    if not isinstance(item["condition_id"], str) or not isinstance(item["first_event_only"], bool):
        raise SpecificationAuditError("condition_id and first_event_only have invalid types")
    quantile = item["quantile"]
    if quantile is not None and (not isinstance(quantile, int) or isinstance(quantile, bool)):
        raise SpecificationAuditError("condition quantile must be integer or null")
    parent = item["parent_condition_id"]
    if parent is not None and not isinstance(parent, str):
        raise SpecificationAuditError("condition parent_condition_id must be string or null")
    try:
        return ConditionDefinition(
            item["condition_id"],
            Side(cast(str, item["side"])),
            quantile,
            SetRelation(cast(str, item["relation"])),
            parent,
            item["first_event_only"],
        )
    except (TypeError, ValueError) as exc:
        raise SpecificationAuditError(f"invalid explicit condition: {exc}") from exc


def _gate(value: object) -> GateRequirement:
    item = _mapping(value, "gate")
    keys = {
        "requirement_id", "path_id", "event_set_id", "weighting_id", "side", "metric_id",
        "slippage_ticks_per_side", "fee_jpy_per_side", "operator", "threshold",
    }
    _exact_keys(item, "gate", keys)
    string_keys = keys - {"slippage_ticks_per_side", "fee_jpy_per_side", "threshold"}
    if any(not isinstance(item[key], str) or not item[key] for key in string_keys):
        raise SpecificationAuditError("gate string fields must be nonempty strings")
    ticks, fee, threshold = (
        item["slippage_ticks_per_side"],
        item["fee_jpy_per_side"],
        item["threshold"],
    )
    if any(not isinstance(item, int) or isinstance(item, bool) for item in (ticks, fee)):
        raise SpecificationAuditError("gate ticks and fee must be integers")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise SpecificationAuditError("gate threshold must be numeric")
    try:
        return GateRequirement(
            cast(str, item["requirement_id"]), cast(str, item["path_id"]),
            cast(str, item["event_set_id"]), cast(str, item["weighting_id"]),
            Side(cast(str, item["side"])), cast(str, item["metric_id"]),
            cast(int, ticks), cast(int, fee), GateOperator(cast(str, item["operator"])),
            float(threshold),
        )
    except ValueError as exc:
        raise SpecificationAuditError(f"invalid gate: {exc}") from exc


def parse_frozen_specification(payload: object) -> FrozenSpecification:
    """Parse a strict, content-addressable S0/S1 specification without defaults."""
    root = _mapping(payload, "specification")
    keys = {
        "identity", "stage", "preregistration_hash", "source_snapshot_hash", "seed",
        "nuisance_ids", "decision_criteria", "conditions", "gates",
    }
    _exact_keys(root, "specification", keys)
    identity = _mapping(root["identity"], "identity")
    _exact_keys(identity, "identity", {"family_id", "study_id", "spec_version", "run_id", "protocol_revision"})
    if any(not isinstance(value, str) or not value for value in identity.values()):
        raise SpecificationAuditError("identity values must be nonempty strings")
    stage_raw = root["stage"]
    if not isinstance(stage_raw, str):
        raise SpecificationAuditError("stage must be a string")
    try:
        stage = ResearchStage(stage_raw)
    except ValueError as exc:
        raise SpecificationAuditError(f"unknown stage: {stage_raw}") from exc
    if stage not in {ResearchStage.S0_DESIGN_AUDIT, ResearchStage.S1_INPUT_IMPLEMENTATION_AUDIT}:
        raise SpecificationAuditError("spec audit supports only S0 or S1 without market-data access")
    if not isinstance(root["preregistration_hash"], str) or not root["preregistration_hash"]:
        raise SpecificationAuditError("preregistration_hash is required")
    if not isinstance(root["source_snapshot_hash"], str) or not root["source_snapshot_hash"]:
        raise SpecificationAuditError("source_snapshot_hash is required")
    if not isinstance(root["seed"], int) or isinstance(root["seed"], bool):
        raise SpecificationAuditError("seed must be an integer")
    if not isinstance(root["conditions"], list) or not isinstance(root["gates"], list):
        raise SpecificationAuditError("conditions and gates must be lists")
    result = FrozenSpecification(
        StudyIdentity(**cast(dict[str, str], identity)),
        stage,
        root["preregistration_hash"],
        root["source_snapshot_hash"],
        root["seed"],
        _strings(root["nuisance_ids"], "nuisance_ids", allow_empty=True),
        _strings(root["decision_criteria"], "decision_criteria", allow_empty=False),
        tuple(_condition(item) for item in cast(list[object], root["conditions"])),
        tuple(_gate(item) for item in cast(list[object], root["gates"])),
    )
    try:
        validate_identity(result.identity)
        validate_condition_matrix(result.conditions)
        assert_gate_satisfiable(result.gates)
    except ValueError as exc:
        raise SpecificationAuditError(str(exc)) from exc
    return result


def audit_specification(specification: FrozenSpecification) -> dict[str, object]:
    """Build non-market-data audit records for a successfully parsed spec."""
    canonical = json.dumps(
        {
            "identity": asdict(specification.identity),
            "stage": specification.stage,
            "preregistration_hash": specification.preregistration_hash,
            "source_snapshot_hash": specification.source_snapshot_hash,
            "seed": specification.seed,
            "nuisance_ids": specification.nuisance_ids,
            "decision_criteria": specification.decision_criteria,
            "conditions": [asdict(item) for item in specification.conditions],
            "gates": [asdict(item) for item in specification.gates],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    audit_hash = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "spec_audit": {
            "status": "VALID_SPECIFICATION",
            "stage": specification.stage.value,
            "identity": asdict(specification.identity),
            "specification_hash": audit_hash,
            "market_data_access": False,
            "checks": {
                "explicit_identity": True,
                "content_addressed_preregistration": True,
                "content_addressed_source_snapshot": True,
                "seed_explicit": True,
                "nuisance_declared": True,
                "decision_criteria_explicit": True,
                "condition_matrix_explicit": True,
                "gate_satisfiability_static_check": True,
            },
        },
        "condition_resolution": {
            "status": "RESOLVED_FROM_EXPLICIT_MATRIX",
            "conditions": [asdict(item) for item in specification.conditions],
        },
        "gate_witnesses": {
            "status": "NOT_RUN",
            "reason": "A shared synthetic price-path witness is a separate R1 test artifact; this command performs only static specification validation.",
        },
        "causality_audit": {
            "status": "NOT_RUN",
            "required_mutations": ["M02_R046", "M03_R049", "M04_future_exit_or_sensitivity"],
            "reason": "No loader, bar, feature, signal, order, fill, or outcome was invoked by S0/S1 specification audit.",
        },
        "access_ledger": {
            "event": "specification_audit",
            "stage": specification.stage.value,
            "physical_market_data_read": False,
            "protected_period_access": False,
        },
    }


def write_specification_audit(input_path: Path, output: Path) -> Path:
    """Write an exclusive, read-only specification audit directory."""
    payload: Any = json.loads(input_path.read_text(encoding="utf-8"))
    records = audit_specification(parse_frozen_specification(payload))
    output.mkdir(parents=True, exist_ok=False)
    for name, value in records.items():
        (output / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
    return output
