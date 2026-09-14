"""Execute the preregistered Development-only R042-Q003 experiment."""

from __future__ import annotations

import importlib.util
from math import log
from pathlib import Path
from typing import Any, cast

from n225m_bt.io.manifest import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "scripts" / "run_r042_q001_night_terminal_range_followthrough.py"
IDENTIFIER = "r042-q003-20260914-night-terminal-continuous-range-03"
SEED = 20260922


def load_shared() -> Any:
    spec = importlib.util.spec_from_file_location("r042_q002_shared", SHARED)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load shared R042 execution runner: {SHARED}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ols_beta(
    rows: list[dict[str, object]], *, fixed_mean_z: float | None = None
) -> tuple[float, dict[str, object]]:
    """Return the terminal coefficient from the fixed continuous-range OLS."""
    usable = [
        row
        for row in rows
        if row.get("raw_return_reason") == "OK"
        and row.get("base_event_status") in {"terminal", "nonterminal"}
        and float(cast(float, row["ON_points"])) > 0
        and float(cast(float, row["RN_points"])) > 0
    ]
    if not usable:
        raise ValueError("OLS has no complete eligible Development observations")
    z_values = [log(float(row["RN_points"])) - log(float(row["ON_points"])) for row in usable]
    mean_z = sum(z_values) / len(z_values) if fixed_mean_z is None else fixed_mean_z
    years = sorted({str(row["trade_date"])[:4] for row in usable})
    base_year = years[0]
    matrix: list[list[float]] = []
    response: list[float] = []
    for row, z in zip(usable, z_values, strict=True):
        year = str(row["trade_date"])[:4]
        centered = z - mean_z
        matrix.append(
            [
                1.0,
                1.0 if row["base_event_status"] == "terminal" else 0.0,
                centered,
                centered * centered,
                1.0 if float(cast(float, row["rN_points"])) > 0 else 0.0,
                *(1.0 if year == level else 0.0 for level in years if level != base_year),
            ]
        )
        response.append(float(cast(int, row["raw_sign_adjusted_0846_0946_jpy"])))
    width = len(matrix[0])
    normal = [[sum(row[i] * row[j] for row in matrix) for j in range(width)] for i in range(width)]
    target = [sum(row[i] * y for row, y in zip(matrix, response, strict=True)) for i in range(width)]
    rank = 0
    for pivot_col in range(width):
        pivot_row = max(range(pivot_col, width), key=lambda index: abs(normal[index][pivot_col]))
        if abs(normal[pivot_row][pivot_col]) < 1e-10:
            raise ValueError("OLS design matrix is not full rank")
        normal[pivot_col], normal[pivot_row] = normal[pivot_row], normal[pivot_col]
        target[pivot_col], target[pivot_row] = target[pivot_row], target[pivot_col]
        pivot = normal[pivot_col][pivot_col]
        normal[pivot_col] = [value / pivot for value in normal[pivot_col]]
        target[pivot_col] /= pivot
        for row_index in range(width):
            if row_index == pivot_col:
                continue
            factor = normal[row_index][pivot_col]
            normal[row_index] = [
                value - factor * pivot_value
                for value, pivot_value in zip(normal[row_index], normal[pivot_col], strict=True)
            ]
            target[row_index] -= factor * target[pivot_col]
        rank += 1
    return target[1], {
        "formula": "y=alpha+beta*T+gamma1*(z-mean_z)+gamma2*(z-mean_z)^2+eta*U+calendar-year fixed effects+epsilon",
        "y": "sign(rN)*(open_09:46-open_08:46), zero-tick and pre-fee, JPY per contract",
        "z": "ln(RN/ON)",
        "mean_z": mean_z,
        "year_fixed_effect_base": base_year,
        "year_levels": years,
        "observation_count": len(usable),
        "design_columns": width,
        "rank": rank,
        "full_rank": rank == width,
        "beta_terminal_jpy": target[1],
    }


def preregistration(source: dict[str, object], inputs: dict[str, object], files: dict[str, str]) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R042-Q003",
        "status": "frozen_before_price_statistics_eligibility_or_pnl",
        "seed": SEED,
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; no raw, volume, external prices, OOS, or Final Holdout.",
        "supersedes": {
            "study": "R042-Q002",
            "reason": "Q002's diagnostic rolling RN/ON requirement left fewer than 50 valid references in every prior-60 scheduled-night window and therefore produced zero trades. Q003 removes only rolling QM and low/high classification; it does not change the night state, terminal rule, times, or execution path. Q003-01 is retained as BLOCKED because only its post-run Gross-decomposition audit used a reversed entry-slippage sign. Q003-02 is retained as BLOCKED because its bootstrap re-centered z per replicate; Q003-03 fixes the center at the full eligible Development mean before the price read.",
        },
        "duplicate_review": {
            "R001_R042_Q002": "No prior registration combines full same-trade-date normal-night ON/CN/HN/LN, equality-inclusive directional outer-quartile terminal state, fresh 08:45 signal, 08:46 entry, 09:46 exit, and the fixed continuous RN/ON OLS adjustment. Q002 used a rolling diagnostic layer instead of this adjustment.",
            "conclusion": "No duplicate; no alternative trading specification created.",
        },
        "hypothesis": "A same-trade-date complete normal-night close in the directional outer quartile predicts post-cost sign(rN) follow-through from 08:46 to 09:46, exceeds all valid, nonterminal, fixed-direction and reverse controls, remains positive after 09:00, and has positive terminal coefficient after continuous range, direction, and calendar-year adjustment.",
        "fixed_rule": {
            "state": "Exactly one same-trade-date scheduled normal night; require every scheduled eligible minute. ON/CN are scheduled first open/final close, HN/LN are normal-minute extrema; reject isolation, missing/ineligible, outside Development, ON<=0, RN<=0 or rN=0; no observed-end, auction, force-flat, or older-night substitute.",
            "terminal": "rN>0: 4*(CN-LN)>=3*RN; rN<0: 4*(HN-CN)>=3*RN. Equality is terminal. No rolling reference, quantile, absolute or relative range filter/classification is used.",
            "conditions": "A terminal sign(rN); B all valid sign(rN); C nonterminal sign(rN); D/E terminal always buy/sell; F terminal -sign(rN); A2/A3 2/3 ticks per side; A_delay 08:47 entry; A_tse 09:00 entry after 08:59 signal.",
            "execution": "Fresh day signal after 08:45 close fills 08:46 open; fixed exit signal 09:45 fills 09:46 open. A_tse signal after 08:59 fills 09:00. The 08:45-and-later prices never affect state, side, selection or cancellation. One contract/trade_date/position; no cross-session pending, stop, target, re-entry, update or early exit.",
        },
        "ols": {
            "formula": "y=alpha+beta*T+gamma1*(z-mean(z))+gamma2*(z-mean(z))^2+eta*U+calendar-year fixed effects+epsilon",
            "population": "B_all complete eligible Development nights; y=sign(rN)*(open_09:46-open_08:46), z=ln(RN/ON), U=1[rN>0]. mean(z) is computed once on that same population. Full rank is required.",
        },
        "evaluation": {
            "axis": "Fixed 1,111 Development day trade_dates excluding isolated day sessions; every skip/cancel/no-trade remains 0JPY.",
            "bootstrap": "20 trade-date noncircular moving blocks, 10,000 common-index repetitions, tail truncation, linear percentiles, seed 20260922; recompute conditional means and OLS each repetition.",
            "information_gate": "B>=700; A/C>=200; A long/short>=70; terminal/nonterminal x rN direction four groups each>=60.",
            "decision": "BLOCKED for input/synthetic/execution/accounting/OLS identification failure; INCONCLUSIVE for information failure; else REJECT unless all fixed criteria pass; all pass is Development-only INVESTIGATE.",
        },
        "inputs": {"physical": inputs, "r004_fixed_isolation": "45 sessions/27,345 bars removed; 2,216 sessions/1,326,086 bars retained; hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa", "quality_ceiling": "PASS_LIMITED"},
        "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": files, "implementation_files_hash": canonical_hash(files), "input_manifest_hash": canonical_hash(inputs), "seed": SEED},
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def main() -> None:
    shared = load_shared()
    original_bootstrap = shared.bootstrap
    shared.IDENTIFIER = IDENTIFIER
    shared.OUT = ROOT / "results" / "research" / IDENTIFIER
    shared.SEED = SEED
    shared.REQUIRE_ROLLING_RANGE_LAYER = False
    shared.Q003_CONTINUOUS_RANGE_ADJUSTMENT = True
    shared.IMPLEMENTATION_SCRIPT = Path(__file__).resolve()
    shared.EXTRA_IMPLEMENTATION_FILES = (SHARED.relative_to(ROOT),)
    shared.preregistration = preregistration

    full_rows: list[dict[str, object]] = []

    def beta(rows: list[dict[str, object]]) -> float:
        coefficient, audit = ols_beta(rows, fixed_mean_z=shared.FIXED_MEAN_Z)
        shared.OLS_AUDIT = audit
        return coefficient

    shared.ols_terminal_beta = beta

    def bootstrap(*args: Any) -> dict[str, object]:
        records = cast(dict[str, list[dict[str, object]]], args[1])
        full_rows.extend(
            row for row in records["B_all"] if row.get("raw_return_reason") == "OK"
        )
        _, full_audit = ols_beta(full_rows)
        shared.FIXED_MEAN_Z = cast(float, full_audit["mean_z"])
        beta(full_rows)
        result = original_bootstrap(*args)
        result["ols"] = full_audit
        return result

    shared.bootstrap = bootstrap
    shared.main()


if __name__ == "__main__":
    main()
