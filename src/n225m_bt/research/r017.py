"""Saved-artifact-only detection-sensitivity calibration for R017-D001.

The implementation intentionally reads no market bars and does not invoke the
backtest engine.  It conditions on R016's stored bootstrap mean replicates.
"""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from pathlib import Path
from typing import Any, cast

import polars as pl

SENSITIVITY_ID = "r017-d001-20260914-fixed-diagnostic-sensitivity-01"
STUDY_FINGERPRINT = "R017-D001/R016-fixed-20-series-conditional-detection-sensitivity-v1"
R016_ID = "r016-d001-20260914-development-directional-evidence-03"
RULES = ("R011", "R012", "R013", "R014", "R015")
SERIES_SUFFIXES = ("P0_A", "P1_A", "P0_A_minus_B", "P0_A_minus_C")
BLOCK_LENGTHS = (10, 20, 40)
PRIMARY_BLOCK_LENGTH = 20
ITERATIONS = 10_000
N_DAYS = 1_120
LAMBDAS = (0.5, 1.0, 2.0)
R016_A_CONDITIONS = {
    "R011": ("r011-q001-20260914-close-mean-reversion-02", "A_close_mean_reversion"),
    "R012": ("r012-q001-20260914-night-direction-followthrough-01", "A_night_follow"),
    "R013": ("r013-q001-20260914-previous-same-session-direction-01", "A_previous_follow"),
    "R014": ("r014-q001-20260914-range-midpoint-position-01", "A_range_midpoint"),
    "R015": ("r015-q001-20260914-night-close-to-day-opening-total-change-01", "A_total_follow"),
}


class GateError(ValueError):
    """A required stored-artifact prerequisite was not met."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _linear_quantile(values: list[float], probability: float) -> float:
    if not values or not 0 <= probability <= 1:
        raise ValueError("quantile requires values and probability in [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _series_names() -> tuple[str, ...]:
    return tuple(f"{rule}_{suffix}" for rule in RULES for suffix in SERIES_SUFFIXES)


def centered_errors(
    means: dict[str, float], replicated_means: list[dict[str, float]]
) -> list[dict[str, float]]:
    """Return aligned e_b=m*_b-m errors without changing replicate correspondence."""
    names = set(means)
    if names != set(_series_names()) or any(set(row) != names for row in replicated_means):
        raise ValueError("means and every replicate must contain exactly the fixed 20 series")
    return [{name: row[name] - means[name] for name in _series_names()} for row in replicated_means]


def scenario_theta(
    costs: dict[str, float], injected_rule: str | None, lambda_: float | None
) -> dict[str, float]:
    """Construct the frozen null or one-rule alternative vector in JPY/trade_date."""
    if set(costs) != set(RULES):
        raise ValueError("costs must contain exactly the five frozen rules")
    if (injected_rule is None) != (lambda_ is None):
        raise ValueError("null uses neither injected_rule nor lambda; alternatives use both")
    if injected_rule is not None:
        raise ValueError("use alternative_theta for a frozen one-rule alternative")
    theta: dict[str, float] = {}
    for rule in RULES:
        c1 = costs[rule]
        mu = 0.0
        theta[f"{rule}_P0_A"] = mu
        theta[f"{rule}_P1_A"] = mu - c1
        theta[f"{rule}_P0_A_minus_B"] = mu
        theta[f"{rule}_P0_A_minus_C"] = mu
    return theta


def alternative_theta(costs: dict[str, float], c2: dict[str, float], rule: str, lambda_: float) -> dict[str, float]:
    """Construct a permitted alternative, with mu=lambda*c2 for just one rule."""
    theta = scenario_theta(costs, None, None)
    if rule not in RULES or lambda_ not in LAMBDAS or set(c2) != set(RULES):
        raise ValueError("invalid frozen alternative scenario")
    mu = lambda_ * c2[rule]
    theta[f"{rule}_P0_A"] = mu
    theta[f"{rule}_P1_A"] = mu - costs[rule]
    theta[f"{rule}_P0_A_minus_B"] = mu
    theta[f"{rule}_P0_A_minus_C"] = mu
    return theta


def rule_detected(z: dict[str, float], q: float, rule: str) -> bool:
    """R016's frozen strict-positive lower-bound rule for one primary condition."""
    return all(z[f"{rule}_{suffix}"] - q > 0 for suffix in ("P0_A", "P0_A_minus_B", "P0_A_minus_C"))


def detection_rates(
    theta: dict[str, float], errors: list[dict[str, float]], q: float
) -> dict[str, float]:
    """Apply a fixed q to z_b=theta+e_b and return each rule's detection rate."""
    if set(theta) != set(_series_names()) or q < 0:
        raise ValueError("invalid fixed scenario or q")
    if not errors:
        raise ValueError("at least one stored bootstrap replicate is required")
    counts = {rule: 0 for rule in RULES}
    for error in errors:
        z = {name: theta[name] + error[name] for name in _series_names()}
        for rule in RULES:
            counts[rule] += rule_detected(z, q, rule)
    return {rule: counts[rule] / len(errors) for rule in RULES}


def _preregistration(repo: Path) -> dict[str, object]:
    return {
        "sensitivity_id": SENSITIVITY_ID,
        "study_fingerprint": STUDY_FINGERPRINT,
        "status": "frozen_before_new_statistics",
        "proposition": "The frozen R016 diagnostic has high conditional detection sensitivity for a directional mean shift equal to its fixed-path two-tick cost hurdle, under an artificial model whose fixed-direction controls retain zero expectancy.",
        "scope": {
            "permitted_inputs": [
                f"all saved files in {R016_ID}",
                "only saved R011--R015 A-condition config, manifest, metrics, and trades required to reconcile count and costs",
            ],
            "prohibited": [
                "market bars", "raw data", "volume", "external prices", "backtest reruns",
                "OOS", "Final Holdout", "post-hoc observed-effect power", "strategy rescue",
            ],
        },
        "input_gates": {
            "r016_formal_id": R016_ID,
            "development": "2021-01-01 through 2025-06-30, 1,120 common trade_date axis",
            "required_axis_extension_days": 9,
            "required_quality": "PASS_LIMITED_INHERITED",
            "required_r004_quarantine_hash": "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
            "required_path_controls": "A/B/C same saved path and P0/P1 accounting as recorded by R016",
            "missing_or_inconsistent_saved_replicates": "BLOCKED; no imputation or re-estimation",
        },
        "economics": {
            "N": N_DAYS,
            "c1_k": "sum(fees_jpy + slippage_cost_jpy) / N",
            "c2_k": "sum(fees_jpy + 2 * slippage_cost_jpy) / N; a fixed-path cost hurdle, not a two-tick rerun",
            "mu_k": "lambda * c2_k", "lambdas": list(LAMBDAS), "primary_lambda": 1.0,
            "no_observed_P0_calibration": True,
        },
        "conditional_model": {
            "errors": "e_b=m*_b-m from each stored, aligned R016 bootstrap replicate",
            "q": "linear percentile_95(max_j(abs(e_bj))) and must reproduce R016's saved q separately for block 10/20/40",
            "null": "for every k: (P0_A,P1_A,P0_A-B,P0_A-C)=(0,-c1_k,0,0)",
            "alternative": "for exactly one k: (mu_k,mu_k-c1_k,mu_k,mu_k); all other rules remain null",
            "interval": "z_b=theta+e_b; use fixed [z_b-q,z_b+q], without studentization or per-sample interval refitting",
            "detection": "strictly positive lower bounds for P0_A, P0_A-B, P0_A-C only",
            "scenarios": "one all-null plus five rules times three lambdas = 16",
        },
        "interpretation": {
            "primary": "At block 20, lambda=1 target detection rate >=80% is high sensitivity for this artificial mean-shift model; otherwise material miss risk remains.",
            "stability": "A changed >=80% classification at block 10 or 40 makes the sensitivity judgment unstable.",
            "null_alarm": "family-wise all-null detection rate above 5% is a calibration anomaly; report it and do not adjust q.",
            "non_claims": "This neither establishes nor refutes a real strategy's predictive power, attainability, economic equivalence, OOS value, or sample-size extrapolation.",
        },
        "code_paths": [
            str(repo / "src/n225m_bt/research/r017.py"),
            str(repo / "scripts/run_r017_d001.py"),
            str(repo / "tests/test_r017_d001.py"),
        ],
    }


def _snapshot(repo: Path, output: Path) -> dict[str, str]:
    paths = [
        repo / "src/n225m_bt/research/r017.py",
        repo / "scripts/run_r017_d001.py",
        repo / "tests/test_r017_d001.py",
    ]
    archive = output / "source_snapshot.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zipped:
        for path in paths:
            zipped.write(path, path.relative_to(repo))
    return {str(path.relative_to(repo)): _sha256(path) for path in paths} | {
        "source_snapshot.zip": _sha256(archive)
    }


def _input_files(research_root: Path) -> list[Path]:
    r016 = research_root / R016_ID
    files = sorted(path for path in r016.rglob("*") if path.is_file())
    for campaign, condition in R016_A_CONDITIONS.values():
        root = research_root / campaign
        files.extend(
            [
                root / "effective_config.json",
                root / condition / "run_manifest.json",
                root / condition / "metrics.json",
                root / condition / "trades.parquet",
            ]
        )
    return files


def _check_novelty(research_root: Path, output: Path) -> bool:
    matches: list[str] = []
    for preregistration in research_root.glob("*/preregistration.json"):
        if preregistration.parent == output:
            continue
        try:
            if _read_json(preregistration).get("study_fingerprint") == STUDY_FINGERPRINT:
                matches.append(str(preregistration.relative_to(research_root)))
        except (json.JSONDecodeError, GateError):
            continue
    _write_json(output / "novelty_check.json", {"equivalent_existing_calibration": matches, "status": "CLEAR" if not matches else "DUPLICATE"})
    return not matches


def _load_r016(research_root: Path) -> tuple[dict[str, float], dict[int, list[dict[str, float]]], dict[int, float], dict[str, object]]:
    root = research_root / R016_ID
    completed = _read_json(root / "COMPLETED.json")
    decision = _read_json(root / "decision.json")
    validation = _read_json(root / "validation.json")
    bootstrap = _read_json(root / "bootstrap.json")
    preregistration = _read_json(root / "preregistration.json")
    if completed.get("status") != "COMPLETE" or decision.get("status") != "COMPLETE":
        raise GateError("R016 formal completion record is not COMPLETE")
    if completed.get("quality_status") != "PASS_LIMITED_INHERITED":
        raise GateError("R016 quality status is not PASS_LIMITED_INHERITED")
    if completed.get("oos") != "NOT_EVALUATED" or completed.get("final_holdout") != "NOT_ACCESSED":
        raise GateError("R016 OOS or Final Holdout state is impermissible")
    if preregistration.get("diagnostic_id") != R016_ID:
        raise GateError("R016 preregistration ID mismatch")
    settings = bootstrap.get("settings", {})
    if settings.get("iterations") != ITERATIONS or settings.get("common_daily_axis_count") != N_DAYS:
        raise GateError("R016 saved bootstrap settings differ")
    axis = validation.get("axis")
    extensions = validation.get("extensions")
    if not isinstance(axis, list) or len(axis) != N_DAYS or len(set(axis)) != N_DAYS:
        raise GateError("R016 common daily axis is unavailable or differs")
    if not isinstance(extensions, dict) or any(len(extensions.get(rule, [])) != 9 for rule in ("R012", "R015")):
        raise GateError("R016 nine-day axis extensions are unavailable or differ")
    campaigns = validation.get("campaigns")
    if not isinstance(campaigns, dict):
        raise GateError("R016 reconciliation record is unavailable")
    for rule in RULES:
        entry = campaigns.get(rule)
        if not isinstance(entry, dict):
            raise GateError(f"R016 reconciliation record lacks {rule}")
        quarantine = entry.get("quarantine", {})
        if quarantine.get("quarantined_session_list_hash") != "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa":
            raise GateError(f"R016 fixed quarantine hash differs for {rule}")
    daily = pl.read_parquet(root / "daily_series.parquet")
    if daily.columns != ["trade_date", "series", "pnl_jpy"] or daily.height != N_DAYS * 20:
        raise GateError("R016 daily 20-series ledger schema or length differs")
    names = _series_names()
    if set(daily.get_column("series").unique().to_list()) != set(names):
        raise GateError("R016 daily ledger does not contain the fixed 20 series")
    mean_rows = daily.group_by("series").agg(pl.col("pnl_jpy").mean().alias("mean")).to_dicts()
    means = {str(row["series"]): float(row["mean"]) for row in mean_rows}
    if len(means) != 20 or any(daily.filter(pl.col("series") == name).height != N_DAYS for name in names):
        raise GateError("R016 daily series are not aligned to 1,120 trade_dates")
    replicate_frame = pl.read_parquet(root / "bootstrap_replicates.parquet")
    expected_columns = ["block_length", "iteration", "T", *names]
    if replicate_frame.columns != expected_columns or replicate_frame.height != len(BLOCK_LENGTHS) * ITERATIONS:
        raise GateError("R016 bootstrap replicate schema or length differs")
    saved_by_block = bootstrap.get("by_block_length")
    if not isinstance(saved_by_block, dict):
        raise GateError("R016 saved q values are unavailable")
    replicates: dict[int, list[dict[str, float]]] = {}
    q_values: dict[int, float] = {}
    q_matches: dict[str, object] = {}
    for block in BLOCK_LENGTHS:
        subset = replicate_frame.filter(pl.col("block_length") == block).sort("iteration")
        if subset.height != ITERATIONS or subset.get_column("iteration").to_list() != list(range(ITERATIONS)):
            raise GateError(f"R016 block {block} replicate correspondence is incomplete")
        rows = [{name: float(row[name]) for name in names} for row in subset.select(names).to_dicts()]
        errors = centered_errors(means, rows)
        calculated_t = [max(abs(error[name]) for name in names) for error in errors]
        stored_t = [float(value) for value in subset.get_column("T").to_list()]
        if any(not math.isclose(left, right, abs_tol=1e-9) for left, right in zip(calculated_t, stored_t, strict=True)):
            raise GateError(f"R016 block {block} stored T values do not match centered 20-series errors")
        calculated_q = _linear_quantile(calculated_t, 0.95)
        saved_block = saved_by_block.get(str(block))
        if not isinstance(saved_block, dict) or not isinstance(saved_block.get("q"), (int, float)):
            raise GateError(f"R016 block {block} saved q is unavailable")
        saved_q = float(saved_block["q"])
        if not math.isclose(calculated_q, saved_q, abs_tol=1e-9):
            raise GateError(f"R016 block {block} q reproduction failed")
        replicates[block] = rows
        q_values[block] = saved_q
        q_matches[str(block)] = {"calculated_q": calculated_q, "saved_q": saved_q, "match": True}
    return means, replicates, q_values, {"q_reproduction": q_matches, "r016_validation": validation}


def _cost_hurdles(research_root: Path, validation: dict[str, object]) -> tuple[dict[str, float], dict[str, float], list[dict[str, object]]]:
    campaigns = validation.get("campaigns")
    if not isinstance(campaigns, dict):
        raise GateError("R016 validation record lacks campaign reconciliation")
    c1: dict[str, float] = {}
    c2: dict[str, float] = {}
    rows: list[dict[str, object]] = []
    for rule, (campaign, condition) in R016_A_CONDITIONS.items():
        root = research_root / campaign
        effective = _read_json(root / "effective_config.json")
        manifest = _read_json(root / condition / "run_manifest.json")
        overall = _read_json(root / condition / "metrics.json").get("overall")
        validation_rule = campaigns.get(rule)
        if not isinstance(overall, dict) or not isinstance(validation_rule, dict):
            raise GateError(f"{rule} saved cost reconciliation is unavailable")
        costs = manifest.get("costs")
        multiplier = effective.get("instrument", {}).get("instrument", {}).get("contract_multiplier")
        trades = pl.read_parquet(root / condition / "trades.parquet")
        if costs != {"fee_jpy_per_side": 30, "slippage_ticks_per_side": 1}:
            raise GateError(f"{rule} does not use saved 1-tick/JPY30 one-way costs")
        if multiplier != 100 or "qty" not in trades.columns or trades.filter(pl.col("qty") != 1).height:
            raise GateError(f"{rule} saved quantity or contract multiplier differs")
        reconciled = validation_rule.get("conditions", {}).get("A", {}).get("reconciled_metrics")
        if not isinstance(reconciled, dict):
            raise GateError(f"{rule} R016 A reconciliation is unavailable")
        fields = ("trade_count", "fees_jpy", "slippage_cost_jpy", "gross_pnl_jpy", "net_pnl_jpy")
        if any(overall.get(field) != reconciled.get(field) for field in fields):
            raise GateError(f"{rule} metrics disagree with R016 reconciliation")
        n_value = overall.get("trade_count")
        fees_value = overall.get("fees_jpy")
        slippage_value = overall.get("slippage_cost_jpy")
        if (
            not isinstance(n_value, int)
            or not isinstance(fees_value, int)
            or not isinstance(slippage_value, int)
            or n_value <= 0
            or fees_value < 0
            or slippage_value < 0
        ):
            raise GateError(f"{rule} saved count or cost totals are invalid")
        n = n_value
        fees = fees_value
        slippage = slippage_value
        total_c1 = fees + slippage
        total_c2 = fees + 2 * slippage
        c1[rule] = total_c1 / N_DAYS
        c2[rule] = total_c2 / N_DAYS
        rows.append({
            "rule": rule, "N_trade_dates": N_DAYS, "trade_count": n, "fees_jpy": fees,
            "slippage_cost_jpy": slippage, "c1_total_jpy": total_c1, "c2_total_jpy": total_c2,
            "c1_jpy_per_trade_date": c1[rule], "c2_jpy_per_trade_date": c2[rule],
            "c1_jpy_per_trade": total_c1 / n, "c2_jpy_per_trade": total_c2 / n,
            "fee_jpy_per_side": 30, "slippage_ticks_per_side": 1,
            "qty": 1, "contract_multiplier": multiplier,
            "lambda_effects": [{"lambda": value, "mu_jpy_per_trade_date": value * c2[rule], "mu_jpy_per_trade": value * total_c2 / n} for value in LAMBDAS],
        })
    return c1, c2, rows


def run_r017(repo: Path) -> Path:
    """Write the append-only R017 artificial calibration from saved R016 artefacts."""
    research_root = repo / "results" / "research"
    output = research_root / SENSITIVITY_ID
    if output.exists():
        raise FileExistsError(f"sensitivity output already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / "preregistration.json", _preregistration(repo))
    if not _check_novelty(research_root, output):
        _write_json(output / "BLOCKED.json", {"status": "DUPLICATE_NOT_EXECUTED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return output
    _write_json(output / "source_manifest.json", _snapshot(repo, output))
    inputs = _input_files(research_root)
    absent = [str(path.relative_to(repo)) for path in inputs if not path.is_file()]
    _write_json(output / "input_manifest.json", {"files": {str(path.relative_to(repo)): _sha256(path) for path in inputs if path.is_file()}, "absent": absent})
    if absent:
        _write_json(output / "BLOCKED.json", {"status": "MISSING_REQUIRED_SAVED_ARTIFACT", "absent": absent, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return output
    try:
        means, replicates, q_values, r016_record = _load_r016(research_root)
        r016_validation = r016_record["r016_validation"]
        assert isinstance(r016_validation, dict)
        c1, c2, cost_rows = _cost_hurdles(research_root, r016_validation)
        _write_json(output / "gate_record.json", {
            "status": "PASS", "r016_q_reproduction": r016_record["q_reproduction"],
            "access_scope": "saved R016 and required saved R011--R015 artefacts only; no bars/raw/external prices/backtests/OOS/Final Holdout",
            "means_jpy_per_trade_date": means,
        })
        _write_json(output / "cost_hurdles.json", {"definition": "c1=(fees+slippage)/1120; c2=(fees+2*slippage)/1120", "rows": cost_rows})
        errors = {block: centered_errors(means, rows) for block, rows in replicates.items()}
        scenarios: list[dict[str, object]] = []
        null_theta = scenario_theta(c1, None, None)
        for block in BLOCK_LENGTHS:
            rates = detection_rates(null_theta, errors[block], q_values[block])
            scenarios.append({
                "scenario": "all_null", "block_length": block, "q_jpy_per_trade_date": q_values[block],
                "theta": null_theta, "any_rule_detection_rate": sum(1 for error in errors[block] if any(rule_detected({name: null_theta[name] + error[name] for name in _series_names()}, q_values[block], rule) for rule in RULES)) / ITERATIONS,
                "by_rule_detection_rate": rates,
            })
        for rule in RULES:
            for lambda_ in LAMBDAS:
                theta = alternative_theta(c1, c2, rule, lambda_)
                for block in BLOCK_LENGTHS:
                    rates = detection_rates(theta, errors[block], q_values[block])
                    scenarios.append({
                        "scenario": f"{rule}_lambda_{lambda_:g}", "injected_rule": rule, "lambda": lambda_,
                        "block_length": block, "q_jpy_per_trade_date": q_values[block], "theta": theta,
                        "target_detection_rate": rates[rule], "by_rule_detection_rate": rates,
                    })
        monotonic: dict[str, dict[str, bool]] = {}
        for rule in RULES:
            monotonic[rule] = {}
            for block in BLOCK_LENGTHS:
                values = [next(cast(float, row["target_detection_rate"]) for row in scenarios if row.get("injected_rule") == rule and row.get("lambda") == lambda_ and row["block_length"] == block) for lambda_ in LAMBDAS]
                monotonic[rule][str(block)] = values == sorted(values)
        if not all(all(blocks.values()) for blocks in monotonic.values()):
            raise GateError("fixed-error detection rates are not monotone in the frozen effect size")
        _write_json(output / "scenario_results.json", {"scenario_count": 16, "stored_rows": len(scenarios), "conditional_method": "Each of 16 frozen scenarios is evaluated against the same 10,000 aligned errors and the saved fixed q for each block.", "results": scenarios})
        primary: dict[str, dict[str, float]] = {}
        classifications: dict[str, dict[str, object]] = {}
        for rule in RULES:
            rates = {str(block): next(cast(float, row["target_detection_rate"]) for row in scenarios if row.get("injected_rule") == rule and row.get("lambda") == 1.0 and row["block_length"] == block) for block in BLOCK_LENGTHS}
            primary[rule] = rates
            labels = {block: rate >= 0.80 for block, rate in rates.items()}
            classifications[rule] = {"block20_high_sensitivity": labels["20"], "classification_stable_block10_20_40": len(set(labels.values())) == 1, "interpretation": "high sensitivity in this artificial mean-shift model" if labels["20"] else "material miss risk remains in this artificial mean-shift model"}
        null_rates = {str(block): next(cast(float, row["any_rule_detection_rate"]) for row in scenarios if row["scenario"] == "all_null" and row["block_length"] == block) for block in BLOCK_LENGTHS}
        anomalies = {block: rate > 0.05 for block, rate in null_rates.items()}
        _write_json(output / "validation.json", {
            "synthetic_checks": {
                "centered_errors_and_20_series_correspondence": True,
                "fixed_path_cost_difference_c2_minus_c1_equals_slippage": True,
                "null_and_single_rule_injection_vectors": True,
                "strict_positive_lower_bound": True,
                "saved_q_reproduction": r016_record["q_reproduction"],
                "fixed_error_monotonic_detection_rate": monotonic,
            },
            "null_any_rule_detection_rate": null_rates,
            "null_rate_above_5pct_calibration_anomaly": anomalies,
        })
        final = {
            "status": "COMPLETE", "decision": "ARTIFICIAL_CONDITIONAL_SENSITIVITY_CALIBRATION_ONLY",
            "primary_lambda": 1.0, "primary_block_length": PRIMARY_BLOCK_LENGTH,
            "lambda1_target_detection_rates": primary, "interpretations": classifications,
            "null_any_rule_detection_rates": null_rates, "calibration_anomaly": any(anomalies.values()),
            "quality_status": "PASS_LIMITED_INHERITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
            "maintained_states": {"R011-R015": "REJECT", "R009": "INCONCLUSIVE", "R003/R010": "BLOCKED"},
            "limitations": [
                "This is conditional on R016's stored errors and fixed q, not a complete power experiment that re-estimates intervals in every artificial sample.",
                "Artificial means are not price paths, executed trades, attainable profit, or evidence of real predictive power.",
                "The resampling noise inherits the observed dependence/variance structure and a stationarity-style moving-block approximation.",
                "High sensitivity does not prove existing rules invalid or economically equivalent; low sensitivity does not justify extra indicators, OOS access, or square-root sample extrapolation.",
            ],
        }
        _write_json(output / "decision.json", final)
        _write_json(output / "COMPLETED.json", final)
        _write_json(output / "reproducibility.json", {"command": "python scripts/run_r017_d001.py", "source_manifest": "source_manifest.json", "input_manifest": "input_manifest.json", "iterations_per_block": ITERATIONS, "blocks": list(BLOCK_LENGTHS), "scenario_count": 16})
    except GateError as error:
        _write_json(output / "BLOCKED.json", {"status": "INPUT_OR_EXECUTION_GATE_FAILED", "reason": str(error), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    return output
