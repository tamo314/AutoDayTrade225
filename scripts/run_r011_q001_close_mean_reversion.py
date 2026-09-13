"""Execute the preregistered Development-only R011-Q001 experiment."""

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
from n225m_bt.domain import Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r009 import session_groups
from n225m_bt.research.r011 import close_mean_reversion_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.session_fixed_time import SessionFixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r011-q001-20260914-close-mean-reversion-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
R004_OUT = ROOT / "results" / "research" / "r004-q001-20260913-failed-breakout-01"
Condition = Literal["A_close_mean_reversion", "B_always_long", "C_always_short"]
CONDITIONS: tuple[Condition, ...] = ("A_close_mean_reversion", "B_always_long", "C_always_short")


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, Any]]:
    """Reproduce R004's frozen whole-session tick-grid isolation exactly."""
    groups = session_groups(data.bars)
    bad = {key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in rows)}
    included = [bar for key, rows in groups.items() if key not in bad for bar in rows]
    session_list = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(bad)]
    audit: dict[str, Any] = {
        "parent_data_version": data.data_version, "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included), "quarantined_sessions": len(bad),
        "quarantined_sessions_by_type": dict(sorted(Counter(s.value for _, s in bad).items())),
        "included_bars": len(included), "included_sessions": len(groups) - len(bad),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": session_list, "quarantined_session_list_hash": canonical_hash(session_list),
        "legacy_r003_q001_session_list": "NOT_PERSISTED_IN_LEGACY_ARTIFACT",
    }
    expected = {"parent_data_version": "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0", "quarantined_bars": 27345, "quarantined_sessions": 45, "included_bars": 1326086, "included_sessions": 2216, "quarantined_sessions_by_type": {"day": 20, "night": 25}, "quarantined_session_list_hash": "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"}
    mismatches = {key: {"actual": audit.get(key), "expected": value} for key, value in expected.items() if audit.get(key) != value}
    if audit["included_tick_grid_violations"]:
        mismatches["included_tick_grid_violations"] = audit["included_tick_grid_violations"]
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": session_list}), data.quality | {"quarantine": audit}), audit


def schedule_table(classifier: CalendarClassifier) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for day in (date(2021, 9, 17), date(2021, 9, 21), date(2024, 11, 5), date(2024, 11, 6)):
        for session in (Session.DAY, Session.NIGHT):
            start, close = classifier.session_open(day, session), classifier.session_close(day, session)
            rows.append({"trade_date_example": day.isoformat(), "session": session.value, "S_jst": start.isoformat(), "t_S_plus_119_jst": (start + timedelta(minutes=119)).isoformat(), "E_S_plus_120_jst": (start + timedelta(minutes=120)).isoformat(), "X_S_plus_180_jst": (start + timedelta(minutes=180)).isoformat(), "new_entry_cutoff_jst": (close - timedelta(minutes=15)).isoformat(), "F_force_flat_jst": (close - timedelta(minutes=5)).isoformat()})
    return rows


def all_months() -> list[str]:
    return [f"{year:04d}-{month:02d}" for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)]


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    low, high = floor(position), ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap(values: dict[str, list[int]]) -> dict[str, Any]:
    count = len(values["A_close_mean_reversion"])
    if count == 0 or any(len(item) != count for item in values.values()):
        raise ValueError("invalid aligned daily bootstrap inputs")
    block, rng = min(20, count), Random(20260913)
    samples: dict[str, list[float]] = {"A": [], "A_minus_B": [], "A_minus_C": []}
    a, b, c = (values[name] for name in CONDITIONS)
    for _ in range(10_000):
        indexes: list[int] = []
        while len(indexes) < count:
            start = rng.randrange(count - block + 1)
            indexes.extend(range(start, start + block))
        indexes = indexes[:count]
        samples["A"].append(fmean(a[index] for index in indexes))
        samples["A_minus_B"].append(fmean(a[index] - b[index] for index in indexes))
        samples["A_minus_C"].append(fmean(a[index] - c[index] for index in indexes))

    def estimate(name: str, observed: float) -> dict[str, Any]:
        return {"estimate": observed, "ci95_percentile_linear": [percentile(samples[name], .025), percentile(samples[name], .975)]}

    return {"method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate", "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10000, "seed": 20260913, "common_indices_all_conditions": True, "percentile_implementation": "linear interpolation at (n-1)*q", "A_daily_mean_net_jpy": estimate("A", fmean(a)), "A_minus_B_daily_mean_net_jpy": estimate("A_minus_B", fmean(x - y for x, y in zip(a, b, strict=True))), "A_minus_C_daily_mean_net_jpy": estimate("A_minus_C", fmean(x - y for x, y in zip(a, c, strict=True)))}


def lower_positive(bootstrap_output: dict[str, Any], key: str) -> bool:
    return cast(list[float], bootstrap_output[key]["ci95_percentile_linear"])[0] > 0


def preregistration(source: dict[str, object], classifier: CalendarClassifier) -> dict[str, Any]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {"experiment_id": IDENTIFIER, "study_id": "R011-Q001", "status": "frozen_before_r011_price_statistics_events_or_pnl", "scope": "Development only, trade_date 2021-01-01..2025-06-30; exploratory after existing Development results, not independent confirmation, unused sample, or multiplicity-corrected validation.", "novelty_correspondence": {"R001": "session-opening direction reversal", "R004": "failed-breakout reversal", "R006": "session-gap reversal", "R007": "single-bar local-shock reversal", "R009": "close-change path consistency continuation", "conclusion": "No R001-R010 evaluation uses the prior-60-close arithmetic mean deviation Z at fixed S+119 and reverses it through S+180."}, "hypothesis": "At fixed mid-session time, a close deviating from the arithmetic mean of its immediately prior 60 closes reverses toward that mean over a fixed 60-minute hold after costs.", "fixed_event_and_execution": {"schedule": "Versioned day/night S; t=S+119, E=S+120, X=S+180. Require E<=new-entry cutoff and X<=F; never infer schedule from observed edges.", "window": "Require 61 consecutive eligible same-session bars [t-60,t]. M_t=sum(close[t-60:t-1])/60 and Z_t=60*close_t-sum(close[t-60:t-1]); mean is never tick-rounded.", "conditions": {"A": "Z>0 short; Z<0 long.", "B": "always long on precisely A's preselected nonzero-Z event.", "C": "always short on precisely A's preselected nonzero-Z event.", "A2": "same A event/direction with engine rerun at 2 ticks per side."}, "skip": "Z=0, missing/ineligible window, or session crossing skips all conditions; no substitution, threshold, standardization, carry-forward, intersection on successful fills, Stop/Target, re-entry, mean-touch exit, or parameter/time exploration.", "orders": "Signal only after t close; earliest eligible E open entry. EXIT signal after X-1 close; earliest X open. Entry delay does not extend X.", "schedule_versions": schedule_table(classifier)}, "quality_and_inputs": {"physical_input": "Only Development-selected normalized center_continuous Parquet partitions before collect; OOS/Final Holdout paths never selected.", "logical_price_access": "trade_date filter 2021-01-01..2025-06-30 in lazy scan before collect.", "r004_quarantine_required": {"quarantined_sessions": 45, "quarantined_bars": 27345, "included_sessions": 2216, "included_bars": 1326086, "quarantine_hash": "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"}, "quality_ceiling": "PASS_LIMITED; inherited continuous-series/roll/adjustment, legacy list, and post-quality isolation limitations remain."}, "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; slippage attribution is not deducted again."}, "evaluation": {"aligned_daily": "sum day/night Net per remaining trade_date; no trade=0; exclude both-isolated dates; retain a non-isolated session.", "bootstrap": {"block_length_trade_dates": 20, "repetitions": 10000, "seed": 20260913, "common_indices_all_conditions": True, "sampling": "non-circular moving blocks with replacement then tail truncate", "ci": "95% linear percentile"}, "gates": ["A/B/C >=200", "A long/short >=50", "A Net>0 and PF>1", "A, A-B, A-C CI lower bounds strictly >0", "A2 expectancy>0", "A positive months>=27/54", "A top10-winner-excluded Net>0"], "decision": "BLOCKED if input/execution/accounting invalid; INCONCLUSIVE if count gate fails; REJECT if count passes but another gate fails; otherwise Development primary conditions passed, PASS_LIMITED only; never automatic CANDIDATE."}, "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "event_selector_sha256": hashes["src/n225m_bt/research/r011.py"], "strategy_sha256": hashes["src/n225m_bt/strategies/session_fixed_time.py"], "test_sha256": sha256((ROOT / "tests" / "test_r011_q001.py").read_bytes()).hexdigest(), "r004_preflight_sha256": sha256((R004_OUT / "preflight.json").read_bytes()).hexdigest()}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}


def direction_for(condition: Condition, event: dict[str, object]) -> str:
    return "long" if condition == "B_always_long" else "short" if condition == "C_always_short" else cast(str, event["direction"])


def run_condition(data: ResearchData, classifier: CalendarClassifier, engine: BacktestEngine, condition: Condition) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int], list[str]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    groups = session_groups(data.bars)
    for (trade_day, session), bars in sorted(groups.items()):
        event = close_mean_reversion_event(classifier, trade_day, session, bars)
        event["condition"], event["pre_event_status"] = condition, event["status"]
        audit[f"event_{event['status']}"] += 1
        if event["status"] != "event":
            audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
            events.append(event)
            continue
        signal_time = datetime.fromisoformat(cast(str, event["t_signal_jst"]))
        result = engine.run(bars, SessionFixedTimeStrategy(f"r011_q001_{condition}", signal_time, direction_for(condition, event)), canonical_hash({"condition": condition, "event": event, "data_version": data.data_version}))
        if len(result.trades) > 1:
            raise AssertionError("R011 produced more than one trade per condition/session")
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
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "status": "complete", "data_version": data.data_version, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}})
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(daily), "net_pnl_jpy": [daily[key] for key in sorted(daily)]}).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int) -> dict[str, object]:
    filled = [event for event in events if event.get("status") == "filled"]
    checks = {"one_trade_per_filled_event": len(filled) == len(trades), "entry_within_max_delay": all(0 <= cast(int, event["entry_delay_minutes"]) <= max_delay for event in filled), "fixed_signal_exit_within_max_delay": all(event["exit_reason"] == "signal" and 0 <= cast(int, event["exit_delay_minutes"]) <= max_delay for event in filled), "no_force_flat": all(trade.exit_reason.value != "force_flat" for trade in trades), "no_end_of_data": all(trade.exit_reason.value != "end_of_data" for trade in trades), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades)}
    return {"checks": checks, "all_pass": all(checks.values()), "accounting": "slippage attribution informational only; never additionally subtracted"}


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    if not (R004_OUT / "preflight.json").exists():
        raise ValueError("R004 frozen preflight artifact is missing")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close)
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R011 specification")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    plan = preregistration(source, classifier)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json"), "schedule": schedule_table(classifier)})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r011_prices", "source": source, "plan_hash": canonical_hash(plan), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    checks = {"pytest": [executable, "-m", "pytest", "tests/test_r011_q001.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src", "tests", "scripts/run_r011_q001_close_mean_reversion.py"], "mypy": [executable, "-m", "mypy", "src", "tests/test_r011_q001.py", "scripts/run_r011_q001_close_mean_reversion.py"]}
    validation: dict[str, Any] = {"coverage": ["prior 60 only/current excluded/integer Z/sign/zero", "missing and session boundary", "same endpoint/last change but different Z path", "fixed schedule/next eligible entry/fixed exit/no extension", "prefix invariance, one position, accounting", "existing engine regression suite covers cutoff-held exit and pending EXIT before force-flat", "OOS/Final Holdout input rejection"]}
    for name, command in checks.items():
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in checks) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R011 validation failed before Development data access")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit = quarantine(development)
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "physical_io": "Development-selected Parquet partitions only; OOS/Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only", "quality_limit": "PASS_LIMITED"})
    results: dict[str, object] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    target_dates: list[str] | None = None
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    for condition in CONDITIONS:
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
    stress_trades, stress_events, stress_audit, stress_dates = run_condition(view, classifier, BacktestEngine(instrument.instrument.to_spec(), stress, classifier), "A_close_mean_reversion")
    if target_dates != stress_dates:
        raise ValueError("stress target-date universe differs")
    stress_metrics, stress_daily = research_metrics(stress_trades, view.bars), daily_for_targets(stress_trades, stress_dates)
    write_condition(reserve_directory(OUT, "A_close_mean_reversion_2tick"), "A_close_mean_reversion_2tick", stress_trades, stress_events, stress_metrics, stress_audit, view, 2, stress_daily)
    results["A_close_mean_reversion_2tick"] = {"trade_count": len(stress_trades), "metrics": stress_metrics, "execution_audit": stress_audit}
    audits = {condition: path_audit(events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes) for condition in CONDITIONS} | {"A_close_mean_reversion_2tick": path_audit(stress_events, stress_trades, baseline.execution.max_fill_delay_minutes)}
    shared = all((events_by["A_close_mean_reversion"][index]["pre_event_status"], events_by["A_close_mean_reversion"][index]["t_signal_jst"]) == (events_by[other][index]["pre_event_status"], events_by[other][index]["t_signal_jst"]) for other in CONDITIONS[1:] for index in range(len(events_by["A_close_mean_reversion"])))
    post = {"status": "PASS" if shared and all(cast(bool, audit["all_pass"]) for audit in audits.values()) else "BLOCKED", "conditions": audits, "A_B_C_shared_pre_event": shared, "policy": "Any path/accounting failure blocks campaign; no trade is removed."}
    write_json(OUT / "post_execution_validation.json", post)
    assert target_dates is not None
    values: dict[str, list[int]] = {
        condition: [daily_by[condition][day] for day in target_dates] for condition in CONDITIONS
    }
    boot = bootstrap(values)
    months = {month: 0 for month in all_months()}
    for day, value in daily_by["A_close_mean_reversion"].items():
        months[day[:7]] += value
    a_metrics = cast(dict[str, Any], cast(dict[str, Any], results["A_close_mean_reversion"])["metrics"])
    a_overall, stress_overall = cast(dict[str, Any], a_metrics["overall"]), cast(dict[str, Any], stress_metrics["overall"])
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_close_mean_reversion"])
    short_count = len(trades_by["A_close_mean_reversion"]) - long_count
    gates = {"A_trade_count_at_least_200": len(trades_by["A_close_mean_reversion"]) >= 200, "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200, "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200, "A_long_at_least_50": long_count >= 50, "A_short_at_least_50": short_count >= 50, "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower_positive(boot, "A_daily_mean_net_jpy"), "A_minus_B_bootstrap_lower_above_0": lower_positive(boot, "A_minus_B_daily_mean_net_jpy"), "A_minus_C_bootstrap_lower_above_0": lower_positive(boot, "A_minus_C_daily_mean_net_jpy"), "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None and stress_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])["net_excluding_top10_jpy"] > 0}
    count_keys = [key for key in gates if "trade_count" in key or key in {"A_long_at_least_50", "A_short_at_least_50"}]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(gates[key] for key in count_keys) else "DEVELOPMENT_PRIMARY_CONDITIONS_PASSED_PASS_LIMITED" if all(gates.values()) else "REJECT"
    aligned: dict[str, object] = {"trade_dates": target_dates, "no_trade": "0", "both_sessions_quarantined": "excluded", "one_session_quarantined": "remaining included session retained"}
    aligned.update(values)
    write_json(OUT / "daily_net_pnl_aligned.json", aligned)
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "A_direction_counts": {"long": long_count, "short": short_count}, "positive_months_of_54": sum(value > 0 for value in months.values()), "monthly_A_net_jpy": months, "conditions": results, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; 2025 Jan-Jun; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
