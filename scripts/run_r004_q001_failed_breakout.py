"""Run the preregistered R004-Q001 Development-only failed-break test."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from math import floor
from pathlib import Path
from random import Random
from statistics import fmean
from typing import Any

import polars as pl

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.failed_breakout import Condition, FailedBreakoutReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r004-q001-20260913-failed-breakout-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
R003_OUT = ROOT / "results" / "research" / "r003-q001-20260913-development-campaign-01"
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE = {
    "quarantined_sessions": 45,
    "quarantined_bars": 27345,
    "included_bars": 1326086,
    "included_sessions": 2216,
    "quarantined_sessions_by_type": {"day": 20, "night": 25},
}


def session_groups(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    grouped: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.trade_date, bar.session)].append(bar)
    return {key: sorted(value, key=lambda item: item.ts_jst) for key, value in grouped.items()}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, Any]]:
    grouped = session_groups(data.bars)
    bad = {
        key
        for key, bars in grouped.items()
        if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in bars)
    }
    included = [bar for key, bars in grouped.items() if key not in bad for bar in bars]
    if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included):
        raise ValueError("quarantine left a tick-grid violation")
    sessions = [
        {"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(bad)
    ]
    audit: dict[str, Any] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(bad),
        "quarantined_sessions_by_type": dict(
            sorted(Counter(session.value for _, session in bad).items())
        ),
        "included_bars": len(included),
        "included_sessions": len(grouped) - len(bad),
        "included_tick_grid_violations": 0,
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": sessions,
        "quarantined_session_list_hash": canonical_hash(sessions),
        "legacy_r003_q001_session_list": "NOT_PERSISTED_IN_LEGACY_ARTIFACT; reproduced directly from identical parent view and frozen rule",
    }
    expected = EXPECTED_QUARANTINE | {"parent_data_version": EXPECTED_PARENT_VERSION}
    mismatches = {
        key: {"actual": audit.get(key), "expected": value}
        for key, value in expected.items()
        if audit.get(key) != value
    }
    audit["expected_match"] = not mismatches
    audit["mismatches"] = mismatches
    if mismatches:
        raise ValueError(f"R003-Q001 quarantine reproduction mismatch: {mismatches}")
    view_version = canonical_hash(
        {"parent": data.data_version, "rule": audit["rule"], "sessions": sessions}
    )
    return ResearchData(included, view_version, data.quality | {"quarantine": audit}), audit


def _ledger_values(folder: Path) -> dict[str, Any]:
    frame = pl.read_parquet(folder / "trades.parquet")
    net = frame.get_column("net_pnl_jpy").to_list()
    gross = frame.get_column("gross_pnl_jpy").to_list()
    fees = frame.get_column("fees_jpy").to_list()
    slippage = frame.get_column("slippage_cost_jpy").to_list()
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    return {
        "trade_count": len(net),
        "gross_pnl_jpy": sum(gross),
        "fees_jpy": sum(fees),
        "slippage_cost_jpy": sum(slippage),
        "net_pnl_jpy": sum(net),
        "expectancy_jpy": fmean(net) if net else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "reference_price_pnl_inferred_jpy": sum(gross) + sum(slippage),
        "net_formula_holds": sum(net) == sum(gross) - sum(fees),
    }


def audit_r003_q001() -> dict[str, Any]:
    results = json.loads((R003_OUT / "development_results.json").read_text(encoding="utf-8"))
    checked: list[dict[str, Any]] = []
    for run in results["runs"]:
        ledger = _ledger_values(R003_OUT / run["experiment_id"])
        expected = run["overall"]
        keys = (
            "trade_count",
            "gross_pnl_jpy",
            "fees_jpy",
            "slippage_cost_jpy",
            "net_pnl_jpy",
            "expectancy_jpy",
            "profit_factor",
        )
        mismatches = {
            key: {"ledger": ledger[key], "existing": expected[key]}
            for key in keys
            if ledger[key] != expected[key]
        }
        if not ledger["net_formula_holds"]:
            mismatches["net_formula"] = ledger
        checked.append(
            {
                "experiment_id": run["experiment_id"],
                "ledger_accounting": ledger,
                "existing_overall": {key: expected[key] for key in keys},
                "matches": not mismatches,
                "mismatches": mismatches,
            }
        )
    mismatched = [item["experiment_id"] for item in checked if not item["matches"]]
    report = {
        "campaign_id": results["campaign_id"],
        "read_only": True,
        "conditions_checked": len(checked),
        "all_match": not mismatched,
        "mismatched_conditions": mismatched,
        "runs": checked,
        "accounting_note": "Gross PnL already uses slippage-inclusive fills. The inferred reference-price PnL adds the separately reported slippage attribution only for decomposition and is never used to subtract slippage a second time.",
    }
    if mismatched:
        raise ValueError(f"R003-Q001 accounting mismatch: {mismatched}")
    return report


def run_condition(
    data: ResearchData,
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    condition: Condition,
) -> tuple[tuple[Trade, ...], list[dict[str, Any]], dict[str, int]]:
    events: list[dict[str, Any]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for (trade_day, session), bars in sorted(
        session_groups(data.bars).items(), key=lambda item: item[1][0].ts_jst
    ):
        session_open = classifier.session_open(trade_day, session)
        session_close = classifier.session_close(trade_day, session)
        strategy = FailedBreakoutReversalStrategy(
            strategy_id=f"r004_q001_{condition}",
            session_open=session_open,
            entry_cutoff=session_close - timedelta(minutes=15),
            condition=condition,
        )
        result = engine.run(
            bars,
            strategy,
            canonical_hash({"condition": condition, "data_version": data.data_version}),
        )
        if len(result.trades) > 1:
            raise AssertionError("R004 session produced more than one trade")
        event = strategy.finalize(bars)
        event.update({"trade_date": trade_day.isoformat(), "session": session.value})
        audit["target_sessions"] += 1
        audit[f"event_{event.get('reason', event.get('status', 'unknown'))}"] += 1
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update(
                {
                    "status": "filled",
                    "entry_ts_jst": trade.entry_ts.isoformat(),
                    "exit_ts_jst": trade.exit_ts.isoformat(),
                    "exit_reason": trade.exit_reason.value,
                    "net_pnl_jpy": trade.net_pnl_jpy,
                }
            )
            audit["orders"] += 1
            audit["fills"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
        elif event.get("status") == "order_issued":
            event.update({"status": "no_fill", "reason": "PENDING_ORDER_NOT_FILLED"})
        events.append(event)
        trades.extend(result.trades)
    trades.sort(key=lambda trade: trade.entry_ts)
    stable = tuple(
        replace(trade, trade_id=f"trade-{index:06d}") for index, trade in enumerate(trades, 1)
    )
    return stable, events, dict(sorted(audit.items()))


def daily_pair(
    metrics_a: dict[str, Any], metrics_b: dict[str, Any]
) -> tuple[list[str], list[int], list[int]]:
    daily_a = metrics_a["daily_net_pnl_jpy"]
    daily_b = metrics_b["daily_net_pnl_jpy"]
    if set(daily_a) != set(daily_b):
        raise ValueError("A/B daily trade-date universes differ")
    dates = sorted(daily_a)
    return dates, [daily_a[day] for day in dates], [daily_b[day] for day in dates]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[floor((len(ordered) - 1) * q)]


def paired_block_bootstrap(values_a: list[int], values_b: list[int]) -> dict[str, Any]:
    count = len(values_a)
    if count != len(values_b) or count == 0:
        raise ValueError("invalid paired daily series")
    block = min(20, count)
    starts = list(range(count - block + 1))
    random = Random(20260913)
    means_a: list[float] = []
    means_diff: list[float] = []
    for _ in range(10_000):
        selected: list[int] = []
        while len(selected) < count:
            start = starts[random.randrange(len(starts))]
            selected.extend(range(start, start + block))
        selected = selected[:count]
        sample_a = [values_a[index] for index in selected]
        sample_b = [values_b[index] for index in selected]
        means_a.append(fmean(sample_a))
        means_diff.append(fmean(a - b for a, b in zip(sample_a, sample_b, strict=True)))
    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": 20260913,
        "same_resampling_indices_for_A_B": True,
        "A_daily_mean_net_jpy": {
            "estimate": fmean(values_a),
            "ci95_percentile": [percentile(means_a, 0.025), percentile(means_a, 0.975)],
        },
        "A_minus_B_daily_mean_net_jpy": {
            "estimate": fmean(a - b for a, b in zip(values_a, values_b, strict=True)),
            "ci95_percentile": [percentile(means_diff, 0.025), percentile(means_diff, 0.975)],
        },
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r004_q001_failed_breakout.py')

    if not (OUT / "preregistration.json").exists() or not (OUT / "campaign_plan.json").exists():
        raise ValueError("missing R004 frozen preregistration or campaign plan")
    if (OUT / "COMPLETED.json").exists():
        raise ValueError("R004 output exists; never overwrite an experiment")
    plan = json.loads((OUT / "campaign_plan.json").read_text(encoding="utf-8"))
    if plan["status"] != "frozen_before_pnl" or plan["conditions"] != [
        "A_return_confirmation",
        "B_immediate_control",
    ]:
        raise ValueError("R004 plan is not the frozen two-condition plan")
    accounting = audit_r003_q001()
    write_json(OUT / "r003_q001_accounting_audit.json", accounting)
    instrument, sessions, data_config, backtest = load_project_config(ROOT / "config")
    if backtest.execution.slippage_ticks != 1 or backtest.fees.jpy_per_side_per_contract != 30:
        raise ValueError("existing cost contract differs from preregistration")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "plan_hash": canonical_hash(plan),
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit = quarantine(development)
    preflight = {
        "status": "PASS_LIMITED",
        "development_input": development.quality,
        "quarantine": quarantine_audit,
        "physical_io": "Polars scan_parquet may read Parquet metadata/row groups in selected partition files; logical rows are trade_date-filtered before collect.",
        "logical_price_access": "only trade_date 2021-01-01 through 2025-06-30 from selected Development partition paths; OOS and Final Holdout rows rejected by load_split boundary",
        "data_quality_limit": "continuous series and unresolved contract/roll/adjustment provenance; not executable-price certification",
    }
    write_json(OUT / "preflight.json", preflight)
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), backtest, classifier)
    results: dict[str, Any] = {}
    conditions: tuple[Condition, Condition] = ("A_return_confirmation", "B_immediate_control")
    for condition in conditions:
        folder = reserve_directory(OUT, condition)
        trades, events, execution_audit = run_condition(view, classifier, engine, condition)
        metrics = research_metrics(trades, view.bars)
        write_results(
            folder,
            trades,
            (),
            {
                "experiment_id": folder.name,
                "campaign_id": IDENTIFIER,
                "condition": condition,
                "data_version": view.data_version,
                "costs": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
                "status": "complete",
                "oos": "NOT_EVALUATED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
        write_json(folder / "metrics_research.json", metrics)
        write_json(folder / "execution_audit.json", execution_audit)
        pl.DataFrame(events).write_parquet(folder / "events.parquet")
        daily = metrics["daily_net_pnl_jpy"]
        if not isinstance(daily, dict):
            raise ValueError("research metrics daily Net must be a mapping")
        pl.DataFrame(
            {"trade_date": list(daily), "net_pnl_jpy": list(daily.values())}
        ).write_parquet(folder / "daily_net_pnl.parquet")
        results[condition] = {
            "metrics": metrics,
            "execution_audit": execution_audit,
            "trade_count": len(trades),
        }
    dates, daily_a, daily_b = daily_pair(
        results["A_return_confirmation"]["metrics"], results["B_immediate_control"]["metrics"]
    )
    bootstrap = paired_block_bootstrap(daily_a, daily_b)
    sensitivity = {
        "per_completed_A_round_trip_additional_jpy": 1000,
        "A_trade_count": results["A_return_confirmation"]["trade_count"],
        "A_net_before_jpy": results["A_return_confirmation"]["metrics"]["overall"]["net_pnl_jpy"],
        "A_net_after_jpy": results["A_return_confirmation"]["metrics"]["overall"]["net_pnl_jpy"]
        - 1000 * results["A_return_confirmation"]["trade_count"],
        "interpretation": "cost attribution sensitivity only; orders/fills are unchanged and this is not a re-execution stress",
    }
    a_overall = results["A_return_confirmation"]["metrics"]["overall"]
    a_audit = results["A_return_confirmation"]["execution_audit"]
    lower_a = bootstrap["A_daily_mean_net_jpy"]["ci95_percentile"][0]
    lower_diff = bootstrap["A_minus_B_daily_mean_net_jpy"]["ci95_percentile"][0]
    checks = {
        "minimum_A_trades_200": results["A_return_confirmation"]["trade_count"] >= 200,
        "A_net_positive": a_overall["net_pnl_jpy"] > 0,
        "A_net_profit_factor_above_1": a_overall["profit_factor"] is not None
        and a_overall["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": lower_a > 0,
        "A_minus_B_bootstrap_lower_above_0": lower_diff > 0,
        "A_sensitivity_net_positive": sensitivity["A_net_after_jpy"] > 0,
        "no_end_of_data_exits": a_audit.get("exit_end_of_data", 0) == 0,
    }
    if not checks["minimum_A_trades_200"]:
        decision = "INCONCLUSIVE"
    elif all(checks.values()):
        decision = "CONTINUE_DEV_ONLY"
    else:
        decision = "REJECT"
    write_json(
        OUT / "daily_net_pnl_aligned.json", {"trade_dates": dates, "A": daily_a, "B": daily_b}
    )
    write_json(OUT / "interval_estimates.json", bootstrap)
    write_json(OUT / "cost_sensitivity.json", sensitivity)
    write_json(
        OUT / "development_results.json",
        {
            "campaign_id": IDENTIFIER,
            "quality_status": "PASS_LIMITED",
            "decision": decision,
            "checks": checks,
            "conditions": results,
            "sensitivity": sensitivity,
            "bootstrap": bootstrap,
            "scope": "Development only; no WFA/OOS/Final Holdout",
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
