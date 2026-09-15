"""Non-market-data R2 audit for source semantics and price lineage evidence."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REQUIRED_PROVIDER_EVIDENCE = (
    "bar_interval_label",
    "ohlc_meaning",
    "volume_unit_and_non_cumulative",
    "missing_and_correction_policy",
    "contract_identity_and_roll",
    "adjustment_method",
    "price_available_at",
)


class SemanticsAuditError(ValueError):
    """The non-market configuration cannot be audited safely."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_declared_source_semantics(
    config_path: Path,
    audit_id: str,
    commit: str,
    evidence_path: Path | None = None,
    *,
    accept_unresolved_source_semantics: bool = False,
) -> dict[str, object]:
    """Record configuration claims without treating them as provider evidence."""
    module_path = Path(__file__)
    cli_path = module_path.parents[1] / "cli.py"
    payload: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SemanticsAuditError("data configuration must be a mapping")
    source = payload.get("source")
    source_format = payload.get("source_format")
    if not isinstance(source, str) or not isinstance(source_format, dict):
        raise SemanticsAuditError("data configuration lacks source/source_format")
    claims = {
        "source": source,
        "series_type": payload.get("series_type"),
        "bar_interval": payload.get("bar_interval"),
        "source_date_semantics": source_format.get("source_date_semantics"),
        "configured_columns": source_format.get("source_mapping"),
    }
    public_evidence: list[object] = []
    if evidence_path is not None:
        sidecar: Any = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
        if not isinstance(sidecar, dict) or not isinstance(sidecar.get("provider_public_claims"), list):
            raise SemanticsAuditError("source semantics evidence must declare provider_public_claims")
        public_evidence = sidecar["provider_public_claims"]
    missing = list(REQUIRED_PROVIDER_EVIDENCE)
    if public_evidence:
        missing.remove("bar_interval_label")
    quality_status = "PASS_LIMITED" if accept_unresolved_source_semantics else "BLOCKED"
    evidence = {
        "status": quality_status,
        "evidence_level": "CONFIG_DECLARATION_ONLY",
        "claims": claims,
        "provider_public_evidence": public_evidence,
        "required_provider_evidence_missing": missing,
        "reason": (
            "OWNER_ACCEPTED_UNRESOLVED_SOURCE_SEMANTICS_FOR_LIMITED_DEVELOPMENT"
            if accept_unresolved_source_semantics
            else "configuration is not evidence of the supplier's source semantics"
        ),
    }
    return {
        "audit_manifest": {
            "audit_id": audit_id,
            "scope": "R2 source semantics and price lineage; no market-data read",
            "commit": commit,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "environment": {"python": sys.version, "platform": platform.platform()},
            "input_sha256": {
                str(config_path): _sha256(config_path),
                **({str(evidence_path): _sha256(evidence_path)} if evidence_path else {}),
            },
            "code_sha256": {
                "src/n225m_bt/research/semantics_audit.py": _sha256(module_path),
                "src/n225m_bt/cli.py": _sha256(cli_path),
            },
            "market_data_access": False,
            "risk_acceptance": {
                "accepted": accept_unresolved_source_semantics,
                "scope": "limited Development data audit only",
                "does_not_establish": [
                    "supplier bar semantics",
                    "contract-level execution interpretation",
                    "OOS eligibility",
                    "live or paper execution eligibility",
                ],
            },
        },
        "source_semantics_evidence": evidence,
        "price_lineage_audit": {
            "status": "BLOCKED",
            "series_type": claims["series_type"],
            "contract_month": "UNKNOWN_FOR_CONTINUOUS_SERIES",
            "roll_observation_status": "UNKNOWN",
            "adjustment_method": "UNKNOWN",
            "reason": "no provider contract/roll/adjustment evidence was supplied",
        },
        "roll_constituents_evidence": {
            "status": "BLOCKED",
            "reason": "continuous-series constituents and switch observations are absent",
        },
        "availability_audit": {
            "status": "BLOCKED",
            "price_available_at": "UNKNOWN",
            "volume_available_at": "UNKNOWN",
            "quality_available_at": "UNKNOWN",
            "reason": "bar label does not establish publication/decision availability",
        },
        "causal_quality_audit": {
            "status": "BLOCKED",
            "reason": "QUALITY_AVAILABLE_AT_UNPROVEN",
            "scope": "R2 data semantics and price lineage audit",
            "market_data_read": False,
            "finding": (
                "The provider has not supplied a quality publication-time or correction "
                "policy. Quality labels therefore cannot be shown to be available at a "
                "decision cutoff."
            ),
            "blocked_dependency": "supplier_or_feed_semantics_evidence",
        },
        "outcome_missingness": {
            "status": "NOT_RUN",
            "reason": "R2_SEMANTICS_AUDIT_DOES_NOT_READ_MARKET_PRICES_OR_OUTCOMES",
            "market_data_read": False,
            "required_before": "Development or OOS outcome analysis",
        },
        "access_ledger": {
            "status": "PASS",
            "scope": "R2 source semantics audit only",
            "permitted_reads": ["configuration", "public-evidence sidecar", "source code"],
            "prohibited_and_not_read": [
                "raw market prices",
                "Silver or Gold price data",
                "features",
                "price-derived quality caches",
                "prior run trades, PnL, or bootstrap values",
                "OOS and Final Holdout market inputs",
            ],
            "evidence": "runner accepts only --config and --evidence; no data-path option exists",
        },
        "quality_gate": {
            "data_quality": quality_status,
            "basis": (
                "OWNER_RISK_ACCEPTANCE_RECORDED_UNKNOWN"
                if accept_unresolved_source_semantics
                else "SUPPLIER_OR_FEED_SEMANTICS_EVIDENCE_REQUIRED"
            ),
            "allowed_use": (
                ["documentation", "synthetic tests", "source-evidence acquisition planning"]
                if not accept_unresolved_source_semantics
                else [
                    "documentation",
                    "synthetic tests",
                    "source-evidence acquisition planning",
                    "limited Development data-quality and fixed-spec diagnostic audit",
                ]
            ),
            "prohibited_use": ["OOS", "Final Holdout", "candidate selection", "execution claims"],
            "blocked_dependency": None if accept_unresolved_source_semantics else "supplier_or_feed_semantics_evidence",
            "unresolved_risks": missing,
            "reopen_condition": "source-semantics-related anomaly during a Development data audit",
        },
    }


def write_semantics_audit(
    config_path: Path,
    output: Path,
    audit_id: str,
    commit: str,
    evidence_path: Path | None = None,
    *,
    accept_unresolved_source_semantics: bool = False,
) -> Path:
    """Write a fresh R2 audit without opening raw, Silver, Gold, or results data."""
    records = audit_declared_source_semantics(
        config_path,
        audit_id,
        commit,
        evidence_path,
        accept_unresolved_source_semantics=accept_unresolved_source_semantics,
    )
    quality_status = "PASS_LIMITED" if accept_unresolved_source_semantics else "BLOCKED"
    output.mkdir(parents=True, exist_ok=False)
    for name, value in records.items():
        (output / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
    (output / "summary.md").write_text(
        "# R2 source semantics audit\n\n"
        f"Status: {quality_status}. "
        "Configuration declarations were not promoted to supplier data semantics.\n",
        encoding="utf-8",
    )
    return output
