"""Fail-closed access and identity contracts for new research paths only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResearchStage(StrEnum):
    S0_DESIGN_AUDIT = "S0"
    S1_INPUT_IMPLEMENTATION_AUDIT = "S1"
    S2_FEASIBILITY_CALIBRATION = "S2"
    S3_DEVELOPMENT = "S3"
    S4_TIME_STABILITY = "S4"
    S5_OOS_REVIEW = "S5"
    S6_OOS = "S6"


class MarketSplit(StrEnum):
    DEVELOPMENT = "development"
    OOS = "out_of_sample"
    FINAL_HOLDOUT = "final_holdout"


@dataclass(frozen=True, slots=True)
class StudyIdentity:
    family_id: str
    study_id: str
    spec_version: str
    run_id: str
    protocol_revision: str


@dataclass(frozen=True, slots=True)
class AccessRequest:
    identity: StudyIdentity
    stage: ResearchStage
    split: MarketSplit
    preregistration_hash: str
    access_plan_hash: str | None = None
    oos_opening_approved: bool = False


class AccessDeniedError(PermissionError):
    """A protected split was requested without the required frozen evidence."""


def validate_identity(identity: StudyIdentity) -> None:
    values = (
        identity.family_id,
        identity.study_id,
        identity.spec_version,
        identity.run_id,
        identity.protocol_revision,
    )
    if any(not value for value in values):
        raise ValueError("family/study/spec/run/protocol identifiers must all be explicit")


def authorize_market_access(request: AccessRequest) -> None:
    """Apply the revised period lock without changing legacy loader behavior."""
    validate_identity(request.identity)
    if not request.preregistration_hash:
        raise AccessDeniedError("content-addressed preregistration is required")
    if request.split is MarketSplit.FINAL_HOLDOUT:
        raise AccessDeniedError("Final Holdout is locked")
    if request.split is MarketSplit.OOS:
        if request.stage is not ResearchStage.S6_OOS:
            raise AccessDeniedError("OOS access requires S6")
        if not request.oos_opening_approved or not request.access_plan_hash:
            raise AccessDeniedError("OOS access requires approved access plan and opening approval")
    elif request.stage not in {ResearchStage.S2_FEASIBILITY_CALIBRATION, ResearchStage.S3_DEVELOPMENT, ResearchStage.S4_TIME_STABILITY}:
        raise AccessDeniedError("Development price access is not permitted at this stage")
