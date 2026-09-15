"""Causal routing for the frozen TASK-R098-Q001 state-conditioned meta test."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

import numpy as np
from numpy.typing import NDArray

CALIBRATION_DAYS = 252
STATE_LOOKBACK_OBSERVATIONS = 60
MAX_SCHEDULED_LOOKBACK_DAYS = 360
UNCONDITIONAL_LOOKBACK_DAYS = 120
BLOCK_LENGTH = 20
BOOTSTRAP_REPETITIONS = 10_000
BOOTSTRAP_SEED = 20260916
STATES = ("L", "M", "H")


@dataclass(frozen=True)
class StateSelectionRow:
    """One score-ready, state-known date selected using strict-prior daily nets."""

    index: int
    state: str
    matching_prior_indices: tuple[int, ...]
    selected_strategy_id: str | None
    rank_two_strategy_id: str | None
    static_strategy_id: str | None
    unconditional_strategy_id: str | None
    state_scores: dict[str, float]
    unconditional_scores: dict[str, float]


def _validate(daily_net: Mapping[str, Sequence[int]], states: Sequence[str | None]) -> list[str]:
    strategy_ids = sorted(daily_net)
    if len(strategy_ids) < 2:
        raise ValueError("at least two strategies are required")
    lengths = {len(daily_net[strategy_id]) for strategy_id in strategy_ids}
    if len(lengths) != 1 or lengths.pop() != len(states):
        raise ValueError("daily-net axes and state axis must be aligned")
    if len(states) <= CALIBRATION_DAYS:
        raise ValueError("insufficient scheduled axis for calibration/evaluation")
    if any(state not in {*STATES, None} for state in states):
        raise ValueError("unregistered state")
    return strategy_ids


def state_static_choices(
    daily_net: Mapping[str, Sequence[int]], states: Sequence[str | None]
) -> dict[str, str | None]:
    """Choose each static state route from calibration only, or leave it cash."""
    strategy_ids = _validate(daily_net, states)
    choices: dict[str, str | None] = {}
    for state in STATES:
        indices = [index for index in range(CALIBRATION_DAYS) if states[index] == state]
        if not indices:
            choices[state] = None
            continue
        scores = {
            strategy_id: fmean(daily_net[strategy_id][index] for index in indices)
            for strategy_id in strategy_ids
        }
        best = min(strategy_ids, key=lambda strategy_id: (-scores[strategy_id], strategy_id))
        choices[state] = best if scores[best] > 0 else None
    return choices


def select_state_paths(
    daily_net: Mapping[str, Sequence[int]],
    states: Sequence[str | None],
    *,
    state_observations: int = STATE_LOOKBACK_OBSERVATIONS,
    max_scheduled_lookback: int = MAX_SCHEDULED_LOOKBACK_DAYS,
) -> tuple[list[StateSelectionRow], dict[str, str | None], dict[int, str]]:
    """Route A/Q/S/U with state scores that exclude the current scheduled day."""
    strategy_ids = _validate(daily_net, states)
    if state_observations not in {40, 60, 80}:
        raise ValueError("unregistered state observation count")
    if max_scheduled_lookback != MAX_SCHEDULED_LOOKBACK_DAYS:
        raise ValueError("unregistered maximum scheduled lookback")
    static_choices = state_static_choices(daily_net, states)
    unavailable: dict[int, str] = {}
    rows: list[StateSelectionRow] = []
    for index in range(CALIBRATION_DAYS, len(states)):
        state = states[index]
        if state is None:
            unavailable[index] = "STATE_UNAVAILABLE"
            continue
        matching = tuple(
            prior
            for prior in range(max(0, index - max_scheduled_lookback), index)
            if states[prior] == state
        )
        matching = matching[-state_observations:]
        state_scores: dict[str, float] = {}
        selected: str | None = None
        rank_two: str | None = None
        if len(matching) == state_observations:
            state_scores = {
                strategy_id: fmean(daily_net[strategy_id][prior] for prior in matching)
                for strategy_id in strategy_ids
            }
            ranked = sorted(
                strategy_ids, key=lambda strategy_id: (-state_scores[strategy_id], strategy_id)
            )
            selected = ranked[0] if state_scores[ranked[0]] > 0 else None
            rank_two = ranked[1] if state_scores[ranked[1]] > 0 else None
        unconditional_scores = {
            strategy_id: fmean(daily_net[strategy_id][index - UNCONDITIONAL_LOOKBACK_DAYS : index])
            for strategy_id in strategy_ids
        }
        universal_ranked = sorted(
            strategy_ids, key=lambda strategy_id: (-unconditional_scores[strategy_id], strategy_id)
        )
        unconditional = (
            universal_ranked[0] if unconditional_scores[universal_ranked[0]] > 0 else None
        )
        rows.append(
            StateSelectionRow(
                index=index,
                state=state,
                matching_prior_indices=matching,
                selected_strategy_id=selected,
                rank_two_strategy_id=rank_two,
                static_strategy_id=static_choices[state],
                unconditional_strategy_id=unconditional,
                state_scores=state_scores,
                unconditional_scores=unconditional_scores,
            )
        )
    return rows, static_choices, unavailable


def routed_daily_net(
    rows: Sequence[StateSelectionRow], daily_net: Mapping[str, Sequence[int]]
) -> tuple[list[int], list[int], list[int], list[int]]:
    """Return the scheduled-date A/Q/S/U paths for the already selected rows."""
    paths: list[list[int]] = [[], [], [], []]
    for row in rows:
        for target, strategy_id in zip(
            paths,
            (
                row.selected_strategy_id,
                row.rank_two_strategy_id,
                row.static_strategy_id,
                row.unconditional_strategy_id,
            ),
            strict=True,
        ):
            target.append(0 if strategy_id is None else daily_net[strategy_id][row.index])
    return paths[0], paths[1], paths[2], paths[3]


def mbb_indices(
    length: int,
    *,
    seed: int = BOOTSTRAP_SEED,
    repetitions: int = BOOTSTRAP_REPETITIONS,
    block_length: int = BLOCK_LENGTH,
) -> NDArray[np.int64]:
    """Return common, non-wrapping, tail-truncated moving-block indices."""
    if length < block_length:
        raise ValueError("daily series is shorter than moving-block length")
    blocks = -(-length // block_length)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, length - block_length + 1, size=(repetitions, blocks))
    return (starts[:, :, None] + np.arange(block_length)).reshape(repetitions, -1)[:, :length]


def percentile_ci(values: NDArray[np.float64]) -> list[float]:
    """Frozen two-sided 95% linear-percentile interval."""
    low, high = np.quantile(values, (0.025, 0.975), method="linear")
    return [float(low), float(high)]
