"""Execute the preregistered Development-only R037-Q001 experiment."""

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
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r037 import opening_path_efficiency_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.opening_path_efficiency import OpeningPathEfficiencyStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r037-q001-20260914-opening-path-efficiency-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
R025 = ROOT / "results" / "research" / "r025-q001-20260914-prior-tse-day-range-acceptance-03"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20260915
Condition = Literal["A_eff", "B_all", "C_noneff", "D_buy", "E_sell", "F_reverse"]
CONDITIONS: tuple[Condition, ...] = ("A_eff", "B_all", "C_noneff", "D_buy", "E_sell", "F_reverse")


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
        "status": "frozen_before_r037_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, volume, external prices, OOS and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def frozen_cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R025 / "institutional_evidence" / "r020_cabinet_office_public_holidays.csv"
    if not source.exists():
        raise ValueError("frozen official TSE holiday evidence is unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copied = destination / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    if not (
        calendar.source_start <= date(2020, 12, 31) and calendar.source_end >= date(2025, 6, 30)
    ):
        raise ValueError("frozen TSE evidence does not cover Development")
    evidence: dict[str, object] = {
        "holiday_csv_sha256": digest(copied),
        "r025_completed_sha256": digest(R025 / "COMPLETED.json"),
        "rule": "TSE business day comes from frozen Cabinet Office holiday evidence, weekend, Jan 1-3 and Dec 31; it is never inferred from observed bars.",
    }
    write_json(destination / "evidence_manifest.json", evidence)
    return calendar, evidence


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [row for key, rows in grouped.items() if key not in isolated for row in rows]
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
        Path("src/n225m_bt/research/r037.py"),
        Path("src/n225m_bt/strategies/opening_path_efficiency.py"),
        Path("tests/test_r037_q001.py"),
    ]
    return {str(name): digest(ROOT / name) for name in names}


def preregistration(
    source: dict[str, object],
    inputs: dict[str, object],
    evidence: dict[str, object],
    files: dict[str, str],
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R037-Q001",
        "status": "frozen_before_r037_price_statistics_events_or_pnl",
        "supersedes": {
            "experiment_id": "r037-q001-20260914-opening-path-efficiency-01",
            "reason": "ΔM bootstrap incorrectly substituted zero when an observed magnitude stratum was absent; no trading, input, cost, or decision-rule change.",
        },
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development exploratory evidence, not independent confirmation.",
        "duplicate_review": {
            "R001_R036": "R033 uses a 30-minute opening range, compression against 20 days, then a 31-90 minute breakout. No registered R001-R036 rule uses opening-path efficiency |D|/V, 60 prior scheduled-day references, strict 75% rank, and immediate fixed 60-minute direction following.",
            "conclusion": "No duplicate; execute this specification only.",
        },
        "hypothesis": "A day whose first 30-minute net direction has high path efficiency relative to the prior 60 scheduled day sessions continues in that direction for the next 60 minutes after costs and exceeds unconditional following, low-efficiency following, always long, always short, and reversal. Efficiency is only a price proxy, not evidence identifying order flow, volume, participants or news.",
        "fixed_rule": {
            "universe": "Official TSE-open scheduled OSE day sessions; TSE closures are retained as zero on the common planned OSE day axis.",
            "opening": "Require the 30 contiguous scheduled day bars. p0=bar 1 open; p1..p30=each close. D=p30-p0; V=sum |pi-p(i-1)|. The opening gap is not separately included. D=0 or V=0 is no event.",
            "reference": "Use exactly the immediately preceding 60 scheduled day sessions, newest first, without target inclusion or old-session backfill. Out-of-Development, quarantine, missing or ineligible references remain invalid. With >=50 D!=0,V>0 references, Qeta is ascending ceil(.75*n)-th eta. Strict abs(D)*Qeta_V > abs(Qeta_D)*V is high; exact ties are non-high.",
            "amplitude_diagnostic": "Record 25/50/75% nearest-rank |D| thresholds from the same valid references and the target's four ex-ante magnitude bucket; never filter or select on it.",
            "conditions": {
                "A_eff": "high efficiency sign(D)",
                "B_all": "all valid sign(D)",
                "C_noneff": "non-high efficiency sign(D)",
                "D_buy": "A event/timing always long",
                "E_sell": "A event/timing always short",
                "F_reverse": "A event/timing -sign(D)",
                "A2_A3": "A at 2/3 ticks per side",
                "A_delay": "A entry signal delayed one bar, same absolute exit",
            },
            "orders": "p30 close signals; E is next scheduled eligible bar open; fixed X is E+60 minutes bar open. One contract; at most one trade/day and one position. No Stop/Target, re-entry, updates or early exit.",
            "prohibited": [
                "weekday/year/gap/range/direction/volume filters",
                "rescue exploration",
                "WFA",
                "OOS",
                "Final Holdout",
            ],
        },
        "inputs": {
            "physical": inputs,
            "r004_fixed_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; list hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
            "quality_ceiling": "PASS_LIMITED",
            "common_axis": "All scheduled Development OSE day trade_dates except day-isolated dates. TSE closure, history insufficiency, D/V zero, missing, A non-high, cancellation and no trade are recorded as zero.",
        },
        "costs": {
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "A3": {"slippage_ticks_per_side": 3, "fee_jpy_per_side": 30},
            "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees; slippage is never deducted twice.",
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
            "information_gate": "B_all>=800; A>=180 and long/short each>=60; C_noneff>=500; high/non-high x direction each>=60; every magnitude bucket high>=20 and non-high>=60.",
            "pass_requires_all_after_information": "A Net>0; PF>1; all requested CI lower bounds>0; A2/A3/A_delay expectancy>0; >=3 positive 2021-2024 years; >=27/54 positive months; top10-winner-excluded Net>0.",
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


def selectable(condition: Condition, status: str) -> bool:
    return (
        (condition == "B_all" and status in {"high_efficiency", "noneff"})
        or (condition == "C_noneff" and status == "noneff")
        or (condition in {"A_eff", "D_buy", "E_sell", "F_reverse"} and status == "high_efficiency")
    )


def side(condition: Condition, event: dict[str, object]) -> str:
    direction = cast(str, event["direction"])
    if condition in {"A_eff", "B_all", "C_noneff"}:
        return direction
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    return "short" if direction == "long" else "long"


def event_for_day(
    classifier: CalendarClassifier,
    cash: TSECashMarketCalendar,
    target: date,
    groups: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    schedule: list[date],
) -> dict[str, object]:
    index = schedule.index(target)
    history = [
        (
            schedule[offset],
            groups.get((schedule[offset], Session.DAY)),
            (schedule[offset], Session.DAY) in isolated,
        )
        for offset in range(index - 1, index - 61, -1)
        if offset >= 0
    ]
    return opening_path_efficiency_event(
        classifier,
        target,
        groups.get((target, Session.DAY)),
        history,
        target_quarantined=(target, Session.DAY) in isolated,
        target_tse_open=cash.is_open(target),
    )


def run_condition(
    data: ResearchData,
    cash: TSECashMarketCalendar,
    engine: BacktestEngine,
    condition: Condition,
    groups: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    targets: list[date],
    schedule: list[date],
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for target in targets:
        event = event_for_day(engine.classifier, cash, target, groups, isolated, schedule)
        status = cast(str, event["status"])
        event.update(
            {"condition": condition, "base_event_status": status, "pre_event_status": status}
        )
        if not selectable(condition, status):
            event.update({"status": "skipped", "reason": event.get("reason", "EVENT_FILTER")})
            audit[f"no_trade_{event['reason']}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["signal_ts_jst"]))
        result = engine.run(
            groups[(target, Session.DAY)],
            OpeningPathEfficiencyStrategy(
                f"r037_{condition}", signal, side(condition, event), delay
            ),
            canonical_hash(
                {"condition": condition, "event": event, "data": data.data_version, "delay": delay}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R037 produced more than one daily trade")
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
            for number, trade in enumerate(sorted(trades, key=lambda row: row.entry_ts), 1)
        ),
        events,
        dict(sorted(audit.items())),
    )


def daily(trades: tuple[Trade, ...], days: list[date]) -> dict[str, int]:
    values = dict.fromkeys((day.isoformat() for day in days), 0)
    for trade in trades:
        values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def summary(events: list[dict[str, object]]) -> tuple[dict[str, object], dict[str, object]]:
    groups: dict[str, object] = {}
    strata: dict[str, object] = {}
    for status in ("high_efficiency", "noneff"):
        for direction in ("long", "short"):
            rows = [
                row
                for row in events
                if row.get("base_event_status") == status and row.get("direction") == direction
            ]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            groups[f"{status}_{direction}"] = {
                "event_count": len(rows),
                "trade_count": len(filled),
                "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in filled),
                "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled),
                "net_pnl_jpy": sum(net),
                "expectancy_jpy": fmean(net) if net else None,
            }
        for bucket in range(1, 5):
            rows = [
                row
                for row in events
                if row.get("base_event_status") == status and row.get("magnitude_bucket") == bucket
            ]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            strata[f"{status}_magnitude_{bucket}"] = {
                "event_count": len(rows),
                "trade_count": len(filled),
                "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in filled),
                "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled),
                "net_pnl_jpy": sum(net),
                "expectancy_jpy": fmean(net) if net else None,
            }
    return groups, strata


def write_condition(
    folder: Path,
    name: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    audit: dict[str, int],
    data: ResearchData,
    ticks: int,
    values: dict[str, int],
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
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {"trade_date": sorted(values), "net_pnl_jpy": [values[key] for key in sorted(values)]}
    ).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def percentile(values: list[float], q: float) -> float:
    rows, point = sorted(values), (len(values) - 1) * q
    low, high = floor(point), ceil(point)
    return rows[low] if low == high else rows[low] + (rows[high] - rows[low]) * (point - low)


def filled_by_day(
    events: list[dict[str, object]], keys: list[str]
) -> tuple[dict[str, int], dict[str, int]]:
    net = {
        cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"])
        for row in events
        if row.get("status") == "filled"
    }
    return net, {key: int(key in net) for key in keys}


def bootstrap(
    values: dict[str, dict[str, int]], events: dict[str, list[dict[str, object]]]
) -> dict[str, object]:
    keys = sorted(values["A_eff"])
    a = [float(values["A_eff"][key]) for key in keys]
    series: dict[str, list[float]] = {"A_daily_mean_net_jpy": a} | {
        f"A_minus_{name}_daily_mean_net_jpy": [
            left - values[name][key] for left, key in zip(a, keys, strict=True)
        ]
        for name in ("D_buy", "E_sell", "F_reverse")
    }
    a_net, a_count = filled_by_day(events["A_eff"], keys)
    c_net, c_count = filled_by_day(events["C_noneff"], keys)
    strata: dict[tuple[str, int], tuple[dict[str, int], dict[str, int]]] = {}
    for status in ("high_efficiency", "noneff"):
        for bucket in range(1, 5):
            selected = [
                row
                for row in events["B_all"]
                if row.get("base_event_status") == status and row.get("magnitude_bucket") == bucket
            ]
            strata[(status, bucket)] = filled_by_day(selected, keys)
    magnitude_estimable = all(
        sum(strata[("high_efficiency", bucket)][1].values()) >= 20
        and sum(strata[("noneff", bucket)][1].values()) >= 60
        for bucket in range(1, 5)
    )
    labels = [
        *series,
        "high_minus_noneff_conditional_net_expectancy_jpy",
        "magnitude_adjusted_delta_M_jpy",
    ]
    samples: dict[str, list[float]] = {name: [] for name in labels}
    rng, count, block = Random(SEED), len(keys), 20

    def conditional(net: dict[str, int], counts: dict[str, int], picked: list[int]) -> float | None:
        total_count = sum(counts[keys[index]] for index in picked)
        return (
            sum(net.get(keys[index], 0) for index in picked) / total_count if total_count else None
        )

    for _ in range(10000):
        picked: list[int] = []
        while len(picked) < count:
            start = rng.randrange(count - block + 1)
            picked.extend(range(start, start + block))
        picked = picked[:count]
        for name, rows in series.items():
            samples[name].append(fmean(rows[index] for index in picked))
        high, low = conditional(a_net, a_count, picked), conditional(c_net, c_count, picked)
        samples["high_minus_noneff_conditional_net_expectancy_jpy"].append(
            high - low if high is not None and low is not None else 0.0
        )
        if magnitude_estimable:
            differences: list[float] = []
            for bucket in range(1, 5):
                high_net, high_count = strata[("high_efficiency", bucket)]
                low_net, low_count = strata[("noneff", bucket)]
                high_raw, low_raw = (
                    conditional(high_net, high_count, picked),
                    conditional(low_net, low_count, picked),
                )
                if high_raw is None or low_raw is None:
                    differences = []
                    break
                # Reconstruct 0-tick, fee-free gross from baseline Net.
                differences.append((high_raw + 1060) - (low_raw + 1060))
            if differences:
                samples["magnitude_adjusted_delta_M_jpy"].append(fmean(differences))

    def estimate_conditional(net: dict[str, int], counts: dict[str, int]) -> float:
        denominator = sum(counts.values())
        return sum(net.values()) / denominator if denominator else 0.0

    result: dict[str, object] = {
        "method": "noncircular moving blocks with replacement, tail truncate; conditional statistics recompute sums/counts each replication",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": SEED,
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
    result["high_minus_noneff_conditional_net_expectancy_jpy"] = {
        "estimate": estimate_conditional(a_net, a_count) - estimate_conditional(c_net, c_count),
        "ci95_percentile_linear": [
            percentile(samples["high_minus_noneff_conditional_net_expectancy_jpy"], 0.025),
            percentile(samples["high_minus_noneff_conditional_net_expectancy_jpy"], 0.975),
        ],
    }
    if magnitude_estimable and len(samples["magnitude_adjusted_delta_M_jpy"]) == 10000:
        deltas = []
        for bucket in range(1, 5):
            high_net, high_count = strata[("high_efficiency", bucket)]
            low_net, low_count = strata[("noneff", bucket)]
            deltas.append(
                estimate_conditional(high_net, high_count)
                - estimate_conditional(low_net, low_count)
            )
        result["magnitude_adjusted_delta_M_jpy"] = {
            "estimate": fmean(deltas),
            "ci95_percentile_linear": [
                percentile(samples["magnitude_adjusted_delta_M_jpy"], 0.025),
                percentile(samples["magnitude_adjusted_delta_M_jpy"], 0.975),
            ],
            "definition": "Each bucket's zero-tick, fee-free sign(D)-adjusted future 60-minute return high-minus-noneff, then simple average. Baseline Net is adjusted by +1,060 JPY (two 5-point fills x 100 JPY plus two 30 JPY fees).",
        }
    else:
        result["magnitude_adjusted_delta_M_jpy"] = {
            "estimate": None,
            "ci95_percentile_linear": None,
            "status": "NOT_ESTIMABLE_INFORMATION_GATE",
            "definition": "ΔM requires all four observed magnitude strata to contain high>=20 and noneff>=60; no zero is substituted for an empty stratum.",
        }
    return result


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.max_fill_delay_minutes,
        baseline.execution.allow_cross_session_pending_order,
    ) != (1, 30, 10, False):
        raise ValueError("active execution/cost contract differs from R037")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    cash, evidence = frozen_cash_calendar()
    source, files, inputs = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        implementation(),
        input_manifest(data_config.gold_root),
    )
    plan = preregistration(source, inputs, evidence, files)
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
            "status": "preregistered_before_r037_price_statistics_events_or_pnl",
            "source": source,
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
            "tests/test_r037_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r037.py",
            "src/n225m_bt/strategies/opening_path_efficiency.py",
            "tests/test_r037_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r037.py",
            "src/n225m_bt/strategies/opening_path_efficiency.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
    }
    validation: dict[str, Any] = {
        name: {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
        for name, command in commands.items()
        for done in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation["coverage"] = (
        "calendar/trade-date and regime schedule; first 30 bars/p0-p30/D/V/gap exclusion; exact preceding-60/no-backfill/valid>=50/ranks/ties/cross-product/magnitude; missing/quarantine/outside/prefix; next-open/fixed exit/delay, accounting and holdout lock."
    )
    validation["status"] = (
        "PASS"
        if all(
            cast(dict[str, object], value)["returncode"] == 0
            for key, value in validation.items()
            if key in commands
        )
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R037 validation failed before Development price access")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    schedule = [
        row.trade_date
        for row in calendar.trading_days()
        if date(2021, 1, 1) <= row.trade_date <= date(2025, 6, 30)
    ]
    targets = [day for day in schedule if (day, Session.DAY) not in isolated]
    if len(schedule) != 1131 or len(targets) != 1111:
        raise ValueError(
            f"unexpected fixed axis: scheduled={len(schedule)}, targets={len(targets)}"
        )
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": quarantine_audit,
            "fixed_target_day_trade_dates": [day.isoformat() for day in targets],
            "fixed_target_day_count": len(targets),
            "physical_io": "Development normalized Parquet only; OOS/Final Holdout never selected",
        },
    )
    groups = session_groups(view.bars)
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, dict[str, object]] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(
            view, cash, engine, condition, groups, isolated, targets, schedule
        )
        values = daily(trades, targets)
        metrics = write_condition(
            OUT / condition, condition, trades, events, audit, view, 1, values
        )
        trades_by[condition], events_by[condition], daily_by[condition], results[condition] = (
            trades,
            events,
            values,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )
    for ticks, name, delay in ((2, "A_eff_2tick", 0), (3, "A_eff_3tick", 0), (1, "A_eff_delay", 1)):
        config = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, events, audit = run_condition(
            view,
            cash,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            "A_eff",
            groups,
            isolated,
            targets,
            schedule,
            delay,
        )
        values = daily(trades, targets)
        metrics = write_condition(OUT / name, name, trades, events, audit, view, ticks, values)
        trades_by[name], events_by[name], daily_by[name], results[name] = (
            trades,
            events,
            values,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )

    def by_date(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
        return {cast(str, row["trade_date"]): row for row in rows}

    ledgers = {name: by_date(rows) for name, rows in events_by.items()}

    def equality(
        left: dict[str, object], right: dict[str, object], fields: tuple[str, ...]
    ) -> bool:
        return all(left.get(field) == right.get(field) for field in fields)

    a, b, c = ledgers["A_eff"], ledgers["B_all"], ledgers["C_noneff"]
    audit_checks = {
        "high_A_B_path_equal": all(
            equality(
                a[key],
                b[key],
                (
                    "direction",
                    "signal_ts_jst",
                    "side",
                    "entry_ts_jst",
                    "exit_ts_jst",
                    "net_pnl_jpy",
                ),
            )
            for key in a
            if a[key].get("base_event_status") == "high_efficiency"
        ),
        "noneff_B_C_path_equal": all(
            equality(
                b[key],
                c[key],
                (
                    "direction",
                    "signal_ts_jst",
                    "side",
                    "entry_ts_jst",
                    "exit_ts_jst",
                    "net_pnl_jpy",
                ),
            )
            for key in c
            if c[key].get("base_event_status") == "noneff"
        ),
        "A_F_side_exact_opposite": all(
            a[key].get("side") != ledgers["F_reverse"][key].get("side")
            for key in a
            if a[key].get("status") == "filled"
        ),
        "A_variants_event_side_equal": all(
            equality(row, a[key], ("trade_date", "direction", "signal_ts_jst", "side"))
            for name in ("A_eff_2tick", "A_eff_3tick", "A_eff_delay")
            for key, row in ledgers[name].items()
        ),
        "fixed_exit_max_one_position": all(
            trade.exit_reason.value == "signal"
            and trade.exit_ts
            == datetime.fromisoformat(
                cast(str, ledgers[name][trade.trade_date.isoformat()]["X_planned_exit_jst"])
            )
            for name, trades in trades_by.items()
            for trade in trades
        ),
        "accounting_no_double_slippage": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "checks": audit_checks,
            "all_pass": all(audit_checks.values()),
            "accounting": "Gross is slippage-inclusive; Net=Gross-fees.",
        },
    )
    if not all(audit_checks.values()):
        raise ValueError("R037 execution/accounting audit failed")
    groups_summary, strata_summary = summary(events_by["B_all"])
    boot = bootstrap(daily_by, events_by)
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "event_stratification.json", groups_summary)
    write_json(OUT / "magnitude_stratification.json", strata_summary)
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {"trade_dates": [day.isoformat() for day in targets], "no_trade": "0", "series": daily_by},
    )
    write_json(OUT / "development_results.json", results)
    a_metrics = cast(dict[str, object], results["A_eff"]["metrics"])
    a_overall = cast(dict[str, object], a_metrics["overall"])
    segments = cast(dict[str, object], a_metrics["segments"])
    side_metrics = cast(dict[str, dict[str, object]], segments["side"])
    yearly = cast(dict[str, dict[str, object]], segments["year"])
    concentration = cast(dict[str, object], a_metrics["concentration"])
    sufficient = (
        len(trades_by["B_all"]) >= 800
        and len(trades_by["A_eff"]) >= 180
        and len(trades_by["C_noneff"]) >= 500
        and all(
            cast(int, side_metrics.get(value, {}).get("trade_count", 0)) >= 60
            for value in ("long", "short")
        )
        and all(
            cast(int, row["event_count"]) >= 60
            for row in cast(dict[str, dict[str, object]], groups_summary).values()
        )
        and all(
            cast(
                int,
                cast(dict[str, object], strata_summary[f"high_efficiency_magnitude_{bucket}"])[
                    "event_count"
                ],
            )
            >= 20
            and cast(
                int,
                cast(dict[str, object], strata_summary[f"noneff_magnitude_{bucket}"])[
                    "event_count"
                ],
            )
            >= 60
            for bucket in range(1, 5)
        )
    )

    def lower(name: str) -> bool:
        interval = cast(dict[str, object], boot[name])["ci95_percentile_linear"]
        return isinstance(interval, list) and bool(interval) and cast(float, interval[0]) > 0

    gates = {
        "A_net_positive": cast(int, a_overall["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": a_overall["profit_factor"] is not None
        and cast(float, a_overall["profit_factor"]) > 1,
        "all_requested_CI_lowers_positive": all(
            lower(name)
            for name in (
                "A_daily_mean_net_jpy",
                "A_minus_D_buy_daily_mean_net_jpy",
                "A_minus_E_sell_daily_mean_net_jpy",
                "A_minus_F_reverse_daily_mean_net_jpy",
                "high_minus_noneff_conditional_net_expectancy_jpy",
                "magnitude_adjusted_delta_M_jpy",
            )
        ),
        "A2_A3_delay_expectancy_positive": all(
            cast(
                float,
                cast(
                    dict[str, object], cast(dict[str, object], results[name]["metrics"])["overall"]
                )["expectancy_jpy"],
            )
            > 0
            for name in ("A_eff_2tick", "A_eff_3tick", "A_eff_delay")
        ),
        "three_positive_years_2021_2024": sum(
            cast(int, yearly.get(str(year), {}).get("net_pnl_jpy", 0)) > 0
            for year in range(2021, 2025)
        )
        >= 3,
        "positive_months_at_least_27": cast(float, concentration["positive_month_fraction"]) >= 0.5,
        "top10_excluded_net_positive": cast(int, concentration["net_excluding_top10_jpy"]) > 0,
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
                "B_all_trades": len(trades_by["B_all"]),
                "A_trades": len(trades_by["A_eff"]),
                "C_noneff_trades": len(trades_by["C_noneff"]),
                "high_noneff_direction_groups": groups_summary,
                "magnitude_groups": strata_summary,
            },
            "fixed_gates": gates,
            "A_overall": a_overall,
            "conditional_B_all_comparison": {
                "A_eff_expectancy_jpy": a_overall["expectancy_jpy"],
                "B_all_expectancy_jpy": cast(
                    dict[str, object],
                    cast(dict[str, object], results["B_all"]["metrics"])["overall"],
                )["expectancy_jpy"],
                "note": "B_all contains A high-efficiency events; A vs B_all is reported, while the preregistered conditional discriminator is high vs noneff.",
            },
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
