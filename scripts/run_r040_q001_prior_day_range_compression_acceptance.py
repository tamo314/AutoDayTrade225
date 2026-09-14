"""Execute the preregistered Development-only R040-Q001 experiment."""

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
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r040 import prior_day_compression_acceptance_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.prior_day_compression_breakout import PriorDayCompressionBreakoutStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r040-q001-20260914-prior-day-range-compression-acceptance-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20260918
Condition = Literal["A_compressed", "B_all", "C_noncompressed", "D_buy", "E_sell", "F_reverse"]
CONDITIONS: tuple[Condition, ...] = (
    "A_compressed",
    "B_all",
    "C_noncompressed",
    "D_buy",
    "E_sell",
    "F_reverse",
)
VARIANTS = {"A2_2tick": (2, 0), "A3_3tick": (3, 0), "A_delay": (1, 1)}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def input_manifest(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_r040_price_statistics_thresholds_events_or_pnl",
        "scope": "Selected normalized Development Parquet only; raw, volume, external prices, OOS and Final Holdout prohibited.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
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
        raise ValueError(f"R004 fixed isolation mismatch: {mismatch}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def preregistration(
    source: dict[str, object], inputs: dict[str, object], files: dict[str, str]
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R040-Q001",
        "status": "frozen_before_r040_price_statistics_thresholds_events_or_pnl",
        "seed": SEED,
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation or unused validation.",
        "duplicate_review": "R001-R039 reviewed before price access. R033 is opening-range compression/breakout; R036 is prior-day boundary failed acceptance reversal. None combines prior full normal-day range, prior-60 scheduled-day Q25 compression, first-120-minute strict close breakout, fixed b+5 outside acceptance, and 60-minute continuation.",
        "hypothesis": "When the immediate prior scheduled day normal-session range is strictly below the lower-quartile nearest-rank range of its preceding planned 60 days, the next day's first accepted strict close breakout of that prior range within 120 minutes follows through in breakout direction for 60 minutes after costs, exceeding all accepted breakouts, noncompressed accepted breakouts, fixed long, fixed short, and reverse controls. This is a price-discovery interpretation only; it does not identify order flow, volume, news, or options demand.",
        "fixed_rule": {
            "prior_state": "For target d choose exactly immediate preceding scheduled day p; no older fallback. p must be Development, non-isolated, and have every eligible scheduled normal continuous day bar from scheduled open through versioned normal end; PDH=max(high), PDL=min(low), W=PDH-PDL>0. Observed final rows, force-flat and closing-auction prices never define W.",
            "reference": "Use exactly the 60 scheduled day sessions before p, newest first. Out-of-Development, isolated, missing, ineligible and W<=0 observations are invalid and never replaced. With >=50 valid widths, QW is ascending ceil(.25*n)-th width. W<QW is compressed; W>=QW including ties is noncompressed; p is excluded.",
            "event": "d first scheduled normal-day bar open must be inside inclusive [PDL,PDH], otherwise gap-day skip. Scan first 120 scheduled bars: close>PDH is long, close<PDL short; high/low contact and equality are not breaks. Use only first break b. Require contiguous eligible b..b+5; accepted iff close(b+5) remains strictly outside in same direction; rejected/unaccepted days do not seek another break.",
            "conditions": {
                "A_compressed": "compressed accepted breakout direction",
                "B_all": "all accepted breakout direction",
                "C_noncompressed": "noncompressed accepted breakout direction",
                "D_buy": "A event/time fixed long",
                "E_sell": "A event/time fixed short",
                "F_reverse": "A event/time opposite breakout direction",
                "A2_A3": "A at 2/3 ticks per side",
                "A_delay": "A entry one scheduled bar later, same absolute exit",
            },
            "execution": "After k=b+5 is known, engine receives order signal at k and fills next eligible scheduled bar open E=k+1. Fixed exit fill is E+60 minutes open (engine exit signal k+60); delayed entry fills E+1 and does not extend exit. One contract, <=1 trade/day/position; no stop, target, reentry, updates or early exit.",
            "prohibited": [
                "rescue parameter changes",
                "gap-day use",
                "absolute-range/direction/weekday/year/trend/volume filters",
                "WFA",
                "OOS",
                "Final Holdout",
            ],
        },
        "inputs": {
            "physical": inputs,
            "r004_fixed_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
            "quality_ceiling": "PASS_LIMITED",
            "common_axis": "Scheduled Development OSE day dates excluding day-isolated dates; every skip/cancellation/no-trade is 0 JPY.",
        },
        "costs": {
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "A3": {"slippage_ticks_per_side": 3, "fee_jpy_per_side": 30},
            "accounting": "Gross includes slippage; Net=Gross-fees; no double deduction.",
        },
        "evaluation": {
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10000,
                "seed": SEED,
                "common_indices": True,
                "noncircular": True,
                "tail_truncate": True,
                "percentile": "linear",
            },
            "information_gate": "B>=400; A>=100 and long/short>=35; C>=250; compressed/noncompressed x direction each>=35.",
            "pass_requires_all_after_information": "A Net>0; PF>1; lower CI>0 for A daily mean, A-B/A-C conditional Net expectancy, A-D/E/F daily mean; A2/A3/delay expectancy>0; >=3 positive years 2021-24; >=27 positive months/54; top10 winner excluded Net>0.",
            "decision": "BLOCKED for input/synthetic/execution/accounting failure; INCONCLUSIVE for information failure; otherwise REJECT for any failed fixed requirement; all pass is INVESTIGATE only.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "implementation_files": files,
            "implementation_files_hash": canonical_hash(files),
            "input_manifest_hash": canonical_hash(inputs),
            "seed": SEED,
        },
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def event_for_day(
    classifier: CalendarClassifier,
    target: date,
    groups: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    schedule: list[date],
) -> dict[str, object]:
    index = schedule.index(target)
    prior = (
        None
        if index < 1
        else (
            schedule[index - 1],
            groups.get((schedule[index - 1], Session.DAY)),
            (schedule[index - 1], Session.DAY) in isolated,
        )
    )
    history = [
        (
            schedule[position],
            groups.get((schedule[position], Session.DAY)),
            (schedule[position], Session.DAY) in isolated,
        )
        for position in range(index - 2, index - 62, -1)
        if position >= 0
    ]
    return prior_day_compression_acceptance_event(
        classifier,
        target,
        groups.get((target, Session.DAY)),
        prior,
        history,
        target_quarantined=(target, Session.DAY) in isolated,
    )


def selectable(condition: Condition, status: str) -> bool:
    return (
        (condition == "B_all" and status in {"compressed_accepted", "noncompressed_accepted"})
        or (condition == "C_noncompressed" and status == "noncompressed_accepted")
        or (
            condition in {"A_compressed", "D_buy", "E_sell", "F_reverse"}
            and status == "compressed_accepted"
        )
    )


def direction(condition: Condition, event: dict[str, object]) -> str:
    breakout = cast(str, event["breakout_direction"])
    if condition in {"A_compressed", "B_all", "C_noncompressed"}:
        return breakout
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    return "short" if breakout == "long" else "long"


def run_condition(
    data: ResearchData,
    engine: BacktestEngine,
    condition: Condition,
    groups: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    targets: list[date],
    schedule: list[date],
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for target in targets:
        event = event_for_day(engine.classifier, target, groups, isolated, schedule)
        status = cast(str, event["status"])
        event.update(
            {"condition": condition, "base_event_status": status, "variant_delay_minutes": delay}
        )
        if not selectable(condition, status):
            event.update(
                {"status": "skipped", "reason": event.get("reason", f"CONDITION_{status}")}
            )
            audit[f"skipped_{event['reason']}"] += 1
            records.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["k_ts_jst"]))
        side = direction(condition, event)
        result = engine.run(
            groups[(target, Session.DAY)],
            PriorDayCompressionBreakoutStrategy(f"r040_{condition}", signal, side, delay),
            canonical_hash(
                {"condition": condition, "event": event, "data": data.data_version, "delay": delay}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R040 violated one trade per day")
        audit["canceled_orders"] += result.canceled_orders
        if not result.trades:
            event.update(
                {"status": "eligible_order_unfilled", "reason": "ENGINE_NO_FILL_OR_FIXED_EXIT"}
            )
            audit["eligible_order_unfilled"] += 1
            records.append(event)
            continue
        trade = result.trades[0]
        event.update(
            {
                "status": "filled",
                "side": trade.side.value,
                "entry_ts_jst": trade.entry_ts.isoformat(),
                "exit_ts_jst": trade.exit_ts.isoformat(),
                "entry_signal_ts_jst": trade.entry_signal_ts.isoformat(),
                "exit_signal_ts_jst": trade.exit_signal_ts.isoformat()
                if trade.exit_signal_ts
                else None,
                "entry_delay_minutes": int(
                    (
                        trade.entry_ts
                        - datetime.fromisoformat(cast(str, event["E_planned_entry_jst"]))
                    ).total_seconds()
                    // 60
                ),
                "exit_delay_minutes": int(
                    (
                        trade.exit_ts
                        - datetime.fromisoformat(cast(str, event["X_planned_exit_jst"]))
                    ).total_seconds()
                    // 60
                ),
                "exit_reason": trade.exit_reason.value,
                "gross_pnl_jpy": trade.gross_pnl_jpy,
                "slippage_cost_jpy": trade.slippage_cost_jpy,
                "fees_jpy": trade.fees_jpy,
                "net_pnl_jpy": trade.net_pnl_jpy,
            }
        )
        trades.append(trade)
        audit["trades"] += 1
        records.append(event)
    ordered = tuple(
        replace(trade, trade_id=f"trade-{number:06d}")
        for number, trade in enumerate(sorted(trades, key=lambda row: row.entry_ts), 1)
    )
    return ordered, records, dict(sorted(audit.items()))


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    low, high = floor(point), ceil(point)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (point - low)
    )


def bootstrap(
    daily: dict[str, dict[str, int]], records: dict[str, list[dict[str, object]]], axis: list[str]
) -> dict[str, object]:
    arrays: dict[str, list[int]] = {name: [daily[name][day] for day in axis] for name in CONDITIONS}
    nets: dict[str, dict[str, int]] = {
        name: {
            cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"])
            for row in records[name]
            if row.get("status") == "filled"
        }
        for name in ("A_compressed", "B_all", "C_noncompressed")
    }
    samples: dict[str, list[float]] = {
        name: []
        for name in (
            "A_daily_mean_net_jpy",
            "A_minus_D_buy_daily_mean_net_jpy",
            "A_minus_E_sell_daily_mean_net_jpy",
            "A_minus_F_reverse_daily_mean_net_jpy",
            "A_minus_B_all_conditional_expectancy_jpy",
            "A_minus_C_noncompressed_conditional_expectancy_jpy",
        )
    }
    rng, count, block = Random(SEED), len(axis), 20
    if count < block:
        raise ValueError("R040 common axis shorter than fixed bootstrap block")
    for _ in range(10_000):
        picked: list[int] = []
        while len(picked) < count:
            start = rng.randrange(count - block + 1)
            picked.extend(range(start, start + block))
        picked = picked[:count]
        samples["A_daily_mean_net_jpy"].append(
            fmean(arrays["A_compressed"][index] for index in picked)
        )
        for name in ("D_buy", "E_sell", "F_reverse"):
            samples[f"A_minus_{name}_daily_mean_net_jpy"].append(
                fmean(arrays["A_compressed"][index] - arrays[name][index] for index in picked)
            )
        for name in ("B_all", "C_noncompressed"):
            a_sum, a_count = (
                sum(nets["A_compressed"].get(axis[index], 0) for index in picked),
                sum(axis[index] in nets["A_compressed"] for index in picked),
            )
            b_sum, b_count = (
                sum(nets[name].get(axis[index], 0) for index in picked),
                sum(axis[index] in nets[name] for index in picked),
            )
            samples[f"A_minus_{name}_conditional_expectancy_jpy"].append(
                a_sum / a_count - b_sum / b_count if a_count and b_count else 0.0
            )
    output: dict[str, object] = {
        "method": "noncircular moving-block bootstrap with replacement; tail truncate; conditionally recompute sum/count every replicate",
        "block_length_trade_dates": 20,
        "repetitions": 10000,
        "seed": SEED,
        "common_indices_all_conditions": True,
        "percentile": "linear",
    }
    for name, values in samples.items():
        if name == "A_daily_mean_net_jpy":
            estimate = fmean(arrays["A_compressed"])
        elif "conditional" in name:
            control = "B_all" if "B_all" in name else "C_noncompressed"
            estimate = fmean(nets["A_compressed"].values()) - fmean(nets[control].values())
        else:
            control = name.split("_minus_")[1].removesuffix("_daily_mean_net_jpy")
            estimate = fmean(
                arrays["A_compressed"][index] - arrays[control][index] for index in range(count)
            )
        output[name] = {
            "estimate": estimate,
            "ci95_percentile_linear": [percentile(values, 0.025), percentile(values, 0.975)],
        }
    return output


def strata(records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for compressed in (True, False):
        for direction_name in ("long", "short"):
            source = [
                row
                for row in records
                if row.get("compression") is compressed
                and row.get("breakout_direction") == direction_name
            ]
            accepted = [
                row
                for row in source
                if row.get("base_event_status") in {"compressed_accepted", "noncompressed_accepted"}
            ]
            filled = [row for row in accepted if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            output[f"{'compressed' if compressed else 'noncompressed'}_{direction_name}"] = {
                "eligible_days": sum(row.get("compression") is compressed for row in records),
                "breakout_count": len(source),
                "accepted_count": len(accepted),
                "trade_count": len(filled),
                "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in filled),
                "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled),
                "net_pnl_jpy": sum(net),
                "expectancy_jpy": fmean(net) if net else None,
            }
    return output


def main() -> None:
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
        raise ValueError("active execution/cost contract differs from R040")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    paths = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r040.py"),
        Path("src/n225m_bt/strategies/prior_day_compression_breakout.py"),
        Path("tests/test_r040_q001.py"),
    ]
    files, inputs = (
        {str(path): digest(ROOT / path) for path in paths},
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
            "status": "preregistered_before_r040_price_statistics_thresholds_events_or_pnl",
            "plan_hash": canonical_hash(plan),
            "seed": SEED,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    commands = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r040_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r040.py",
            "src/n225m_bt/strategies/prior_day_compression_breakout.py",
            "tests/test_r040_q001.py",
            str(paths[0]),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r040.py",
            "src/n225m_bt/strategies/prior_day_compression_breakout.py",
            "tests/test_r040_q001.py",
            str(paths[0]),
        ],
    }
    validation: dict[str, object] = {
        name: {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        for name, command in commands.items()
        for result in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation["coverage"] = (
        "schedule/calendar/trade_date, immediate p, normal-session/auction exclusion, 60 scheduled/no backfill/50-valid/Q25/strict tie, gap, strict upper/lower, first b, b+5 acceptance, missing/isolation/outside, prefix, next-open/fixed exit/delay/accounting/holdout lock"
    )
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, object], validation[name])["returncode"] == 0 for name in commands)
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R040 synthetic/static gate failed before Development price access")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    groups = session_groups(view.bars)
    full_schedule = [item.trade_date for item in classifier.exchange_calendar.trading_days()]
    targets = [
        day
        for day in full_schedule
        if date(2021, 1, 1) <= day <= date(2025, 6, 30) and (day, Session.DAY) not in isolated
    ]
    if len(targets) != 1111:
        raise ValueError(f"unexpected R040 fixed target axis: {len(targets)}")
    axis = [day.isoformat() for day in targets]
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": quarantine_audit,
            "fixed_target_day_trade_dates": axis,
            "fixed_target_day_count": len(axis),
            "schedule_snapshot_sha256": digest(ROOT / "config" / "sessions.yaml"),
            "calendar_snapshot_sha256": digest(ROOT / "config" / "local_calendar.yaml"),
            "physical_io": "Development normalized Parquet only; no OOS/Final Holdout selected",
        },
    )
    records_by: dict[str, list[dict[str, object]]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    for name in CONDITIONS:
        trades, records, audit = run_condition(
            view,
            BacktestEngine(instrument.instrument.to_spec(), baseline, classifier),
            name,
            groups,
            isolated,
            targets,
            full_schedule,
        )
        daily = dict.fromkeys(axis, 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = OUT / name
        folder.mkdir()
        write_results(
            folder,
            trades,
            (),
            {"campaign_id": IDENTIFIER, "condition": name, "cost": "1 tick/side + 30JPY/side"},
        )
        metrics = research_metrics(
            trades, [bar for day in targets for bar in groups.get((day, Session.DAY), [])]
        )
        write_json(folder / "events.json", records)
        pl.DataFrame(records).write_parquet(folder / "events.parquet")
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "metrics_research.json", metrics)
        write_json(folder / "execution_audit.json", audit)
        records_by[name], trades_by[name], daily_by[name], results[name] = (
            records,
            trades,
            daily,
            {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit},
        )
    for variant_name, (ticks, delay) in VARIANTS.items():
        cost = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, records, audit = run_condition(
            view,
            BacktestEngine(instrument.instrument.to_spec(), cost, classifier),
            "A_compressed",
            groups,
            isolated,
            targets,
            full_schedule,
            delay,
        )
        daily = dict.fromkeys(axis, 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = OUT / variant_name
        folder.mkdir()
        write_results(
            folder,
            trades,
            (),
            {
                "campaign_id": IDENTIFIER,
                "condition": variant_name,
                "cost": f"{ticks} tick/side + 30JPY/side",
            },
        )
        metrics = research_metrics(
            trades, [bar for day in targets for bar in groups.get((day, Session.DAY), [])]
        )
        write_json(folder / "events.json", records)
        pl.DataFrame(records).write_parquet(folder / "events.parquet")
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "metrics_research.json", metrics)
        write_json(folder / "execution_audit.json", audit)
        (
            records_by[variant_name],
            trades_by[variant_name],
            daily_by[variant_name],
            results[variant_name],
        ) = (
            records,
            trades,
            daily,
            {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit},
        )
    by_day = {
        name: {cast(str, row["trade_date"]): row for row in records}
        for name, records in records_by.items()
    }
    path = ("side", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")

    def same(left: dict[str, object], right: dict[str, object]) -> bool:
        return all(left.get(field) == right.get(field) for field in path)

    checks = {
        "compressed_A_B_path_equal": all(
            same(by_day["A_compressed"][day], by_day["B_all"][day])
            for day in axis
            if by_day["A_compressed"][day].get("status") == "filled"
        ),
        "noncompressed_B_C_path_equal": all(
            same(by_day["B_all"][day], by_day["C_noncompressed"][day])
            for day in axis
            if by_day["C_noncompressed"][day].get("status") == "filled"
        ),
        "A_F_opposite_sides": all(
            by_day["A_compressed"][day].get("side") != by_day["F_reverse"][day].get("side")
            for day in axis
            if by_day["A_compressed"][day].get("status") == "filled"
        ),
        "A_variants_event_side_equal": all(
            all(
                by_day["A_compressed"][day].get(field) == by_day[name][day].get(field)
                for field in (
                    "trade_date",
                    "p_trade_date",
                    "PDH_points",
                    "PDL_points",
                    "W_points",
                    "QW_points",
                    "compression",
                    "breakout_direction",
                    "k_ts_jst",
                    "side",
                )
            )
            for name in VARIANTS
            for day in axis
        ),
        "one_trade_per_day": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in trades_by.values()
        ),
        "fixed_exit_delay_nonextended": all(
            row.get("exit_reason") == ExitReason.SIGNAL.value
            and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0)
            and row.get("exit_delay_minutes") == 0
            for name, records in records_by.items()
            for row in records
            if row.get("status") == "filled"
        ),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
        "slippage_not_double_deducted": all(
            trade.fees_jpy == 60
            for name, trades in trades_by.items()
            if name not in VARIANTS
            for trade in trades
        ),
    }
    execution_audit = {
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "checks": checks,
        "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees.",
    }
    write_json(OUT / "execution_accounting_audit.json", execution_audit)
    boot, group_table = bootstrap(daily_by, records_by, axis), strata(records_by["B_all"])
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "strata.json", group_table)
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {
            "trade_dates": axis,
            "no_trade": "0 JPY",
            "series": {name: [daily_by[name][day] for day in axis] for name in records_by},
        },
    )
    a_metrics = cast(dict[str, Any], cast(dict[str, object], results["A_compressed"])["metrics"])
    overall, concentration = (
        cast(dict[str, Any], a_metrics["overall"]),
        cast(dict[str, Any], a_metrics["concentration"]),
    )
    years = {
        str(year): sum(daily_by["A_compressed"][day] for day in axis if day.startswith(str(year)))
        for year in range(2021, 2026)
    }
    months = {
        f"{year}-{month:02d}": sum(
            daily_by["A_compressed"][day] for day in axis if day.startswith(f"{year}-{month:02d}")
        )
        for year in range(2021, 2026)
        for month in range(1, 13)
        if not (year == 2025 and month > 6)
    }
    information = {
        "B_trade_count_at_least_400": len(trades_by["B_all"]) >= 400,
        "A_trade_count_at_least_100": len(trades_by["A_compressed"]) >= 100,
        "A_long_at_least_35": cast(int, group_table["compressed_long"]["trade_count"]) >= 35,
        "A_short_at_least_35": cast(int, group_table["compressed_short"]["trade_count"]) >= 35,
        "C_trade_count_at_least_250": len(trades_by["C_noncompressed"]) >= 250,
        "all_four_state_direction_groups_at_least_35": all(
            cast(int, row["trade_count"]) >= 35 for row in group_table.values()
        ),
    }

    def lower(metric_name: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[metric_name])["ci95_percentile_linear"])[
                0
            ]
            > 0
        )

    gates = information | {
        "A_net_positive": cast(int, overall["net_pnl_jpy"]) > 0,
        "A_profit_factor_above_one": overall["profit_factor"] is not None
        and cast(float, overall["profit_factor"]) > 1,
        "all_requested_ci_lowers_positive": all(
            lower(name)
            for name in (
                "A_daily_mean_net_jpy",
                "A_minus_B_all_conditional_expectancy_jpy",
                "A_minus_C_noncompressed_conditional_expectancy_jpy",
                "A_minus_D_buy_daily_mean_net_jpy",
                "A_minus_E_sell_daily_mean_net_jpy",
                "A_minus_F_reverse_daily_mean_net_jpy",
            )
        ),
        "A2_A3_delay_expectancy_positive": all(
            cast(dict[str, Any], cast(dict[str, object], results[name])["metrics"])["overall"][
                "expectancy_jpy"
            ]
            is not None
            and cast(
                float,
                cast(dict[str, Any], cast(dict[str, object], results[name])["metrics"])["overall"][
                    "expectancy_jpy"
                ],
            )
            > 0
            for name in VARIANTS
        ),
        "at_least_three_positive_years_2021_2024": sum(
            value > 0 for year, value in years.items() if year != "2025"
        )
        >= 3,
        "positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "top10_winners_removed_net_positive": cast(int, concentration["net_excluding_top10_jpy"])
        > 0,
    }
    decision = (
        "BLOCKED"
        if execution_audit["status"] != "PASS"
        else "INCONCLUSIVE"
        if not all(information.values())
        else "INVESTIGATE"
        if all(gates.values())
        else "REJECT"
    )
    write_json(
        OUT / "development_results.json",
        {
            "campaign_id": IDENTIFIER,
            "quality_status": "PASS_LIMITED",
            "decision": decision,
            "information_gate": information,
            "gates": gates,
            "conditions": results,
            "A_aligned_year_net_jpy": years,
            "A_aligned_month_net_jpy": months,
            "strata": group_table,
            "scope": "Development only; 2025 Jan-Jun partial; no WFA/OOS/Final Holdout.",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "campaign_id": IDENTIFIER,
            "status": "development_complete",
            "decision": decision,
            "quality_status": "PASS_LIMITED",
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
