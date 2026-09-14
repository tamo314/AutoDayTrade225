"""Execute the preregistered, Development-only R053-Q001 experiment."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, floor, log
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, cast

import numpy as np

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r053 import (
    SCHEDULE,
    TSE_SCHEDULE_ID,
    R053QNotIdentifiableError,
    fwl_delta,
    r053_event,
)
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r053-q002-20260915-tse-afternoon-open-gap-fade-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
AXIS = (
    ROOT
    / "results"
    / "research"
    / "r012-q001-20260914-night-direction-followthrough-01"
    / "daily_net_pnl_aligned.json"
)
R045 = ROOT / "results" / "research" / "r045-q001-20260914-tse-lunch-placebo-reversal-03"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20261001
PINV_RCOND = 1e-12
RESIDUAL_SS_TOLERANCE = 1e-12
BASE = ("A", "B", "C", "A_continue", "A_buy", "A_sell")
VARIANTS = {"A2": (2, 0, 90, 30), "A3": (3, 0, 90, 30), "A_delay": (1, 1, 90, 30)}
SENSITIVITIES = {
    "A85": (1, 0, 85, 30),
    "A95": (1, 0, 95, 30),
    "A_h15": (1, 0, 90, 15),
    "A_h45": (1, 0, 90, 45),
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fixed_axis() -> list[str]:
    result = json.loads(AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(result, list) or len(result) != 1111 or len(set(result)) != 1111:
        raise ValueError("R053 requires fixed 1,111 trade_date axis")
    return cast(list[str], result)


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[date]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in bar.quality_flags for bar in included
        ),
    }
    expected = {
        "parent_data_version": PARENT_HASH,
        "quarantined_sessions": 45,
        "quarantined_bars": 27345,
        "included_sessions": 2216,
        "included_bars": 1326086,
        "quarantined_session_list_hash": QUARANTINE_HASH,
        "included_tick_grid_violations": 0,
    }
    mismatches = {
        key: {"actual": audit[key], "expected": value}
        for key, value in expected.items()
        if audit[key] != value
    }
    audit["mismatches"] = mismatches
    if mismatches:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatches}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        {day for day, session in isolated if session is Session.DAY},
    )


def input_manifest(root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(root, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R045 / "institutional_evidence" / "cabinet_office_public_holidays.csv"
    if not source.exists():
        raise FileNotFoundError("R053 frozen TSE holiday evidence unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copied = destination / source.name
    copied.write_bytes(source.read_bytes())
    return TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932")), {
        "holiday_csv_sha256": digest(copied),
        "schedule_id": TSE_SCHEDULE_ID,
        "schedule": {
            "morning_start": SCHEDULE.morning_start.isoformat(),
            "morning_end_exclusive": SCHEDULE.morning_end.isoformat(),
            "afternoon_start": SCHEDULE.afternoon_start.isoformat(),
        },
    }


def selected(event: dict[str, object], condition: str, threshold: int) -> bool:
    if event.get("status") != "E" or not event.get("direction_eligible"):
        return False
    x = float(event["x"])
    if condition == "B":
        return x >= float(event["q75"])
    if condition == "C":
        return float(event["q75"]) <= x < float(event["q90"])
    return x >= float(event[f"q{threshold}"])


def side(event: dict[str, object], condition: str) -> str:
    gap_side = "long" if int(event["gap_sign"]) > 0 else "short"
    if condition == "A_continue":
        return gap_side
    if condition == "A_buy":
        return "long"
    if condition == "A_sell":
        return "short"
    return "short" if gap_side == "long" else "long"


def run_condition(
    base: list[dict[str, object]],
    bars: dict[date, list[Bar]],
    engine: BacktestEngine,
    condition: str,
    *,
    ticks: int,
    delay: int,
    threshold: int,
    holding: int,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for original in base:
        row, day = dict(original), date.fromisoformat(cast(str, original["trade_date"]))
        allowed = selected(row, condition, threshold)
        row.update(
            condition=condition,
            condition_eligible=allowed,
            requested_slippage_ticks=ticks,
            requested_delay_minutes=delay,
            requested_holding_minutes=holding,
        )
        if not allowed:
            row.update(status="skipped", condition_reason="DIRECTION_ZERO_OR_THRESHOLD_NOT_MET")
            records.append(row)
            continue
        entry = datetime.fromisoformat(cast(str, row["planned_entry_jst"]))
        exit_time = datetime.fromisoformat(cast(str, row[f"planned_exit{holding}_jst"]))
        direction = side(row, condition)
        row.update(side=direction, planned_exit_jst=exit_time.isoformat())
        result = engine.run(
            bars.get(day, []),
            R049FixedTimeStrategy(f"r053_{condition}", entry, exit_time, direction, delay),
            canonical_hash(
                {
                    "condition": condition,
                    "ticks": ticks,
                    "delay": delay,
                    "holding": holding,
                    "threshold": threshold,
                }
            ),
        )
        audit["canceled_orders"] += result.canceled_orders
        if len(result.trades) > 1:
            raise ValueError("R053 maximum one position violated")
        if not result.trades:
            row.update(status="cancelled", condition_reason="ENGINE_NO_FILL_OR_EXIT")
            audit["cancelled"] += 1
            records.append(row)
            continue
        trade = result.trades[0]
        row.update(
            status="filled",
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            entry_signal_ts_jst=trade.entry_signal_ts.isoformat(),
            exit_signal_ts_jst=trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None,
            exit_reason=trade.exit_reason.value,
            gross_pnl_jpy=trade.gross_pnl_jpy,
            fees_jpy=trade.fees_jpy,
            slippage_cost_jpy=trade.slippage_cost_jpy,
            net_pnl_jpy=trade.net_pnl_jpy,
            entry_delay_minutes=round((trade.entry_ts - entry).total_seconds() / 60),
            exit_delay_minutes=round((trade.exit_ts - exit_time).total_seconds() / 60),
        )
        trades.append(trade)
        records.append(row)
    return (
        tuple(
            replace(trade, trade_id=f"trade-{index:06d}")
            for index, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
        ),
        records,
        dict(audit),
    )


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    lo, hi = floor(point), ceil(point)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (point - lo)


def regression_design(rows: list[dict[str, object]]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    years = sorted({date.fromisoformat(cast(str, row["trade_date"])).year for row in rows})
    matrix: list[list[float]] = []
    y: list[float] = []
    for row in rows:
        year = date.fromisoformat(cast(str, row["trade_date"])).year
        matrix.append(
            [
                1.0,
                float(row["Q"]),
                float(row["z"]),
                float(row["morning_reverse_adjusted_return_bps"]),
                float(row["morning_range_bps"]),
                float(row["gap_up"]),
                *[1.0 if year == value else 0.0 for value in years[1:]],
            ]
        )
        y.append(float(row["y_gross_jpy"]))
    return (
        np.asarray(matrix, dtype=float),
        np.asarray(y, dtype=float),
        [
            "intercept",
            "Q",
            "z_ln_x_over_q90",
            "morning_reverse_adjusted_return_bps",
            "morning_range_bps",
            "gap_up",
            *[f"calendar_year_{year}" for year in years[1:]],
        ],
    )


def ols(rows: list[dict[str, object]]) -> tuple[float, int, int, float]:
    design, y, _ = regression_design(rows)
    rank = int(np.linalg.matrix_rank(design))
    delta, q_residual_ss = fwl_delta(
        design[:, 1],
        y,
        np.delete(design, 1, axis=1),
        pinv_rcond=PINV_RCOND,
        residual_ss_tolerance=RESIDUAL_SS_TOLERANCE,
    )
    return delta, rank, int(design.shape[1]), q_residual_ss


def bootstrap_days(axis: list[str]) -> list[list[int]]:
    rng, count, block = Random(SEED), len(axis), 20
    result: list[list[int]] = []
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            indices.extend(range((start := rng.randrange(count - block + 1)), start + block))
        result.append(indices[:count])
    return result


def technical_fwl_gate(regression: list[dict[str, object]], axis: list[str]) -> dict[str, object]:
    """Ensure Q is identified in the observed sample and every frozen bootstrap draw."""
    design, y, columns = regression_design(regression)
    row_index = {cast(str, row["trade_date"]): index for index, row in enumerate(regression)}
    observed_rank = int(np.linalg.matrix_rank(design))
    try:
        _, observed_q_ss = fwl_delta(
            design[:, 1],
            y,
            np.delete(design, 1, axis=1),
            pinv_rcond=PINV_RCOND,
            residual_ss_tolerance=RESIDUAL_SS_TOLERANCE,
        )
    except R053QNotIdentifiableError as exc:
        return {
            "status": "BLOCKED",
            "stage": "observational_sample",
            "error": str(exc),
            "columns": columns,
            "rank": observed_rank,
            "column_count": int(design.shape[1]),
        }
    for replicate, indices in enumerate(bootstrap_days(axis), 1):
        weights = np.zeros(len(regression), dtype=float)
        for axis_index in indices:
            row = row_index.get(axis[axis_index])
            if row is not None:
                weights[row] += 1.0
        included = weights > 0
        root_weights = np.sqrt(weights[included])
        weighted = design[included] * root_weights[:, None]
        try:
            fwl_delta(
                weighted[:, 1],
                y[included] * root_weights,
                np.delete(weighted, 1, axis=1),
                pinv_rcond=PINV_RCOND,
                residual_ss_tolerance=RESIDUAL_SS_TOLERANCE,
            )
        except R053QNotIdentifiableError as exc:
            return {
                "status": "BLOCKED",
                "stage": "bootstrap_replicate",
                "error": str(exc),
                "replicate_number_one_based": replicate,
                "sampled_axis_indices_zero_based": indices,
                "sampled_trade_dates": [axis[index] for index in indices],
                "columns": columns,
                "rank": int(np.linalg.matrix_rank(weighted)),
                "column_count": int(weighted.shape[1]),
                "singular_values": [float(value) for value in np.linalg.svd(weighted, compute_uv=False)],
            }
    return {
        "status": "PASS",
        "stage": "all_10000_bootstrap_replicates",
        "columns": columns,
        "rank": observed_rank,
        "column_count": int(design.shape[1]),
        "observed_Q_residual_sum_of_squares": observed_q_ss,
        "seed": SEED,
        "repetitions": 10_000,
        "block_length_trade_dates": 20,
        "pinv_rcond": PINV_RCOND,
        "residual_ss_tolerance": RESIDUAL_SS_TOLERANCE,
    }


def bootstrap(
    daily: dict[str, dict[str, int]],
    records: dict[str, list[dict[str, object]]],
    regression: list[dict[str, object]],
    axis: list[str],
) -> dict[str, object]:
    labels = (
        "A_mean_net_jpy",
        "A_minus_B_conditional_mean_jpy",
        "A_minus_C_conditional_mean_jpy",
        "A_minus_A_continue_conditional_mean_jpy",
        "A_minus_A_buy_conditional_mean_jpy",
        "A_minus_A_sell_conditional_mean_jpy",
        "delta_ols_jpy",
    )
    samples: dict[str, list[float]] = {label: [] for label in labels}
    filled = {
        name: {
            cast(str, row["trade_date"]): int(row["net_pnl_jpy"])
            for row in rows
            if row.get("status") == "filled"
        }
        for name, rows in records.items()
    }
    x, y, _ = regression_design(regression)
    row_index = {cast(str, row["trade_date"]): index for index, row in enumerate(regression)}

    def resampled_delta(days: list[str]) -> float:
        """Weighted least squares exactly equals duplicated bootstrap rows."""
        weights = np.zeros(len(regression), dtype=float)
        for value in days:
            index = row_index.get(value)
            if index is not None:
                weights[index] += 1.0
        included = weights > 0
        root_weights = np.sqrt(weights[included])
        design = x[included] * root_weights[:, None]
        return fwl_delta(
            design[:, 1],
            y[included] * root_weights,
            np.delete(design, 1, axis=1),
            pinv_rcond=PINV_RCOND,
            residual_ss_tolerance=RESIDUAL_SS_TOLERANCE,
        )[0]

    for indices in bootstrap_days(axis):
        days = [axis[index] for index in indices]
        samples["A_mean_net_jpy"].append(fmean(daily["A"][day] for day in days))
        for name in ("B", "C", "A_continue", "A_buy", "A_sell"):
            a, b = (
                [filled["A"][day] for day in days if day in filled["A"]],
                [filled[name][day] for day in days if day in filled[name]],
            )
            samples[f"A_minus_{name}_conditional_mean_jpy"].append(
                fmean(a) - fmean(b) if a and b else float("nan")
            )
        samples["delta_ols_jpy"].append(resampled_delta(days))
    point = {"A_mean_net_jpy": fmean(daily["A"].values()), "delta_ols_jpy": ols(regression)[0]}
    for name in ("B", "C", "A_continue", "A_buy", "A_sell"):
        point[f"A_minus_{name}_conditional_mean_jpy"] = fmean(filled["A"].values()) - fmean(
            filled[name].values()
        )
    if any(any(np.isnan(value) for value in values) for values in samples.values()):
        raise ValueError("R053 bootstrap missing conditional population")
    return {
        "method": "20 trade_date noncircular moving-block bootstrap; common indices, tail truncation, linear percentile; delta uses FWL with a nuisance-only Moore-Penrose projection",
        "repetitions": 10000,
        "seed": SEED,
        "block_length_trade_dates": 20,
        "pinv_rcond": PINV_RCOND,
        "residual_ss_tolerance": RESIDUAL_SS_TOLERANCE,
        **{
            name: {
                "estimate": point[name],
                "ci95_percentile_linear": [percentile(values, 0.025), percentile(values, 0.975)],
            }
            for name, values in samples.items()
        },
    }


def build_regression(
    base: list[dict[str, object]], bars_by_day: dict[date, list[Bar]]
) -> list[dict[str, object]]:
    regression: list[dict[str, object]] = []
    for row in base:
        if row.get("status") != "E" or not row.get("direction_eligible"):
            continue
        day = cast(str, row["trade_date"])
        lookup = {bar.ts_jst: bar for bar in bars_by_day[date.fromisoformat(day)]}
        entry = datetime.fromisoformat(cast(str, row["planned_entry_jst"]))
        exit_time = datetime.fromisoformat(cast(str, row["planned_exit30_jst"]))
        q90, x = float(row["q90"]), float(row["x"])
        morning = [
            lookup[entry.replace(hour=9, minute=0) + __import__("datetime").timedelta(minutes=index)]
            for index in range(150)
        ]
        if q90 <= 0 or x <= 0:
            raise ValueError("R053 preregistered OLS covariate invalid")
        sign = int(row["gap_sign"])
        regression.append(
            {
                "trade_date": day,
                "y_gross_jpy": -sign * (lookup[exit_time].open - lookup[entry].open) * 100,
                "Q": int(x >= q90),
                "z": log(x / q90),
                "morning_reverse_adjusted_return_bps": row["morning_reverse_adjusted_return_bps"],
                "morning_range_bps": (
                    max(bar.high for bar in morning) - min(bar.low for bar in morning)
                )
                / morning[0].open
                * 10000,
                "gap_up": int(sign > 0),
            }
        )
    return regression


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable R053 output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    runtime = baseline.model_copy(
        update={
            "risk": baseline.risk.model_copy(
                update={"new_entry_cutoff_minutes_before_session_close": 0}
            )
        }
    )
    if runtime.execution.slippage_ticks != 1 or runtime.fees.jpy_per_side_per_contract != 30:
        raise ValueError("R053 requires 1 tick and JPY30 per side")
    source, inputs, cash = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        input_manifest(data_config.gold_root),
        None,
    )
    cash, evidence = cash_calendar()
    files = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r053.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r053_q001.py"),
    ]
    implementation = {str(path): digest(ROOT / path) for path in files}
    plan: dict[str, object] = {
        "experiment_id": IDENTIFIER,
        "study_id": "R053-Q002",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Development 2021-01-01..2025-06-30 only; known-Development exploratory result, not independent confirmation.",
        "supersedes": "R053-Q001 (...-06) is an immutable BLOCKED record. Its first rank-deficient bootstrap replicate is independently archived in ...-07-bootstrap-rank-audit: 2021, the omitted calendar-year baseline, was absent, making the intercept collinear with the retained 2022--2025 dummies. This R053-Q002 changes only the preregistered delta estimator's rank-deficiency handling: every observed/resampled delta is Frisch-Waugh-Lovell with a Moore-Penrose inverse used only for the fixed nuisance span. No event, input, execution, cost, sensitivity, resampling, seed, statistic, or decision rule changes.",
        "duplicate_review": "R001-R052 reviewed before price statistics: R038/R045 use intralunch continuous futures returns/q75 and R051 uses a post-reopen 15-minute breakout. No registered study combines cM=scheduled morning-final close, oA=scheduled afternoon-first open, 120-day q75/q85/q90/q95 gap magnitude, next-scheduled-open entry and 30-minute reversal.",
        "hypothesis": "Extreme causal cM-to-oA gaps reverse toward cM during the following 30 scheduled minutes and exceed smaller-gap reversal and same-event continuation/fixed-direction controls.",
        "rule": "cM is last scheduled morning one-minute close; oA is first scheduled afternoon one-minute open; g=(oA-cM)/cM, x=abs(g). g is known only after oA. entry is the following scheduled open and exits are entry+15/+30/+45 scheduled opens. Exact prior 120 scheduled TSE days; >=100 valid x; nearest-rank q75/q85/q90/q95; no target inclusion/backfill; equality is upper. Common E requires morning open/cM/oA, q, entry and all exits. Missing/isolation/g=0 remain E but g=0 is direction-ineligible.",
        "conditions": "A x>=q90 fade -sign(g); B x>=q75 fade; C q75<=x<q90 fade; A_continue sign(g), A_buy, A_sell on A events. A2/A3 use 2/3 ticks; A_delay delays entry one scheduled minute without extension; A85/A95 and 15/45 holds only.",
        "ols": "all direction-eligible E: y=-sign(g)*(open(exit30)-open(entry))*100 JPY gross at zero tick/pre-fee; Q=1[x>=q90], z=ln(x/q90), reverse-adjusted morning return bps, morning range bps, gap-up, calendar-year FE. Delta is FWL: (M_Z Q)'(M_Z y)/(M_Z Q)'(M_Z Q), with Moore-Penrose inverse used only in M_Z for fixed nuisance Z, pinv rcond=1e-12 and residualized-Q SS tolerance=1e-12. Every replicate stops BLOCKED if SS<=tolerance; no discard/redraw/result-dependent column deletion. Nuisance collinearity and missing year dummies remain in Z.",
        "inputs": inputs,
        "input_manifest_hash": canonical_hash(inputs),
        "schedule": evidence,
        "implementation": implementation,
        "implementation_hash": canonical_hash(implementation),
        "source": source,
        "r004": "45 sessions/27345 bars quarantined; expected retained 2216/1326086",
        "evaluation": "fixed 1111 axis; 20-day noncircular MBB x10000 seed 20261001, common indices. Information E>=850,A>=90,B>=220,C>=100,A buy/sell>=30,A95>=40. If sufficient, all user-provided economic/robustness gates are mandatory.",
        "prohibited": [
            "OOS",
            "Final Holdout",
            "WFA",
            "rescue search",
            "other thresholds/rolling/times/filters",
            "Stop/Target/reentry/early exit",
        ],
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "status": "preregistered",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "seed": SEED,
            "plan_hash": canonical_hash(plan),
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    commands = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r053_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r053.py",
            "tests/test_r053_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r053.py",
        ],
    }
    validation: dict[str, Any] = {
        name: {"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr}
        for name, command in commands.items()
        for item in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation["coverage"] = (
        "holiday/regime boundaries/trade_date, endpoint/prefix, exact120/no-backfill/min100/nearest-rank/equality, missing/isolation/zero, signal-next-open/fixed exits/delay/cost/no double slip/max1, predicates/event paths/axis/OOS-final locks"
    )
    validation["status"] = (
        "PASS"
        if all(
            value["returncode"] == 0
            for value in validation.values()
            if isinstance(value, dict) and "returncode" in value
        )
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R053 static validation failed before Development price access")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    axis = fixed_axis()
    development = load_split(data_config.gold_root, "development")
    view, qaudit, isolated = quarantine(development)
    bars_by_day: dict[date, list[Bar]] = {}
    for bar in view.bars:
        if bar.session is Session.DAY:
            bars_by_day.setdefault(bar.trade_date, []).append(bar)
    for values in bars_by_day.values():
        values.sort(key=lambda item: item.ts_jst)
    if {day.isoformat() for day in bars_by_day} != set(axis):
        raise ValueError("R053 fixed 1111-date axis does not reproduce Development day bars")
    tse_days = [day for day in sorted(bars_by_day) if cash.is_open(day)]
    base = []
    for value in axis:
        day = date.fromisoformat(value)
        history = (
            [
                (prior, bars_by_day.get(prior), prior in isolated)
                for prior in tse_days[max(0, tse_days.index(day) - 120) : tse_days.index(day)]
            ]
            if day in tse_days
            else []
        )
        base.append(
            r053_event(day, bars_by_day.get(day), history, cash, quarantined=day in isolated)
        )
    write_json(OUT / "all_candidate_event_ledger.json", base)
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": qaudit,
            "fixed_axis": axis,
            "tse_open_days": len(tse_days),
            "physical_io": "Development normalized Parquet only; OOS and Final Holdout not selected.",
        },
    )
    regression = build_regression(base, bars_by_day)
    technical_gate = technical_fwl_gate(regression, axis)
    write_json(OUT / "technical_fwl_gate.json", technical_gate)
    if technical_gate["status"] != "PASS":
        write_json(
            OUT / "BLOCKED.json",
            {
                "experiment_id": IDENTIFIER,
                "status": "BLOCKED",
                "stage": "FWL_Q_identification",
                "failure": technical_gate,
                "action": "No replicate was discarded, redrawn, or altered; no trading performance, conditional PnL, OOS, WFA, or Final Holdout was generated.",
            },
        )
        return
    configs = (
        {name: (name, 1, 0, 90, 30) for name in BASE}
        | {name: ("A", *value) for name, value in VARIANTS.items()}
        | {name: ("A", *value) for name, value in SENSITIVITIES.items()}
    )
    records: dict[str, list[dict[str, object]]] = {}
    trades: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int]] = {}
    results: dict[str, dict[str, object]] = {}
    target_bars = [bar for values in bars_by_day.values() for bar in values]
    from n225m_bt.research.metrics import research_metrics

    for name, (condition, ticks, delay, threshold, holding) in configs.items():
        config = runtime.model_copy(
            update={"execution": runtime.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        filled, ledger, audit = run_condition(
            base,
            bars_by_day,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            condition,
            ticks=ticks,
            delay=delay,
            threshold=threshold,
            holding=holding,
        )
        series = dict.fromkeys(axis, 0)
        for trade in filled:
            series[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = OUT / name
        folder.mkdir()
        metrics = research_metrics(filled, target_bars)
        write_results(
            folder,
            filled,
            (),
            {"campaign_id": IDENTIFIER, "condition": name, "execution_audit": audit},
        )
        write_json(folder / "events.json", ledger)
        write_json(folder / "daily_net_pnl_aligned.json", series)
        write_json(folder / "research_metrics.json", metrics)
        records[name], trades[name], daily[name], results[name] = (
            ledger,
            filled,
            series,
            {"trade_count": len(filled), "metrics": metrics, "audit": audit},
        )
    a_days = [row["trade_date"] for row in records["A"] if row.get("status") == "filled"]
    path_fields = ("gap_sign", "planned_entry_jst", "planned_exit_jst")
    checks = {
        "A_subset_B_C_subset_B": all(
            (not selected(row, "A", 90) or selected(row, "B", 90))
            and (not selected(row, "C", 90) or selected(row, "B", 90))
            for row in base
        ),
        "predicates": all(
            (row.get("status") == "filled") == selected(base[index], condition, threshold)
            for name, (condition, _, _, threshold, _) in configs.items()
            for index, row in enumerate(records[name])
        ),
        "same_A_controls_path": all(
            all(
                records[name][index].get(field) == records["A"][index].get(field)
                for field in path_fields
            )
            for name in ("A_continue", "A_buy", "A_sell")
            for index, day in enumerate(axis)
            if day in a_days
        ),
        "continue_opposite_side": all(
            records["A"][index].get("side") != records["A_continue"][index].get("side")
            for index, day in enumerate(axis)
            if day in a_days
        ),
        "A_event_preserving_variants_same_event_side": all(
            all(
                records[name][index].get(field) == records["A"][index].get(field)
                for field in ("gap_sign", "side", "planned_entry_jst")
            )
            for name in ("A2", "A3", "A_h15", "A_h45")
            for index in range(len(axis))
            if records[name][index].get("status") == "filled"
        ),
        "threshold_sensitivity_predicate_and_fade_side": all(
            row.get("side") == side(base[index], "A")
            for name in ("A85", "A95")
            for index, row in enumerate(records[name])
            if row.get("status") == "filled"
        ),
        "delay_same_event_side_fixed_exit": all(
            records["A_delay"][index].get(field) == records["A"][index].get(field)
            for field in ("gap_sign", "side", "planned_exit_jst")
            for index, day in enumerate(axis)
            if day in a_days
        ),
        "fixed_axis": all(len(value) == 1111 for value in daily.values()),
        "one_trade_max": all(
            len({trade.trade_date for trade in value}) == len(value) for value in trades.values()
        ),
        "execution": all(
            row.get("exit_reason") == ExitReason.SIGNAL.value
            and row.get("exit_delay_minutes") == 0
            and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0)
            and int(row["net_pnl_jpy"]) == int(row["gross_pnl_jpy"]) - int(row["fees_jpy"])
            for name, rows in records.items()
            for row in rows
            if row.get("status") == "filled"
        ),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "status": "PASS" if all(checks.values()) else "BLOCKED",
            "checks": checks,
            "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees without double deduction.",
        },
    )
    if not all(checks.values()):
        raise ValueError("R053 execution/accounting gate failed")
    delta, rank, columns, q_residual_ss = ols(regression)
    boot = bootstrap(daily, records, regression, axis)
    boot["ols"] = {
        "delta": delta,
        "rank": rank,
        "columns": columns,
        "Q_residual_sum_of_squares": q_residual_ss,
        "rows": len(regression),
        "formula": "y~Q+ln(x/q90)+reverse-adjusted morning return bps+morning range bps+gap-up+calendar-year FE",
    }
    write_json(OUT / "bootstrap.json", boot)
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {"trade_dates": axis, "no_trade_value_jpy": 0, "series": daily},
    )
    write_json(OUT / "regression_ledger.json", regression)
    a_metrics = cast(dict[str, Any], cast(dict[str, object], results["A"])["metrics"])
    years = {
        str(year): sum(daily["A"][day] for day in axis if day.startswith(str(year)))
        for year in range(2021, 2026)
    }
    months = {
        f"{year}-{month:02d}": sum(
            daily["A"][day] for day in axis if day.startswith(f"{year}-{month:02d}")
        )
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }
    directions = {
        value: sum(
            row.get("status") == "filled" and row.get("side") == value for row in records["A"]
        )
        for value in ("long", "short")
    }
    info = {
        "E>=850": sum(row.get("status") == "E" for row in base) >= 850,
        "A>=90": len(trades["A"]) >= 90,
        "B>=220": len(trades["B"]) >= 220,
        "C>=100": len(trades["C"]) >= 100,
        "A_buy_sell>=30": all(value >= 30 for value in directions.values()),
        "A95>=40": len(trades["A95"]) >= 40,
    }

    def positive(name: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
        )

    gates = {
        "A_net_positive": a_metrics["overall"]["net_pnl_jpy"] > 0,
        "A_pf_gt1": a_metrics["overall"]["profit_factor"] is not None
        and a_metrics["overall"]["profit_factor"] > 1,
        "CI_A_AminusB_AminusC_delta_positive": all(
            positive(name)
            for name in (
                "A_mean_net_jpy",
                "A_minus_B_conditional_mean_jpy",
                "A_minus_C_conditional_mean_jpy",
                "delta_ols_jpy",
            )
        ),
        "A_beats_continue_fixed_buy_fixed_sell": all(
            positive(f"A_minus_{name}_conditional_mean_jpy")
            for name in ("A_continue", "A_buy", "A_sell")
        ),
        "all_variants_positive_pf": all(
            cast(dict[str, Any], results[name]["metrics"])["overall"]["net_pnl_jpy"] > 0
            and cast(dict[str, Any], results[name]["metrics"])["overall"]["profit_factor"]
            is not None
            and cast(dict[str, Any], results[name]["metrics"])["overall"]["profit_factor"] > 1
            for name in (*VARIANTS, *SENSITIVITIES)
        ),
        "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3,
        "positive_months>=27": sum(value > 0 for value in months.values()) >= 27,
        "top10_removed_positive": a_metrics["concentration"]["net_excluding_top10_jpy"] > 0,
    }
    decision = (
        "INCONCLUSIVE"
        if not all(info.values())
        else "INVESTIGATE"
        if all(gates.values())
        else "REJECT"
    )
    write_json(
        OUT / "breakdowns.json",
        {
            "A_year_net_jpy": years,
            "A_month_net_jpy": months,
            "positive_months": sum(value > 0 for value in months.values()),
            "A_direction_count": directions,
            "A_direction_performance": {
                value: research_metrics(
                    tuple(trade for trade in trades["A"] if trade.side.value == value), target_bars
                )["overall"]
                for value in directions
            },
        },
    )
    write_json(
        OUT / "development_results.json",
        {
            "experiment_id": IDENTIFIER,
            "decision": decision,
            "quality": "PASS_LIMITED",
            "information_gate": info,
            "fixed_gates": gates,
            "E": sum(row.get("status") == "E" for row in base),
            "conditions": results,
            "OLS": boot["ols"],
            "scope": "Development only; OOS and Final Holdout not evaluated/accessed.",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "development_complete",
            "decision": decision,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
