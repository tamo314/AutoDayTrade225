"""Revalidate frozen R004-Q001 under the corrected post-cutoff EXIT callback."""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import polars as pl
from run_r004_q001_failed_breakout import (
    EXPECTED_PARENT_VERSION,
    EXPECTED_QUARANTINE,
    paired_block_bootstrap,
    quarantine,
    run_condition,
)

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import load_split
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r004-q001-20260913-corrected-exit-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
OLD = ROOT / "results" / "research" / "r004-q001-20260913-failed-breakout-01"
EXIT_DIAGNOSTIC = ROOT / "results" / "research" / "r005-q001-20260913-exit-path-diagnostic-01"
R005_IMPACT = (
    ROOT
    / "results"
    / "research"
    / "r005-q001-20260913-corrected-exit-02"
    / "r001_r004_exit_regression_impact.json"
)
CONDITIONS = ("A_return_confirmation", "B_immediate_control")


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def preregistration() -> dict[str, Any]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R004-Q001",
        "created_at_jst": "2026-09-13",
        "status": "frozen_before_corrected_r004_events_prices_or_pnl",
        "execution_revision": "same fixed R004 A/B hypothesis rerun after the normative held-position EXIT callback correction; this is not a new hypothesis, independent replication, or unused Development sample",
        "prior_knowledge": {
            "old_experiment": OLD.name,
            "old_decision": "REJECT",
            "development_already_seen": True,
            "exit_diagnostic": EXIT_DIAGNOSTIC.name,
            "r005_synthetic_impact": str(R005_IMPACT.relative_to(ROOT)),
            "why_real_data_rerun_is_needed": "The synthetic R004 case has constant prices and proves only that a suppressed timed EXIT can become a signal EXIT. Real R004 trades can have a 60-minute EXIT after the new-entry cutoff with different observed prices, gaps, protective exits, or force-flat timing; therefore synthetic invariance cannot establish the aggregate historical effect.",
            "possible_effect_condition": "A filled R004 position survives protective exits until its 60-minute timed EXIT is due at or after the new-entry cutoff. Legacy code suppresses the held-position callback and can force-flat later; corrected code permits only that EXIT callback, leaving new entries prohibited.",
        },
        "hypothesis": "After the first close-confirmed break of a frozen 30-minute opening range by one tick, A enters opposite the break only after the first close back inside the range within five clock minutes and has positive post-cost expectancy and higher Net per common eligible trade_date than B, which enters opposite the same first break at the next eligible open without waiting for return. A/B differs as a complete rule, including confirmation wait, entry time, and Stop; it is not a causal estimate of confirmation alone.",
        "fixed_rules": {
            "scope": "Development trade_date 2021-01-01 through 2025-06-30 only; day and night; one contract; at most one position and one trade per condition/session; no re-entry, addition, filters, WFA, OOS, Final Holdout, parameter search, time-width change, or TP/SL change.",
            "timestamp_and_fill": "JST bar-start timestamps; a close decision is known after that minute closes and fills at the earliest next eligible observed same-session bar open. Pending orders are not cancelled after observing an entry gap. Missing next bars use the existing next-eligible rule and cancel only when delay exceeds 10 minutes.",
            "schedule_versions": {
                "through_2021_09_20": "day 08:45-15:15; night 16:30-05:30 next calendar day",
                "2021_09_21_to_2024_11_04": "day 08:45-15:15; night 16:30-06:00 next calendar day",
                "from_2024_11_05": "day 08:45-15:45; night 17:00-06:00 next calendar day",
            },
            "opening_and_event": "Offsets 0..29 form U=max(high), L=min(low), M=(U+L)/2 after offset 29 close. First only close at offsets 30..119 with close >= U+5 or close <= L-5 is the break. event_id is trade_date/session/b timestamp/reversal side.",
            "A_return_confirmation": "For b+1..b+5 at exact consecutive real-minute timestamps, first L <= close <= U is r; submit after r close for next eligible open. Sell Stop=max(high[b:r])+5; buy Stop=min(low[b:r])-5. Sell TP=ceil(M/5)*5; buy TP=floor(M/5)*5. The current r close must lie strictly target < close < Stop (sell) or Stop < close < target (buy). Missing, invalid sequence, absent return, price-order failure, or cutoff is reason-coded and has no order.",
            "B_immediate_control": "After b close, submit opposite-break order for next eligible open without waiting for return and without conditioning later analysis on a return. Sell Stop=high_b+5; buy Stop=low_b-5. TP and strict price-order eligibility are the same tick-grid rules as A.",
            "exit_and_execution": "From actual entry fill t, a 60-minute EXIT is issued at close(t+59) and fills at next open t+60, unless the existing forced-flat five minutes before session close occurs earlier. Corrected code allows this EXIT callback for a held position after the 15-minute new-entry cutoff; it does not permit a new entry. Pending EXIT is applied before force-flat at the same bar. Conservative same-bar TP/SL chooses Stop; stop gap fills adversely at bar open. Entry-bar protective breach is resolved once with one round-trip fee.",
            "costs": "tick_size=5 points; multiplier=100 JPY/point; quantity=1; adverse slippage=1 tick each side; fee=30 JPY each side. Gross uses slippage-inclusive fills; Net=Gross-fees; reported slippage attribution is never deducted again.",
        },
        "data_policy": {
            "physical_io": "only selected Development Parquet partitions (prior December night allowance plus 2021-01 through 2025-06); no OOS or Final Holdout partitions",
            "logical_price_access": "trade_date filter 2021-01-01..2025-06-30 is applied by load_split before collection; no all-period preload",
            "quarantine": "reproduce R004 frozen whole-session rule: exclude exactly a (trade_date, session) containing any TICK_GRID_VIOLATION before all events, signals, orders, fills, or PnL. Expected: 45 sessions / 27,345 bars removed; 2,216 sessions / 1,326,086 bars retained; day=20, night=25.",
            "prohibitions": "No raw/Gold modification, rounding repair, interpolation, new exclusion, inferred real contract/roll/adjustment, or post-hoc session conditioning. Quality is PASS_LIMITED at most; legacy R003-Q001 did not preserve its session list.",
        },
        "evaluation": {
            "outputs": "event/order/fill/trade ledgers; stage and reason counts; Gross/fees/slippage attribution/Net; expectancy, Net PF, maximum realized DD; year/day-night/direction segments (2025 first half); aligned daily Net with included no-trade dates zero and quarantined sessions excluded.",
            "bootstrap": "moving-block bootstrap with replacement, no wrap; 20 included trade_date blocks, truncate final block, 10,000 repetitions, seed=20260913, common A/B indices, percentile sorted[floor((n-1)*q)] at q=.025/.975; estimates A daily mean Net and A-B daily mean Net.",
            "sensitivity": "A only: subtract 1,000 JPY per completed round trip without changing orders/fills; cost attribution sensitivity, not re-execution stress.",
            "decision_rules": "BLOCKED on view/implementation/accounting/unexplained-path mismatch; INCONCLUSIVE if A<200 trades; REJECT if A>=200 and any required condition fails; CONTINUE_DEV_ONLY only if all pass. Required: A>=200, A Net>0, A PF>1, lower 95% A daily mean>0, lower 95% A-B>0, A sensitivity Net>0, no end_of_data or unexpected force-flat exits. B alone cannot pass the study.",
        },
        "identifiers_before_run": {
            "old_preregistration_sha256": file_hash(OLD / "preregistration.json"),
            "old_preflight_sha256": file_hash(OLD / "preflight.json"),
            "exit_pre_fix_sha256": file_hash(EXIT_DIAGNOSTIC / "pre_fix_results.json"),
            "exit_post_fix_sha256": file_hash(EXIT_DIAGNOSTIC / "post_fix_results.json"),
            "r005_synthetic_impact_sha256": file_hash(R005_IMPACT),
            "implementation_sha256": {
                "runner": file_hash(Path(__file__)),
                "original_r004_runner": file_hash(ROOT / "scripts" / "run_r004_q001_failed_breakout.py"),
                "engine": file_hash(ROOT / "src" / "n225m_bt" / "backtest" / "engine.py"),
                "failed_breakout": file_hash(ROOT / "src" / "n225m_bt" / "strategies" / "failed_breakout.py"),
            },
            "expected_parent_data_version": EXPECTED_PARENT_VERSION,
            "expected_quarantine": EXPECTED_QUARANTINE,
        },
    }


def validation_gate() -> dict[str, Any]:
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r004_q001.py", "tests/test_exit_after_entry_cutoff.py", "tests/test_execution.py", "tests/test_r005_q001.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts/run_r004_q001_corrected_exit.py"],
        "mypy": [sys.executable, "-m", "mypy", "src"],
    }
    outcomes: dict[str, Any] = {}
    for name, command in commands.items():
        run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        outcomes[name] = {"command": command, "returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
    return {"status": "PASS" if all(x["returncode"] == 0 for x in outcomes.values()) else "BLOCKED", "checks": outcomes}


def event_rows(condition: str, frame: pl.DataFrame) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {(condition, str(row["trade_date"]), str(row["session"])): row for row in frame.to_dicts()}


def stage_counts(frame: pl.DataFrame) -> dict[str, Any]:
    rows = frame.to_dicts()
    reasons = Counter(str(row["reason"]) for row in rows if row.get("reason") is not None)
    exits = Counter(str(row["exit_reason"]) for row in rows if row.get("exit_reason") is not None)
    return {
        "target_sessions": len(rows),
        "first_breaks": sum(row.get("event_id") is not None for row in rows),
        "returns": sum(row.get("return_ts_jst") is not None for row in rows),
        "orders": sum(row.get("order_signal_ts_jst") is not None for row in rows),
        "fills": sum(row.get("status") == "filled" for row in rows),
        "no_order_reasons": dict(sorted(reasons.items())),
        "exit_reasons": dict(sorted(exits.items())),
    }


def compare_events(old: dict[str, pl.DataFrame], new: dict[str, pl.DataFrame]) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    changed = 0
    unexpected: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        left, right = event_rows(condition, old[condition]), event_rows(condition, new[condition])
        for key in sorted(set(left) | set(right)):
            before, after = left.get(key), right.get(key)
            fields = sorted(set(before or {}) | set(after or {}))
            diffs = [field for field in fields if (before or {}).get(field) != (after or {}).get(field)]
            row = {"condition": condition, "trade_date": key[1], "session": key[2], "old_present": before is not None, "new_present": after is not None, "changed_fields": diffs}
            for field in fields:
                row[f"old_{field}"] = (before or {}).get(field)
                row[f"new_{field}"] = (after or {}).get(field)
            output.append(row)
            changed += bool(diffs)
            if diffs:
                unexpected.append({"key": key, "changed_fields": diffs})
    pl.DataFrame(output).write_parquet(OUT / "old_new_event_impact.parquet")
    # Signals and entry setup must be identical; exit annotations are added only after execution.
    setup_fields = {"event_id", "break_ts_jst", "return_ts_jst", "order_signal_ts_jst", "target_price", "stop_price", "status", "reason"}
    setup_changes = [item for item in unexpected if set(item["changed_fields"]) & setup_fields]
    return {"old": {c: stage_counts(old[c]) for c in CONDITIONS}, "new": {c: stage_counts(new[c]) for c in CONDITIONS}, "rows": len(output), "changed_rows": changed, "setup_changes": setup_changes}


def trade_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["trade_date"]), str(row["entry_signal_ts"]), str(row["side"])


def comparable_ledger_rows(
    condition: str, old: pl.DataFrame, new: pl.DataFrame, kind: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    old_rows, new_rows = old.to_dicts(), new.to_dicts()
    old_map = {trade_key(row): row for row in old_rows}
    new_map = {trade_key(row): row for row in new_rows}
    output: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    fields = (
        "entry_signal_ts", "entry_ts", "entry_reference_price", "entry_fill_price", "exit_signal_ts", "exit_ts", "exit_reference_price", "exit_fill_price", "exit_reason", "gross_pnl_jpy", "slippage_cost_jpy", "fees_jpy", "net_pnl_jpy"
    )
    for key in sorted(set(old_map) | set(new_map)):
        before, after = old_map.get(key), new_map.get(key)
        diffs = [field for field in fields if (before or {}).get(field) != (after or {}).get(field)]
        row: dict[str, Any] = {"condition": condition, "trade_date": key[0], "entry_signal_key": key[1], "side": key[2], "old_present": before is not None, "new_present": after is not None, "changed_fields": diffs}
        for field in fields:
            row[f"old_{field}"] = (before or {}).get(field)
            row[f"new_{field}"] = (after or {}).get(field)
        allowed = (not diffs) or (
            before is not None
            and after is not None
            and before["entry_signal_ts"] == after["entry_signal_ts"]
            and before["entry_ts"] == after["entry_ts"]
            and before["entry_reference_price"] == after["entry_reference_price"]
            and before["entry_fill_price"] == after["entry_fill_price"]
            and before["fees_jpy"] == after["fees_jpy"]
            and before["slippage_cost_jpy"] == after["slippage_cost_jpy"]
            and before["exit_reason"] == "force_flat"
            and after["exit_reason"] == "signal"
            and set(diffs).issubset({"exit_signal_ts", "exit_ts", "exit_reference_price", "exit_fill_price", "exit_reason", "gross_pnl_jpy", "net_pnl_jpy"})
        )
        row["change_classification"] = "unchanged" if not diffs else ("corrected_timed_exit" if allowed else "unexplained")
        output.append(row)
        if not allowed:
            anomalies.append(row)
    return output, anomalies


def order_fill_impact(condition: str, old_folder: Path, new_folder: Path, file_name: str) -> dict[str, Any]:
    old_trades = pl.read_parquet(old_folder / "trades.parquet").to_dicts()
    new_trades = pl.read_parquet(new_folder / "trades.parquet").to_dicts()
    old_ids = {row["trade_id"]: trade_key(row) for row in old_trades}
    new_ids = {row["trade_id"]: trade_key(row) for row in new_trades}
    old_rows = {(*old_ids[row["trade_id"]], str(row.get("order_kind", row.get("fill_kind")))): row for row in pl.read_parquet(old_folder / file_name).to_dicts()}
    new_rows = {(*new_ids[row["trade_id"]], str(row.get("order_kind", row.get("fill_kind")))): row for row in pl.read_parquet(new_folder / file_name).to_dicts()}
    rows: list[dict[str, Any]] = []
    for key in sorted(set(old_rows) | set(new_rows)):
        before, after = old_rows.get(key), new_rows.get(key)
        fields = sorted(set(before or {}) | set(after or {}) - {"trade_id"})
        diffs = [field for field in fields if field != "trade_id" and (before or {}).get(field) != (after or {}).get(field)]
        rows.append({"condition": condition, "trade_date": key[0], "entry_signal_key": key[1], "side": key[2], "kind": key[3], "old_present": before is not None, "new_present": after is not None, "changed_fields": diffs, **{f"old_{field}": (before or {}).get(field) for field in fields if field != "trade_id"}, **{f"new_{field}": (after or {}).get(field) for field in fields if field != "trade_id"}})
    return {"rows": rows, "changed_rows": sum(bool(row["changed_fields"]) for row in rows), "unmatched_rows": sum(not row["old_present"] or not row["new_present"] for row in rows)}


def main() -> None:
    if OUT.exists():
        raise ValueError(f"output exists and must never be overwritten: {OUT}")
    required = [OLD / "preregistration.json", OLD / "preflight.json", EXIT_DIAGNOSTIC / "post_fix_results.json", R005_IMPACT]
    if any(not path.exists() for path in required):
        raise ValueError("required immutable R004/EXIT evidence is missing")
    reserve_directory(OUT.parent, IDENTIFIER)
    plan = preregistration()
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "campaign_plan.json", {"campaign_id": IDENTIFIER, "status": "frozen_before_pnl", "conditions": list(CONDITIONS), "scope": "corrected EXIT Development-only R004 rerun", "prohibitions": ["no R001-R003 rerun", "no R005 rerun", "no parameter search", "no OOS", "no Final Holdout"]})
    instrument, sessions, data_config, backtest = load_project_config(ROOT / "config")
    effective = {"instrument": instrument.model_dump(mode="json"), "backtest": backtest.model_dump(mode="json"), "fixed_r004": plan["fixed_rules"], "max_fill_delay_minutes": backtest.execution.max_fill_delay_minutes}
    write_json(OUT / "effective_config.json", effective)
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_corrected_r004_price_work", "source": source, "preregistration_sha256": file_hash(OUT / "preregistration.json"), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    gate = validation_gate()
    write_json(OUT / "validation_gate.json", gate)
    if gate["status"] != "PASS":
        write_json(OUT / "BLOCKED.json", {"campaign_id": IDENTIFIER, "status": "BLOCKED", "reason": "validation_gate_failed", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        raise RuntimeError("validation gate failed before Development prices were loaded")
    if backtest.execution.slippage_ticks != 1 or backtest.fees.jpy_per_side_per_contract != 30 or backtest.execution.max_fill_delay_minutes != 10:
        raise ValueError("active cost or maximum-delay contract differs from frozen R004 specification")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit = quarantine(development)
    preflight = {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "physical_io": plan["data_policy"]["physical_io"], "logical_price_access": plan["data_policy"]["logical_price_access"], "data_quality_limit": "continuous series with unresolved contract, roll, and adjustment provenance; post-hoc whole-session quarantine; not executable constituent-contract certification"}
    write_json(OUT / "preflight.json", preflight)
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    engine = BacktestEngine(instrument.instrument.to_spec(), backtest, classifier)
    results: dict[str, Any] = {}
    old_events: dict[str, pl.DataFrame] = {}
    new_events: dict[str, pl.DataFrame] = {}
    trade_anomalies: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        folder = reserve_directory(OUT, condition)
        trades, events, audit = run_condition(view, classifier, engine, condition)
        metrics = research_metrics(trades, view.bars)
        write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "data_version": view.data_version, "costs": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "status": "complete", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        write_json(folder / "metrics_research.json", metrics)
        write_json(folder / "execution_audit.json", audit)
        pl.DataFrame(events).write_parquet(folder / "events.parquet")
        daily = metrics["daily_net_pnl_jpy"]
        if not isinstance(daily, dict):
            raise ValueError("daily Net output is not a mapping")
        pl.DataFrame({"trade_date": list(daily), "net_pnl_jpy": list(daily.values())}).write_parquet(folder / "daily_net_pnl.parquet")
        old_events[condition] = pl.read_parquet(OLD / condition / "events.parquet")
        new_events[condition] = pl.read_parquet(folder / "events.parquet")
        impact_rows, anomalies = comparable_ledger_rows(condition, pl.read_parquet(OLD / condition / "trades.parquet"), pl.read_parquet(folder / "trades.parquet"), "trades")
        pl.DataFrame(impact_rows).write_parquet(folder / "old_new_trade_impact.parquet")
        trade_anomalies.extend(anomalies)
        for ledger in ("orders.parquet", "fills.parquet"):
            impact = order_fill_impact(condition, OLD / condition, folder, ledger)
            pl.DataFrame(impact["rows"]).write_parquet(folder / f"old_new_{ledger}")
            write_json(folder / f"old_new_{ledger}.json", {key: value for key, value in impact.items() if key != "rows"})
        results[condition] = {"metrics": metrics, "execution_audit": audit, "trade_count": len(trades)}
    event_impact = compare_events(old_events, new_events)
    trade_impact_files = {condition: str((OUT / condition / "old_new_trade_impact.parquet").relative_to(OUT)) for condition in CONDITIONS}
    write_json(OUT / "old_new_impact_summary.json", {"event_impact": event_impact, "trade_impact_files": trade_impact_files, "unexplained_trade_paths": trade_anomalies})
    if event_impact["setup_changes"] or trade_anomalies:
        write_json(OUT / "BLOCKED.json", {"campaign_id": IDENTIFIER, "status": "BLOCKED", "reason": "unexplained_old_new_path_difference", "event_setup_changes": event_impact["setup_changes"], "trade_anomalies": trade_anomalies, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        raise RuntimeError("unexplained corrected-EXIT path difference")
    daily_a = results[CONDITIONS[0]]["metrics"]["daily_net_pnl_jpy"]
    daily_b = results[CONDITIONS[1]]["metrics"]["daily_net_pnl_jpy"]
    if not isinstance(daily_a, dict) or not isinstance(daily_b, dict) or set(daily_a) != set(daily_b):
        raise ValueError("A/B common daily trade-date universe mismatch")
    dates = sorted(daily_a)
    values_a, values_b = [daily_a[day] for day in dates], [daily_b[day] for day in dates]
    bootstrap = paired_block_bootstrap(values_a, values_b)
    sensitivity = {"per_completed_A_round_trip_additional_jpy": 1000, "A_trade_count": results[CONDITIONS[0]]["trade_count"], "A_net_before_jpy": results[CONDITIONS[0]]["metrics"]["overall"]["net_pnl_jpy"], "A_net_after_jpy": results[CONDITIONS[0]]["metrics"]["overall"]["net_pnl_jpy"] - 1000 * results[CONDITIONS[0]]["trade_count"], "interpretation": "cost attribution only; no orders or fills were changed"}
    a_overall = results[CONDITIONS[0]]["metrics"]["overall"]
    a_audit = results[CONDITIONS[0]]["execution_audit"]
    checks = {"minimum_A_trades_200": results[CONDITIONS[0]]["trade_count"] >= 200, "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_net_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": bootstrap["A_daily_mean_net_jpy"]["ci95_percentile"][0] > 0, "A_minus_B_bootstrap_lower_above_0": bootstrap["A_minus_B_daily_mean_net_jpy"]["ci95_percentile"][0] > 0, "A_sensitivity_net_positive": sensitivity["A_net_after_jpy"] > 0, "no_end_of_data_exits": a_audit.get("exit_end_of_data", 0) == 0, "no_unexpected_force_flat_exits": a_audit.get("exit_force_flat", 0) == 0}
    decision = "INCONCLUSIVE" if not checks["minimum_A_trades_200"] else ("CONTINUE_DEV_ONLY" if all(checks.values()) else "REJECT")
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": dates, "A": values_a, "B": values_b, "included_no_trade_dates_are_zero": True, "quarantined_sessions_are_excluded": True})
    write_json(OUT / "interval_estimates.json", bootstrap)
    write_json(OUT / "cost_sensitivity.json", sensitivity)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "checks": checks, "conditions": results, "sensitivity": sensitivity, "bootstrap": bootstrap, "scope": "Development only; no WFA/OOS/Final Holdout"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
