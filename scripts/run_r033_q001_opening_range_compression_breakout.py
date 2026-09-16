"""Execute the preregistered Development-only R033-Q001 experiment."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, Literal, cast

import polars as pl

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r033 import opening_range_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r033-q001-20260914-opening-range-compression-breakout-06"
OUT = ROOT / "results" / "research" / IDENTIFIER
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal[
    "A_compressed_follow", "B_all_follow", "C_noncompressed_follow", "D_buy", "E_sell", "F_reverse"
]
CONDITIONS: tuple[Condition, ...] = (
    "A_compressed_follow",
    "B_all_follow",
    "C_noncompressed_follow",
    "D_buy",
    "E_sell",
    "F_reverse",
)


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold_root, "development")
    ]
    return {
        "status": "frozen_before_r033_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, volume, external prices, OOS and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {
        key
        for key, rows in groups.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [row for key, rows in groups.items() if key not in isolated for row in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in row.quality_flags for row in included
        ),
        "rule": "exclude whole (trade_date, session) for any TICK_GRID_VIOLATION",
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
    audit.update({"expected_match": not mismatch, "mismatches": mismatch})
    if mismatch:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatch}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def implementation() -> dict[str, str]:
    names = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r033.py"),
        Path("src/n225m_bt/strategies/opening_range_compression_breakout.py"),
        Path("tests/test_r033_q001.py"),
    ]
    return {str(name): digest(ROOT / name) for name in names}


def preregistration(
    source: dict[str, object], inputs: dict[str, object], files: dict[str, str]
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R033-Q001",
        "status": "frozen_before_r033_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development exploratory evidence, not independent confirmation.",
        "duplicate_review": {
            "R001_R032": "R008 is same-session three-window compression with S+90..S+180 candidates and 30-minute holding; R003 is an unexecuted median-ratio opening design with multiple holds. No R001--R032 rule uses exactly prior 20 scheduled day opening ranges, their fifth order statistic, first close breakout in minutes 31--90, and a 60-minute fixed hold.",
            "conclusion": "No duplicate. Execute this specification only; no alternative specification.",
        },
        "hypothesis": "On days whose first 30-minute day-session high-low range is at or below the fifth-smallest range of exactly the 20 immediately preceding scheduled day sessions, the first close strictly outside that range in the next 60 minutes has positive post-cost 60-minute continuation expectancy, exceeding noncompressed, always-buy, always-sell, and reverse controls. It identifies neither order flow, volume, liquidity, cash, nor overseas markets.",
        "fixed_rule": {
            "history": "Use exactly the 20 immediately preceding scheduled day sessions in calendar order; every one must be Development, present, eligible and unquarantined. Never backfill with older days.",
            "event": "Opening H/L/R use exactly 30 continuous eligible 1-minute bars from planned day start; require R>0. T is ascending fifth of the 20 previous R values. Compression is R<=T; target R never enters T. In minutes 31--90, first close >H is long and first close <L short; high/low touch and close equality are no breakout; no breakout is no trade.",
            "execution": "Signal after breakout close; normal entry is next eligible open E, fixed exit E+60 minute open X, with no extension on delay. A_delay signals one further bar later but keeps X. One contract, at most one trade/day/condition, no stop/target/reentry/early exit.",
            "conditions": {
                "A": "compressed breakout follow",
                "B_all": "all valid breakout follow",
                "C_noncompressed": "noncompressed breakout follow",
                "D": "A event always long",
                "E": "A event always short",
                "F": "A event reverse",
                "A2_A3": "A rerun at 2/3 tick per side",
                "A_delay": "A signal delayed one bar",
            },
        },
        "inputs": {
            "physical": inputs,
            "r004_fixed_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; list hash "
            + QUARANTINE_HASH,
            "quality_ceiling": "PASS_LIMITED",
            "common_axis": "All versioned scheduled Development day trade_dates; history shortages, missing/isolated sessions, no-breakout, noncompression A filter, cancellation and no trade remain zero.",
        },
        "costs": {
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "A3": {"slippage_ticks_per_side": 3, "fee_jpy_per_side": 30},
            "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; slippage is not deducted twice.",
        },
        "evaluation": {
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10000,
                "seed": 20260914,
                "common_indices": True,
                "noncircular": True,
                "tail_truncate": True,
                "percentile": "linear",
            },
            "information_gate": ["A>=180 and A long/short>=60", "C>=400 and C long/short>=140"],
            "pass_requires_all": [
                "A Net>0 and PF>1",
                "A and A-D/A-E/A-F daily-mean CI lower bounds>0",
                "A-C conditional expectancy-difference CI lower bound>0",
                "A2/A3/A_delay expectancy>0",
                "A Net>0 in at least 3 of 2021--2024",
                "positive months>=27/54",
                "A top10-excluded Net>0",
            ],
            "decision": "BLOCKED for gate failure; INCONCLUSIVE for count failure; otherwise REJECT on any failed requirement; all pass is INVESTIGATE only.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "implementation_files": files,
            "implementation_files_hash": canonical_hash(files),
            "input_manifest_hash": canonical_hash(inputs),
            "seed": 20260914,
        },
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def planned_days(calendar: ExchangeCalendar) -> list[date]:
    return [
        row.trade_date
        for row in calendar.trading_days()
        if date(2021, 1, 1) <= row.trade_date <= date(2025, 6, 30)
    ]


def side(condition: Condition, event: dict[str, object]) -> str:
    direction = cast(str, event["breakout_direction"])
    if condition in {"A_compressed_follow", "B_all_follow", "C_noncompressed_follow"}:
        return direction
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    return "short" if direction == "long" else "long"


def run_condition(
    data: ResearchData,
    engine: BacktestEngine,
    condition: Condition,
    groups: dict[tuple[date, Session], list],
    quarantined: set[tuple[date, Session]],
    schedule_days: list[date],
    targets: list[date],
    *,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for target in targets:
        index = schedule_days.index(target)
        history_days = schedule_days[max(0, index - 20) : index][::-1]
        history = [
            (previous, groups.get((previous, Session.DAY)), (previous, Session.DAY) in quarantined)
            for previous in history_days
        ]
        key = (target, Session.DAY)
        event = opening_range_event(
            engine.classifier,
            target,
            groups.get(key),
            history,
            target_quarantined=key in quarantined,
        )
        base = cast(str, event["status"])
        event.update({"condition": condition, "base_event_status": base, "pre_event_status": base})
        tradable = base in {"compressed", "noncompressed"} and (
            condition == "B_all_follow"
            or (condition == "A_compressed_follow" and base == "compressed")
            or (condition == "C_noncompressed_follow" and base == "noncompressed")
            or (condition in {"D_buy", "E_sell", "F_reverse"} and base == "compressed")
        )
        if not tradable:
            event.update(
                {
                    "status": "skipped",
                    "reason": "COMPRESSION_FILTER"
                    if base == "noncompressed" and condition != "B_all_follow"
                    else "NONCOMPRESSION_FILTER"
                    if base == "compressed" and condition == "C_noncompressed_follow"
                    else event.get("reason"),
                }
            )
            audit[f"no_trade_{event.get('reason')}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["breakout_ts_jst"]))
        result = engine.run(
            groups[key],
            OpeningRangeCompressionBreakoutStrategy(
                f"r033_{condition}", signal, side(condition, event), delay
            ),
            canonical_hash(
                {"condition": condition, "event": event, "data": data.data_version, "delay": delay}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R033 produced more than one day trade")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update(
                {
                    "status": "filled",
                    "side": trade.side.value,
                    "entry_ts_jst": trade.entry_ts.isoformat(),
                    "exit_ts_jst": trade.exit_ts.isoformat(),
                    "entry_delay_minutes": int((trade.entry_ts - signal).total_seconds() // 60 - 1),
                    "exit_delay_minutes": int((trade.exit_ts - signal).total_seconds() // 60 - 61),
                    "exit_reason": trade.exit_reason.value,
                    "gross_pnl_jpy": trade.gross_pnl_jpy,
                    "slippage_cost_jpy": trade.slippage_cost_jpy,
                    "fees_jpy": trade.fees_jpy,
                    "net_pnl_jpy": trade.net_pnl_jpy,
                }
            )
            trades.append(trade)
            audit["trades"] += 1
        else:
            event["status"] = "eligible_order_unfilled"
            audit["eligible_order_unfilled"] += 1
        events.append(event)
    return (
        tuple(
            replace(trade, trade_id=f"trade-{number:06d}")
            for number, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
        ),
        events,
        dict(sorted(audit.items())),
    )


def daily(trades: tuple[Trade, ...], days: list[date]) -> dict[str, int]:
    output = dict.fromkeys((day.isoformat() for day in days), 0)
    for trade in trades:
        output[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return output


def write_condition(
    folder: Path,
    name: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    audit: dict[str, int],
    data: ResearchData,
    ticks: int,
    daily_values: dict[str, int],
) -> dict[str, object]:
    from n225m_bt.research.metrics import research_metrics

    folder.mkdir(parents=True, exist_ok=True)
    write_results(
        folder,
        trades,
        (),
        {
            "experiment_id": folder.name,
            "campaign_id": IDENTIFIER,
            "condition": name,
            "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30},
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    metrics = research_metrics(trades, data.bars)
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {
            "trade_date": sorted(daily_values),
            "net_pnl_jpy": [daily_values[key] for key in sorted(daily_values)],
        }
    ).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def percentile(values: list[float], q: float) -> float:
    rows, point = sorted(values), (len(values) - 1) * q
    lower, upper = floor(point), ceil(point)
    return (
        rows[lower]
        if lower == upper
        else rows[lower] + (rows[upper] - rows[lower]) * (point - lower)
    )


def bootstrap(
    daily_values: dict[str, dict[str, int]],
    a_events: list[dict[str, object]],
    c_events: list[dict[str, object]],
) -> dict[str, object]:
    keys = sorted(daily_values["A_compressed_follow"])
    a = [daily_values["A_compressed_follow"][key] for key in keys]
    series: dict[str, list[float]] = {"A_daily_mean_net_jpy": a}
    for condition in ("D_buy", "E_sell", "F_reverse"):
        series[f"A_minus_{condition}_daily_mean_net_jpy"] = [
            left - daily_values[condition][key] for left, key in zip(a, keys, strict=True)
        ]
    a_by_day = {
        cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"])
        for row in a_events
        if row.get("status") == "filled"
    }
    c_by_day = {
        cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"])
        for row in c_events
        if row.get("status") == "filled"
    }
    a_amounts = [a_by_day.get(key, 0) for key in keys]
    c_amounts = [c_by_day.get(key, 0) for key in keys]
    a_counts = [1 if key in a_by_day else 0 for key in keys]
    c_counts = [1 if key in c_by_day else 0 for key in keys]
    if not a or not a_by_day or not c_by_day:
        raise ValueError("empty R033 bootstrap series")
    samples: dict[str, list[float]] = {name: [] for name in series} | {
        "A_minus_C_noncompressed_conditional_expectancy_jpy": []
    }
    rng, count, block = Random(20260914), len(keys), 20
    for _ in range(10000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        picked = indices[:count]
        for name, rows in series.items():
            samples[name].append(fmean(rows[index] for index in picked))
        a_sum, a_count = (
            sum(a_amounts[index] for index in picked),
            sum(a_counts[index] for index in picked),
        )
        c_sum, c_count = (
            sum(c_amounts[index] for index in picked),
            sum(c_counts[index] for index in picked),
        )
        samples["A_minus_C_noncompressed_conditional_expectancy_jpy"].append(
            a_sum / a_count - c_sum / c_count if a_count and c_count else 0.0
        )
    return {
        "method": "noncircular moving blocks with replacement, tail truncate; conditional A-C recomputes sum/count per replicate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": 20260914,
        "common_indices_all_conditions": True,
        "percentile": "linear",
        **{
            name: {
                "estimate": fmean(rows)
                if name in series
                else (
                    sum(a_by_day.values()) / len(a_by_day) - sum(c_by_day.values()) / len(c_by_day)
                ),
                "ci95_percentile_linear": [
                    percentile(samples[name], 0.025),
                    percentile(samples[name], 0.975),
                ],
            }
            for name, rows in series.items()
        },
        "A_minus_C_noncompressed_conditional_expectancy_jpy": {
            "estimate": sum(a_by_day.values()) / len(a_by_day)
            - sum(c_by_day.values()) / len(c_by_day),
            "ci95_percentile_linear": [
                percentile(samples["A_minus_C_noncompressed_conditional_expectancy_jpy"], 0.025),
                percentile(samples["A_minus_C_noncompressed_conditional_expectancy_jpy"], 0.975),
            ],
        },
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r033_q001_opening_range_compression_breakout.py')

    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.max_fill_delay_minutes,
        baseline.execution.allow_cross_session_pending_order,
    ) != (1, 30, 10, False):
        raise ValueError("active execution/cost contract differs from frozen R033 specification")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    source, files, inputs = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        implementation(),
        input_manifest(data_config.gold_root),
    )
    plan = preregistration(source, inputs, files)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "effective_config.json",
        {
            "instrument": instrument.model_dump(mode="json"),
            "backtest": baseline.model_dump(mode="json"),
        },
    )
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "preregistered_before_r033_price_statistics_events_or_pnl",
            "source": source,
            "plan_hash": canonical_hash(plan),
            "seed": 20260914,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    checks = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r033_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r033.py",
            "src/n225m_bt/strategies/opening_range_compression_breakout.py",
            "tests/test_r033_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r033.py",
            "src/n225m_bt/strategies/opening_range_compression_breakout.py",
        ],
    }
    validation: dict[str, Any] = {
        name: {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        for name, command in checks.items()
        for completed in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, object], value)["returncode"] == 0 for value in validation.values())
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R033 validation failed before Development price access")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, quarantined = quarantine(development)
    schedule_days, groups = planned_days(calendar), session_groups(view.bars)
    days = [day for day in schedule_days if (day, Session.DAY) not in quarantined]
    if (
        len(schedule_days) != 1131
        or len(days) != 1111
        or len(set(schedule_days)) != len(schedule_days)
    ):
        raise ValueError(
            f"invalid fixed Development day axes: scheduled={len(schedule_days)}, target={len(days)}"
        )
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": quarantine_audit,
            "fixed_target_day_trade_dates": [day.isoformat() for day in days],
            "fixed_target_day_count": len(days),
            "physical_io": "Development normalized Parquet only; OOS/Final Holdout never selected",
        },
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    result: dict[str, object] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(
            view, engine, condition, groups, quarantined, schedule_days, days
        )
        values = daily(trades, days)
        metrics = write_condition(
            OUT / condition, condition, trades, events, audit, view, 1, values
        )
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, values
        result[condition] = {"trade_count": len(trades), "metrics": metrics, "audit": audit}
    for ticks, name, delay in (
        (2, "A_compressed_follow_2tick", 0),
        (3, "A_compressed_follow_3tick", 0),
        (1, "A_compressed_follow_delay", 1),
    ):
        config = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, events, audit = run_condition(
            view,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            "A_compressed_follow",
            groups,
            quarantined,
            schedule_days,
            days,
            delay=delay,
        )
        result[name] = {
            "trade_count": len(trades),
            "metrics": write_condition(
                OUT / name, name, trades, events, audit, view, ticks, daily(trades, days)
            ),
            "audit": audit,
        }
        events_by[name] = events
    audits = {
        "a_b_compressed_path_equal": all(
            left.get(key) == right.get(key)
            for left, right in zip(
                events_by["A_compressed_follow"], events_by["B_all_follow"], strict=True
            )
            if left.get("base_event_status") == "compressed"
            for key in (
                "breakout_direction",
                "breakout_ts_jst",
                "side",
                "entry_ts_jst",
                "exit_ts_jst",
                "net_pnl_jpy",
            )
        ),
        "noncompressed_A_skips_B_C_equal": all(
            left.get("status") == "skipped"
            and middle.get("status") == right.get("status")
            and middle.get("side") == right.get("side")
            and middle.get("net_pnl_jpy") == right.get("net_pnl_jpy")
            for left, middle, right in zip(
                events_by["A_compressed_follow"],
                events_by["B_all_follow"],
                events_by["C_noncompressed_follow"],
                strict=True,
            )
            if left.get("base_event_status") == "noncompressed"
        ),
        "A_variants_event_side_equal": all(
            all(
                row.get(key) == events_by["A_compressed_follow"][index].get(key)
                for key in ("trade_date", "breakout_direction", "breakout_ts_jst")
            )
            for name in (
                "A_compressed_follow_2tick",
                "A_compressed_follow_3tick",
                "A_compressed_follow_delay",
            )
            for index, row in enumerate(events_by[name])
        ),
        "accounting": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "checks": audits,
            "all_pass": all(audits.values()),
            "accounting": "Gross is slippage-inclusive; Net=Gross-fees.",
        },
    )
    if not all(audits.values()):
        raise ValueError("R033 execution/accounting audit failed")
    boot = bootstrap(
        daily_by, events_by["A_compressed_follow"], events_by["C_noncompressed_follow"]
    )
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", result)
    a_metrics = cast(dict[str, object], result["A_compressed_follow"])["metrics"]
    a_overall = cast(dict[str, object], a_metrics)["overall"]
    a_segments = cast(dict[str, object], a_metrics)["segments"]
    sides = cast(dict[str, object], a_segments)["side"]
    c_metrics = cast(dict[str, object], result["C_noncompressed_follow"])["metrics"]
    sufficient = (
        len(trades_by["A_compressed_follow"]) >= 180
        and len(trades_by["C_noncompressed_follow"]) >= 400
        and all(
            cast(dict[str, object], sides).get(direction, {}).get("trade_count", 0)
            >= (60 if direction in {"long", "short"} else 0)
            for direction in ("long", "short")
        )
        and all(
            cast(dict[str, object], cast(dict[str, object], c_metrics)["segments"])["side"]
            .get(direction, {})
            .get("trade_count", 0)
            >= 140
            for direction in ("long", "short")
        )
    )
    write_json(
        OUT / "decision.json",
        {
            "status": "INCONCLUSIVE" if not sufficient else "REJECT",
            "information_sufficient": sufficient,
            "fixed_rule_note": "No rescue exploration, WFA, OOS or Final Holdout executed.",
            "A_overall": a_overall,
            "bootstrap": boot,
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "complete",
            "decision": "INCONCLUSIVE" if not sufficient else "REJECT",
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
