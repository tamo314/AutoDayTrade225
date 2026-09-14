"""Execute the preregistered Development-only R034-Q001 experiment."""

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
from n225m_bt.domain import Bar, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r034 import opening_range_rejection_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r034-q001-20260914-opening-range-fixed15-rejection-05"
OUT = ROOT / "results" / "research" / IDENTIFIER
Condition = Literal[
    "A_rejection_fade",
    "B_all_fade",
    "C_persistent_fade",
    "D_buy",
    "E_sell",
    "F_continue",
]
CONDITIONS: tuple[Condition, ...] = (
    "A_rejection_fade",
    "B_all_fade",
    "C_persistent_fade",
    "D_buy",
    "E_sell",
    "F_continue",
)
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


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
        "status": "frozen_before_r034_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, volume, external prices, OOS and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    """Reproduce the fixed R004 whole-session isolation without changing Gold."""
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
        Path("src/n225m_bt/research/r034.py"),
        Path("src/n225m_bt/strategies/opening_range_compression_breakout.py"),
        Path("tests/test_r033_q001.py"),
        Path("tests/test_r034_q001.py"),
    ]
    return {str(name): digest(ROOT / name) for name in names}


def preregistration(
    source: dict[str, object], inputs: dict[str, object], files: dict[str, str]
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R034-Q001",
        "status": "frozen_before_r034_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development exploratory evidence, not independent confirmation.",
        "duplicate_review": {
            "R001_R033": "R004 requires a 1-tick external break followed by first return within five minutes and uses protective exits. R033 is a prior-20-session compression-conditioned breakout continuation. No prior study uses every day opening 30-minute H/L, first strict close breakout in bars 31--90, only fixed b+15 close classification, and a k+60 fixed exit for rejection fade versus persistent/all controls.",
            "conclusion": "No duplicate. Execute this specification only; no alternative specification.",
        },
        "hypothesis": "When the first strict close breakout from the first 30 planned day bars is not still outside the original range exactly 15 scheduled bars later, fading its direction from the next eligible open for a fixed 60 minutes has positive post-cost expectancy and exceeds unconditional fading, persistent-event fading, always buy, always sell, and breakout continuation. This uses price only and identifies neither liquidity nor order flow.",
        "fixed_rule": {
            "event": "Use exactly the first continuous 30 eligible planned day bars to fix H=max(high) and L=min(low). In bars 31--90, the first close>H is upper/long breakout and first close<L is lower/short breakout; equality/touch is excluded and no breakout is no trade. Require all b+1..b+15 scheduled bars continuous and eligible. At k=b+15, upper is rejected iff close_k<=H and persistent iff close_k>H; lower is rejected iff close_k>=L and persistent iff close_k<L. A traversal to the opposite side is rejected. Do not use first re-entry time or add filters.",
            "execution": "After k close, normal entry is the next eligible open E. The fixed EXIT signal is k+60 and the shared engine fills X=E+60=k+61 at its next eligible open. Delay never extends X. One contract, maximum one trade/day/condition and one position; no stop, target, reentry, update, or early exit.",
            "conditions": {
                "A": "rejected events, fade breakout direction",
                "B_all": "all classified events, fade breakout direction",
                "C_persistent": "persistent events, fade breakout direction",
                "D_buy": "A events, always long",
                "E_sell": "A events, always short",
                "F_continue": "A events, follow breakout direction",
                "A2_A3": "A rerun at 2/3 tick per side",
                "A_delay": "A signal one bar later with the same absolute X",
            },
        },
        "inputs": {
            "physical": inputs,
            "r004_fixed_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; list hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
            "quality_ceiling": "PASS_LIMITED",
            "common_axis": "All versioned scheduled Development day trade_dates; no trade, missing, quarantine and cancellation remain zero JPY.",
        },
        "costs": {
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "A3": {"slippage_ticks_per_side": 3, "fee_jpy_per_side": 30},
            "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; slippage is not deducted twice.",
        },
        "evaluation": {
            "four_groups": "rejected/persistent x upper/lower breakout: count, trades, gross, fees, net, expectancy and breakout 15-minute bucket are persisted.",
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10000,
                "seed": 20260914,
                "common_indices": True,
                "noncircular": True,
                "tail_truncate": True,
                "percentile": "linear",
            },
            "information_gate": "B_all>=600; A and C>=200; A/C original upper/lower each>=60; every rejection/persistent x upper/lower group>=60.",
            "pass_requires_all": "A Net>0, PF>1, all requested bootstrap CI lower bounds>0, A2/A3/A_delay expectancy>0, A positive Net in >=3 of 2021--2024, >=27 positive months of 54, and A top10-winner-excluded Net>0.",
            "decision": "BLOCKED for execution/preflight failure; INCONCLUSIVE for information failure; otherwise REJECT on any failed requirement; all pass is INVESTIGATE only.",
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
    if condition in {"A_rejection_fade", "B_all_fade", "C_persistent_fade"}:
        return "short" if direction == "long" else "long"
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    return direction


def tradable(condition: Condition, status: str) -> bool:
    return (
        (condition == "B_all_fade" and status in {"rejected", "persistent"})
        or (condition == "C_persistent_fade" and status == "persistent")
        or (
            condition in {"A_rejection_fade", "D_buy", "E_sell", "F_continue"}
            and status == "rejected"
        )
    )


def run_condition(
    data: ResearchData,
    engine: BacktestEngine,
    condition: Condition,
    groups: dict[tuple[date, Session], list[Bar]],
    quarantined: set[tuple[date, Session]],
    targets: list[date],
    *,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for target in targets:
        key = (target, Session.DAY)
        event = opening_range_rejection_event(
            engine.classifier, target, groups.get(key), target_quarantined=key in quarantined
        )
        status = cast(str, event["status"])
        event.update(
            {"condition": condition, "base_event_status": status, "pre_event_status": status}
        )
        if not tradable(condition, status):
            event.update({"status": "skipped", "reason": event.get("reason", "EVENT_FILTER")})
            audit[f"no_trade_{event['reason']}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["k_ts_jst"]))
        result = engine.run(
            groups[key],
            OpeningRangeCompressionBreakoutStrategy(
                f"r034_{condition}", signal, side(condition, event), delay
            ),
            canonical_hash(
                {"condition": condition, "event": event, "data": data.data_version, "delay": delay}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R034 produced more than one day trade")
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
                    "exit_delay_minutes": int((trade.exit_ts - signal).total_seconds() // 60 - 60),
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


def group_summary(events: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for classification in ("rejected", "persistent"):
        for direction in ("long", "short"):
            rows = [
                row
                for row in events
                if row.get("base_event_status") == classification
                and row.get("breakout_direction") == direction
            ]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            output[f"{classification}_{direction}"] = {
                "event_count": len(rows),
                "trade_count": len(filled),
                "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in filled),
                "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled),
                "net_pnl_jpy": sum(net),
                "expectancy_jpy": fmean(net) if net else None,
                "breakout_15m_bucket": dict(
                    sorted(Counter(cast(str, row["breakout_15m_bucket"]) for row in rows).items())
                ),
            }
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
    write_json(folder / "event_group_summary.json", group_summary(events))
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
    keys = sorted(daily_values["A_rejection_fade"])
    a: list[float] = [float(daily_values["A_rejection_fade"][key]) for key in keys]
    series: dict[str, list[float]] = {"A_daily_mean_net_jpy": a}
    for condition in ("B_all_fade", "D_buy", "E_sell", "F_continue"):
        series[f"A_minus_{condition}_daily_mean_net_jpy"] = [
            left - daily_values[condition][key] for left, key in zip(a, keys, strict=True)
        ]

    def ledger(events: list[dict[str, object]]) -> tuple[dict[str, int], dict[str, int]]:
        by_day = {
            cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"])
            for row in events
            if row.get("status") == "filled"
        }
        return by_day, {key: int(key in by_day) for key in keys}

    a_by_day, a_counts = ledger(a_events)
    c_by_day, c_counts = ledger(c_events)
    if not a_by_day or not c_by_day:
        raise ValueError("empty R034 conditional bootstrap series")
    samples: dict[str, list[float]] = {
        name: [] for name in [*series, "A_minus_C_persistent_conditional_expectancy_jpy"]
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
            sum(a_by_day.get(keys[index], 0) for index in picked),
            sum(a_counts[keys[index]] for index in picked),
        )
        c_sum, c_count = (
            sum(c_by_day.get(keys[index], 0) for index in picked),
            sum(c_counts[keys[index]] for index in picked),
        )
        samples["A_minus_C_persistent_conditional_expectancy_jpy"].append(
            a_sum / a_count - c_sum / c_count if a_count and c_count else 0.0
        )
    result: dict[str, object] = {
        "method": "noncircular moving blocks with replacement, tail truncate; conditional A-C recomputes sum/count per replicate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": 20260914,
        "common_indices_all_conditions": True,
        "percentile": "linear",
    }
    for name, rows in series.items():
        result[name] = {
            "estimate": fmean(rows),
            "ci95_percentile_linear": [
                percentile(samples[name], 0.025),
                percentile(samples[name], 0.975),
            ],
        }
    result["A_minus_C_persistent_conditional_expectancy_jpy"] = {
        "estimate": sum(a_by_day.values()) / len(a_by_day) - sum(c_by_day.values()) / len(c_by_day),
        "ci95_percentile_linear": [
            percentile(samples["A_minus_C_persistent_conditional_expectancy_jpy"], 0.025),
            percentile(samples["A_minus_C_persistent_conditional_expectancy_jpy"], 0.975),
        ],
    }
    return result


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
        raise ValueError("active execution/cost contract differs from frozen R034 specification")
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
            "status": "preregistered_before_r034_price_statistics_events_or_pnl",
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
            "tests/test_r034_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r034.py",
            "tests/test_r034_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r034.py",
            str(Path(__file__).relative_to(ROOT)),
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
        raise ValueError("R034 validation failed before Development price access")
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
        trades, events, audit = run_condition(view, engine, condition, groups, quarantined, days)
        values = daily(trades, days)
        metrics = write_condition(
            OUT / condition, condition, trades, events, audit, view, 1, values
        )
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, values
        result[condition] = {"trade_count": len(trades), "metrics": metrics, "audit": audit}
    for ticks, name, delay in (
        (2, "A_rejection_fade_2tick", 0),
        (3, "A_rejection_fade_3tick", 0),
        (1, "A_rejection_fade_delay", 1),
    ):
        config = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, events, audit = run_condition(
            view,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            "A_rejection_fade",
            groups,
            quarantined,
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
        trades_by[name], events_by[name] = trades, events

    def equal_rows(
        left: dict[str, object], right: dict[str, object], keys: tuple[str, ...]
    ) -> bool:
        return all(left.get(key) == right.get(key) for key in keys)

    checks_audit = {
        "rejected_A_B_path_equal": all(
            equal_rows(
                left,
                right,
                (
                    "breakout_direction",
                    "k_ts_jst",
                    "side",
                    "entry_ts_jst",
                    "exit_ts_jst",
                    "net_pnl_jpy",
                ),
            )
            for left, right in zip(
                events_by["A_rejection_fade"], events_by["B_all_fade"], strict=True
            )
            if left.get("base_event_status") == "rejected"
        ),
        "persistent_B_C_path_equal": all(
            equal_rows(
                left,
                right,
                (
                    "breakout_direction",
                    "k_ts_jst",
                    "side",
                    "entry_ts_jst",
                    "exit_ts_jst",
                    "net_pnl_jpy",
                ),
            )
            for left, right in zip(
                events_by["B_all_fade"], events_by["C_persistent_fade"], strict=True
            )
            if left.get("base_event_status") == "persistent"
        ),
        "A_variants_event_side_equal": all(
            equal_rows(
                row,
                events_by["A_rejection_fade"][index],
                ("trade_date", "breakout_direction", "k_ts_jst", "side"),
            )
            for name in (
                "A_rejection_fade_2tick",
                "A_rejection_fade_3tick",
                "A_rejection_fade_delay",
            )
            for index, row in enumerate(events_by[name])
        ),
        "fixed_exit_and_max_one_position": all(
            trade.exit_ts
            == datetime.fromisoformat(
                cast(
                    str,
                    next(
                        row
                        for row in events_by[name]
                        if row.get("status") == "filled"
                        and row.get("trade_date") == trade.trade_date.isoformat()
                    )["X_planned_exit_jst"],
                )
            )
            and trade.exit_reason.value == "signal"
            for name, trades in trades_by.items()
            for trade in trades
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
            "checks": checks_audit,
            "all_pass": all(checks_audit.values()),
            "accounting": "Gross is slippage-inclusive; Net=Gross-fees.",
        },
    )
    if not all(checks_audit.values()):
        raise ValueError("R034 execution/accounting audit failed")
    boot = bootstrap(daily_by, events_by["A_rejection_fade"], events_by["C_persistent_fade"])
    write_json(OUT / "bootstrap.json", boot)
    groups_summary = group_summary(events_by["B_all_fade"])
    write_json(OUT / "event_stratification.json", groups_summary)
    write_json(OUT / "development_results.json", result)
    a_metrics = cast(dict[str, object], result["A_rejection_fade"])["metrics"]
    c_metrics = cast(dict[str, object], result["C_persistent_fade"])["metrics"]
    a_overall, c_overall = (
        cast(dict[str, object], a_metrics)["overall"],
        cast(dict[str, object], c_metrics)["overall"],
    )
    a_side, c_side = (
        cast(dict[str, object], cast(dict[str, object], a_metrics)["segments"])["side"],
        cast(dict[str, object], cast(dict[str, object], c_metrics)["segments"])["side"],
    )
    a_side_map = cast(dict[str, dict[str, object]], a_side)
    c_side_map = cast(dict[str, dict[str, object]], c_side)
    group_counts = cast(dict[str, dict[str, object]], groups_summary)
    sufficient = (
        len(trades_by["B_all_fade"]) >= 600
        and len(trades_by["A_rejection_fade"]) >= 200
        and len(trades_by["C_persistent_fade"]) >= 200
        and all(
            cast(int, a_side_map.get(direction, {}).get("trade_count", 0)) >= 60
            and cast(int, c_side_map.get(direction, {}).get("trade_count", 0)) >= 60
            for direction in ("long", "short")
        )
        and all(cast(int, item["trade_count"]) >= 60 for item in group_counts.values())
    )
    lower_positive = all(
        cast(list[float], cast(dict[str, object], item)["ci95_percentile_linear"])[0] > 0
        for item in boot.values()
        if isinstance(item, dict) and "ci95_percentile_linear" in item
    )
    concentration = cast(dict[str, object], a_metrics)["concentration"]
    yearly = cast(dict[str, object], cast(dict[str, object], a_metrics)["segments"])["year"]
    concentration_map = cast(dict[str, object], concentration)
    yearly_map = cast(dict[str, dict[str, object]], yearly)
    a_overall_map = cast(dict[str, object], a_overall)

    def variant_expectancy(name: str) -> float:
        condition_result = cast(dict[str, object], result[name])
        condition_metrics = cast(dict[str, object], condition_result["metrics"])
        overall = cast(dict[str, object], condition_metrics["overall"])
        return cast(float, overall["expectancy_jpy"])

    gates = {
        "A_net_positive": cast(int, a_overall_map["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": a_overall_map["profit_factor"] is not None
        and cast(float, a_overall_map["profit_factor"]) > 1,
        "all_bootstrap_ci_lowers_positive": lower_positive,
        "A2_A3_delay_expectancy_positive": all(
            variant_expectancy(name) > 0
            for name in (
                "A_rejection_fade_2tick",
                "A_rejection_fade_3tick",
                "A_rejection_fade_delay",
            )
        ),
        "three_positive_years_2021_2024": sum(
            cast(int, yearly_map.get(str(year), {}).get("net_pnl_jpy", 0)) > 0
            for year in range(2021, 2025)
        )
        >= 3,
        "positive_months_at_least_27": cast(float, concentration_map["positive_month_fraction"])
        >= 0.5,
        "top10_excluded_net_positive": cast(int, concentration_map["net_excluding_top10_jpy"]) > 0,
    }
    decision = (
        "INCONCLUSIVE" if not sufficient else "INVESTIGATE" if all(gates.values()) else "REJECT"
    )
    write_json(
        OUT / "decision.json",
        {
            "status": decision,
            "information_sufficient": sufficient,
            "information_gate": {
                "B_all_trades": len(trades_by["B_all_fade"]),
                "A_trades": len(trades_by["A_rejection_fade"]),
                "C_trades": len(trades_by["C_persistent_fade"]),
                "four_groups": groups_summary,
            },
            "fixed_gates": gates,
            "A_overall": a_overall,
            "C_overall": c_overall,
            "bootstrap": boot,
            "fixed_rule_note": "No rescue exploration, WFA, OOS or Final Holdout executed.",
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
