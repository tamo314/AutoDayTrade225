"""Causal state construction and fixed inference for TASK-R099-Q002."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Literal

import numpy as np
from numpy.typing import NDArray

BASE_SCHEDULED_WINDOW = 180
BASE_HISTORY_COUNT = 100
BLOCK_LENGTH = 20
BOOTSTRAP_REPETITIONS = 10_000
BOOTSTRAP_SEED = 20260916

State = Literal["HE", "ME", "LE"]


@dataclass(frozen=True)
class NightMeasure:
    efficiency: float
    range_points: int


@dataclass(frozen=True)
class StateRow:
    index: int
    state: State | None
    v_band: State | None
    references: tuple[int, ...]
    reason: str


def nearest_rank(values: Sequence[float], percentile: int) -> float:
    """Return the registered nearest-rank threshold without interpolation."""
    if not values or percentile not in {25, 33, 40, 60, 67, 75}:
        raise ValueError("unregistered percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def state_rows(
    measures: Sequence[NightMeasure | None],
    *,
    scheduled_window: int = BASE_SCHEDULED_WINDOW,
    history_count: int = BASE_HISTORY_COUNT,
    low: int = 33,
    high: int = 67,
) -> list[StateRow]:
    """Use the latest fixed count of complete nights from a strict-prior window."""
    allowed_profiles = {
        (180, 100),
        (180, 80),
        (180, 120),
        (150, 100),
        (210, 100),
    }
    if (scheduled_window, history_count) not in allowed_profiles or (low, high) not in {
        (25, 75),
        (33, 67),
        (40, 60),
    }:
        raise ValueError("unregistered R099-Q002 state profile")
    rows: list[StateRow] = []
    for index, current in enumerate(measures):
        valid = [
            prior
            for prior in range(max(0, index - scheduled_window), index)
            if measures[prior] is not None
        ]
        references = tuple(valid[-history_count:])
        if current is None:
            rows.append(StateRow(index, None, None, references, "CURRENT_NIGHT_UNAVAILABLE"))
            continue
        if len(valid) < history_count:
            rows.append(
                StateRow(index, None, None, references, "INSUFFICIENT_COMPLETE_PRIOR_NIGHTS")
            )
            continue
        reference_measures = [measures[prior] for prior in references]
        complete_references = [item for item in reference_measures if item is not None]
        efficiency = [item.efficiency for item in complete_references]
        ranges = [float(item.range_points) for item in complete_references]
        q_low, q_high = nearest_rank(efficiency, low), nearest_rank(efficiency, high)
        v_low, v_high = nearest_rank(ranges, 33), nearest_rank(ranges, 67)
        if q_low >= q_high:
            rows.append(StateRow(index, None, None, references, "EFFICIENCY_QUANTILES_DEGENERATE"))
            continue
        state: State = "LE" if current.efficiency <= q_low else "HE" if current.efficiency >= q_high else "ME"
        v_band: State = "LE" if current.range_points <= v_low else "HE" if current.range_points >= v_high else "ME"
        rows.append(StateRow(index, state, v_band, references, "STATE_AVAILABLE"))
    return rows


def route(state: State | None, name: str) -> str | None:
    """Route the fixed R078/R079 pair; no non-extreme fallback is permitted."""
    if state not in {"HE", "LE"}:
        return None
    if name == "A":
        return "R079-A" if state == "HE" else "R078-A"
    if name == "C":
        return "R079-A"
    if name == "F":
        return "R078-A"
    if name == "I":
        return "R078-A" if state == "HE" else "R079-A"
    raise ValueError("unregistered route")


def mbb_indices(length: int) -> NDArray[np.int64]:
    """Frozen common-index non-wrapping, tail-truncated moving-block bootstrap."""
    if length < BLOCK_LENGTH:
        raise ValueError("axis shorter than block length")
    blocks = -(-length // BLOCK_LENGTH)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    starts = rng.integers(0, length - BLOCK_LENGTH + 1, size=(BOOTSTRAP_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(BLOCK_LENGTH)).reshape(BOOTSTRAP_REPETITIONS, -1)[:, :length]


def percentile_ci(values: NDArray[np.float64]) -> list[float]:
    low, high = np.quantile(values, (0.025, 0.975), method="linear")
    return [float(low), float(high)]
