"""Execute the preregistered Development-only R058-Q001 experiment."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor, log
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.r058 import (
    PINV_RCOND,
    RESIDUAL_SS_TOLERANCE,
    R058QNotIdentifiableError,
    all_events,
    fwl_delta,
)
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.day_close_night_reversal import DayCloseNightReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r058-q001-20260915-closing-pressure-reversal-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
AXIS_SOURCE = (
    ROOT
    / "results"
    / "research"
    / "r012-q001-20260914-night-direction-followthrough-01"
    / "daily_net_pnl_aligned.json"
)
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20261005
BASE = ("A", "B", "C", "P", "A_continue", "A_buy", "A_sell")
VARIANTS = {
    "A2": ("A", 2, 0, 90, 30, 30),
    "A3": ("A", 3, 0, 90, 30, 30),
    "A_delay": ("A", 1, 1, 90, 30, 30),
}
SENSITIVITIES = {
    "A85": ("A", 1, 0, 85, 30, 30),
    "A95": ("A", 1, 0, 95, 30, 30),
    "A_w20": ("A", 1, 0, 90, 30, 20),
    "A_w40": ("A", 1, 0, 90, 30, 40),
    "A_h15": ("A", 1, 0, 90, 15, 30),
    "A_h45": ("A", 1, 0, 90, 45, 30),
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fixed_axis() -> list[str]:
    values = json.loads(AXIS_SOURCE.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(values, list) or len(values) != 1111 or len(set(values)) != 1111:
        raise ValueError("R058 requires the fixed 1,111 trade_date axis")
    return cast(list[str], values)


def grouped(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    result: dict[tuple[date, Session], list[Bar]] = {}
    for bar in bars:
        result.setdefault((bar.trade_date, bar.session), []).append(bar)
    return {key: sorted(value, key=lambda row: row.ts_jst) for key, value in result.items()}


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    sessions = grouped(data.bars)
    isolated = {
        key
        for key, rows in sessions.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in sessions.items() if key not in isolated for bar in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(sessions) - len(isolated),
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
    mismatch = {
        key: {"actual": audit[key], "expected": value}
        for key, value in expected.items()
        if audit[key] != value
    }
    audit["mismatches"] = mismatch
    if mismatch:
        raise ValueError(f"R058 R004 isolation mismatch: {mismatch}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def input_manifest(root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(root, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, OOS, and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def selected(event: dict[str, object], condition: str, threshold: int) -> bool:
    if event.get("status") != "E":
        return False
    if condition == "P":
        prior = cast(dict[str, object], event["prior"])
        q = cast(dict[str, object], event["prior_thresholds"])
        return int(cast(int, prior["sign"])) != 0 and float(cast(float, prior["x"])) >= float(
            cast(float, q["q90"])
        )
    close = cast(dict[str, object], event["closing"])
    thresholds = cast(dict[str, object], event["closing_thresholds"])
    if int(cast(int, close["sign"])) == 0:
        return False
    x = float(cast(float, close["x"]))
    if condition == "B":
        return x >= float(cast(float, thresholds["q75"]))
    if condition == "C":
        return float(cast(float, thresholds["q75"])) <= x < float(cast(float, thresholds["q90"]))
    return x >= float(cast(float, thresholds[f"q{threshold}"]))


def side(event: dict[str, object], condition: str) -> str:
    block = cast(dict[str, object], event["prior"] if condition == "P" else event["closing"])
    continuation = "long" if int(cast(int, block["sign"])) > 0 else "short"
    if condition == "A_continue":
        return continuation
    if condition == "A_buy":
        return "long"
    if condition == "A_sell":
        return "short"
    return "short" if continuation == "long" else "long"


def execution_path(
    event: dict[str, object], bars: dict[tuple[date, Session], list[Bar]], closing_minutes: int
) -> list[Bar] | None:
    ref, target = (
        date.fromisoformat(cast(str, event["tse_reference_trade_date"])),
        date.fromisoformat(cast(str, event["trade_date"])),
    )
    close = cast(dict[str, object], event["closing"])
    first = datetime.fromisoformat(cast(str, close["start_jst"]))
    night = datetime.fromisoformat(cast(str, event["planned_entry_jst"]))
    d = {bar.ts_jst: bar for bar in bars.get((ref, Session.DAY), [])}
    n = {bar.ts_jst: bar for bar in bars.get((target, Session.NIGHT), [])}
    rows = [d.get(first + timedelta(minutes=index)) for index in range(closing_minutes)] + [
        n.get(night + timedelta(minutes=index)) for index in range(46)
    ]
    if any(row is None or not row.is_eligible for row in rows):
        return None
    return [cast(Bar, row) for row in rows]


def run_condition(
    base: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Bar]],
    engine: BacktestEngine,
    condition: str,
    *,
    ticks: int,
    delay: int,
    threshold: int,
    holding: int,
    closing_minutes: int,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for original in base:
        row = dict(original)
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
        path = execution_path(row, bars, closing_minutes)
        if path is None:
            row.update(status="skipped", condition_reason="EXECUTION_PATH_INELIGIBLE")
            records.append(row)
            continue
        entry, exit_time = (
            datetime.fromisoformat(cast(str, row["planned_entry_jst"])),
            datetime.fromisoformat(cast(str, row[f"planned_exit{holding}_jst"])),
        )
        entry_signal = (
            entry
            if delay
            else datetime.fromisoformat(
                cast(str, cast(dict[str, object], row["closing"])["last_bar_start_jst"])
            )
        )
        exit_signal = exit_time - timedelta(minutes=1)
        direction = side(row, condition)
        result = engine.run(
            path,
            DayCloseNightReversalStrategy(
                f"r058_{condition}", entry_signal, exit_signal, direction
            ),
            canonical_hash(
                {
                    "condition": condition,
                    "ticks": ticks,
                    "delay": delay,
                    "threshold": threshold,
                    "holding": holding,
                    "closing": closing_minutes,
                }
            ),
        )
        audit["cancelled_orders"] += result.canceled_orders
        if len(result.trades) > 1:
            raise ValueError("R058 maximum one position violated")
        if not result.trades:
            row.update(status="cancelled", condition_reason="ENGINE_NO_FILL_OR_EXIT")
            records.append(row)
            continue
        trade = result.trades[0]
        row.update(
            status="filled",
            side=direction,
            planned_exit_jst=exit_time.isoformat(),
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
            for index, trade in enumerate(sorted(trades, key=lambda row: row.entry_ts), 1)
        ),
        records,
        dict(audit),
    )


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    lo, hi = floor(point), ceil(point)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (point - lo)


def regression_rows(
    base: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Bar]],
    classifier: CalendarClassifier,
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for event in base:
        if not selected(event, "B", 90):
            continue
        ref, target = (
            date.fromisoformat(cast(str, event["tse_reference_trade_date"])),
            date.fromisoformat(cast(str, event["trade_date"])),
        )
        close, prior, q = (
            cast(dict[str, object], event["closing"]),
            cast(dict[str, object], event["prior"]),
            cast(dict[str, object], event["closing_thresholds"]),
        )
        sign, x, q75, q90 = (
            int(cast(int, close["sign"])),
            float(cast(float, close["x"])),
            float(cast(float, q["q75"])),
            float(cast(float, q["q90"])),
        )
        entry, exit_time = (
            datetime.fromisoformat(cast(str, event["planned_entry_jst"])),
            datetime.fromisoformat(cast(str, event["planned_exit30_jst"])),
        )
        day = {bar.ts_jst: bar for bar in bars[(ref, Session.DAY)]}
        night = {bar.ts_jst: bar for bar in bars[(target, Session.NIGHT)]}
        tse_open = classifier.session_open(ref, Session.DAY)
        opening = day.get(tse_open)
        if opening is None or x <= 0 or q75 <= 0 or q90 <= 0:
            raise ValueError("R058 fixed regression covariate unavailable")
        output.append(
            {
                "trade_date": event["trade_date"],
                "y_gross_jpy": -sign * (night[exit_time].open - night[entry].open) * 100,
                "Q": int(x >= q90),
                "z": log(x / q75),
                "closing_range_bps": close["range_bps"],
                "closing_efficiency": close["efficiency"],
                "prior_adjusted_bps": sign * float(cast(float, prior["r"])) * 10_000,
                "session_adjusted_bps": sign
                * (float(cast(float, close["close"])) - opening.open)
                / opening.open
                * 10_000,
                "gap_adjusted_bps": sign
                * (night[entry].open - float(cast(float, close["close"])))
                / float(cast(float, close["close"]))
                * 10_000,
                "closing_up": int(sign > 0),
            }
        )
    return output


def design(
    rows: list[dict[str, object]],
) -> tuple[NDArray[np.float64], NDArray[np.float64], list[str]]:
    years = sorted({date.fromisoformat(cast(str, row["trade_date"])).year for row in rows})
    names = [
        "intercept",
        "Q",
        "ln_x_over_q75",
        "closing_range_bps",
        "closing_efficiency",
        "prior30_adjusted_bps",
        "tse_session_adjusted_bps",
        "tse_to_ose_gap_adjusted_bps",
        "closing_up",
        *[f"calendar_year_{year}" for year in years[1:]],
    ]
    matrix: list[list[float]] = [
        [
            1.0,
            float(cast(float, row["Q"])),
            float(cast(float, row["z"])),
            float(cast(float, row["closing_range_bps"])),
            float(cast(float, row["closing_efficiency"])),
            float(cast(float, row["prior_adjusted_bps"])),
            float(cast(float, row["session_adjusted_bps"])),
            float(cast(float, row["gap_adjusted_bps"])),
            float(cast(float, row["closing_up"])),
            *[
                1.0 if date.fromisoformat(cast(str, row["trade_date"])).year == year else 0.0
                for year in years[1:]
            ],
        ]
        for row in rows
    ]
    x: NDArray[np.float64] = np.asarray(matrix, dtype=np.float64)
    y: NDArray[np.float64] = np.asarray(
        [float(cast(float, row["y_gross_jpy"])) for row in rows], dtype=np.float64
    )
    return x, y, names


def draws(axis: list[str]) -> list[list[int]]:
    rng = Random(SEED)
    result: list[list[int]] = []
    for _ in range(10_000):
        values: list[int] = []
        while len(values) < len(axis):
            values.extend(range((start := rng.randrange(len(axis) - 19)), start + 20))
        result.append(values[: len(axis)])
    return result


def fwl_gate(
    rows: list[dict[str, object]], axis: list[str], samples: list[list[int]]
) -> dict[str, object]:
    x, y, names = design(rows)
    index = {cast(str, row["trade_date"]): i for i, row in enumerate(rows)}
    try:
        _, ss = fwl_delta(x[:, 1], y, np.delete(x, 1, axis=1))
    except R058QNotIdentifiableError as exc:
        return {"status": "BLOCKED", "stage": "observed", "error": str(exc), "columns": names}
    for replicate, draw in enumerate(samples, 1):
        weights = np.zeros(len(rows))
        for item in draw:
            if axis[item] in index:
                weights[index[axis[item]]] += 1
        used = weights > 0
        root = np.sqrt(weights[used])
        try:
            fwl_delta(
                (x[used] * root[:, None])[:, 1],
                y[used] * root,
                np.delete(x[used] * root[:, None], 1, axis=1),
            )
        except R058QNotIdentifiableError as exc:
            return {
                "status": "BLOCKED",
                "stage": "bootstrap",
                "replicate": replicate,
                "error": str(exc),
                "sampled_axis_indices": draw,
                "columns": names,
            }
    return {
        "status": "PASS",
        "observed_Q_residual_sum_of_squares": ss,
        "rows": len(rows),
        "columns": names,
        "repetitions": 10000,
        "seed": SEED,
        "block_length_trade_dates": 20,
        "pinv_rcond": PINV_RCOND,
        "residual_ss_tolerance": RESIDUAL_SS_TOLERANCE,
    }


def bootstrap(
    daily: dict[str, dict[str, int]],
    records: dict[str, list[dict[str, object]]],
    regression: list[dict[str, object]],
    axis: list[str],
    samples: list[list[int]],
) -> dict[str, object]:
    labels = (
        "A_mean_net_jpy",
        "A_minus_C_conditional_mean_jpy",
        "A_minus_B_conditional_mean_jpy",
        "A_minus_P_conditional_mean_jpy",
        "A_minus_A_continue_conditional_mean_jpy",
        "A_minus_A_buy_conditional_mean_jpy",
        "A_minus_A_sell_conditional_mean_jpy",
        "delta_ols_jpy",
    )
    values: dict[str, list[float]] = {label: [] for label in labels}
    filled = {
        name: {
            cast(str, row["trade_date"]): int(cast(int, row["net_pnl_jpy"]))
            for row in rows
            if row.get("status") == "filled"
        }
        for name, rows in records.items()
    }
    x, y, _ = design(regression)
    index = {cast(str, row["trade_date"]): i for i, row in enumerate(regression)}
    for draw in samples:
        days = [axis[item] for item in draw]
        values["A_mean_net_jpy"].append(fmean(daily["A"][day] for day in days))
        for name in ("C", "B", "P", "A_continue", "A_buy", "A_sell"):
            a = [filled["A"][day] for day in days if day in filled["A"]]
            b = [filled[name][day] for day in days if day in filled[name]]
            values[f"A_minus_{name}_conditional_mean_jpy"].append(
                fmean(a) - fmean(b) if a and b else float("nan")
            )
        weights = np.zeros(len(regression))
        for day in days:
            if day in index:
                weights[index[day]] += 1
        used = weights > 0
        root = np.sqrt(weights[used])
        values["delta_ols_jpy"].append(
            fwl_delta(
                (x[used] * root[:, None])[:, 1],
                y[used] * root,
                np.delete(x[used] * root[:, None], 1, axis=1),
            )[0]
        )
    if any(any(np.isnan(item) for item in value) for value in values.values()):
        raise ValueError("R058 bootstrap conditional population missing")
    estimates = {"A_mean_net_jpy": fmean(daily["A"].values())}
    estimates.update(
        {
            f"A_minus_{name}_conditional_mean_jpy": fmean(filled["A"].values())
            - fmean(filled[name].values())
            for name in ("C", "B", "P", "A_continue", "A_buy", "A_sell")
        }
    )
    estimates["delta_ols_jpy"] = fwl_delta(x[:, 1], y, np.delete(x, 1, axis=1))[0]
    return {
        "method": "20 trade_date noncircular MBB, common index, tail truncate, linear percentile; fixed nuisance-only FWL",
        "repetitions": 10000,
        "seed": SEED,
        "block_length_trade_dates": 20,
        **{
            key: {
                "estimate": estimates[key],
                "ci95_percentile_linear": [percentile(value, 0.025), percentile(value, 0.975)],
            }
            for key, value in values.items()
        },
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable R058 output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    runtime = baseline.model_copy(
        update={
            "execution": baseline.execution.model_copy(
                update={"allow_cross_session_pending_order": True, "max_fill_delay_minutes": 180}
            ),
            "risk": baseline.risk.model_copy(
                update={"new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    if (runtime.execution.slippage_ticks, runtime.fees.jpy_per_side_per_contract) != (1, 30):
        raise ValueError("R058 requires one tick and JPY30 per side")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    inputs = input_manifest(data_config.gold_root)
    paths = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r058.py"),
        Path("src/n225m_bt/strategies/day_close_night_reversal.py"),
        Path("tests/test_r058_q001.py"),
    ]
    implementation = {str(path): digest(ROOT / path) for path in paths}
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R058-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "seed": SEED,
        "scope": "Development 2021-01-01..2025-06-30 only; OOS and Final Holdout forbidden.",
        "duplicate_review": "R001-R057 reviewed before price statistics. R039 shares final-30/TSE-to-night/30m geometry but uses 60-day/50-valid strict q75 as its main extremeness rule; it is not the registered 120-day/100-valid nearest-rank q90 equality-inclusive test. No prior study registers all of the present extremeness rule and fixed regression/controls.",
        "hypothesis": "Extreme final scheduled TSE 30-minute directional moves reverse during the first 30 scheduled minutes of the uniquely corresponding OSE night, exceeding moderate, adjacent-block placebo, continuation and fixed-direction controls.",
        "rule": "rC=(cC-oC)/oC over final 30 planned TSE minutes; rP is adjacent prior 30 minutes. Exact prior 120 scheduled TSE days, >=100 valid nonzero x, nearest-rank q75/q85/q90/q95, target excluded, no backfill, equality upper. E requires continuous final 60, all rolling history, mapped night entry and 15/30/45 exits. A q90 fade, B q75 fade, C q75-q90 fade, P independently qP90 fade, A controls identical events. Gap is excluded from PnL.",
        "correction": "…-01 is immutable and not used for judgment: its regression ledger mistakenly included all direction-eligible E rows rather than the preregistered B rows. This run changes only that technical sample predicate; events, orders, costs, sensitivities, seed, bootstrap, and decision rules are unchanged.",
        "ols": "All direction-eligible B events: zero-tick/pre-fee 30m fade y; Q, ln(xC/q75), closing range/efficiency, prior adjusted return, full-session adjusted return, TSE-close-to-OSE-entry adjusted gap, close-up, calendar-year FE. FWL nuisance-only pinv rcond=1e-12 and residual-Q SS tolerance=1e-12; observed or any bootstrap non-identification BLOCKED without redraw/deletion.",
        "evaluation": "fixed 1111 axis; 20-day noncircular MBB x10000 common indices. Information E>=800,B>=190,A>=70,C>=100,P>=70,A buy/sell each>=25,A95>=30,A_w20/A_w40 each>=50. All listed economic gates are mandatory after information passes.",
        "inputs": inputs,
        "implementation": implementation,
        "source": source,
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
        "prohibited": [
            "OOS",
            "Final Holdout",
            "WFA",
            "rescue search",
            "other windows/thresholds/filters/exits",
            "stop/target/reentry/early exit",
        ],
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
            "tests/test_r058_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r058.py",
            "tests/test_r058_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r058.py"],
    }
    validation: dict[str, Any] = {
        name: {"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr}
        for name, command in commands.items()
        for item in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation["status"] = (
        "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R058 validation failed before price access")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    axis = fixed_axis()
    schedule_audit = []
    for value in axis:
        target = date.fromisoformat(value)
        record = classifier.exchange_calendar.get(target)
        schedule_audit.append(
            {
                "target_night_trade_date": value,
                "tse_reference_trade_date": record.previous_trade_date.isoformat()
                if record and record.previous_trade_date
                else None,
                "night_calendar_start_date": record.night_calendar_start_date.isoformat()
                if record and record.night_calendar_start_date
                else None,
            }
        )
    if any(
        row["tse_reference_trade_date"] is None or row["night_calendar_start_date"] is None
        for row in schedule_audit
    ):
        write_json(
            OUT / "BLOCKED.json",
            {
                "experiment_id": IDENTIFIER,
                "status": "BLOCKED",
                "stage": "schedule_mapping_before_price_access",
                "mapping": schedule_audit,
            },
        )
        return
    write_json(OUT / "tse_ose_schedule_mapping.json", schedule_audit)
    development = load_split(data_config.gold_root, "development")
    view, qaudit, isolated = quarantine(development)
    bars = grouped(view.bars)
    base = all_events(
        classifier,
        classifier.exchange_calendar,
        [date.fromisoformat(item) for item in axis],
        bars,
        isolated,
        closing_minutes=30,
    )
    write_json(OUT / "all_candidate_event_ledger.json", base)
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": qaudit,
            "fixed_axis": axis,
            "physical_io": "Development normalized Parquet only",
        },
    )
    regression = regression_rows(base, bars, classifier)
    samples = draws(axis)
    technical = fwl_gate(regression, axis, samples)
    write_json(OUT / "technical_fwl_gate.json", technical)
    if technical["status"] != "PASS":
        write_json(
            OUT / "BLOCKED.json",
            {
                "experiment_id": IDENTIFIER,
                "status": "BLOCKED",
                "stage": "FWL_Q_identification",
                "failure": technical,
            },
        )
        return
    configs = {name: (name, 1, 0, 90, 30, 30) for name in BASE} | VARIANTS | SENSITIVITIES
    records = {}
    trades = {}
    daily = {}
    results = {}
    target_bars = [bar for key, rows in bars.items() if key[0].isoformat() in axis for bar in rows]
    from n225m_bt.research.metrics import research_metrics

    for name, (condition, ticks, delay, threshold, holding, window) in configs.items():
        event_base = (
            base
            if window == 30
            else all_events(
                classifier,
                classifier.exchange_calendar,
                [date.fromisoformat(item) for item in axis],
                bars,
                isolated,
                closing_minutes=window,
            )
        )
        config = runtime.model_copy(
            update={"execution": runtime.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        filled, ledger, audit = run_condition(
            event_base,
            bars,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            condition,
            ticks=ticks,
            delay=delay,
            threshold=threshold,
            holding=holding,
            closing_minutes=window,
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
    a_dates = {
        cast(str, row["trade_date"]) for row in records["A"] if row.get("status") == "filled"
    }
    checks = {
        "B_equals_A_union_C": all(
            selected(row, "B", 90) == (selected(row, "A", 90) or selected(row, "C", 90))
            for row in base
        ),
        "A_C_exclusive": all(
            not (selected(row, "A", 90) and selected(row, "C", 90)) for row in base
        ),
        "controls_same_event_entry_exit": all(
            all(
                records[name][index].get(field) == records["A"][index].get(field)
                for field in ("planned_entry_jst", "planned_exit_jst")
            )
            for name in ("A_continue", "A_buy", "A_sell")
            for index, day in enumerate(axis)
            if day in a_dates
        ),
        "continue_opposite_side": all(
            records["A"][index].get("side") != records["A_continue"][index].get("side")
            for index, day in enumerate(axis)
            if day in a_dates
        ),
        "P_independent_threshold_and_same_night": all(
            row.get("status") != "filled"
            or row.get("planned_entry_jst") == base[index].get("planned_entry_jst")
            for index, row in enumerate(records["P"])
        ),
        "window_sensitivity_rebuilt": all(
            records[name][index].get("closing_minutes") == window
            for name, window in (("A_w20", 20), ("A_w40", 40))
            for index in range(len(axis))
        ),
        "one_trade_max": all(
            len({trade.trade_date for trade in value}) == len(value) for value in trades.values()
        ),
        "execution_accounting": all(
            row.get("exit_reason") == ExitReason.SIGNAL.value
            and row.get("exit_delay_minutes") == 0
            and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0)
            and int(cast(int, row["net_pnl_jpy"]))
            == int(cast(int, row["gross_pnl_jpy"])) - int(cast(int, row["fees_jpy"]))
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
            "accounting": "Gross fill-to-fill includes slippage; Net=Gross-fees, without double deduction.",
        },
    )
    if not all(checks.values()):
        raise ValueError("R058 execution/accounting gate failed")
    boot = bootstrap(daily, records, regression, axis, samples)
    write_json(OUT / "bootstrap.json", boot)
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {"trade_dates": axis, "no_trade_value_jpy": 0, "series": daily},
    )
    write_json(OUT / "regression_ledger.json", regression)
    a_metrics = cast(dict[str, Any], results["A"]["metrics"])
    years = {
        str(year): sum(daily["A"][item] for item in axis if item.startswith(str(year)))
        for year in range(2021, 2026)
    }
    months = {
        f"{year}-{month:02d}": sum(
            daily["A"][item] for item in axis if item.startswith(f"{year}-{month:02d}")
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
        "E>=800": sum(row.get("status") == "E" for row in base) >= 800,
        "B>=190": len(trades["B"]) >= 190,
        "A>=70": len(trades["A"]) >= 70,
        "C>=100": len(trades["C"]) >= 100,
        "P>=70": len(trades["P"]) >= 70,
        "A_buy_sell>=25": all(value >= 25 for value in directions.values()),
        "A95>=30": len(trades["A95"]) >= 30,
        "A_w20>=50": len(trades["A_w20"]) >= 50,
        "A_w40>=50": len(trades["A_w40"]) >= 50,
    }

    def lower(name: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
        )

    gates = {
        "A_net_positive": a_metrics["overall"]["net_pnl_jpy"] > 0,
        "A_pf_gt1": a_metrics["overall"]["profit_factor"] is not None
        and a_metrics["overall"]["profit_factor"] > 1,
        "CI_A_AminusC_AminusB_AminusP_delta_positive": all(
            lower(name)
            for name in (
                "A_mean_net_jpy",
                "A_minus_C_conditional_mean_jpy",
                "A_minus_B_conditional_mean_jpy",
                "A_minus_P_conditional_mean_jpy",
                "delta_ols_jpy",
            )
        ),
        "A_beats_continue_buy_sell": all(
            lower(f"A_minus_{name}_conditional_mean_jpy")
            for name in ("A_continue", "A_buy", "A_sell")
        ),
        "cost_delay_and_all_sensitivities_positive_pf": all(
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
            "OLS": {"delta": boot["delta_ols_jpy"], "formula": "fixed R058 FWL"},
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
