"""Causal first-candidate selection for multi-anchor R1 decision runners."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CandidateIntent:
    condition_id: str
    anchor_id: str
    decision_at: datetime
    selected: bool


def first_selected_by_condition(
    candidates: Iterable[CandidateIntent],
) -> dict[str, CandidateIntent]:
    """Select the first eligible anchor independently for every condition."""
    selected: dict[str, CandidateIntent] = {}
    for candidate in sorted(candidates, key=lambda item: item.decision_at):
        if candidate.selected and candidate.condition_id not in selected:
            selected[candidate.condition_id] = candidate
    return selected


def retry_allowed_after_cancellation(policy: str) -> bool:
    """No silent retry: a cancellation needs an explicit frozen policy."""
    if policy not in {"no_retry", "retry_next_anchor"}:
        raise ValueError("unknown cancellation retry policy")
    return policy == "retry_next_anchor"
