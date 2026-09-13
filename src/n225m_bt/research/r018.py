"""Saved-ledger-only economic upper-bound diagnostic for R018-D001.

This diagnostic has no market-data or backtest-engine dependency.  It adds the
five fixed-path two-tick daily PnL series to R016's preserved 20-series
bootstrap inference family, using only its stored aligned mean replicates.
"""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from pathlib import Path
from statistics import fmean
from typing import Any, cast

import polars as pl

from n225m_bt.research.r017 import (
    BLOCK_LENGTHS,
    ITERATIONS,
    N_DAYS,
    PRIMARY_BLOCK_LENGTH,
    R016_A_CONDITIONS,
    R016_ID,
    RULES,
    GateError,
    _linear_quantile,
    _load_r016,
)

DIAGNOSTIC_ID = "r018-d001-20260914-fixed-path-two-tick-economic-upper-bound-02"
STUDY_FINGERPRINT = "R018-D001/R016-fixed-path-two-tick-economic-upper-bound-v1-retry"
R017_ID = "r017-d001-20260914-fixed-diagnostic-sensitivity-01"
P2_SUFFIX = "P2_A"
SERIES_20_SUFFIXES = ("P0_A", "P1_A", "P0_A_minus_B", "P0_A_minus_C")


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


def p2_linear(p0: float, p1: float) -> float:
    """Return P2=(103/53)P1-(50/53)P0 under the frozen cost contract."""
    return (103.0 * p1 - 50.0 * p0) / 53.0


def p2_daily(p0: int, p1: int, fees: int, slippage: int) -> int:
    """Reconcile one daily saved path and apply exactly one extra tick cost."""
    if p1 != p0 - fees - slippage:
        raise GateError("daily P0/P1/fees/slippage accounting does not reconcile")
    direct = p0 - fees - 2 * slippage
    if not math.isclose(float(direct), p2_linear(float(p0), float(p1)), abs_tol=1e-9):
        raise GateError("daily direct and linear P2 definitions differ")
    return direct


def augmented_replicate(
    replicate: dict[str, float], names_20: tuple[str, ...]
) -> dict[str, float]:
    """Preserve one R016 replicate and append its five aligned P2 means."""
    if set(replicate) != set(names_20):
        raise GateError("stored R016 replicate does not have exactly the fixed 20 series")
    result = dict(replicate)
    for rule in RULES:
        result[f"{rule}_{P2_SUFFIX}"] = p2_linear(
            replicate[f"{rule}_P0_A"], replicate[f"{rule}_P1_A"]
        )
    return result


def simultaneous_q(
    means: dict[str, float], replicates: list[dict[str, float]]
) -> tuple[float, list[float]]:
    """R016's max-deviation linear-percentile q for a fixed aligned family."""
    if not means or not replicates or any(set(row) != set(means) for row in replicates):
        raise GateError("incomplete bootstrap family")
    t_values = [max(abs(row[name] - means[name]) for name in means) for row in replicates]
    return _linear_quantile(t_values, 0.95), t_values


def upper_bound_decision(lower: float, upper: float) -> str:
    """Apply the preregistered strict upper-bound decision without rescue rules."""
    if upper < 0:
        return "ECONOMIC_BREAK_EVEN_EXCLUDED"
    if lower > 0:
        return "REQUIRES_EXISTING_RESULT_CONSISTENCY_REVIEW"
    return "INSUFFICIENT_PRECISION_TO_EXCLUDE_ECONOMIC_BREAK_EVEN"


def _preregistration(repo: Path) -> dict[str, object]:
    return {
        "diagnostic_id": DIAGNOSTIC_ID,
        "study_fingerprint": STUDY_FINGERPRINT,
        "status": "frozen_before_new_statistics",
        "supersedes_unexecuted_preflight": {
            "diagnostic_id": "r018-d001-20260914-fixed-path-two-tick-economic-upper-bound-01",
            "reason": "Its sole BLOCKED record came from an implementation over-gate that incorrectly required R017's cost-only hurdle table to contain Net PnL. No R018 daily P2, transformed replicate, interval, or decision statistic was produced.",
            "specification_change": "none; the fee/slippage reconciliation and all preregistered formulas, inputs, bootstrap method, and decision rule are unchanged.",
        },
        "proposition": "For all five frozen rules, the simultaneous 95% interval upper bound of fixed-path daily P2 under two ticks one-way and JPY30 fee per side is strictly below zero.",
        "purpose": "A post-hoc Development-only economic upper-bound diagnostic. It directly asks whether the stored fixed paths exclude covering the specified two-tick costs; it is not independent confirmation, a future-profit guarantee, or a proof of zero predictive power.",
        "scope": {
            "permitted_inputs": [
                f"R016 formal saved daily 20-series, aligned bootstrap means, settings, manifests, and reconciliation records in {R016_ID}",
                f"R017 formal cost-hurdle and gate records in {R017_ID}",
                "only the saved R011--R015 A ledgers/config/manifests/metrics needed to reconcile daily fees and slippage",
            ],
            "prohibited": [
                "market bars", "raw data", "volume", "external prices", "backtest reruns",
                "OOS", "Final Holdout", "new random bootstrap replicates", "studentization",
                "one-sided intervals", "block selection", "artificial-effect injection", "strategy rescue",
            ],
        },
        "input_gates": {
            "r016_formal_id": R016_ID,
            "r017_formal_id": R017_ID,
            "development": "2021-01-01 through 2025-06-30; common saved 1,120 trade_date axis",
            "r012_r015_extension": "exactly the proven nine R004 day-session quarantine dates only; all saved no-trade dates remain zero",
            "required_r004_quarantine_hash": "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
            "required_quality": "PASS_LIMITED_INHERITED",
            "missing_or_mismatched_input": "BLOCKED; do not impute, re-estimate, or alter coefficients",
        },
        "economics": {
            "P0": "R016 saved pre-cost fixed-path daily PnL",
            "P1": "R016 saved Net daily PnL",
            "C1": "fees_kd + slippage_kd",
            "C2": "fees_kd + 2*slippage_kd",
            "P2": "P0_kd-C2_kd = P1_kd-slippage_kd",
            "frozen_contract": "every saved A trade has C1=1060 JPY and C2=2060 JPY (one-tick one-way slippage plus JPY30 fee per side; qty=1, multiplier=100)",
            "linear_identity": "P2=(103/53)*P1-(50/53)*P0; reconcile it against daily ledgers without using an average-cost subtraction",
            "interpretation": "P2 is a stored-path cost diagnostic, not a two-tick rerun or attainable profit.",
        },
        "inference": {
            "fixed_family": "preserve R016's 20 series and add five P2 series; no selection after results",
            "bootstrap": "use R016's stored 10,000 aligned non-circular, tail-truncated moving-block mean replicates; derive P2 by the same linear transform",
            "seed_origin": 20260913,
            "iterations": ITERATIONS,
            "blocks": list(BLOCK_LENGTHS),
            "primary_block": PRIMARY_BLOCK_LENGTH,
            "interval": "T_b=max over 25 series abs(m*_bj-m_j); q25=linear percentile_95(T); [m_j-q25,m_j+q25]",
            "checks": "reproduce R016 q20, require q25 >= q20, retain R016 intervals unchanged",
        },
        "decision_rule": {
            "per_rule": "upper(P2)<0 strictly means this fixed Development-path approximation excludes the range supporting coverage of two-tick costs; upper=0 does not pass",
            "otherwise": "an interval containing zero is insufficient precision to exclude economic break-even; lower>0 requires consistency review and never auto-promotes",
            "proposition": "supported only if all five P2 upper bounds are strictly below zero at block=20; block-length robust only if all five also pass at 10 and 40",
            "boundaries": "No rule is removed after failure. No REJECT state, OOS state, or Final Holdout state changes.",
        },
        "code_paths": [
            str(repo / "src/n225m_bt/research/r018.py"),
            str(repo / "src/n225m_bt/research/r017.py"),
            str(repo / "scripts/run_r018_d001.py"),
            str(repo / "tests/test_r018_d001.py"),
        ],
    }


def _snapshot(repo: Path, output: Path) -> dict[str, str]:
    paths = [
        repo / "src/n225m_bt/research/r018.py",
        repo / "src/n225m_bt/research/r017.py",
        repo / "scripts/run_r018_d001.py",
        repo / "tests/test_r018_d001.py",
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
    r017 = research_root / R017_ID
    files = [
        r016 / name
        for name in (
            "COMPLETED.json", "decision.json", "preregistration.json", "input_manifest.json",
            "validation.json", "bootstrap.json", "daily_series.parquet", "bootstrap_replicates.parquet",
        )
    ] + [
        r017 / name
        for name in (
            "COMPLETED.json", "decision.json", "preregistration.json", "input_manifest.json",
            "gate_record.json", "cost_hurdles.json",
        )
    ]
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
    _write_json(
        output / "novelty_check.json",
        {"equivalent_existing_diagnostic": matches, "status": "CLEAR" if not matches else "DUPLICATE"},
    )
    return not matches


def _daily_series_maps(daily: pl.DataFrame) -> dict[str, dict[str, int]]:
    names = tuple(f"{rule}_{suffix}" for rule in RULES for suffix in SERIES_20_SUFFIXES)
    if daily.columns != ["trade_date", "series", "pnl_jpy"] or daily.height != N_DAYS * len(names):
        raise GateError("R016 daily ledger schema or fixed 20-series length differs")
    result: dict[str, dict[str, int]] = {}
    for name in names:
        subset = daily.filter(pl.col("series") == name).sort("trade_date")
        if subset.height != N_DAYS:
            raise GateError(f"R016 daily ledger lacks a complete {name} axis")
        values = {str(row["trade_date"]): int(row["pnl_jpy"]) for row in subset.to_dicts()}
        if len(values) != N_DAYS:
            raise GateError(f"R016 daily ledger has duplicate dates for {name}")
        result[name] = values
    if len({tuple(values) for values in result.values()}) != 1:
        raise GateError("R016 fixed 20-series daily axes are not identical")
    return result


def _load_daily_p2(
    research_root: Path, daily_maps: dict[str, dict[str, int]], r016_validation: dict[str, object]
) -> tuple[dict[str, list[int]], list[dict[str, object]], list[dict[str, object]]]:
    campaigns = r016_validation.get("campaigns")
    if not isinstance(campaigns, dict):
        raise GateError("R016 campaign reconciliation record is unavailable")
    r017_root = research_root / R017_ID
    r017_gate = _read_json(r017_root / "gate_record.json")
    r017_costs = _read_json(r017_root / "cost_hurdles.json")
    if r017_gate.get("status") != "PASS":
        raise GateError("R017 saved gate record is not PASS")
    r017_rows = r017_costs.get("rows")
    if not isinstance(r017_rows, list):
        raise GateError("R017 saved cost-hurdle rows are unavailable")
    cost_by_rule = {str(row.get("rule")): row for row in r017_rows if isinstance(row, dict)}
    if set(cost_by_rule) != set(RULES):
        raise GateError("R017 saved cost-hurdle rules differ")
    p2_by_rule: dict[str, list[int]] = {}
    daily_rows: list[dict[str, object]] = []
    cost_rows: list[dict[str, object]] = []
    for rule, (campaign, condition) in R016_A_CONDITIONS.items():
        root = research_root / campaign
        config = _read_json(root / "effective_config.json")
        manifest = _read_json(root / condition / "run_manifest.json")
        metrics = _read_json(root / condition / "metrics.json").get("overall")
        trades = pl.read_parquet(root / condition / "trades.parquet")
        multiplier = config.get("instrument", {}).get("instrument", {}).get("contract_multiplier")
        if manifest.get("costs") != {"fee_jpy_per_side": 30, "slippage_ticks_per_side": 1}:
            raise GateError(f"{rule} cost manifest differs from the frozen contract")
        if multiplier != 100 or "qty" not in trades.columns or trades.filter(pl.col("qty") != 1).height:
            raise GateError(f"{rule} saved qty or multiplier differs from the frozen contract")
        required_columns = {"trade_date", "net_pnl_jpy", "fees_jpy", "slippage_cost_jpy"}
        if not required_columns <= set(trades.columns):
            raise GateError(f"{rule} A ledger lacks required cost columns")
        if trades.filter(
            (pl.col("fees_jpy") + pl.col("slippage_cost_jpy") != 1060)
            | (pl.col("fees_jpy") + 2 * pl.col("slippage_cost_jpy") != 2060)
        ).height:
            raise GateError(f"{rule} has a saved trade outside C1=1060/C2=2060")
        grouped = trades.group_by("trade_date").agg(
            pl.col("net_pnl_jpy").sum().alias("p1"),
            pl.col("fees_jpy").sum().alias("fees"),
            pl.col("slippage_cost_jpy").sum().alias("slippage"),
        )
        daily_costs = {
            str(row["trade_date"]): (int(row["p1"]), int(row["fees"]), int(row["slippage"]))
            for row in grouped.to_dicts()
        }
        p0_map = daily_maps[f"{rule}_P0_A"]
        p1_map = daily_maps[f"{rule}_P1_A"]
        p2_values: list[int] = []
        for day in p0_map:
            p1, fees, slippage = daily_costs.get(day, (0, 0, 0))
            if p1_map[day] != p1:
                raise GateError(f"{rule} R016 P1 and saved A daily ledger differ on {day}")
            value = p2_daily(p0_map[day], p1, fees, slippage)
            p2_values.append(value)
            daily_rows.append(
                {
                    "trade_date": day, "rule": rule, "P0_A_jpy": p0_map[day],
                    "P1_A_jpy": p1, "fees_jpy": fees, "slippage_jpy": slippage, "P2_A_jpy": value,
                }
            )
        totals = {
            "trade_count": trades.height,
            "fees_jpy": int(trades.get_column("fees_jpy").sum()),
            "slippage_cost_jpy": int(trades.get_column("slippage_cost_jpy").sum()),
            "net_pnl_jpy": int(trades.get_column("net_pnl_jpy").sum()),
        }
        if not isinstance(metrics, dict) or any(metrics.get(key) != value for key, value in totals.items()):
            raise GateError(f"{rule} saved A metrics differ from its ledger")
        r017_row = cost_by_rule[rule]
        c1 = totals["fees_jpy"] + totals["slippage_cost_jpy"]
        c2 = totals["fees_jpy"] + 2 * totals["slippage_cost_jpy"]
        r017_cost_fields = {
            "trade_count": totals["trade_count"], "fees_jpy": totals["fees_jpy"],
            "slippage_cost_jpy": totals["slippage_cost_jpy"], "c1_total_jpy": c1,
            "c2_total_jpy": c2,
        }
        if any(r017_row.get(key) != value for key, value in r017_cost_fields.items()):
            raise GateError(f"{rule} R017 cost hurdle does not match the saved A ledger")
        validation_rule = campaigns.get(rule)
        if not isinstance(validation_rule, dict):
            raise GateError(f"R016 validation lacks {rule}")
        reconciled = validation_rule.get("conditions", {}).get("A", {}).get("reconciled_metrics")
        if not isinstance(reconciled, dict) or any(reconciled.get(key) != value for key, value in totals.items()):
            raise GateError(f"{rule} R016 A reconciliation differs from the saved ledger")
        p2_by_rule[rule] = p2_values
        cost_rows.append(
            {
                "rule": rule, "trade_count": trades.height, "fees_jpy": totals["fees_jpy"],
                "slippage_cost_jpy": totals["slippage_cost_jpy"], "C1_total_jpy": c1,
                "C2_total_jpy": c2, "C1_jpy_per_trade": c1 / trades.height,
                "C2_jpy_per_trade": c2 / trades.height, "C1_jpy_per_trade_date": c1 / N_DAYS,
                "C2_jpy_per_trade_date": c2 / N_DAYS, "frozen_trade_contract_pass": True,
            }
        )
    return p2_by_rule, daily_rows, cost_rows


def run_r018(repo: Path) -> Path:
    """Write the append-only R018 economic upper-bound diagnostic."""
    research_root = repo / "results" / "research"
    output = research_root / DIAGNOSTIC_ID
    if output.exists():
        raise FileExistsError(f"diagnostic output already exists: {output}")
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
        means_20, replicates_20, q20, r016_record = _load_r016(research_root)
        r016_validation = r016_record["r016_validation"]
        if not isinstance(r016_validation, dict):
            raise GateError("R016 validation record is unavailable")
        daily = pl.read_parquet(research_root / R016_ID / "daily_series.parquet")
        daily_maps = _daily_series_maps(daily)
        p2_by_rule, daily_rows, cost_rows = _load_daily_p2(research_root, daily_maps, r016_validation)
        axis = list(daily_maps["R011_P0_A"])
        means_25 = dict(means_20)
        for rule in RULES:
            direct_mean = fmean(p2_by_rule[rule])
            linear_mean = p2_linear(means_20[f"{rule}_P0_A"], means_20[f"{rule}_P1_A"])
            if not math.isclose(direct_mean, linear_mean, abs_tol=1e-9):
                raise GateError(f"{rule} daily and mean-level P2 linear transformations differ")
            means_25[f"{rule}_{P2_SUFFIX}"] = direct_mean
        names_20 = tuple(means_20)
        names_25 = tuple(means_25)
        block_results: dict[str, object] = {}
        replicate_rows: list[dict[str, object]] = []
        decisions_by_block: dict[int, dict[str, str]] = {}
        q_checks: dict[str, object] = {}
        for block in BLOCK_LENGTHS:
            augmented = [augmented_replicate(row, names_20) for row in replicates_20[block]]
            q_reproduced, _ = simultaneous_q(means_20, replicates_20[block])
            if not math.isclose(q_reproduced, q20[block], abs_tol=1e-9):
                raise GateError(f"R016 q20 reproduction failed at block {block}")
            q25, t_values = simultaneous_q(means_25, augmented)
            if q25 + 1e-9 < q20[block]:
                raise GateError(f"q25 is below preserved q20 at block {block}")
            intervals = {
                name: {"lower": means_25[name] - q25, "upper": means_25[name] + q25}
                for name in names_25
            }
            decisions = {
                rule: upper_bound_decision(
                    intervals[f"{rule}_{P2_SUFFIX}"]["lower"],
                    intervals[f"{rule}_{P2_SUFFIX}"]["upper"],
                )
                for rule in RULES
            }
            decisions_by_block[block] = decisions
            q_checks[str(block)] = {"q20_reproduced": q_reproduced, "q20_saved": q20[block], "q25": q25, "q25_not_less_than_q20": True}
            block_results[str(block)] = {
                "q25_jpy_per_trade_date": q25, "q20_preserved_jpy_per_trade_date": q20[block],
                "intervals_jpy_per_trade_date": intervals, "P2_upper_bound_decisions": decisions,
            }
            for iteration, (row, t_value) in enumerate(zip(augmented, t_values, strict=True)):
                replicate_rows.append(
                    cast(dict[str, object], {"block_length": block, "iteration": iteration, "T25": t_value} | row)
                )
        pl.DataFrame(daily_rows).write_parquet(output / "daily_p2.parquet")
        pl.DataFrame(replicate_rows).write_parquet(output / "bootstrap_replicates_25.parquet")
        _write_json(output / "cost_reconciliation.json", {"definition": "C1=fees+slippage; C2=fees+2*slippage; P2=P0-C2=P1-slippage", "rows": cost_rows})
        _write_json(output / "intervals.json", {"series_count": len(names_25), "means_jpy_per_trade_date": means_25, "by_block_length": block_results})
        primary_passes = {
            rule: decisions_by_block[PRIMARY_BLOCK_LENGTH][rule] == "ECONOMIC_BREAK_EVEN_EXCLUDED"
            for rule in RULES
        }
        robust_passes = {
            rule: all(decisions_by_block[block][rule] == "ECONOMIC_BREAK_EVEN_EXCLUDED" for block in BLOCK_LENGTHS)
            for rule in RULES
        }
        proposition = all(primary_passes.values())
        robust_proposition = all(robust_passes.values())
        _write_json(output / "gate_record.json", {
            "status": "PASS", "access_scope": "only listed saved R016/R017/R011--R015 artefacts; no bars/raw/external prices/backtests/OOS/Final Holdout",
            "common_trade_date_count": len(axis), "preserved_r016_q_and_q25_checks": q_checks,
            "r012_r015_zero_extension_inherited": r016_validation.get("extensions"),
            "r004_quarantine_validation_inherited": {rule: r016_validation.get("campaigns", {}).get(rule, {}).get("quarantine") for rule in RULES},
        })
        _write_json(output / "validation.json", {
            "synthetic_test_coverage": [
                "daily varying fees/slippage, no-trade days, long and short ledger rows",
                "no slippage double deduction; both daily P2 formulas",
                "aligned replicate transformation and mean identity",
                "25-series family expansion and q25>=q20",
                "strict upper-bound decision",
            ],
            "runtime_checks": {
                "all_saved_A_trades_C1_1060_C2_2060": True,
                "daily_P0_P1_cost_reconciliation": True,
                "daily_and_replicate_linear_transforms": True,
                "preserved_R016_q20_reproduction": q_checks,
                "fixed_25_series_family": len(names_25) == 25,
                "strict_upper_bound_rule": True,
            },
        })
        final = {
            "status": "COMPLETE",
            "decision": "ALL_FIVE_TWO_TICK_ECONOMIC_UPPER_BOUNDS_STRICTLY_NEGATIVE" if proposition else "ECONOMIC_BREAK_EVEN_NOT_EXCLUDED_FOR_ALL_FIVE",
            "primary_block_length": PRIMARY_BLOCK_LENGTH,
            "per_rule_primary_pass": primary_passes,
            "proposition_supported": proposition,
            "block_length_robust_per_rule": robust_passes,
            "block_length_robust_proposition": robust_proposition,
            "quality_status": "PASS_LIMITED_INHERITED",
            "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
            "maintained_states": {"R011-R015": "REJECT", "R009": "INCONCLUSIVE", "R003/R010": "BLOCKED"},
            "interpretation": "P2 is a fixed saved-path two-tick-cost diagnostic only, not a two-tick re-execution or attainable-profit estimate.",
            "limitations": [
                "The approximate simultaneous intervals cover only the fixed 25 saved-ledger series, not adaptive research-wide exploration.",
                "This is a post-hoc Development diagnostic; it is not independent confirmation, future-profit evidence, or a proof that predictive power is zero.",
                "The moving-block bootstrap inherits a stationarity-style approximation and the R004 post-hoc isolation, continuous-series, real-contract/roll, and provider limitations.",
            ],
        }
        _write_json(output / "decision.json", final)
        _write_json(output / "COMPLETED.json", final)
        _write_json(output / "reproducibility.json", {"command": "python scripts/run_r018_d001.py", "iterations_per_block": ITERATIONS, "blocks": list(BLOCK_LENGTHS), "source_manifest": "source_manifest.json", "input_manifest": "input_manifest.json"})
    except GateError as error:
        _write_json(output / "BLOCKED.json", {"status": "INPUT_OR_EXECUTION_GATE_FAILED", "reason": str(error), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    return output
