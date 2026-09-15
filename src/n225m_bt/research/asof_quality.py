"""Decision-time quality filtering for new R1 research paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class QualityObservation:
    """A quality fact with distinct availability and later detection timestamps."""

    available_at: datetime
    detected_at: datetime
    blocks_decision: bool
    reason: str


def quality_allows_decision(
    decision_at: datetime, observations: tuple[QualityObservation, ...]
) -> tuple[bool, tuple[str, ...]]:
    """Use facts available at the decision, never later correction labels."""
    known = tuple(item for item in observations if item.available_at <= decision_at)
    blocks = tuple(item.reason for item in known if item.blocks_decision)
    return not blocks, blocks
