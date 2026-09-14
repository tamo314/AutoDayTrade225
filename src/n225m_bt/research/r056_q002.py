"""Fixed-design block-wild score inference for the R056-Q002 delta coefficient."""

from __future__ import annotations

from collections.abc import Sequence
from random import Random

import numpy as np
from numpy.typing import NDArray

from n225m_bt.research.r053 import fwl_delta

SCORE_BOOTSTRAP_SEED = 20261004
BLOCK_TRADE_DATES = 20
REPETITIONS = 10_000


def _blocks(axis_length: int, block_length: int) -> list[tuple[int, int]]:
    """Partition the immutable day axis, retaining its final short block."""
    if axis_length <= 0 or block_length <= 0:
        raise ValueError("axis_length and block_length must be positive")
    return [(start, min(start + block_length, axis_length)) for start in range(0, axis_length, block_length)]


def fixed_design_score_bootstrap(
    q: NDArray[np.float64],
    y: NDArray[np.float64],
    nuisance: NDArray[np.float64],
    regression_days: Sequence[str],
    axis: Sequence[str],
    *,
    seed: int = SCORE_BOOTSTRAP_SEED,
    block_length: int = BLOCK_TRADE_DATES,
    repetitions: int = REPETITIONS,
) -> tuple[float, float, NDArray[np.float64], dict[str, object]]:
    """Return R056-Q002's observed delta and fixed-design wild-score errors.

    The design, residualized Q, model residuals and denominator are calculated once.
    Bootstrap draws only assign independent Rademacher signs to consecutive immutable
    trade-date blocks; zero-event axis dates therefore retain their zero score.
    """
    if len(q) != len(y) or len(q) != len(regression_days) or nuisance.shape[0] != len(q):
        raise ValueError("R056-Q002 FWL inputs have incompatible dimensions")
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    point, denominator = fwl_delta(
        q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12
    )
    q_tilde = q - nuisance @ (np.linalg.pinv(nuisance, rcond=1e-12) @ q)
    full_design = np.column_stack((q, nuisance))
    full_residual = y - full_design @ (np.linalg.pinv(full_design, rcond=1e-12) @ y)
    by_day = dict.fromkeys(axis, 0.0)
    for index, day in enumerate(regression_days):
        if day not in by_day:
            raise ValueError(f"regression day outside fixed axis: {day}")
        by_day[day] += float(q_tilde[index] * full_residual[index])
    day_scores = np.asarray([by_day[day] for day in axis], dtype=float)
    blocks = _blocks(len(axis), block_length)
    errors = np.empty(repetitions, dtype=float)
    rng = Random(seed)
    for replicate in range(repetitions):
        numerator = sum(
            (1 if rng.randrange(2) else -1) * float(day_scores[start:end].sum())
            for start, end in blocks
        )
        errors[replicate] = numerator / denominator
    audit: dict[str, object] = {
        "method": "fixed-design 20 trade_date block-wild score bootstrap",
        "seed": seed,
        "repetitions": repetitions,
        "block_length_trade_dates": block_length,
        "fixed_axis_trade_dates": len(axis),
        "block_count": len(blocks),
        "tail_block_trade_dates": blocks[-1][1] - blocks[-1][0],
        "non_event_axis_dates_retained": sum(day not in set(regression_days) for day in axis),
        "q_residual_sum_of_squares": denominator,
        "q_tilde": q_tilde.tolist(),
        "full_model_residual": full_residual.tolist(),
        "day_scores": day_scores.tolist(),
    }
    return point, denominator, errors, audit
