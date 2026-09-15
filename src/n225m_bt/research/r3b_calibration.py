"""Synthetic-only calibration for the R3-B finite-batch gate procedure.

The calibration deliberately operates on a synthetic scheduled axis and
synthetic gross/fill/net observations.  It does not import a market loader,
open an existing run's trade ledger, or invoke the production execution
engine.  Consequently its result is evidence about the *method* (axis,
accounting, bootstrap, and AND gates), never evidence that a market order
would have filled.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt
from typing import Literal, cast

import numpy as np

ScenarioName = Literal["gross_zero", "net_zero", "hypothetical_minimum_net"]


@dataclass(frozen=True, slots=True)
class CalibrationPlan:
    """Fixed simulation and inference inputs for a method-only calibration."""

    axis_length: int = 1111
    repetitions: int = 400
    bootstrap_repetitions: int = 400
    block_length: int = 20
    seed: int = 20260915
    alpha: float = 0.05
    target_false_positive_rate: float = 0.05
    target_power: float = 0.80
    minimum_a_trades: int = 70
    minimum_c_trades: int = 70
    cost_jpy_round_trip: int = 1060
    hypothetical_minimum_net_jpy_per_trade: int = 1500
    innovation_scale_jpy: int = 1500
    ar1: float = 0.35
    student_t_df: int = 5

    def validate(self) -> None:
        if self.axis_length <= 0 or self.repetitions <= 0 or self.bootstrap_repetitions <= 0:
            raise ValueError("axis and repetition counts must be positive")
        if not 0 < self.block_length <= self.axis_length:
            raise ValueError("block_length must be within the scheduled axis")
        if not 0 < self.alpha < 1 or not 0 < self.target_false_positive_rate < 1:
            raise ValueError("alpha and false-positive target must be probabilities")
        if not 0 < self.target_power <= 1:
            raise ValueError("target power must be a probability")
        if self.cost_jpy_round_trip <= 0 or self.hypothetical_minimum_net_jpy_per_trade <= 0:
            raise ValueError("cost and hypothetical effect must be positive")
        if not 0 <= self.ar1 < 1 or self.student_t_df <= 2:
            raise ValueError("AR(1) and tail parameters are invalid")


@dataclass(frozen=True, slots=True)
class GateResult:
    """One finite-axis evaluation before its Monte Carlo aggregation."""

    a_trade_count: int
    c_trade_count: int
    main_daily_mean_jpy: float
    main_ci95: tuple[float, float]
    contrast_daily_mean_jpy: float
    contrast_ci95: tuple[float, float]
    identified: bool
    passed: bool


def moving_block_ci(
    values: np.ndarray,
    *,
    repetitions: int,
    block_length: int,
    alpha: float,
    rng: np.random.Generator,
) -> tuple[float, float]:
    """Percentile MBB CI for the mean with no wrap and tail truncation."""
    if values.ndim != 1 or len(values) < block_length:
        raise ValueError("values must be one-dimensional and at least one block long")
    blocks = ceil(len(values) / block_length)
    starts = rng.integers(0, len(values) - block_length + 1, size=(repetitions, blocks))
    offsets = np.arange(block_length, dtype=np.int64)
    indices = (starts[:, :, None] + offsets).reshape(repetitions, -1)[:, : len(values)]
    means = values[indices].mean(axis=1)
    lower, upper = np.quantile(means, (alpha / 2, 1 - alpha / 2))
    return float(lower), float(upper)


def evaluate_gate(
    a_daily_net: np.ndarray,
    c_daily_net: np.ndarray,
    *,
    a_trade_count: int,
    c_trade_count: int,
    plan: CalibrationPlan,
    rng: np.random.Generator,
) -> GateResult:
    """Apply the fixed main and co-primary gates on the common daily axis."""
    if a_daily_net.shape != c_daily_net.shape or a_daily_net.ndim != 1:
        raise ValueError("A and C require the same one-dimensional scheduled axis")
    main_ci = moving_block_ci(
        a_daily_net,
        repetitions=plan.bootstrap_repetitions,
        block_length=plan.block_length,
        alpha=plan.alpha,
        rng=rng,
    )
    contrast = a_daily_net - c_daily_net
    contrast_ci = moving_block_ci(
        contrast,
        repetitions=plan.bootstrap_repetitions,
        block_length=plan.block_length,
        alpha=plan.alpha,
        rng=rng,
    )
    identified = a_trade_count >= plan.minimum_a_trades and c_trade_count >= plan.minimum_c_trades
    passed = identified and main_ci[0] > 0 and contrast_ci[0] > 0
    return GateResult(
        a_trade_count=a_trade_count,
        c_trade_count=c_trade_count,
        main_daily_mean_jpy=float(a_daily_net.mean()),
        main_ci95=main_ci,
        contrast_daily_mean_jpy=float(contrast.mean()),
        contrast_ci95=contrast_ci,
        identified=identified,
        passed=passed,
    )


def _dependent_noise(plan: CalibrationPlan, rng: np.random.Generator) -> np.ndarray:
    """Heavy-tailed AR(1) noise, intentionally retained across the full axis."""
    innovation = rng.standard_t(plan.student_t_df, size=plan.axis_length) * plan.innovation_scale_jpy
    noise = np.empty(plan.axis_length, dtype=float)
    noise[0] = innovation[0]
    scale = sqrt(1 - plan.ar1**2)
    for index in range(1, plan.axis_length):
        noise[index] = plan.ar1 * noise[index - 1] + scale * innovation[index]
    return noise


def synthetic_axis_outcomes(
    scenario: ScenarioName, plan: CalibrationPlan, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Generate disjoint A/C events on one shared scheduled axis.

    Every generated selected trade has ``gross = net + cost``.  Thus
    ``gross_zero`` and ``net_zero`` remain distinct estimands rather than two
    labels for the same simulated outcome.
    """
    categories = rng.choice(3, size=plan.axis_length, p=(0.10, 0.10, 0.80))
    a_mask, c_mask = categories == 0, categories == 1
    a_noise, c_noise = _dependent_noise(plan, rng), _dependent_noise(plan, rng)
    if scenario == "gross_zero":
        a_net_mean = c_net_mean = -plan.cost_jpy_round_trip
    elif scenario == "net_zero":
        a_net_mean = c_net_mean = 0
    elif scenario == "hypothetical_minimum_net":
        a_net_mean, c_net_mean = plan.hypothetical_minimum_net_jpy_per_trade, 0
    else:  # pragma: no cover - Literal protects callers, retained fail-closed.
        raise ValueError(f"unknown scenario: {scenario}")
    a_daily = np.where(a_mask, a_net_mean + a_noise, 0.0)
    c_daily = np.where(c_mask, c_net_mean + c_noise, 0.0)
    return a_daily, c_daily, int(a_mask.sum()), int(c_mask.sum())


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """A 95% Wilson interval for a Monte Carlo pass rate."""
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("invalid binomial count")
    p = successes / trials
    denominator = 1 + z**2 / trials
    center = (p + z**2 / (2 * trials)) / denominator
    radius = z * sqrt((p * (1 - p) + z**2 / (4 * trials)) / trials) / denominator
    return center - radius, center + radius


def calibrate(plan: CalibrationPlan) -> dict[str, object]:
    """Run all fixed scenarios without discarding failed or empty repetitions."""
    plan.validate()
    seed_sequence = np.random.SeedSequence(plan.seed)
    scenario_results: dict[ScenarioName, dict[str, object]] = {}
    scenarios: tuple[ScenarioName, ...] = (
        "gross_zero",
        "net_zero",
        "hypothetical_minimum_net",
    )
    for scenario, scenario_seed in zip(
        scenarios, seed_sequence.spawn(3), strict=True
    ):
        rng = np.random.default_rng(scenario_seed)
        gates: list[GateResult] = []
        failures: list[str] = []
        for repetition in range(plan.repetitions):
            try:
                a_daily, c_daily, a_count, c_count = synthetic_axis_outcomes(scenario, plan, rng)
                gates.append(
                    evaluate_gate(
                        a_daily,
                        c_daily,
                        a_trade_count=a_count,
                        c_trade_count=c_count,
                        plan=plan,
                        rng=rng,
                    )
                )
            except Exception as exc:  # A broken repetition is evidence, not resampled away.
                failures.append(f"replication={repetition}:{type(exc).__name__}:{exc}")
        passes = sum(gate.passed for gate in gates)
        scenario_results[scenario] = {
            "attempted_repetitions": plan.repetitions,
            "completed_repetitions": len(gates),
            "failed_repetitions": len(failures),
            "failure_examples": failures[:5],
            "gate_passes": passes,
            "gate_pass_rate": passes / plan.repetitions,
            "mc_wilson_95": wilson_interval(passes, plan.repetitions),
            "identified_repetitions": sum(gate.identified for gate in gates),
            "mean_a_trade_count": float(np.mean([gate.a_trade_count for gate in gates])),
            "mean_c_trade_count": float(np.mean([gate.c_trade_count for gate in gates])),
            "mean_main_daily_net_jpy": float(np.mean([gate.main_daily_mean_jpy for gate in gates])),
            "mean_contrast_daily_net_jpy": float(
                np.mean([gate.contrast_daily_mean_jpy for gate in gates])
            ),
        }
    net_null = scenario_results["net_zero"]
    alternative = scenario_results["hypothetical_minimum_net"]
    false_positive_upper = cast(tuple[float, float], net_null["mc_wilson_95"])[1]
    power_lower = cast(tuple[float, float], alternative["mc_wilson_95"])[0]
    status = (
        "PASS_METHOD_DIAGNOSTIC"
        if cast(int, net_null["failed_repetitions"]) == 0
        and cast(int, alternative["failed_repetitions"]) == 0
        and false_positive_upper <= plan.target_false_positive_rate
        and power_lower >= plan.target_power
        else "FAIL_METHOD_DIAGNOSTIC"
    )
    return {
        "status": status,
        "method_classification": "METHOD_DIAGNOSTIC_ONLY_NO_PRICE_TO_EXECUTION_PATH",
        "no_repetition_discarded_or_redrawn": True,
        "pass_rule": {
            "false_positive": "net_zero Wilson 95% upper <= 0.05",
            "power": "hypothetical_minimum_net Wilson 95% lower >= 0.80",
            "gross_zero": "diagnostic distinction only; it must not be relabelled net_zero",
        },
        "scenario_results": scenario_results,
    }
