"""Explicit condition and gate definitions for new, frozen research specifications.

This module is deliberately additive: it does not reinterpret or mutate legacy
study dictionaries.  New specifications must construct a condition matrix
before a runner can resolve selections or orders from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from n225m_bt.domain import Side


class SetRelation(StrEnum):
    ROOT = "root"
    SUBSET = "subset"
    EQUAL = "equal"
    DISJOINT = "disjoint"


class GateOperator(StrEnum):
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="


@dataclass(frozen=True, slots=True)
class ConditionDefinition:
    """A fully explicit condition; ``None`` is an explicit no-quantile value."""

    condition_id: str
    side: Side
    quantile: int | None
    relation: SetRelation
    parent_condition_id: str | None
    first_event_only: bool


@dataclass(frozen=True, slots=True)
class GateRequirement:
    """A numeric requirement with enough identity to check shared-path logic."""

    requirement_id: str
    path_id: str
    event_set_id: str
    weighting_id: str
    side: Side
    metric_id: str
    slippage_ticks_per_side: int
    fee_jpy_per_side: int
    operator: GateOperator
    threshold: float


class ConditionSpecificationError(ValueError):
    """The specification is incomplete or contradictory before a price read."""


def validate_condition_matrix(conditions: tuple[ConditionDefinition, ...]) -> None:
    """Reject ambiguous condition wiring without applying any legacy default."""
    if not conditions:
        raise ConditionSpecificationError("at least one explicit condition is required")
    identifiers = [condition.condition_id for condition in conditions]
    if any(not identifier for identifier in identifiers):
        raise ConditionSpecificationError("condition_id must be nonempty")
    if len(set(identifiers)) != len(identifiers):
        raise ConditionSpecificationError("condition_id must be unique")
    known = set(identifiers)
    for condition in conditions:
        if condition.quantile is not None and not 0 < condition.quantile < 100:
            raise ConditionSpecificationError("quantile must be between 1 and 99 when specified")
        if condition.relation is SetRelation.ROOT and condition.parent_condition_id is not None:
            raise ConditionSpecificationError("root condition cannot inherit a parent")
        if condition.relation is not SetRelation.ROOT:
            if condition.parent_condition_id is None:
                raise ConditionSpecificationError("non-root condition requires an explicit parent")
            if condition.parent_condition_id not in known:
                raise ConditionSpecificationError("condition parent is absent from the matrix")
            if condition.parent_condition_id == condition.condition_id:
                raise ConditionSpecificationError("condition cannot be its own parent")


def assert_gate_satisfiable(requirements: tuple[GateRequirement, ...]) -> None:
    """Reject the R031-type impossible AND before any market-data access.

    On one shared event path with identical weights, long and short 0-tick,
    pre-fee gross values sum to zero.  Both directional means therefore cannot
    be required to be strictly negative at the same time.
    """
    if len({item.requirement_id for item in requirements}) != len(requirements):
        raise ConditionSpecificationError("gate requirement_id must be unique")
    groups: dict[tuple[str, str, str], list[GateRequirement]] = {}
    for requirement in requirements:
        groups.setdefault(
            (requirement.path_id, requirement.event_set_id, requirement.weighting_id), []
        ).append(requirement)
    for shared_path, items in groups.items():
        negative_sides = {
            item.side
            for item in items
            if item.metric_id == "gross_pre_fee_mean_jpy"
            and item.slippage_ticks_per_side == 0
            and item.fee_jpy_per_side == 0
            and item.operator is GateOperator.LESS_THAN
            and item.threshold == 0
        }
        if negative_sides == {Side.LONG, Side.SHORT}:
            raise ConditionSpecificationError(
                "unsatisfiable shared-path opposite-side 0-tick pre-fee negative means: "
                f"path={shared_path[0]} event_set={shared_path[1]} weighting={shared_path[2]}"
            )


def shared_path_gross_witness(
    signed_price_changes: tuple[int, ...], multiplier_jpy_per_point: int
) -> dict[str, float]:
    """Construct a finite, shared-price witness for 0-tick pre-fee gates.

    It is deliberately only a test witness, not a return calculator for a
    study.  Long and short are derived from the same path, preserving the
    identity required by the R031 logical audit.
    """
    if not signed_price_changes or multiplier_jpy_per_point <= 0:
        raise ValueError("witness needs price changes and a positive multiplier")
    long_mean = sum(signed_price_changes) * multiplier_jpy_per_point / len(signed_price_changes)
    return {
        "long_gross_pre_fee_mean_jpy": long_mean,
        "short_gross_pre_fee_mean_jpy": -long_mean,
    }
