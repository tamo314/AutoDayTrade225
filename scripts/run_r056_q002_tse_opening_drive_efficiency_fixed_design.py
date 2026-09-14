"""Execute R056-Q002 without inspecting R056-Q001's saved result artifacts."""

# ruff: noqa: E402

from __future__ import annotations

import importlib.util
from math import ceil, floor
from pathlib import Path
from random import Random
from sys import path as sys_path
from typing import Any, cast

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys_path.insert(0, str(ROOT / "src"))

from n225m_bt.research.r056_q002 import (
    BLOCK_TRADE_DATES,
    REPETITIONS,
    SCORE_BOOTSTRAP_SEED,
    fixed_design_score_bootstrap,
)
from n225m_bt.research.runner import write_json

IDENTIFIER = "r056-q002-20260915-tse-opening-drive-efficiency-fixed-design-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
DAILY_MBB_SEED = 20261003


def _load_q001_implementation() -> Any:
    source = ROOT / "scripts" / "run_r056_q001_tse_opening_drive_efficiency.py"
    spec = importlib.util.spec_from_file_location("r056_q001_implementation", source)
    if spec is None or spec.loader is None:
        raise ImportError("unable to load frozen R056-Q001 implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    lower, upper = floor(position), ceil(position)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def q002_bootstrap(module: Any, daily_by: dict[str, dict[str, int]], b_records: list[dict[str, object]]) -> dict[str, object]:
    axis = sorted(daily_by["A"])
    q, y, nuisance, regression_days = module.regression(b_records)
    point, denominator, errors, score_audit = fixed_design_score_bootstrap(
        q, y, nuisance, regression_days, axis, seed=SCORE_BOOTSTRAP_SEED,
        block_length=BLOCK_TRADE_DATES, repetitions=REPETITIONS,
    )
    series = {"A_mean_net_jpy": [float(daily_by["A"][day]) for day in axis]}
    for name in ("D", "B", "A_fade", "A_buy", "A_sell"):
        series[f"A_minus_{name}_mean_jpy"] = [float(daily_by["A"][day] - daily_by[name][day]) for day in axis]
    index_rows = np.empty((REPETITIONS, len(axis)), dtype=np.uint16)
    samples: dict[str, list[float]] = {name: [] for name in series}
    rng = Random(DAILY_MBB_SEED)
    for replicate in range(REPETITIONS):
        indices: list[int] = []
        while len(indices) < len(axis):
            start = rng.randrange(len(axis) - BLOCK_TRADE_DATES + 1)
            indices.extend(range(start, start + BLOCK_TRADE_DATES))
        indices = indices[: len(axis)]
        index_rows[replicate] = indices
        for name, values in series.items():
            samples[name].append(sum(values[index] for index in indices) / len(indices))
    np.save(module.OUT / "bootstrap_common_day_indices.npy", index_rows)
    np.save(module.OUT / "delta_fixed_design_score_errors.npy", errors)
    write_json(module.OUT / "regression_ledger.json", {
        "trade_dates": regression_days, "Q_high_efficiency": q.tolist(),
        "y_s_adjusted_0tick_gross_jpy": y.tolist(), "nuisance": nuisance.tolist(),
        "fwl": {"delta": point, "q_residual_ss": denominator, "pinv_rcond": 1e-12, "residual_ss_tolerance": 1e-12},
        "fixed_design_score_bootstrap": score_audit,
    })
    write_json(module.OUT / "technical_fwl_gate.json", {
        "status": "PASS", "stage": "fixed_design_all_10000_score_replicates",
        "observed_Q_residual_sum_of_squares": denominator, "pinv_rcond": 1e-12,
        "residual_ss_tolerance": 1e-12, "seed": SCORE_BOOTSTRAP_SEED,
        "repetitions": REPETITIONS, "block_length_trade_dates": BLOCK_TRADE_DATES,
        "fixed_design": True,
    })
    result: dict[str, object] = {
        "method": "daily: 20 trade_date noncircular moving-block bootstrap; delta: fixed-design 20 trade_date block-wild score bootstrap; linear percentile",
        "repetitions": REPETITIONS,
        "seed": {"daily_mbb": DAILY_MBB_SEED, "delta_score": SCORE_BOOTSTRAP_SEED},
        "block_length_trade_dates": BLOCK_TRADE_DATES, "target_trade_dates": len(axis),
        "common_index": True, "index_file": "bootstrap_common_day_indices.npy",
        "delta_error_file": "delta_fixed_design_score_errors.npy",
    }
    for name, values in samples.items():
        result[name] = {"estimate": sum(series[name]) / len(axis), "ci95_percentile_linear": [percentile(values, .025), percentile(values, .975)]}
    delta_samples = (point + errors).tolist()
    result["delta_fwl_jpy"] = {"estimate": point, "ci95_percentile_linear": [percentile(delta_samples, .025), percentile(delta_samples, .975)]}
    return result


def q002_preregistration(module: Any, original: Any, source: dict[str, object], inputs: dict[str, object], implementation: dict[str, str]) -> dict[str, object]:
    plan = original(source, inputs, implementation)
    plan.update({
        "experiment_id": IDENTIFIER, "study_id": "R056-Q002",
        "prior_immutable_attempts": "R056-Q001 saved price statistics, event counts, PnL and coefficients were not accessed. The only carried-forward fact is that its moving-block FWL bootstrap was BLOCKED at replicate 411 because Q was unidentified. Q002 changes the delta CI mechanism only.",
        "ols": "All direction-valid B events: y is s-adjusted 0-tick/pre-cost 30-minute gross; Q=1[e>=qe50]; nuisance Z is intercept, ln(x/qx75), first-30m high-low range bps, first-5m s-adjusted return bps, upward indicator and calendar-year fixed effects. Nuisance-only Moore-Penrose FWL uses pinv rcond=1e-12 and Q residual SS tolerance=1e-12; full-sample nonidentification is BLOCKED.",
        "bootstrap": {"daily_and_contrasts": {"method": "20 trade_date noncircular moving-block", "repetitions": REPETITIONS, "seed": DAILY_MBB_SEED, "common_index": True, "tail_truncate": True, "percentile": "linear"}, "delta_ci_only": {"method": "fixed-design consecutive 20 trade_date block-wild score", "repetitions": REPETITIONS, "seed": SCORE_BOOTSTRAP_SEED, "rademacher": True, "fixed_design_q_tilde_full_residual_and_denominator": True, "tail_block_retained": True, "non_event_days_score_zero": True, "percentile": "linear"}},
        "implementation": implementation | {"src/n225m_bt/research/r056_q002.py": module.digest(ROOT / "src/n225m_bt/research/r056_q002.py"), "tests/test_r056_q002.py": module.digest(ROOT / "tests/test_r056_q002.py")},
    })
    return cast(dict[str, object], plan)


def main() -> None:
    module = _load_q001_implementation()
    module.IDENTIFIER, module.OUT, module.SEED = IDENTIFIER, OUT, DAILY_MBB_SEED
    module.__file__ = str(Path(__file__).resolve())
    original_preregistration = module.preregistration
    original_run = module.run

    def validation_run(command: list[str], **kwargs: object) -> object:
        """Include the synthetic fixed-design gate before Development is loaded."""
        amended = list(command)
        if "pytest" in amended:
            amended.insert(amended.index("-q"), "tests/test_r056_q002.py")
        return original_run(amended, **kwargs)

    module.preregistration = lambda source, inputs, implementation: q002_preregistration(module, original_preregistration, source, inputs, implementation)
    module.bootstrap = lambda daily_by, b_records: q002_bootstrap(module, daily_by, b_records)
    module.run = validation_run
    module.main()


if __name__ == "__main__":
    main()
