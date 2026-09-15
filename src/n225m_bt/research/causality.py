"""Synthetic, decision-time causality assertions for the R1 audit path."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """State visible at a decision boundary; outcomes are intentionally separate."""

    decision_id: str
    decision_at: datetime
    rolling_u: Mapping[str, object]
    exec_eligibility: Mapping[str, object]
    signal: Mapping[str, object] | None
    order: Mapping[str, object] | None
    analysis_eligibility: Mapping[str, object]
    outcome: Mapping[str, object]


class FutureInformationLeakError(AssertionError):
    """A mutation after a decision altered information that must have been as-of."""


def assert_prefix_invariant(
    before: tuple[DecisionSnapshot, ...],
    after: tuple[DecisionSnapshot, ...],
    cutoff: datetime,
) -> None:
    """Require U/E_exec/signal/order equality through ``cutoff``.

    ``analysis_eligibility`` and ``outcome`` are excluded on purpose: later
    fill, exit, or observation changes may legitimately affect them.
    """
    before_by_id = {item.decision_id: item for item in before if item.decision_at <= cutoff}
    after_by_id = {item.decision_id: item for item in after if item.decision_at <= cutoff}
    if before_by_id.keys() != after_by_id.keys():
        raise FutureInformationLeakError("decision population changed before cutoff")
    fields = ("rolling_u", "exec_eligibility", "signal", "order")
    for decision_id, original in before_by_id.items():
        changed = after_by_id[decision_id]
        if original.decision_at != changed.decision_at:
            raise FutureInformationLeakError(f"decision timestamp changed: {decision_id}")
        for field in fields:
            if getattr(original, field) != getattr(changed, field):
                raise FutureInformationLeakError(
                    f"future mutation changed {field} before cutoff: {decision_id}"
                )
