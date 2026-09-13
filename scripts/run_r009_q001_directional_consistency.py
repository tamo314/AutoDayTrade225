"""Execute the preregistered Development-only R009-Q001 experiment."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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
from n225m_bt.domain import Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r009 import directional_consistency_event, session_groups
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.session_directional_consistency import (
    Condition,
    SessionDirectionalConsistencyStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r009-q001-20260913-directional-consistency-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
R004_OUT = ROOT / "results" / "research" / "r004-q001-20260913-failed-breakout-01"
BasicCondition = Literal["A_directional_consistency", "B_always_long", "C_always_short"]
CONDITIONS: tuple[BasicCondition, ...] = (
    "A_directional_consistency",
    "B_always_long",
    "C_always_short",
)
ALL_CONDITIONS: tuple[Condition, ...] = (*CONDITIONS, "D_direction_only")


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, Any]]:
    """Reproduce R004's frozen whole-session tick-grid isolation."""
    groups = session_groups(data.bars)
    bad = {key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in rows)}
    included = [bar for key, rows in groups.items() if key not in bad for bar in rows]
    sessions = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(bad)]
    audit: dict[str, Any] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(bad),
        "quarantined_sessions_by_type": dict(sorted(Counter(s.value for _, s in bad).items())),
        "included_bars": len(included),
        "included_sessions": len(groups) - len(bad),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in b.quality_flags for b in included),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": sessions,
        "quarantined_session_list_hash": canonical_hash(sessions),
        "legacy_r003_q001_session_list": "NOT_PERSISTED_IN_LEGACY_ARTIFACT",
    }
    expected = {
        "parent_data_version": "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0",
        "quarantined_bars": 27345,
        "quarantined_sessions": 45,
        "included_bars": 1326086,
        "included_sessions": 2216,
        "quarantined_sessions_by_type": {"day": 20, "night": 25},
        "quarantined_session_list_hash": "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
    }
    mismatches = {key: {"actual": audit.get(key), "expected": value} for key, value in expected.items() if audit.get(key) != value}
    if audit["included_tick_grid_violations"]:
        mismatches["included_tick_grid_violations"] = audit["included_tick_grid_violations"]
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    version = canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": sessions})
    return ResearchData(included, version, data.quality | {"quarantine": audit}), audit


def schedule_table(classifier: CalendarClassifier) -> list[dict[str, str]]:
    from n225m_bt.domain import Session

    rows: list[dict[str, str]] = []
    for day in (date(2021, 9, 17), date(2021, 9, 21), date(2024, 11, 5), date(2024, 11, 6)):
        for session in (Session.DAY, Session.NIGHT):
            start, close = classifier.session_open(day, session), classifier.session_close(day, session)
            rows.append({
                "trade_date_example": day.isoformat(), "session": session.value,
                "S_jst": start.isoformat(), "t_S_plus_119_jst": (start + timedelta(minutes=119)).isoformat(),
                "E_S_plus_120_jst": (start + timedelta(minutes=120)).isoformat(),
                "X_S_plus_180_jst": (start + timedelta(minutes=180)).isoformat(),
                "new_entry_cutoff_jst": (close - timedelta(minutes=15)).isoformat(),
                "F_force_flat_jst": (close - timedelta(minutes=5)).isoformat(),
            })
    return rows


def all_months() -> list[str]:
    return [f"{year:04d}-{month:02d}" for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)]


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    low, high = floor(position), ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap(values: dict[str, list[int]]) -> dict[str, Any]:
    count = len(values["A_directional_consistency"])
    if count == 0 or any(len(value) != count for value in values.values()):
        raise ValueError("invalid aligned daily bootstrap inputs")
    block, rng = min(20, count), Random(20260913)
    samples: dict[str, list[float]] = {"A": [], "A_minus_B": [], "A_minus_C": [], "A_minus_D": []}
    a, b, c, d = (values[name] for name in ALL_CONDITIONS)
    for _ in range(10_000):
        indexes: list[int] = []
        while len(indexes) < count:
            start = rng.randrange(count - block + 1)
            indexes.extend(range(start, start + block))
        indexes = indexes[:count]
        samples["A"].append(fmean(a[index] for index in indexes))
        samples["A_minus_B"].append(fmean(a[index] - b[index] for index in indexes))
        samples["A_minus_C"].append(fmean(a[index] - c[index] for index in indexes))
        samples["A_minus_D"].append(fmean(a[index] - d[index] for index in indexes))

    def estimate(sample: str, observed: float) -> dict[str, Any]:
        return {"estimate": observed, "ci95_percentile_linear": [percentile(samples[sample], .025), percentile(samples[sample], .975)]}

    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
        "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10000,
        "seed": 20260913, "common_indices_all_conditions": True,
        "percentile_implementation": "linear interpolation at (n-1)*q",
        "A_daily_mean_net_jpy": estimate("A", fmean(a)),
        "A_minus_B_daily_mean_net_jpy": estimate("A_minus_B", fmean(x - y for x, y in zip(a, b, strict=True))),
        "A_minus_C_daily_mean_net_jpy": estimate("A_minus_C", fmean(x - y for x, y in zip(a, c, strict=True))),
        "A_minus_D_daily_mean_net_jpy": estimate("A_minus_D", fmean(x - y for x, y in zip(a, d, strict=True))),
    }


def preregistration(source: dict[str, object], classifier: CalendarClassifier) -> dict[str, Any]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {
        "experiment_id": IDENTIFIER, "study_id": "R009-Q001",
        "status": "frozen_before_r009_price_statistics_events_or_pnl",
        "scope": "Development only, trade_date 2021-01-01..2025-06-30; exploratory known-Development evidence, not independent confirmation or multiplicity-corrected validation.",
        "novelty_correspondence": {
            "R005": "R005 uses a session-end 60-minute open-to-close direction and F-relative entry/exit; R009 uses 60 one-minute close changes ending at fixed S+119, their absolute-change path ratio, and fixed S+120 to S+180 hold.",
            "R001_R004_R008": "opening windows, failed breakouts, and range compression/breakout conditions do not use the same fixed close-path ratio.",
            "R006_R007": "night/day gap reversal and local-shock reversal differ in reference, direction and holding window.",
            "conclusion": "No R001-R008 evaluation has the same directional path statistic, fixed S+119 signal, S+120/S+180 execution, and continuation rule.",
        },
        "hypothesis": "When the prior 60-minute close path is directionally consistent, A follows its direction for the next fixed 60 minutes after costs and has higher common-target-date Net than same A-event always-long B, always-short C, and independently selected direction-only D.",
        "mechanism_candidate": "Gradual information incorporation may continue; price alone does not establish information arrival or order flow.",
        "alternative_explanations": ["directional drift", "path statistic has no predictive power", "post-move reversal", "cost differences", "few-period concentration"],
        "fixed_event_and_execution": {
            "schedule": "Each scheduled day/night session uses versioned S; t=S+119, E=S+120, X=S+180. Require E<=new-entry cutoff and X<=F. Schedules are never inferred from observed first/last bars.",
            "window": "Require 61 consecutive eligible same-session bars [t-60,t]; d_j=close_j-close_(j-1) for j=t-59..t; Delta=close_t-close_(t-60); V=sum(abs(d_j)). Signal uses all 60 changes only after t closes.",
            "A": "V>0, Delta!=0, and 2*abs(Delta)>=V (equality qualifies); long if Delta>0, short if Delta<0.",
            "B_C_D": "B/C use A's same preselected event long/short. D independently uses same fixed time, quality, V>0 and Delta!=0 but not the path-ratio condition; it follows Delta. A does not constrain D.",
            "orders": "Issue entry after t close for earliest E open. Issue EXIT after X-1 close for earliest X open. Delayed entry never extends X. No Stop/Target, re-entry, later-time substitution, filters, portfolio, parameter/time/holding exploration.",
            "schedule_versions": schedule_table(classifier),
        },
        "quality_and_inputs": {
            "physical_input": "Only Development-selected normalized center_continuous Parquet partitions are selected before collect; OOS and Final Holdout paths are not selected.",
            "logical_price_access": "trade_date filter 2021-01-01..2025-06-30 is in lazy scan before collect.",
            "r004_quarantine_required": {"quarantined_sessions": 45, "quarantined_bars": 27345, "included_sessions": 2216, "included_bars": 1326086},
            "quality_ceiling": "PASS_LIMITED: continuous-series contract/roll/adjustment provenance, legacy R003 list absence, and post-quality whole-session conditioning remain unresolved.",
        },
        "costs": {"tick_size_points": 5, "point_value_jpy": 100, "quantity": 1, "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "stress": {"A_only_slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "accounting": "Gross is fill-to-fill and already slippage-inclusive; Net=Gross-fees; slippage attribution is never deducted again."},
        "evaluation": {"aligned_daily": "day/night Net summed per remaining target trade_date; no trade=0; both isolated excluded; a remaining session is retained.", "bootstrap": {"block_length_trade_dates": 20, "repetitions": 10000, "seed": 20260913, "common_indices_all_conditions": True, "sampling": "non-circular moving blocks with replacement then tail truncate", "ci": "95% linear percentile"}, "continue_requires_all": ["A/B/C/D >=200 trades", "A long/short >=50", "A Net>0 and PF>1", "A, A-B, A-C, A-D bootstrap lower bounds>0", "A 2-tick expectancy>0", "A positive months>=27/54", "A top10-winner-excluded Net>0"], "decision": "BLOCKED premise/path/accounting failure; INCONCLUSIVE count failure; REJECT adequate count with any failure; CONTINUE_DEV_ONLY only all pass; never CANDIDATE."},
        "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "strategy_sha256": hashes["src/n225m_bt/strategies/session_directional_consistency.py"], "event_selector_sha256": hashes["src/n225m_bt/research/r009.py"], "test_sha256": sha256((ROOT / "tests" / "test_r009_q001.py").read_bytes()).hexdigest(), "r004_preflight_sha256": sha256((R004_OUT / "preflight.json").read_bytes()).hexdigest()},
        "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
    }


def run_condition(data: ResearchData, classifier: CalendarClassifier, engine: BacktestEngine, condition: Condition) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int], list[str]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    require_consistency = condition != "D_direction_only"
    groups = session_groups(data.bars)
    for (trade_day, session), bars in sorted(groups.items()):
        audit["included_target_sessions"] += 1
        event = directional_consistency_event(classifier, trade_day, session, bars, require_consistency=require_consistency)
        event["condition"] = condition
        audit[f"event_{event['status']}"] += 1
        if event["status"] != "event":
            audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
            events.append(event)
            continue
        signal_time = datetime.fromisoformat(cast(str, event["t_signal_jst"]))
        strategy = SessionDirectionalConsistencyStrategy(f"r009_q001_{condition}", signal_time, condition, cast(str, event["direction"]))
        result = engine.run(bars, strategy, canonical_hash({"condition": condition, "trade_date": trade_day.isoformat(), "session": session.value, "event": event, "data_version": data.data_version}))
        if len(result.trades) > 1:
            raise AssertionError("R009 produced more than one trade per condition/session")
        event.update(strategy.finalize(bars))
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update({"status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_delay_minutes": int((trade.entry_ts - signal_time - timedelta(minutes=1)).total_seconds() // 60), "exit_delay_minutes": int((trade.exit_ts - signal_time - timedelta(minutes=61)).total_seconds() // 60), "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy})
            trades.append(trade)
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
        else:
            event["status"] = "event_order_unfilled"
            audit["event_order_unfilled"] += 1
        events.append(event)
    stable = tuple(replace(trade, trade_id=f"trade-{index:06d}") for index, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1))
    return stable, events, dict(sorted(audit.items())), sorted({day.isoformat() for day, _ in groups})


def daily_for_targets(trades: tuple[Trade, ...], targets: list[str]) -> dict[str, int]:
    daily = dict.fromkeys(targets, 0)
    for trade in trades:
        daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return daily


def write_condition(folder: Path, condition: str, trades: tuple[Trade, ...], events: list[dict[str, object]], metrics: dict[str, object], audit: dict[str, int], data: ResearchData, ticks: int, daily: dict[str, int]) -> None:
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "status": "complete", "data_version": data.data_version, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(daily), "net_pnl_jpy": [daily[key] for key in sorted(daily)]}).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int) -> dict[str, object]:
    filled = [event for event in events if event.get("status") == "filled"]
    checks = {"one_trade_per_filled_event": len(filled) == len(trades), "entry_within_max_delay": all(0 <= cast(int, event["entry_delay_minutes"]) <= max_delay for event in filled), "scheduled_signal_exit_within_max_delay": all(event["exit_reason"] == "signal" and 0 <= cast(int, event["exit_delay_minutes"]) <= max_delay for event in filled), "no_force_flat": all(trade.exit_reason.value != "force_flat" for trade in trades), "no_end_of_data": all(trade.exit_reason.value != "end_of_data" for trade in trades), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades)}
    return {"checks": checks, "all_pass": all(checks.values()), "filled_paths": [{key: event.get(key) for key in ("trade_date", "session", "t_signal_jst", "E_planned_entry_jst", "entry_ts_jst", "X_planned_exit_jst", "exit_ts_jst", "entry_delay_minutes", "exit_delay_minutes", "exit_reason")} for event in filled], "accounting": "slippage attribution informational only; never additionally subtracted"}


def lower_positive(boot: dict[str, Any], key: str) -> bool:
    return cast(list[float], boot[key]["ci95_percentile_linear"])[0] > 0


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    if not (R004_OUT / "preflight.json").exists():
        raise ValueError("R004 frozen preflight artifact is missing")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close) != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R009 specification")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    plan = preregistration(source, classifier)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json"), "schedule": schedule_table(classifier)})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r009_prices", "source": source, "plan_hash": canonical_hash(plan), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    check = run([executable, "-m", "pytest", "tests/test_r009_q001.py", "-q"], cwd=ROOT, capture_output=True, text=True, check=False)
    validation = {"status": "PASS" if check.returncode == 0 else "BLOCKED", "returncode": check.returncode, "stdout": check.stdout, "stderr": check.stderr, "coverage": ["61 bars/60 changes, Delta=sum changes, ratio equality and below", "monotone/round-trip/V=0/Delta=0", "fixed t and prefix invariance", "same-session quality/missing and no cross-session", "next open/fixed exit/entry delay/no re-entry/one trade", "directional A/D and shared-control strategy directions", "execution accounting"]}
    write_json(OUT / "pre_execution_validation.json", validation)
    if check.returncode:
        raise ValueError("R009 synthetic validation failed before Development data access")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit = quarantine(development)
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "physical_io": "Development-selected Parquet partitions only; OOS/Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only", "quality_limit": "PASS_LIMITED"})
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, object] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    target_dates: list[str] | None = None
    for condition in ALL_CONDITIONS:
        trades, events, audit, dates = run_condition(view, classifier, engine, condition)
        if target_dates is None:
            target_dates = dates
        elif target_dates != dates:
            raise ValueError("condition target-date universe differs")
        metrics, daily = research_metrics(trades, view.bars), daily_for_targets(trades, dates)
        write_condition(reserve_directory(OUT, condition), condition, trades, events, metrics, audit, view, 1, daily)
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, daily
    stress = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    stress_trades, stress_events, stress_audit, stress_dates = run_condition(view, classifier, BacktestEngine(instrument.instrument.to_spec(), stress, classifier), "A_directional_consistency")
    if target_dates != stress_dates:
        raise ValueError("stress target-date universe differs")
    stress_metrics, stress_daily = research_metrics(stress_trades, view.bars), daily_for_targets(stress_trades, stress_dates)
    write_condition(reserve_directory(OUT, "A_directional_consistency_2tick"), "A_directional_consistency_2tick", stress_trades, stress_events, stress_metrics, stress_audit, view, 2, stress_daily)
    results["A_directional_consistency_2tick"] = {"trade_count": len(stress_trades), "metrics": stress_metrics, "execution_audit": stress_audit}
    audits = {condition: path_audit(events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes) for condition in ALL_CONDITIONS} | {"A_directional_consistency_2tick": path_audit(stress_events, stress_trades, baseline.execution.max_fill_delay_minutes)}
    shared = all((events_by["A_directional_consistency"][index].get("status"), events_by["A_directional_consistency"][index].get("t_signal_jst")) == (events_by[other][index].get("status"), events_by[other][index].get("t_signal_jst")) for other in CONDITIONS[1:] for index in range(len(events_by["A_directional_consistency"])))
    post = {"status": "PASS" if shared and all(cast(bool, audit["all_pass"]) for audit in audits.values()) else "BLOCKED", "conditions": audits, "A_B_C_shared_pre_event": shared, "policy": "Any path/accounting failure blocks campaign; no individual trade is removed."}
    write_json(OUT / "post_execution_validation.json", post)
    assert target_dates is not None
    values = {condition: [daily_by[condition][day] for day in target_dates] for condition in ALL_CONDITIONS}
    boot = bootstrap(values)
    months = {month: 0 for month in all_months()}
    for day, value in daily_by["A_directional_consistency"].items():
        months[day[:7]] += value
    a_metrics = cast(dict[str, Any], cast(dict[str, Any], results["A_directional_consistency"])["metrics"])
    a_overall = cast(dict[str, Any], a_metrics["overall"])
    stress_overall = cast(dict[str, Any], stress_metrics["overall"])
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_directional_consistency"])
    short_count = len(trades_by["A_directional_consistency"]) - long_count
    checks = {"A_trade_count_at_least_200": len(trades_by["A_directional_consistency"]) >= 200, "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200, "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200, "D_trade_count_at_least_200": len(trades_by["D_direction_only"]) >= 200, "A_long_at_least_50": long_count >= 50, "A_short_at_least_50": short_count >= 50, "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower_positive(boot, "A_daily_mean_net_jpy"), "A_minus_B_bootstrap_lower_above_0": lower_positive(boot, "A_minus_B_daily_mean_net_jpy"), "A_minus_C_bootstrap_lower_above_0": lower_positive(boot, "A_minus_C_daily_mean_net_jpy"), "A_minus_D_bootstrap_lower_above_0": lower_positive(boot, "A_minus_D_daily_mean_net_jpy"), "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None and stress_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])["net_excluding_top10_jpy"] > 0}
    count_keys = [key for key in checks if "trade_count" in key or key in {"A_long_at_least_50", "A_short_at_least_50"}]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(checks[key] for key in count_keys) else "CONTINUE_DEV_ONLY" if all(checks.values()) else "REJECT"
    aligned: dict[str, object] = {"trade_dates": target_dates, "no_trade": "0", "both_sessions_quarantined": "excluded", "one_session_quarantined": "remaining included session retained"}
    aligned.update({str(key): value for key, value in values.items()})
    write_json(OUT / "daily_net_pnl_aligned.json", aligned)
    write_json(OUT / "bootstrap.json", boot)
    a_rows, d_rows = events_by["A_directional_consistency"], events_by["D_direction_only"]
    def is_event(row: dict[str, object]) -> bool:
        return row.get("status") in {"event", "filled"}

    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "checks": checks, "A_direction_counts": {"long": long_count, "short": short_count}, "positive_months_of_54": sum(value > 0 for value in months.values()), "monthly_A_net_jpy": months, "conditions": results, "A_D_event_comparison": {"both": sum(is_event(a) and is_event(d) for a, d in zip(a_rows, d_rows, strict=True)), "A_only": sum(is_event(a) and not is_event(d) for a, d in zip(a_rows, d_rows, strict=True)), "D_only": sum(not is_event(a) and is_event(d) for a, d in zip(a_rows, d_rows, strict=True)), "interpretation": "A-D includes different selected event composition, trade count and costs; it is not a causal path-ratio-only effect."}, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; 2025 Jan-Jun; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
