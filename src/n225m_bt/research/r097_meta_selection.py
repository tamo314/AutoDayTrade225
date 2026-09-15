"""Frozen daily-net routing rules for TASK-R097-Q001.

The module deliberately accepts only already-audited, immutable daily-net paths.
It contains no market-data loading, event construction, or execution logic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

import numpy as np

LOOKBACK = 120
CALIBRATION_DAYS = 252
BLOCK_LENGTH = 20
BOOTSTRAP_REPETITIONS = 10_000
BOOTSTRAP_SEED = 20260915


@dataclass(frozen=True)
class SelectionRow:
    """One scheduled evaluation-date decision made before that date's event."""

    index: int
    selected_strategy_id: str | None
    rank_two_strategy_id: str | None
    scores: dict[str, float]


def select_paths(
    daily_net: Mapping[str, Sequence[int]],
    *,
    lookback: int = LOOKBACK,
    calibration_days: int = CALIBRATION_DAYS,
) -> tuple[list[SelectionRow], str]:
    """Route A/Q and choose causal S using strict-prior scheduled daily nets.

    A is cash unless its unique lexicographically tie-broken maximum score is
    strictly positive.  Q uses the second-ranked strategy only on those A
    non-cash days.  Scores always use [d-lookback, d), never date d.
    """
    strategy_ids = sorted(daily_net)
    if len(strategy_ids) < 2:
        raise ValueError("at least two strategies are required")
    lengths = {len(daily_net[strategy_id]) for strategy_id in strategy_ids}
    if len(lengths) != 1:
        raise ValueError("daily-net axes are not aligned")
    length = lengths.pop()
    if calibration_days < lookback or length <= calibration_days:
        raise ValueError("insufficient scheduled axis for calibration/evaluation")
    static_id = min(
        strategy_ids,
        key=lambda strategy_id: (-fmean(daily_net[strategy_id][:calibration_days]), strategy_id),
    )
    rows: list[SelectionRow] = []
    for index in range(calibration_days, length):
        scores = {
            strategy_id: fmean(daily_net[strategy_id][index - lookback : index])
            for strategy_id in strategy_ids
        }
        ranked = sorted(strategy_ids, key=lambda strategy_id: (-scores[strategy_id], strategy_id))
        selected = ranked[0] if scores[ranked[0]] > 0 else None
        rows.append(
            SelectionRow(
                index=index,
                selected_strategy_id=selected,
                rank_two_strategy_id=ranked[1] if selected is not None else None,
                scores=scores,
            )
        )
    return rows, static_id


def routed_daily_net(
    rows: Sequence[SelectionRow], daily_net: Mapping[str, Sequence[int]], *, static_id: str
) -> tuple[list[int], list[int], list[int]]:
    """Return A, rank-two Q, and static S daily-net paths on the evaluation axis."""
    a: list[int] = []
    q: list[int] = []
    s: list[int] = []
    for row in rows:
        a.append(
            0
            if row.selected_strategy_id is None
            else daily_net[row.selected_strategy_id][row.index]
        )
        q.append(
            0
            if row.rank_two_strategy_id is None
            else daily_net[row.rank_two_strategy_id][row.index]
        )
        s.append(daily_net[static_id][row.index])
    return a, q, s


def mbb_indices(
    length: int,
    *,
    seed: int = BOOTSTRAP_SEED,
    repetitions: int = BOOTSTRAP_REPETITIONS,
    block_length: int = BLOCK_LENGTH,
) -> np.ndarray:
    """Common-index non-wrapping moving-block bootstrap, tail-truncated."""
    if length < block_length:
        raise ValueError("daily series is shorter than moving-block length")
    blocks = -(-length // block_length)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, length - block_length + 1, size=(repetitions, blocks))
    return (starts[:, :, None] + np.arange(block_length)).reshape(repetitions, -1)[:, :length]


def percentile_ci(values: np.ndarray) -> list[float]:
    """Frozen two-sided 95% linear-percentile interval."""
    low, high = np.quantile(values, (0.025, 0.975), method="linear")
    return [float(low), float(high)]
