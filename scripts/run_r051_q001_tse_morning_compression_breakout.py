"""Execute the preregistered Development-only R051-Q001 experiment."""

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
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r051 import SCHEDULE, TSE_SCHEDULE_ID, r051_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r051-q001-20260915-tse-morning-compression-breakout-02"
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
SEED = 20260930
BASE = ("A", "B", "D", "A_fade", "A_buy", "A_sell")
VARIANTS = {"A2": (2, 0, 40, 60), "A3": (3, 0, 40, 60), "A_delay": (1, 1, 40, 60)}
SENSITIVITIES = {
    "A30": (1, 0, 30, 60),
    "A50": (1, 0, 50, 60),
    "A_h30": (1, 0, 40, 30),
    "A_h90": (1, 0, 40, 90),
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
        raise ValueError("R051 requires the fixed 1,111 trade_date axis")
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
    mismatch = {
        key: {"actual": audit[key], "expected": value}
        for key, value in expected.items()
        if audit[key] != value
    }
    audit["mismatches"] = mismatch
    if mismatch:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatch}")
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
        raise FileNotFoundError("R051 frozen TSE holiday evidence unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copied = destination / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    return calendar, {
        "holiday_csv_sha256": digest(copied),
        "schedule_id": TSE_SCHEDULE_ID,
        "schedule": {
            "morning_start": SCHEDULE.morning_start.isoformat(),
            "morning_end_exclusive": SCHEDULE.morning_end.isoformat(),
            "afternoon_start": SCHEDULE.afternoon_start.isoformat(),
        },
        "rule": "TSE open days from frozen Cabinet Office holiday evidence; all boundaries from R051-TSE-MORNING-AFTERNOON-1.",
    }


def eligible(event: dict[str, object], condition: str, threshold: int) -> bool:
    if event.get("status") != "E" or event.get("breakout_direction") not in {"long", "short"}:
        return False
    value = float(cast(float, event["range_bps"]))
    if condition == "B":
        return True
    if condition == "D":
        return value >= float(cast(float, event["q60_bps"]))
    return value <= float(cast(float, event[f"q{threshold}_bps"]))


def direction(event: dict[str, object], condition: str) -> str:
    breakout = cast(str, event["breakout_direction"])
    if condition == "A_fade":
        return "short" if breakout == "long" else "long"
    if condition == "A_buy":
        return "long"
    if condition == "A_sell":
        return "short"
    return breakout


def run_condition(
    base: list[dict[str, object]],
    bars_by_day: dict[date, list[Bar]],
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
    exit_key = f"planned_exit{holding}_jst"
    for original in base:
        event = dict(original)
        day = date.fromisoformat(cast(str, event["trade_date"]))
        allowed = eligible(event, condition, threshold)
        event.update(
            condition=condition,
            condition_eligible=allowed,
            requested_slippage_ticks=ticks,
            requested_delay_minutes=delay,
            requested_holding_minutes=holding,
        )
        if not allowed:
            event.update(status="skipped", condition_reason="NO_BREAKOUT_OR_RANGE_CONDITION")
            records.append(event)
            continue
        side = direction(event, condition)
        entry = datetime.fromisoformat(cast(str, event["planned_entry_jst"]))
        exit_time = datetime.fromisoformat(cast(str, event[exit_key]))
        event.update(side=side, planned_exit_jst=exit_time.isoformat())
        result = engine.run(
            bars_by_day.get(day, []),
            R049FixedTimeStrategy(f"r051_{condition}", entry, exit_time, side, delay),
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
            raise ValueError("R051 maximum one position violated")
        if not result.trades:
            event.update(status="cancelled", condition_reason="ENGINE_NO_FILL_OR_EXIT")
            audit["cancelled"] += 1
            records.append(event)
            continue
        trade = result.trades[0]
        event.update(
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
        records.append(event)
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


def ols(rows: list[dict[str, object]]) -> tuple[float, int, int]:
    years = sorted({date.fromisoformat(cast(str, row["trade_date"])).year for row in rows})
    columns: list[list[float]] = []
    y: list[float] = []
    for row in rows:
        year = date.fromisoformat(cast(str, row["trade_date"])).year
        columns.append(
            [
                1.0,
                float(row["Q"]),
                float(row["z"]),
                float(row["overshoot_bps"]),
                float(row["gap_bps"]),
                float(row["up_breakout"]),
                *[1.0 if year == item else 0.0 for item in years[1:]],
            ]
        )
        y.append(float(row["y_gross_jpy"]))
    matrix = np.asarray(columns, dtype=float)
    rank = int(np.linalg.matrix_rank(matrix))
    if rank != matrix.shape[1]:
        raise ValueError(f"R051 fixed OLS design not full rank: {rank}/{matrix.shape[1]}")
    beta = np.linalg.lstsq(matrix, np.asarray(y), rcond=None)[0]
    return float(beta[1]), rank, int(matrix.shape[1])


def bootstrap(
    daily: dict[str, dict[str, int]],
    records: dict[str, list[dict[str, object]]],
    regression: list[dict[str, object]],
    axis: list[str],
) -> dict[str, object]:
    rng, count, block = Random(SEED), len(axis), 20
    samples: dict[str, list[float]] = {
        name: []
        for name in (
            "A_mean_net_jpy",
            "A_minus_D_conditional_mean_jpy",
            "A_minus_B_conditional_mean_jpy",
            "A_minus_A_fade_conditional_mean_jpy",
            "A_minus_A_buy_conditional_mean_jpy",
            "A_minus_A_sell_conditional_mean_jpy",
            "delta_ols_jpy",
        )
    }
    filled = {
        name: {
            cast(str, row["trade_date"]): int(row["net_pnl_jpy"])
            for row in rows
            if row.get("status") == "filled"
        }
        for name, rows in records.items()
    }
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        picked = indices[:count]
        picked_days = [axis[index] for index in picked]
        samples["A_mean_net_jpy"].append(fmean(daily["A"][day] for day in picked_days))
        for name in ("D", "B", "A_fade", "A_buy", "A_sell"):
            a = [filled["A"][day] for day in picked_days if day in filled["A"]]
            b = [filled[name][day] for day in picked_days if day in filled[name]]
            samples[f"A_minus_{name}_conditional_mean_jpy"].append(
                fmean(a) - fmean(b) if a and b else float("nan")
            )
        selected = set(picked_days)
        # Repeated blocks retain repeated days in the bootstrap OLS sample.
        sampled_rows = [
            row for day in picked_days for row in regression if row["trade_date"] == day
        ]
        del selected
        samples["delta_ols_jpy"].append(ols(sampled_rows)[0])
    output: dict[str, object] = {
        "method": "20 trade_date noncircular moving-block bootstrap; common indices, tail truncation, linear percentile",
        "repetitions": 10000,
        "seed": SEED,
        "block_length_trade_dates": 20,
    }
    point = {"A_mean_net_jpy": fmean(daily["A"].values())}
    for name in ("D", "B", "A_fade", "A_buy", "A_sell"):
        point[f"A_minus_{name}_conditional_mean_jpy"] = fmean(filled["A"].values()) - fmean(
            filled[name].values()
        )
    point["delta_ols_jpy"] = ols(regression)[0]
    for name, values in samples.items():
        if any(np.isnan(values)):
            raise ValueError(f"R051 bootstrap missing conditional population: {name}")
        output[name] = {
            "estimate": point[name],
            "ci95_percentile_linear": [percentile(values, 0.025), percentile(values, 0.975)],
        }
    return output


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable R051 output exists: {OUT}")
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
        raise ValueError("R051 requires 1 tick and JPY30 per side")
    source, inputs = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        input_manifest(data_config.gold_root),
    )
    cash, evidence = cash_calendar()
    files = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r051.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r051_q001.py"),
    ]
    implementation = {str(path): digest(ROOT / path) for path in files}
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R051-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Development 2021-01-01..2025-06-30 only; known-Development exploratory result, not independent confirmation.",
        "duplicate_review": "R001-R050 were reviewed before price statistics. R033 is a day-opening 30-minute compression and subsequent first-breakout rule; R029/R038/R045 are lunch movement/confirmation studies. None uses full TSE morning range, causal q30/q40/q50/q60 from 120 scheduled days, and the fixed aS+15 close breakout/aS+15-to+aS+75 rule.",
        "hypothesis": "A causal low full-TSE-morning range (range_bps<=q40) followed by a strict aS+15 close breakout of the morning high/low continues in breakout direction to aS+75 and exceeds noncompressed/all-breakout and same-event controls.",
        "rule": "mS=09:00,mE=11:30 exclusive,aS=12:30 from versioned R051 schedule. Full 150 scheduled morning bars define (high-low)/morning open. Immediately preceding 120 scheduled TSE open days only; target excluded, no backfill; >=100 valid range observations; nearest ranks q30/q40/q50/q60. First 15 aS bars close at aS+14 confirms strict > morning high or < morning low; entry is aS+15 open, exits are aS+45/+75/+105 opens.",
        "conditions": "A q40 breakout follow; D q60 breakout follow; B all breakout follow; A_fade opposite A; A_buy/A_sell fixed side on A event; A2/A3 2/3 ticks; A_delay +1 entry no exit extension; A30/A50; 30/90 holds only.",
        "evaluation": "fixed 1111 trade-date 0-JPY axis; 20-day noncircular MBB x10000 seed 20260930; y=direction-adjusted 0tick/pre-fee 60m Gross; Q,z=ln(range/q40), overshoot_bps,direction-adjusted gap_bps,up,year FE OLS; primary design rank must be full.",
        "inputs": inputs,
        "input_manifest_hash": canonical_hash(inputs),
        "schedule": evidence,
        "implementation": implementation,
        "implementation_hash": canonical_hash(implementation),
        "source": source,
        "r004": "45 sessions/27345 bars quarantined; expected retained 2216/1326086",
        "information_gate": "E>=850,A>=120,D>=150,A buy/sell>=40,A30>=80",
        "decision": "BLOCKED for input/schedule/synthetic/execution/accounting/OLS gate failure; INCONCLUSIVE for information failure; otherwise REJECT unless all fixed criteria pass, then INVESTIGATE.",
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
            "tests/test_r051_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r051.py",
            "tests/test_r051_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r051.py"],
    }
    validation: dict[str, Any] = {
        name: {"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr}
        for name, command in commands.items()
        for item in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation["coverage"] = (
        "holiday/schedule/trade_date, morning/afternoon boundaries, exact 120/no-backfill/min100/nearest-rank, strict equality, missing/isolation, signal/prefix, next-open/fixed exit/delay/cost, conditions, fixed axis, OOS/holdout rejection"
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
        raise ValueError("R051 static validation failed before Development price access")
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
    for rows in bars_by_day.values():
        rows.sort(key=lambda item: item.ts_jst)
    observed = {day.isoformat() for day in bars_by_day}
    if observed != set(axis):
        raise ValueError("R051 fixed 1111-date axis does not reproduce Development day bars")
    tse_days = [day for day in sorted(bars_by_day) if cash.is_open(day)]
    base: list[dict[str, object]] = []
    for value in axis:
        day = date.fromisoformat(value)
        history = []
        if day in tse_days:
            index = tse_days.index(day)
            history = [
                (prior, bars_by_day.get(prior), prior in isolated)
                for prior in tse_days[max(0, index - 120) : index]
            ]
        base.append(
            r051_event(day, bars_by_day.get(day), history, cash, quarantined=day in isolated)
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
    records: dict[str, list[dict[str, object]]] = {}
    trades: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int]] = {}
    results: dict[str, Any] = {}
    target_bars = [bar for rows in bars_by_day.values() for bar in rows]
    configs = (
        {name: (name, 1, 0, 40, 60) for name in BASE}
        | {name: ("A", *value) for name, value in VARIANTS.items()}
        | {name: ("A", *value) for name, value in SENSITIVITIES.items()}
    )
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
    path_fields = ("breakout_direction", "planned_entry_jst", "planned_exit_jst")
    checks = {
        "predicate_A_D_B": all(
            (row.get("status") == "filled") == eligible(base[index], condition, 40)
            for condition in ("A", "D", "B")
            for index, row in enumerate(records[condition])
        ),
        "same_event_entry_exit": all(
            all(
                records[name][index].get(field) == records["A"][index].get(field)
                for field in path_fields
            )
            for name in ("A_fade", "A_buy", "A_sell")
            for index, day in enumerate(axis)
            if day in a_days
        ),
        "fade_opposite_side": all(
            records["A"][index].get("side") != records["A_fade"][index].get("side")
            for index, day in enumerate(axis)
            if day in a_days
        ),
        "A_cost_variants_same_event_side": all(
            all(
                records[name][index].get(field) == records["A"][index].get(field)
                for field in ("breakout_direction", "side", "planned_entry_jst", "planned_exit_jst")
            )
            for name in ("A2", "A3")
            for index, day in enumerate(axis)
            if day in a_days
        ),
        "A_delay_same_event_side_fixed_exit": all(
            records["A_delay"][index].get(field) == records["A"][index].get(field)
            for field in ("breakout_direction", "side", "planned_exit_jst")
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
            for name, values in records.items()
            for row in values
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
        raise ValueError("R051 execution/accounting gate failed")
    regression: list[dict[str, object]] = []
    by_day = {row["trade_date"]: row for row in base}
    for row in records["B"]:
        if row.get("status") != "filled":
            continue
        day = cast(str, row["trade_date"])
        raw = by_day[day]
        entry = datetime.fromisoformat(cast(str, raw["planned_entry_jst"]))
        exit_time = datetime.fromisoformat(cast(str, raw["planned_exit60_jst"]))
        lookup = {bar.ts_jst: bar for bar in bars_by_day[date.fromisoformat(day)]}
        start, finish = lookup[entry].open, lookup[exit_time].open
        if (
            float(cast(float, raw["q40_bps"])) <= 0
            or float(cast(float, raw["range_bps"])) <= 0
            or raw["overshoot_bps"] is None
            or raw["direction_adjusted_gap_bps"] is None
        ):
            raise ValueError("R051 preregistered OLS covariate invalid")
        sign = 1 if raw["breakout_direction"] == "long" else -1
        regression.append(
            {
                "trade_date": day,
                "y_gross_jpy": sign * (finish - start) * 100,
                "Q": int(
                    float(cast(float, raw["range_bps"])) <= float(cast(float, raw["q40_bps"]))
                ),
                "z": log(float(cast(float, raw["range_bps"])) / float(cast(float, raw["q40_bps"]))),
                "overshoot_bps": raw["overshoot_bps"],
                "gap_bps": raw["direction_adjusted_gap_bps"],
                "up_breakout": int(raw["breakout_direction"] == "long"),
            }
        )
    delta, rank, columns = ols(regression)
    boot = bootstrap(daily, records, regression, axis)
    boot["ols"] = {
        "delta": delta,
        "rank": rank,
        "columns": columns,
        "rows": len(regression),
        "formula": "y~Q+ln(range/q40)+overshoot_bps+direction-adjusted gap_bps+up+calendar-year FE",
    }
    write_json(OUT / "bootstrap.json", boot)
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {"trade_dates": axis, "no_trade_value_jpy": 0, "series": daily},
    )
    write_json(OUT / "regression_ledger.json", regression)
    a_metrics = cast(dict[str, Any], results["A"]["metrics"])
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
        side: sum(row.get("status") == "filled" and row.get("side") == side for row in records["A"])
        for side in ("long", "short")
    }
    info = {
        "E>=850": sum(row.get("status") == "E" for row in base) >= 850,
        "A>=120": len(trades["A"]) >= 120,
        "D>=150": len(trades["D"]) >= 150,
        "A_buy_sell>=40": all(value >= 40 for value in directions.values()),
        "A30>=80": len(trades["A30"]) >= 80,
    }

    def positive(name: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
        )

    gates = {
        "A_net_positive": a_metrics["overall"]["net_pnl_jpy"] > 0,
        "A_pf_gt1": a_metrics["overall"]["profit_factor"] is not None
        and a_metrics["overall"]["profit_factor"] > 1,
        "CI_A_AminusD_AminusB_delta_positive": all(
            positive(name)
            for name in (
                "A_mean_net_jpy",
                "A_minus_D_conditional_mean_jpy",
                "A_minus_B_conditional_mean_jpy",
                "delta_ols_jpy",
            )
        ),
        "A_beats_fade_fixed_buy_fixed_sell": all(
            positive(f"A_minus_{name}_conditional_mean_jpy")
            for name in ("A_fade", "A_buy", "A_sell")
        ),
        "cost_delay_threshold_holding_robust": all(
            results[name]["metrics"]["overall"]["net_pnl_jpy"] > 0
            and results[name]["metrics"]["overall"]["profit_factor"] is not None
            and results[name]["metrics"]["overall"]["profit_factor"] > 1
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
                side: research_metrics(
                    tuple(trade for trade in trades["A"] if trade.side.value == side), target_bars
                )["overall"]
                for side in directions
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
