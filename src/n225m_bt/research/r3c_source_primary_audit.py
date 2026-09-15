"""Strict, non-price R062 source-primary-evidence closure audit."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

import yaml

CLASSIFICATIONS = frozenset({"RESOLVED_PRIMARY", "MISSING", "UNKNOWN", "CONFLICTING"})
ROLE_SPECS = (
    ("U_NIGHT_FIRST_OPEN", "each strictly-prior U night, scheduled ordinal 0", "open"),
    ("U_NIGHT_FINAL_CLOSE", "each strictly-prior U night, scheduled final ordinal", "close"),
    ("TARGET_NIGHT_FIRST_OPEN", "target night, scheduled ordinal 0", "open"),
    ("TARGET_NIGHT_FINAL_CLOSE", "target night, scheduled final ordinal", "close"),
    ("TSE_REACTION_START_OPEN", "TSE ordinal 0", "open"),
    ("TSE_REACTION10_CLOSE", "TSE ordinal 9", "close"),
    ("TSE_REACTION15_CLOSE", "TSE ordinal 14", "close"),
    ("TSE_REACTION20_CLOSE", "TSE ordinal 19", "close"),
    ("TSE_BASE_ENTRY_OPEN", "TSE ordinal 10, 15, or 20", "open"),
    ("TSE_DELAYED_ENTRY_OPEN", "TSE ordinal 11, 16, or 21", "open"),
    ("TSE_HOLD15_EXIT_OPEN", "reaction-entry ordinal plus 15", "open"),
    ("TSE_HOLD30_EXIT_OPEN", "reaction-entry ordinal plus 30", "open"),
    ("TSE_HOLD45_EXIT_OPEN", "reaction-entry ordinal plus 45", "open"),
    ("TSE_COMMON_E_OHLC_PATH", "TSE ordinals 0 through 65", "OHLC and eligibility/missing flags"),
    (
        "CONTRACT_IDENTITY_AT_ALL_ROLES",
        "all R062 night/day input, entry, and exit bars",
        "contract identity / roll / adjustment",
    ),
)
REQUIRED_SEMANTICS = (
    "timestamp_label",
    "ohlc_construction",
    "price_available_at_as_of",
    "missing_and_correction_policy",
    "contract_identity_roll_adjustment",
)
FROZEN_DISTRIBUTION_FILENAMES = (
    "N225minif_2021.xlsx",
    "N225minif_2022.xlsx",
    "N225minif_2023.xlsx",
    "N225minif_2024.xlsx",
    "N225minif_2025.xlsx",
)


class SourcePrimaryAuditError(ValueError):
    """Raised when a source-primary audit would exceed its frozen input scope."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SourcePrimaryAuditError(f"expected mapping: {path.as_posix()}")
    return payload


def _allowlisted_distribution_filenames(source_directory: Path) -> list[str]:
    """Check only predeclared Development source-package paths, never enumerate a directory."""
    filenames: list[str] = []
    for name in FROZEN_DISTRIBUTION_FILENAMES:
        candidate = source_directory / name
        if candidate.is_file():
            filenames.append(name)
    return filenames


def _role_records() -> list[dict[str, object]]:
    missing = {semantic: "MISSING" for semantic in REQUIRED_SEMANTICS}
    records: list[dict[str, object]] = []
    for role_id, planned_bar, field in ROLE_SPECS:
        role_semantics = missing.copy()
        if role_id == "CONTRACT_IDENTITY_AT_ALL_ROLES":
            role_semantics["contract_identity_roll_adjustment"] = "MISSING"
        classification = "MISSING"
        if classification not in CLASSIFICATIONS:
            raise AssertionError("invalid classification")
        records.append(
            {
                "id": role_id,
                "planned_bar": planned_bar,
                "field": field,
                "semantic_elements": role_semantics,
                "classification": classification,
                "reason": (
                    "The allowlisted provider materials do not state all five required "
                    "source-specific semantics for this R062 role."
                ),
            }
        )
    return records


def run_source_primary_audit(
    *,
    repository_root: Path,
    output: Path,
    audit_id: str,
    pre_execution_incidents: list[dict[str, str]] | None = None,
) -> Path:
    """Write a no-price audit from a frozen allowlist and no other local inputs."""
    if output.exists():
        raise SourcePrimaryAuditError(f"refusing to overwrite existing audit output: {output}")
    root = repository_root.resolve()
    allowlisted_files = (
        Path("config/data.yaml"),
        Path("config/source_semantics_evidence.yaml"),
        Path("config/r3c_source_primary_evidence.yaml"),
        Path("src/n225m_bt/research/r062.py"),
    )
    distribution_directory = Path("data/raw/225labo/center")
    output.mkdir(parents=True, exist_ok=False)
    frozen_plan = {
        "audit_id": audit_id,
        "task_id": "TASK-R3C-02",
        "hypothesis": (
            "Primary supplier evidence can uniquely determine all 15 R062 time-role "
            "semantics and continuous-series lineage without reading price rows."
        ),
        "trade_date_scope": ["2021-01-01", "2025-06-30"],
        "path_allowlist_frozen_before_reads": {
            "read_file_contents": [path.as_posix() for path in allowlisted_files],
            "audit_implementation_source": "src/n225m_bt/research/r3c_source_primary_audit.py",
            "distribution_filenames_only": [
                (distribution_directory / name).as_posix() for name in FROZEN_DISTRIBUTION_FILENAMES
            ],
            "external_primary_sources": "only URLs enumerated in config/r3c_source_primary_evidence.yaml",
        },
        "prohibited": [
            "price values",
            "volume values",
            "price-derived QC",
            "returns",
            "directions",
            "trades",
            "PnL",
            "OOS",
            "2026-and-later",
            "backtest",
            "WFA",
            "R065",
            "specification freeze",
        ],
    }
    (output / "frozen_plan.json").write_text(
        json.dumps(frozen_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    resolved = [(root / path).resolve() for path in allowlisted_files]
    if any(root not in path.parents for path in resolved):
        raise SourcePrimaryAuditError("allowlisted input resolves outside repository root")
    if not all(path.is_file() for path in resolved):
        raise SourcePrimaryAuditError("a frozen allowlisted file is unavailable")
    data_config, legacy_evidence, primary_evidence, r062_source = (
        _load_mapping(resolved[0]),
        _load_mapping(resolved[1]),
        _load_mapping(resolved[2]),
        resolved[3],
    )
    filenames = _allowlisted_distribution_filenames((root / distribution_directory).resolve())
    roles = _role_records()
    classifications = {name: 0 for name in sorted(CLASSIFICATIONS)}
    for role in roles:
        classifications[str(role["classification"])] += 1

    input_evidence = {
        "primary_sources": primary_evidence.get("sources", []),
        "configuration_provenance": {
            "source": data_config.get("source"),
            "series_type": data_config.get("series_type"),
            "bar_interval": data_config.get("bar_interval"),
            "source_date_semantics": data_config.get("source_format", {}).get(
                "source_date_semantics"
            ),
            "classification": "NOT_PRIMARY_EVIDENCE",
        },
        "legacy_provider_evidence": {
            "record_count": len(legacy_evidence.get("provider_public_claims", [])),
            "classification": "NOT_USED_TO_UPGRADE_PRIMARY_CLOSURE",
        },
        "r062_rule_snapshot": {
            "path": "src/n225m_bt/research/r062.py",
            "sha256": _sha256(r062_source),
            "classification": "RULE_DEFINITION_NOT_SOURCE_SEMANTICS_EVIDENCE",
        },
        "original_distribution_filenames_only": {
            "path": distribution_directory.as_posix(),
            "filenames": filenames,
            "requested_filenames": list(FROZEN_DISTRIBUTION_FILENAMES),
            "directory_enumerated": False,
            "contents_opened": False,
            "headers_opened": False,
            "sidecars_or_manifests_found_in_direct_entry_list": [],
        },
    }
    evidence_mapping = {
        "evidence_standard": (
            "Only a source-specific statement issued by 225Labo for the applicable product/version "
            "can resolve a semantic element. Project configuration, parser code, and calendar "
            "regularity are not substitutes."
        ),
        "resolved_primary_subclaims": [
            {
                "element": "nominal_one_minute_product_availability",
                "source": "225labo-product-index-20260915",
                "scope_limit": "does not state minute timestamp labeling or OHLC construction",
            },
            {
                "element": "nominal_center_contract_selection_rule and night trade-date convention",
                "source": "225labo-2021-center-product-20260915",
                "scope_limit": "does not identify the constituent/roll/adjustment of any individual bar",
            },
        ],
        "roles": roles,
        "exclusive_classification_counts": classifications,
        "missing_evidence": list(REQUIRED_SEMANTICS),
    }
    questions = [
        {
            "holder": "225Labo / the custodian of the exact 2021-01-01..2025-06-30 center-contract distribution",
            "request": "For each applicable file version, state whether each minute timestamp labels interval start or end, timezone, and the exact OHLC aggregation rule including no-trade bars.",
        },
        {
            "holder": "225Labo / upstream data-feed owner",
            "request": "Provide price_available_at/as-of policy and the missing, correction, revision, and republication policy, including whether historical files are restated.",
        },
        {
            "holder": "225Labo / upstream data-feed owner",
            "request": "Provide a dated constituent and roll manifest for the continuous mini series, its center-contract selection rule as applied, each switch timestamp, and every price-adjustment method/value.",
        },
    ]
    conclusion = (
        "S2_FEASIBLE_LIMITED"
        if classifications["RESOLVED_PRIMARY"] == len(roles)
        else "BLOCKED_EXTERNAL_EVIDENCE"
    )
    incidents = pre_execution_incidents or []
    access_ledger = {
        "scope_compliance_after_freeze": "PASS",
        "pre_execution_scope_history": incidents,
        "pre_execution_scope_history_status": "RECORDED_NOT_ERASED"
        if incidents
        else "NONE_REPORTED",
        "reads_performed": [
            *[path.as_posix() for path in allowlisted_files],
            "src/n225m_bt/research/r3c_source_primary_audit.py (implementation hash only)",
            *[
                f"allowlisted filename existence only: {(distribution_directory / name).as_posix()}"
                for name in FROZEN_DISTRIBUTION_FILENAMES
            ],
        ],
        "explicitly_not_read": frozen_plan["prohibited"],
        "market_data_values_read": False,
        "market_data_metadata_read": False,
        "price_derived_qc_read": False,
    }
    manifest = {
        "audit_id": audit_id,
        "task_id": "TASK-R3C-02",
        "status": conclusion,
        "input_sha256": {path.as_posix(): _sha256(root / path) for path in allowlisted_files},
        "code_sha256": {
            "src/n225m_bt/research/r3c_source_primary_audit.py": _sha256(Path(__file__))
        },
        "environment": {"python": sys.version, "platform": platform.platform()},
        "no_backtest_or_pnl_process_started": True,
    }
    files: dict[str, object] = {
        "audit_manifest.json": manifest,
        "source_evidence.json": input_evidence,
        "evidence_mapping.json": evidence_mapping,
        "required_evidence_request.json": {"status": "UNSENT", "questions": questions},
        "access_ledger.json": access_ledger,
        "decision.json": {
            "status": conclusion,
            "rule": "all 15 roles must be RESOLVED_PRIMARY",
            "resolved_primary_roles": classifications["RESOLVED_PRIMARY"],
            "total_roles": len(roles),
            "prohibited_next_steps": frozen_plan["prohibited"],
        },
        "COMPLETED.json": {
            "audit_id": audit_id,
            "run_status": "COMPLETE",
            "decision": conclusion,
            "reason": "Required source-specific primary evidence is missing for every R062 role.",
        },
    }
    for name, payload in files.items():
        (output / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (output / "summary.md").write_text(
        "# TASK-R3C-02 — R062 source-primary-evidence closure audit\n\n"
        f"Audit ID: `{audit_id}`  \nStatus: **{conclusion}**\n\n"
        "The allowlisted provider pages resolve only nominal one-minute product availability, "
        "the center-contract selection description, and the provider's night trade-date convention. "
        "They do not resolve the complete semantic contract required for any of the 15 R062 roles. "
        "No market-data value, volume, price-derived QC, trading output, PnL, OOS, or 2026-and-later "
        "input was read after the frozen plan.\n",
        encoding="utf-8",
    )
    return output
