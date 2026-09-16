"""Execute the preregistered Development-only R022-Q001 experiment."""

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
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r022 import previous_scheduled_session, prior_range_end_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.prior_session_range_end import PriorSessionRangeEndStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r022-q001-20260914-prior-session-range-end-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
EXPECTED_TARGET_DATES = 1120
Condition = Literal["A_range_end", "B_always_long", "C_always_short", "D_full_session", "F_late60"]
CONDITIONS: tuple[Condition, ...] = ("A_range_end", "B_always_long", "C_always_short", "D_full_session", "F_late60")


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)} for path in partition_paths(gold_root, "development")]
    return {"status": "frozen_before_r022_price_statistics_events_or_pnl", "scope": "Development selected normalized Parquet only; raw, volume, external prices, OOS, and Final Holdout prohibited.", "trade_date_range": ["2021-01-01", "2025-06-30"], "files": files, "files_hash": canonical_hash(files)}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    bad = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [row for key, rows in grouped.items() if key not in bad for row in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(bad)]
    audit: dict[str, object] = {"parent_data_version": data.data_version, "parent_bars": len(data.bars), "quarantined_bars": len(data.bars) - len(included), "quarantined_sessions": len(bad), "quarantined_sessions_by_type": dict(sorted(Counter(session.value for _, session in bad).items())), "included_bars": len(included), "included_sessions": len(grouped) - len(bad), "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included), "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION", "quarantined_session_list": listed, "quarantined_session_list_hash": canonical_hash(listed)}
    expected = {"parent_data_version": EXPECTED_PARENT_VERSION, "quarantined_sessions": 45, "quarantined_bars": 27345, "included_bars": 1326086, "included_sessions": 2216, "quarantined_sessions_by_type": {"day": 20, "night": 25}, "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH}
    mismatches = {key: {"actual": audit.get(key), "expected": value} for key, value in expected.items() if audit.get(key) != value}
    if cast(int, audit["included_tick_grid_violations"]):
        mismatches["included_tick_grid_violations"] = {"actual": audit["included_tick_grid_violations"], "expected": 0}
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": listed}), data.quality | {"quarantine": audit}), audit, bad


def months() -> list[str]:
    return [f"{year:04d}-{month:02d}" for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)]


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    low, high = floor(point), ceil(point)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def bootstrap(values: dict[str, list[int]]) -> dict[str, object]:
    count, block = len(values["A_range_end"]), 20
    if count != EXPECTED_TARGET_DATES or any(len(value) != count for value in values.values()):
        raise ValueError("R022 daily series do not use the fixed 1,120 trade-date axis")
    a, b, c, d, f = (values[key] for key in CONDITIONS)
    series = {"A": a, "A_minus_B": [x - y for x, y in zip(a, b, strict=True)], "A_minus_C_short": [x - y for x, y in zip(a, c, strict=True)], "A_minus_D": [x - y for x, y in zip(a, d, strict=True)], "A_minus_F_late": [x - y for x, y in zip(a, f, strict=True)]}
    samples: dict[str, list[float]] = {key: [] for key in series}
    rng = Random(20260913)
    for _ in range(10_000):
        indexes: list[int] = []
        while len(indexes) < count:
            start = rng.randrange(count - block + 1)
            indexes.extend(range(start, start + block))
        indexes = indexes[:count]
        for key, values_ in series.items():
            samples[key].append(fmean(values_[index] for index in indexes))
    labels = {"A": "A_daily_mean_net_jpy", "A_minus_B": "A_minus_B_daily_mean_net_jpy", "A_minus_C_short": "A_minus_C_short_daily_mean_net_jpy", "A_minus_D": "A_minus_D_daily_mean_net_jpy", "A_minus_F_late": "A_minus_F_late_daily_mean_net_jpy"}
    return {"method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate", "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10_000, "seed": 20260913, "common_indices_all_conditions": True, "percentile_implementation": "linear interpolation at (n-1)*q", **{labels[key]: {"estimate": fmean(value), "ci95_percentile_linear": [percentile(samples[key], .025), percentile(samples[key], .975)]} for key, value in series.items()}}


def preregistration(source: dict[str, object]) -> dict[str, object]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {"experiment_id": IDENTIFIER, "study_id": "R022-Q001", "status": "frozen_before_r022_price_statistics_events_or_pnl", "scope": "Development trade_date 2021-01-01..2025-06-30 only. Known-Development additional exploration, not independent confirmation or multiplicity-corrected validation.", "duplicate_review": {"R001-R021": "No equivalent prior test found. R014 uses an intra-session 60-minute range position to forecast its next hour; R013 is a previous same-type session early-direction rule; R005/R012/R015 use different reference sessions/times/directions/exits.", "decision": "Proceed only with this fixed distinct specification; no rescue variant."}, "hypothesis": "If the immediately preceding scheduled futures session closes in its full normal-session upper/lower range quartile, following that direction for the first 60 minutes of the next scheduled session has post-cost expectancy and exceeds fixed-long, fixed-short, full-prior-session direction, and final-60-minute direction controls. Range-end is a price proxy only: no order-flow or participant behaviour is observed or identified.", "fixed_rule": {"calendar": "p is exactly the previous scheduled session in real-time order from the versioned ExchangeCalendar (day->night/night->day); never choose p from observed rows, skip holidays/weekends, and never skip over a missing/ineligible/quarantined p.", "reference": "Require every scheduled minute P through final normal one-minute bar. H=max(high), L=min(low), O=first open, C=last normal close, J=C-open(final-60-minute first bar). A upper if 4C>3H+L, lower if 4C<H+3L. H=L, both equality boundaries, middle 50%, C-O=0, J=0 are common skips.", "current": "S only establishes current scheduled eligibility and order issue; its OHLC never enters signal. E=S+1 open; X=S+61 open, with exit signal at S+60. Delayed entry never extends X.", "conditions": {"A": "upper long / lower short", "B": "always long", "C_short": "always short", "D": "sign(C-O)", "F_late": "sign(J)", "A2": "A rerun at 2 ticks/side"}, "prohibited": ["threshold/reference/holding/direction rescue", "Stop", "Target", "re-entry", "early exit", "additional costs/delay", "WFA", "OOS", "Final Holdout"]}, "inputs": {"physical": "selected normalized Development Parquet only", "r004_quarantine": {"parent_data_version": EXPECTED_PARENT_VERSION, "quarantined_sessions": 45, "quarantined_bars": 27345, "included_sessions": 2216, "included_bars": 1326086, "hash": EXPECTED_QUARANTINE_HASH}, "quality_ceiling": "PASS_LIMITED"}, "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; attribution never double-deducted."}, "evaluation": {"daily": "fixed 1,120 trade-date axis, day/night summed; exclude both-isolated dates only, leave skips/cancellations/no-trades as zero", "bootstrap": {"block": 20, "repetitions": 10000, "seed": 20260913, "common_indices": True, "noncircular": True, "tail_truncate": True, "percentile": "linear"}, "information_gate": ["A/B/C/D/F each >=200 trades", "A long/short each >=50", "A current day/night each >=100", "A-D and A-F direction disagreement each >=50"], "required_after_information": ["A Net>0", "A PF>1", "A, A-B, A-C, A-D, A-F CI lower >0", "A2 expectancy>0", "positive months>=27/54", "A top10-excluded Net>0"], "decision": "BLOCKED for input/synthetic/execution/accounting gate failure; INCONCLUSIVE for information failure; otherwise REJECT if any necessary result fails; all pass is INVESTIGATE only."}, "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "script_sha256": sha256_file(Path(__file__)), "selector_sha256": hashes["src/n225m_bt/research/r022.py"], "strategy_sha256": hashes["src/n225m_bt/strategies/prior_session_range_end.py"], "test_sha256": sha256_file(ROOT / "tests" / "test_r022_q001.py")}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}


def direction(condition: Condition, event: dict[str, object]) -> str:
    if condition == "B_always_long":
        return "long"
    if condition == "C_always_short":
        return "short"
    return cast(str, event[{"A_range_end": "A_direction", "D_full_session": "D_direction", "F_late60": "F_late_direction"}[condition]])


def run_condition(data: ResearchData, classifier: CalendarClassifier, engine: BacktestEngine, condition: Condition, bad: set[tuple[date, Session]], targets: list[str], scheduled_days: list[date]) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    for text_day in targets:
        current_day = date.fromisoformat(text_day)
        for current_session in (Session.DAY, Session.NIGHT):
            current_key = (current_day, current_session)
            reference = previous_scheduled_session(classifier, scheduled_days, current_key)
            reference_rows = groups.get(reference) if reference else None
            event = prior_range_end_event(classifier, current_day, current_session, groups.get(current_key), reference, reference_rows, current_quarantined=current_key in bad, reference_quarantined=reference in bad if reference else False)
            event.update({"condition": condition, "pre_event_status": event["status"], "A_D_direction_relation": "same" if event.get("A_direction") == event.get("D_direction") else "different", "A_F_direction_relation": "same" if event.get("A_direction") == event.get("F_late_direction") else "different"})
            audit[f"event_{event['status']}"] += 1
            if event["status"] != "eligible":
                audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
                events.append(event)
                continue
            signal = datetime.fromisoformat(cast(str, event["S_current_open_bar_start_jst"]))
            result = engine.run(groups[current_key], PriorSessionRangeEndStrategy(f"r022_q001_{condition}", signal, direction(condition, event)), canonical_hash({"condition": condition, "event": event, "data_version": data.data_version}))
            if len(result.trades) > 1:
                raise AssertionError("R022 produced more than one trade/session")
            audit["canceled_orders"] += result.canceled_orders
            if result.trades:
                trade = result.trades[0]
                event.update({"status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_delay_minutes": int((trade.entry_ts - signal - timedelta(minutes=1)).total_seconds() // 60), "exit_delay_minutes": int((trade.exit_ts - signal - timedelta(minutes=61)).total_seconds() // 60), "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy})
                trades.append(trade)
                audit["trades"] += 1
                audit[f"exit_{trade.exit_reason.value}"] += 1
            else:
                event["status"] = "eligible_order_unfilled"
                audit["eligible_order_unfilled"] += 1
            events.append(event)
    stable = tuple(replace(item, trade_id=f"trade-{index:06d}") for index, item in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1))
    return stable, events, dict(sorted(audit.items()))


def daily(trades: tuple[Trade, ...], targets: list[str]) -> dict[str, int]:
    output = dict.fromkeys(targets, 0)
    for trade in trades:
        output[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return output


def write_condition(folder: Path, condition: str, trades: tuple[Trade, ...], events: list[dict[str, object]], metrics: dict[str, object], audit: dict[str, int], data: ResearchData, ticks: int, net_daily: dict[str, int]) -> None:
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "status": "complete", "data_version": data.data_version, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(net_daily), "net_pnl_jpy": [net_daily[key] for key in sorted(net_daily)]}).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int) -> dict[str, object]:
    eligible = [item for item in events if item.get("pre_event_status") == "eligible"]
    filled = [item for item in events if item.get("status") == "filled"]
    checks = {"every_eligible_event_filled": len(eligible) == len(filled), "one_trade_per_filled_event": len(filled) == len(trades), "no_canceled_or_unfilled_eligible_order": not any(item.get("status") == "eligible_order_unfilled" for item in events), "entry_within_max_delay": all(0 <= cast(int, item["entry_delay_minutes"]) <= max_delay for item in filled), "fixed_signal_exit_within_max_delay": all(item["exit_reason"] == "signal" and 0 <= cast(int, item["exit_delay_minutes"]) <= max_delay for item in filled), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades), "no_force_flat_or_end_of_data": all(trade.exit_reason.value not in {"force_flat", "end_of_data"} for trade in trades)}
    return {"checks": checks, "all_pass": all(checks.values()), "accounting": "slippage attribution is informational and is not additionally deducted"}


def relation_audit(a_events: list[dict[str, object]], other_events: list[dict[str, object]], label: str) -> dict[str, object]:
    output: dict[str, object] = {}
    field = f"A_{label}_direction_relation"
    for relation in ("same", "different"):
        pairs = [(a, other) for a, other in zip(a_events, other_events, strict=True) if a.get("pre_event_status") == "eligible" and a.get(field) == relation]
        filled = [(a, other) for a, other in pairs if a.get("status") == other.get("status") == "filled"]
        output[relation] = {"pre_event_count": len(pairs), "both_filled": len(filled), "side_entry_exit_gross_net_identical": all(tuple(a.get(key) for key in ("side", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "net_pnl_jpy")) == tuple(other.get(key) for key in ("side", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "net_pnl_jpy")) for a, other in filled), "A_minus_other_net_jpy": sum(cast(int, a.get("net_pnl_jpy", 0)) - cast(int, other.get("net_pnl_jpy", 0)) for a, other in pairs)}
    return output


def event_groups(events: list[dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for quartile in ("upper", "lower"):
        for session in ("day", "night"):
            rows = [item for item in events if item.get("range_quartile") == quartile and item.get("session") == session]
            filled = [item for item in rows if item.get("status") == "filled"]
            result[f"{quartile}_{session}"] = {"pre_event_count": len(rows), "trade_count": len(filled), "fees_jpy": sum(cast(int, item.get("fees_jpy", 0)) for item in filled), "net_pnl_jpy": sum(cast(int, item.get("net_pnl_jpy", 0)) for item in filled)}
    return result


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r022_q001_prior_session_range_end.py')

    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close)
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R022 specification")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    plan = preregistration(source)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "input_manifest.json", input_manifest(data_config.gold_root))
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r022_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": 20260913, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r022_q001.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r022.py", "src/n225m_bt/strategies/prior_session_range_end.py", "tests/test_r022_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r022.py", "src/n225m_bt/strategies/prior_session_range_end.py"]}
    validation: dict[str, Any] = {"coverage": ["calendar day->night/night->day, weekend/holiday mapping, regime change and calendar_date/trade_date", "no skip-over, full scheduled normal-session continuity, final normal bar, H/L/O/C/J, quartile boundaries/zeros/middle and high-low counterfactual", "S-price independence and prefix invariance", "next-open entry, fixed exit/non-extension, cutoff/force-flat, one position and accounting", "Final Holdout input rejection"]}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R022 validation failed before Development price access")
    development = load_split(data_config.gold_root, "development")
    parent_groups = session_groups(development.bars)
    view, quarantine_audit, bad = quarantine(development)
    targets = sorted({day.isoformat() for day, _ in parent_groups if not ((day, Session.DAY) in bad and (day, Session.NIGHT) in bad)})
    if len(targets) != EXPECTED_TARGET_DATES:
        raise ValueError(f"R022 target date count {len(targets)} != {EXPECTED_TARGET_DATES}")
    scheduled_days = [item.trade_date for item in classifier.exchange_calendar.trading_days() if date(2021, 1, 1) <= item.trade_date <= date(2025, 6, 30)]
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_trade_dates": targets, "scheduled_calendar_trade_dates": scheduled_days, "physical_io": "Development selected normalized Parquet only; OOS/Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only"})
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, object] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, classifier, engine, condition, bad, targets, scheduled_days)
        metrics, net_daily = research_metrics(trades, view.bars), daily(trades, targets)
        folder = OUT / condition
        folder.mkdir()
        write_condition(folder, condition, trades, events, metrics, audit, view, 1, net_daily)
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, net_daily
    stress = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    a2_trades, a2_events, a2_audit = run_condition(view, classifier, BacktestEngine(instrument.instrument.to_spec(), stress, classifier), "A_range_end", bad, targets, scheduled_days)
    a2_metrics, a2_daily = research_metrics(a2_trades, view.bars), daily(a2_trades, targets)
    a2_folder = OUT / "A_range_end_2tick"
    a2_folder.mkdir()
    write_condition(a2_folder, "A_range_end_2tick", a2_trades, a2_events, a2_metrics, a2_audit, view, 2, a2_daily)
    results["A_range_end_2tick"] = {"trade_count": len(a2_trades), "metrics": a2_metrics, "execution_audit": a2_audit}
    shared_fields = ("trade_date", "session", "pre_event_status", "reason", "p_trade_date", "p_session", "H_points", "L_points", "O_points", "C_points", "J_points", "range_quartile", "E_planned_entry_jst", "X_planned_exit_jst")
    shared = all(len(events_by[key]) == len(events_by["A_range_end"]) and all(tuple(row.get(field) for field in shared_fields) == tuple(events_by["A_range_end"][index].get(field) for field in shared_fields) for index, row in enumerate(events_by[key])) for key in CONDITIONS[1:])
    a2_shared = len(a2_events) == len(events_by["A_range_end"]) and all(tuple(row.get(field) for field in shared_fields) == tuple(events_by["A_range_end"][index].get(field) for field in shared_fields) for index, row in enumerate(a2_events))
    a_d, a_f = relation_audit(events_by["A_range_end"], events_by["D_full_session"], "D"), relation_audit(events_by["A_range_end"], events_by["F_late60"], "F")
    audits = {key: path_audit(events_by[key], trades_by[key], baseline.execution.max_fill_delay_minutes) for key in CONDITIONS} | {"A_range_end_2tick": path_audit(a2_events, a2_trades, baseline.execution.max_fill_delay_minutes)}
    post = {"status": "PASS" if shared and a2_shared and all(cast(bool, value["all_pass"]) for value in audits.values()) and cast(bool, cast(dict[str, object], a_d["same"])["side_entry_exit_gross_net_identical"]) and cast(bool, cast(dict[str, object], a_f["same"])["side_entry_exit_gross_net_identical"]) else "BLOCKED", "conditions": audits, "all_conditions_identical_pre_event": shared, "A_A2_identical_pre_event": a2_shared, "A_D_direction_diagnostic": a_d, "A_F_direction_diagnostic": a_f, "policy": "Path/accounting mismatch blocks economic decision; successful-fill intersection is prohibited."}
    write_json(OUT / "post_execution_validation.json", post)
    write_json(OUT / "direction_path_audits.json", {"A_D": a_d, "A_F_late": a_f})
    groups = event_groups(events_by["A_range_end"])
    write_json(OUT / "quartile_session_diagnostics.json", groups)
    values: dict[str, list[int]] = {key: [daily_by[key][day] for day in targets] for key in CONDITIONS}
    boot = bootstrap(values)
    monthly = {month: 0 for month in months()}
    for day, amount in daily_by["A_range_end"].items():
        monthly[day[:7]] += amount
    a_metrics = cast(dict[str, Any], cast(dict[str, Any], results["A_range_end"])["metrics"])
    a_overall, a2_overall = cast(dict[str, Any], a_metrics["overall"]), cast(dict[str, Any], a2_metrics["overall"])
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_range_end"])
    day_count = sum(trade.metadata.get("entry_session") == "day" for trade in trades_by["A_range_end"])

    def lower(key: str) -> bool:
        return cast(list[float], cast(dict[str, Any], boot)[key]["ci95_percentile_linear"])[0] > 0
    gates = {"A_trade_count_at_least_200": len(trades_by["A_range_end"]) >= 200, "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200, "C_short_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200, "D_trade_count_at_least_200": len(trades_by["D_full_session"]) >= 200, "F_late_trade_count_at_least_200": len(trades_by["F_late60"]) >= 200, "A_long_at_least_50": long_count >= 50, "A_short_at_least_50": len(trades_by["A_range_end"]) - long_count >= 50, "A_day_at_least_100": day_count >= 100, "A_night_at_least_100": len(trades_by["A_range_end"]) - day_count >= 100, "A_D_different_direction_events_at_least_50": cast(int, cast(dict[str, object], a_d["different"])["pre_event_count"]) >= 50, "A_F_different_direction_events_at_least_50": cast(int, cast(dict[str, object], a_f["different"])["pre_event_count"]) >= 50, "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"), "A_minus_B_bootstrap_lower_above_0": lower("A_minus_B_daily_mean_net_jpy"), "A_minus_C_short_bootstrap_lower_above_0": lower("A_minus_C_short_daily_mean_net_jpy"), "A_minus_D_bootstrap_lower_above_0": lower("A_minus_D_daily_mean_net_jpy"), "A_minus_F_late_bootstrap_lower_above_0": lower("A_minus_F_late_daily_mean_net_jpy"), "A_2tick_expectancy_positive": a2_overall["expectancy_jpy"] is not None and a2_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(amount > 0 for amount in monthly.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])["net_excluding_top10_jpy"] > 0}
    count_keys = [key for key in gates if "trade_count" in key or key.startswith("A_long") or key.startswith("A_short") or key.startswith("A_day") or key.startswith("A_night") or "different_direction" in key]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(gates[key] for key in count_keys) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    aligned: dict[str, object] = {"trade_dates": targets, "no_trade": "0", "both_sessions_quarantined": "excluded", "one_session_quarantined": "remaining session retained; isolated session is audited as zero"}
    aligned.update(values)
    write_json(OUT / "daily_net_pnl_aligned.json", aligned)
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "A_direction_counts": {"long": long_count, "short": len(trades_by["A_range_end"]) - long_count, "day": day_count, "night": len(trades_by["A_range_end"]) - day_count}, "positive_months_of_54": sum(amount > 0 for amount in monthly.values()), "monthly_A_net_jpy": monthly, "conditions": results, "A_quartile_session": groups, "A_year_month_day_night_long_short_metrics": {"year": a_metrics["segments"]["year"], "month": a_metrics["segments"]["month"], "session": a_metrics["segments"]["session"], "side": a_metrics["segments"]["side"]}, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; 2025 is Jan-Jun; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
