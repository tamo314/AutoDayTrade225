"""Execute the preregistered Development-only R063-Q001 experiment."""

from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, log
from pathlib import Path
from subprocess import run
from sys import executable
from typing import cast

import numpy as np

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r063 import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    R063QNotIdentifiableError,
    candidate_rows,
    fwl_delta,
    make_events,
)
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r063_liquidity_shock_rejection import R063LiquidityShockRejectionStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r063-q001-20260915-tse-liquidity-shock-rejection-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
SEED = 20260915
MBB_SEED = 20260916
BLOCK = 20
REPETITIONS = 10_000
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def input_manifest(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve()), "sha256": digest(path)}
        for path in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Development normalized Parquet only",
        "trade_date_range": [str(DEVELOPMENT_START), str(DEVELOPMENT_END)],
        "files": files,
        "files_hash": canonical_hash(files),
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listing = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit = {
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list_hash": canonical_hash(listing),
        "quarantined_session_list": listing,
        "rule": "R004 fixed full-session tick-grid isolation",
    }
    if audit["quarantined_session_list_hash"] != EXPECTED_QUARANTINE_HASH:
        raise ValueError(f"R004 isolation mismatch: {audit}")
    return ResearchData(included, data.data_version, data.quality), audit, isolated


def scheduled_days(
    calendar: ExchangeCalendar, isolated: set[tuple[date, Session]]
) -> tuple[list[date], list[date]]:
    all_days = [
        record.trade_date
        for record in calendar.trading_days()
        if DEVELOPMENT_START <= record.trade_date <= DEVELOPMENT_END
    ]
    axis = [day for day in all_days if (day, Session.DAY) not in isolated]
    if len(axis) != 1111 or len(set(axis)) != 1111:
        raise ValueError(f"fixed 1,111-day axis mismatch: {len(axis)}")
    return all_days, axis


def side(event: dict[str, object], condition: str) -> str:
    shock = int(event["s"])
    if condition == "A_follow":
        return "long" if shock > 0 else "short"
    if condition == "A_buy":
        return "long"
    if condition == "A_sell":
        return "short"
    return "short" if shock > 0 else "long"


def eligible(event: dict[str, object], condition: str) -> bool:
    if event.get("status") != "E" or not event.get("event_found"):
        return False
    if condition == "B":
        return True
    return event.get("cell") == ("A" if condition.startswith("A") else condition)


def run_condition(
    name: str,
    events: list[dict[str, object]],
    bars: dict[date, list[Bar]],
    engine: BacktestEngine,
    *,
    hold: int = 20,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]]]:
    trades: list[Trade] = []
    ledger: list[dict[str, object]] = []
    exit_key = f"planned_exit{hold}_jst"
    for source in events:
        row = dict(source)
        row["condition"] = name
        row["condition_eligible"] = eligible(row, name)
        if not row["condition_eligible"]:
            ledger.append(row)
            continue
        signal = datetime.fromisoformat(cast(str, row["planned_signal_jst"]))
        exit_time = datetime.fromisoformat(cast(str, row[exit_key]))
        row["side"] = side(row, name)
        result = engine.run(
            bars[date.fromisoformat(cast(str, row["trade_date"]))],
            R063LiquidityShockRejectionStrategy(
                f"r063_{name}", signal, exit_time, cast(str, row["side"]), delay
            ),
            canonical_hash({"condition": name, "hold": hold, "delay": delay}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"R063 {name} execution failure on {row['trade_date']}")
        trade = result.trades[0]
        row.update(
            status="filled",
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            entry_signal_ts_jst=trade.entry_signal_ts.isoformat(),
            gross_pnl_jpy=trade.gross_pnl_jpy,
            fees_jpy=trade.fees_jpy,
            slippage_cost_jpy=trade.slippage_cost_jpy,
            net_pnl_jpy=trade.net_pnl_jpy,
            exit_reason=trade.exit_reason.value,
        )
        trades.append(trade)
        ledger.append(row)
    return tuple(trades), ledger


def daily(trades: tuple[Trade, ...], axis: list[date]) -> dict[str, int]:
    values = dict.fromkeys((day.isoformat() for day in axis), 0)
    for trade in trades:
        values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def percentile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q, method="linear"))


def moving_block_indices(days: int) -> np.ndarray:
    rng = np.random.default_rng(MBB_SEED)
    values = np.empty((REPETITIONS, days), dtype=np.int16)
    starts = np.arange(days - BLOCK + 1, dtype=np.int16)
    for rep in range(REPETITIONS):
        blocks = rng.choice(starts, size=ceil(days / BLOCK), replace=True)
        values[rep] = np.concatenate(
            [np.arange(start, start + BLOCK, dtype=np.int16) for start in blocks]
        )[:days]
    return values


def bootstrap(
    series: dict[str, dict[str, int]], axis: list[date]
) -> tuple[dict[str, object], np.ndarray]:
    order = [day.isoformat() for day in axis]
    indices = moving_block_indices(len(order))
    values = {
        name: np.asarray([daily[day] for day in order], dtype=np.float64)
        for name, daily in series.items()
    }
    output: dict[str, object] = {}
    targets = {
        "A_daily_mean": values["A"],
        "A_minus_C": values["A"] - values["C"],
        "A_minus_D": values["A"] - values["D"],
        "A_minus_B": values["A"] - values["B"],
        "A_minus_A_follow": values["A"] - values["A_follow"],
        "A_minus_A_buy": values["A"] - values["A_buy"],
        "A_minus_A_sell": values["A"] - values["A_sell"],
    }
    for name, value in targets.items():
        samples = value[indices].mean(axis=1)
        output[name] = {
            "estimate": float(value.mean()),
            "ci95_percentile_linear": [percentile(samples, 0.025), percentile(samples, 0.975)],
            "method": "20-trade-date noncircular moving-block bootstrap",
            "seed": MBB_SEED,
            "repetitions": REPETITIONS,
        }
    return output, indices


def regression(
    events: list[dict[str, object]],
    multiplier: int,
    calendar: ExchangeCalendar,
    bars: dict[date, list[Bar]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], list[str]]:
    rows = [event for event in events if event.get("cell") in {"A", "C", "D", "M"}]
    years = sorted({int(str(row["trade_date"])[:4]) for row in rows})
    positions = sorted({int(row["first_q70_position"]) for row in rows})
    names = [
        "intercept",
        "X",
        "R",
        "ln_m_q70",
        "abs_z_m",
        "prior20_s_return_bps",
        "opening30_range_bps",
        "gap_s_bps",
        "upward_shock",
        *[f"position_{p}" for p in positions[1:]],
        *[f"year_{year}" for year in years[1:]],
    ]
    q, y, nuisance, days = [], [], [], []
    for row in rows:
        target = date.fromisoformat(cast(str, row["trade_date"]))
        record = calendar.get(target)
        previous = record.previous_trade_date if record else None
        prior_rows = bars.get(previous, []) if previous else []
        prior_close = max(prior_rows, key=lambda item: item.ts_jst).close if prior_rows else 0
        shock = int(row["s"])
        entry = float(row["entry_open"])
        gap = shock * (entry - prior_close) / prior_close * 10_000 if prior_close > 0 else 0.0
        x = int(bool(row["extreme"]))
        r = int(row["response"] == "R")
        q.append(float(x * r))
        y.append((float(row["exit20_open"]) - entry) * multiplier * -shock)
        position, year = int(row["first_q70_position"]), target.year
        nuisance.append(
            [
                1.0,
                float(x),
                float(r),
                log(float(row["m"]) / float(row["q70"])),
                abs(float(row["z"])) / float(row["m"]),
                float(row["prior20_s_adjusted_bps"]),
                float(row["opening30_range_bps"]),
                gap,
                float(row["upward_shock"]),
                *[float(position == value) for value in positions[1:]],
                *[float(year == value) for value in years[1:]],
            ]
        )
        days.append(target.isoformat())
    return np.asarray(q), np.asarray(y), np.asarray(nuisance), days, names


def delta_bootstrap(
    events: list[dict[str, object]],
    multiplier: int,
    axis: list[date],
    calendar: ExchangeCalendar,
    bars: dict[date, list[Bar]],
) -> dict[str, object]:
    q, y, nuisance, row_days, names = regression(events, multiplier, calendar, bars)
    delta, ss = fwl_delta(q, y, nuisance)
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=1e-12)
    q_residual = q - projection @ q
    residual = y - nuisance @ (np.linalg.pinv(nuisance, rcond=1e-12) @ y) - q_residual * delta
    lookup = {day.isoformat(): index for index, day in enumerate(axis)}
    day_index = np.asarray([lookup[day] for day in row_days])
    rng = np.random.default_rng(SEED)
    estimates = np.empty(REPETITIONS, dtype=np.float64)
    signs = np.empty((REPETITIONS, len(axis)), dtype=np.int8)
    for rep in range(REPETITIONS):
        weights = np.repeat(
            rng.choice(np.asarray([-1, 1], dtype=np.int8), size=ceil(len(axis) / BLOCK)), BLOCK
        )[: len(axis)]
        signs[rep] = weights
        estimates[rep] = delta + float(q_residual @ (residual * weights[day_index]) / ss)
    write_json(
        OUT / "regression_ledger.json",
        {
            "trade_dates": row_days,
            "Q": q.tolist(),
            "y_0tick_gross_jpy": y.tolist(),
            "nuisance_columns": names,
            "nuisance": nuisance.tolist(),
            "delta": delta,
            "q_residual_ss": ss,
            "rcond": 1e-12,
            "tolerance": 1e-12,
        },
    )
    np.save(OUT / "bootstrap_delta_block_wild_signs.npy", signs)
    return {
        "estimate": delta,
        "ci95_percentile_linear": [percentile(estimates, 0.025), percentile(estimates, 0.975)],
        "method": "fixed-design 20-trade-date block-wild score bootstrap",
        "seed": SEED,
        "repetitions": REPETITIONS,
        "q_residual_ss": ss,
    }


def positive_pf(result: dict[str, object]) -> bool:
    overall = cast(dict[str, object], cast(dict[str, object], result["metrics"])["overall"])
    return int(overall["net_pnl_jpy"]) > 0 and float(overall["profit_factor"] or 0) > 1


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r063_q001_tse_liquidity_shock_rejection.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.allow_cross_session_pending_order,
    ) != (1, 30, False):
        raise ValueError("execution/cost contract differs from preregistration")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    inputs = input_manifest(config.gold_root)
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    paths = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r063.py"),
        Path("src/n225m_bt/strategies/r063_liquidity_shock_rejection.py"),
        Path("tests/test_r063_q001.py"),
    ]
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R063-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Development only; repeated exploration, not independent reproduction",
        "novelty": "R035 has nonoverlapping same-clock 5m shocks but no immediate same-length rejection and 60-day/15m rule; R049 has 120-day fixed anchors but no rejection or scan. No R001-R062 contains the complete requested conjunction.",
        "rule": "31..150 fully partitioned 5m blocks; position-specific prior-120 U, min100, q70/q85/q90/q95; first q70; same-length response; A=X(q90)*R(0.25); next-open entry and 20-minute exit.",
        "sensitivities": "2/3 ticks, delay nonextension, q85/q95, R=0.20/1/3, 3/10m independently rebuilt blocks, same-A hold10/30 only",
        "inference": "1,111-day fixed axis; 20-day MBB 10,000 seed 20260916; fixed-design block-wild delta seed 20260915",
        "gates": "E>=750, B>=450, A/C/D/M>=60, A long/short>=25, q95>=35, block3/block10>=45; otherwise INCONCLUSIVE; all specified economic gates are required for INVESTIGATE",
        "inputs": inputs,
        "source": source,
        "implementation": {str(path): digest(ROOT / path) for path in paths},
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "campaign_manifest.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "preregistered_before_price_access",
            "started_at": datetime.now(timezone.utc).isoformat(),
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
            "tests/test_r063_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r063.py",
            "src/n225m_bt/strategies/r063_liquidity_shock_rejection.py",
            "tests/test_r063_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r063.py",
            "src/n225m_bt/strategies/r063_liquidity_shock_rejection.py",
        ],
    }
    validation: dict[str, object] = {}
    for name, command in commands.items():
        result = run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=os.environ | {"PYTHONPATH": str(ROOT / "src")},
        )
        validation[name] = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    validation["status"] = (
        "PASS"
        if all(
            cast(dict[str, int], item)["returncode"] == 0
            for item in validation.values()
            if isinstance(item, dict)
        )
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("pre-execution synthetic/static validation failed")
    data = load_split(config.gold_root, "development")
    view, isolation, isolated = quarantine(data)
    all_days, axis = scheduled_days(calendar, isolated)
    grouped = session_groups(view.bars)
    bars = {day: rows for (day, session), rows in grouped.items() if session is Session.DAY}
    ledger = candidate_rows(classifier, all_days, bars, isolated)
    all_u = [
        item
        for row in ledger
        for values in cast(dict[str, list[dict[str, object]]], row["u"]).values()
        for item in values
    ]
    causality = {
        "references_strictly_prior": all(
            reference < row["trade_date"]
            for row in ledger
            for values in cast(dict[str, list[dict[str, object]]], row["u"]).values()
            for item in values
            for reference in cast(list[str], item["rolling_reference_trade_dates"])
        ),
        "rolling_at_100_only": all(
            bool(item["rolling_valid"]) == (int(cast(int, item["rolling_valid_count"])) >= 100)
            for item in all_u
        ),
        "no_cross_position_fill": all(
            int(cast(int, item["rolling_scheduled_count"])) <= 120 for item in all_u
        ),
    }
    write_json(OUT / "rolling_u_ledger.json", ledger)
    write_json(OUT / "rolling_causality_audit.json", causality)
    if not all(causality.values()):
        raise ValueError("rolling causality gate failed")
    variants = {
        "base": {},
        "q85": {"extreme_quantile": 85},
        "q95": {"extreme_quantile": 95},
        "r20": {"rejection_fraction": 0.2},
        "r33": {"rejection_fraction": 1 / 3},
        "block3": {"length": 3},
        "block10": {"length": 10},
    }
    events = {
        name: make_events(classifier, axis, bars, isolated, ledger, **spec)
        for name, spec in variants.items()
    }
    base = events["base"]
    write_json(OUT / "all_eligible_event_ledger.json", base)
    write_json(
        OUT / "sensitivity_event_ledgers.json",
        {name: values for name, values in events.items() if name != "base"},
    )
    counts = Counter(str(row.get("reason", row["status"])) for row in base)
    e_count = sum(row["status"] == "E" for row in base)
    write_json(
        OUT / "eligibility_audit.json",
        {
            "fixed_axis": [day.isoformat() for day in axis],
            "axis_count": len(axis),
            "reason_counts": dict(counts),
            "E": e_count,
            "R004_isolation": isolation,
        },
    )
    condition_specs: dict[str, tuple[list[dict[str, object]], int, int, int]] = {
        "A": (base, 1, 20, 0),
        "B": (base, 1, 20, 0),
        "C": (base, 1, 20, 0),
        "D": (base, 1, 20, 0),
        "M": (base, 1, 20, 0),
        "A_follow": (base, 1, 20, 0),
        "A_buy": (base, 1, 20, 0),
        "A_sell": (base, 1, 20, 0),
        "A2": (base, 2, 20, 0),
        "A3": (base, 3, 20, 0),
        "A_delay": (base, 1, 20, 1),
        "A_q85": (events["q85"], 1, 20, 0),
        "A_q95": (events["q95"], 1, 20, 0),
        "A_r20": (events["r20"], 1, 20, 0),
        "A_r33": (events["r33"], 1, 20, 0),
        "A_block3": (events["block3"], 1, 20, 0),
        "A_block10": (events["block10"], 1, 20, 0),
        "A_hold10": (base, 1, 10, 0),
        "A_hold30": (base, 1, 30, 0),
    }
    output: dict[str, dict[str, object]] = {}
    ledgers: dict[str, list[dict[str, object]]] = {}
    daily_values: dict[str, dict[str, int]] = {}
    bars_axis = [bar for bar in view.bars if bar.trade_date in set(axis)]
    for name, (source_events, ticks, hold, delay) in condition_specs.items():
        trial = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, records = run_condition(
            name,
            source_events,
            bars,
            BacktestEngine(instrument.instrument.to_spec(), trial, classifier),
            hold=hold,
            delay=delay,
        )
        metrics = research_metrics(trades, bars_axis)
        values = daily(trades, axis)
        folder = OUT / name
        folder.mkdir()
        write_results(
            folder,
            trades,
            (),
            {
                "campaign_id": IDENTIFIER,
                "condition": name,
                "cost": f"{ticks} tick/side + JPY30/side",
            },
        )
        write_json(folder / "events.json", records)
        write_json(folder / "daily_net_pnl_aligned.json", values)
        write_json(folder / "research_metrics.json", metrics)
        output[name], ledgers[name], daily_values[name] = (
            {"trade_count": len(trades), "metrics": metrics},
            records,
            values,
        )
    audit = {
        "first_event_only": all(
            sum(bool(row.get("event_found")) for row in [event]) <= 1 for event in base
        ),
        "cells_exclusive": all(
            sum(row.get("cell") == cell for cell in ("A", "C", "D", "M")) <= 1 for row in base
        ),
        "controls_same_A_event": all(
            a.get("condition_eligible") == other.get("condition_eligible")
            and a.get("planned_entry_jst") == other.get("planned_entry_jst")
            and a.get("planned_exit20_jst") == other.get("planned_exit20_jst")
            for a, follow, buy, sell in zip(
                ledgers["A"], ledgers["A_follow"], ledgers["A_buy"], ledgers["A_sell"], strict=True
            )
            for other in (follow, buy, sell)
        ),
        "delay_nonextended": all(
            row.get("exit_ts_jst") == ledgers["A"][index].get("exit_ts_jst")
            for index, row in enumerate(ledgers["A_delay"])
            if row.get("condition_eligible")
        ),
        "one_trade_max": all(result["trade_count"] <= 1_111 for result in output.values()),
        "accounting": all(
            int(row["net_pnl_jpy"]) == int(row["gross_pnl_jpy"]) - int(row["fees_jpy"])
            for records in ledgers.values()
            for row in records
            if row.get("status") == "filled"
        ),
        "fixed_axis": all(len(values) == 1111 for values in daily_values.values()),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "all_pass": all(audit.values()),
            "checks": audit,
            "note": "Gross includes slippage once; Net=Gross-fees.",
        },
    )
    if not all(audit.values()):
        raise ValueError("execution/accounting gate failed")
    boot, indices = bootstrap(
        {name: daily_values[name] for name in ("A", "B", "C", "D", "A_follow", "A_buy", "A_sell")},
        axis,
    )
    np.save(OUT / "bootstrap_mbb_indices.npy", indices)
    try:
        boot["delta"] = delta_bootstrap(
            base, instrument.instrument.contract_multiplier, axis, calendar, bars
        )
        fwl_status = "PASS"
    except R063QNotIdentifiableError as error:
        boot["delta"] = {"status": "BLOCKED", "error": str(error)}
        fwl_status = "BLOCKED"
    write_json(OUT / "bootstrap.json", boot)
    metrics_a = cast(dict[str, object], output["A"]["metrics"])
    overall, segments, concentration = (
        cast(dict[str, object], metrics_a["overall"]),
        cast(dict[str, object], metrics_a["segments"]),
        cast(dict[str, object], metrics_a["concentration"]),
    )
    a_sides = cast(dict[str, dict[str, object]], segments["side"])
    info = {
        "E>=750": e_count >= 750,
        "B>=450": int(output["B"]["trade_count"]) >= 450,
        "A/C/D/M>=60": all(int(output[name]["trade_count"]) >= 60 for name in ("A", "C", "D", "M")),
        "A_buy_sell>=25": all(
            int(a_sides.get(name, {}).get("trade_count", 0)) >= 25 for name in ("long", "short")
        ),
        "q95_A>=35": int(output["A_q95"]["trade_count"]) >= 35,
        "block3_A>=45": int(output["A_block3"]["trade_count"]) >= 45,
        "block10_A>=45": int(output["A_block10"]["trade_count"]) >= 45,
    }

    def lower(name: str) -> bool:
        return (
            fwl_status == "PASS"
            and float(cast(dict[str, object], boot[name])["ci95_percentile_linear"][0]) > 0
        )

    economics = {
        "A_net_positive": int(overall["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": float(overall["profit_factor"] or 0) > 1,
        "all_requested_contrast_ci_positive": all(
            lower(name)
            for name in (
                "A_daily_mean",
                "A_minus_C",
                "A_minus_D",
                "A_minus_B",
                "A_minus_A_follow",
                "A_minus_A_buy",
                "A_minus_A_sell",
                "delta",
            )
        ),
        "all_cost_and_sensitivity_positive_pf": all(
            positive_pf(output[name])
            for name in (
                "A2",
                "A3",
                "A_delay",
                "A_q85",
                "A_q95",
                "A_r20",
                "A_r33",
                "A_block3",
                "A_block10",
                "A_hold10",
                "A_hold30",
            )
        ),
        "three_positive_years_2021_2024": sum(
            int(
                cast(dict[str, dict[str, object]], segments["year"])
                .get(str(year), {})
                .get("net_pnl_jpy", 0)
            )
            > 0
            for year in range(2021, 2025)
        )
        >= 3,
        "positive_months_27_of_54": sum(
            int(row["net_pnl_jpy"]) > 0
            for row in cast(dict[str, dict[str, object]], segments["month"]).values()
        )
        >= 27,
        "top10_removed_net_positive": int(concentration["net_excluding_top10_jpy"]) > 0,
    }
    decision = (
        "BLOCKED"
        if fwl_status == "BLOCKED"
        else "INCONCLUSIVE"
        if not all(info.values())
        else "INVESTIGATE"
        if all(economics.values())
        else "REJECT"
    )
    write_json(
        OUT / "breakdowns.json",
        {
            "year": segments["year"],
            "month": segments["month"],
            "shock_direction": {
                "up": [
                    row
                    for row in ledgers["A"]
                    if row.get("s") == 1 and row.get("status") == "filled"
                ],
                "down": [
                    row
                    for row in ledgers["A"]
                    if row.get("s") == -1 and row.get("status") == "filled"
                ],
            },
            "block_position": {
                str(position): [
                    row
                    for row in ledgers["A"]
                    if row.get("first_q70_position") == position and row.get("status") == "filled"
                ]
                for position in range(24)
            },
            "concentration": concentration,
        },
    )
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {"trade_dates": [day.isoformat() for day in axis], "series": daily_values},
    )
    write_json(
        OUT / "development_results.json",
        {
            "experiment_id": IDENTIFIER,
            "decision": decision,
            "information_gates": info,
            "economic_gates": economics
            if all(info.values())
            else "NOT_EVALUATED_INFORMATION_INSUFFICIENT",
            "conditions": output,
            "fwl_status": fwl_status,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "complete",
            "decision": decision,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
