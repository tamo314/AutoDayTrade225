"""Execute the registered coverage-only repair for TASK-R099-Q002 once."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean
from subprocess import run
from typing import Any

import numpy as np
from numpy.typing import NDArray
from run_r074_q001_low_night_range_opening_breakout import quarantine

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.r074_low_night_range_opening_breakout import _night_range
from n225m_bt.research.r099_q002_night_efficiency import (
    BASE_HISTORY_COUNT,
    BASE_SCHEDULED_WINDOW,
    BLOCK_LENGTH,
    BOOTSTRAP_REPETITIONS,
    BOOTSTRAP_SEED,
    NightMeasure,
    StateRow,
    mbb_indices,
    percentile_ci,
    route,
    state_rows,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r099-q002-night-efficiency-coverage-repair-20260916-02"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = Path("docs/strategy/108_r099_q002_night_efficiency_coverage_repair.md")
Q001 = ROOT / "results/research/task-r099-q001-night-efficiency-fixed-switch-20260916-01"
CANDIDATES: tuple[dict[str, str], ...] = (
    {"strategy_id": "R078-A", "run": "r078-q001-20260915-cash-first-hour-extreme-fade-01", "daily": "extreme_fade_daily_axis.json", "trades": "extreme_fade_trades.json", "events": "primary_events.json"},
    {"strategy_id": "R079-A", "run": "r079-q001-20260915-cash-first-hour-extreme-continuation-01", "daily": "extreme_continuation_daily_axis.json", "trades": "extreme_continuation_trades.json", "events": "primary_events.json"},
)
PNL_ARTIFACTS = {"bootstrap.json", "primary_results.json", "fixed_diagnostics.json", "bootstrap_common_indices.npy"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for source in files:
        shutil.copy2(ROOT / source, destination / source.name)


def event_dates(path: Path) -> set[str]:
    return {str(row["trade_date"]) for row in read_json(path) if row.get("state") == "E" and row.get("status") == "EXECUTABLE"}


def execution_records(path: Path) -> dict[str, dict[str, Any]]:
    """Read only execution identity fields, never the trade PnL field."""
    return {
        str(row["trade_date"]): {key: row.get(key) for key in ("trade_date", "side", "qty", "entry_signal_ts", "entry_ts", "exit_signal_ts", "exit_ts", "exit_reason")}
        for row in read_json(path)
    }


def reproduce_q001() -> tuple[bool, dict[str, Any], dict[str, set[str]], dict[str, dict[str, dict[str, Any]]]]:
    """Prove Q001 stopped before PnL and replay its identity checks without PnL parsing."""
    required = ["decision.json", "pre_pnl_gate.json", "night_efficiency_state_ledger_before_evaluation_pnl.json", "constituent_freeze_before_evaluation_pnl.json"]
    exists = all((Q001 / name).is_file() for name in required)
    decision = read_json(Q001 / "decision.json") if exists else {}
    pre = read_json(Q001 / "pre_pnl_gate.json") if exists else {}
    ledger = read_json(Q001 / "night_efficiency_state_ledger_before_evaluation_pnl.json") if exists else {}
    frozen = read_json(Q001 / "constituent_freeze_before_evaluation_pnl.json") if exists else {}
    events = ledger.get("events", []) if isinstance(ledger, dict) else []
    reasons = Counter(str(row.get("reason")) for row in events)
    reference_counts = [int(row.get("reference_valid_count", -1)) for row in events]
    expected = {str(item["strategy_id"]): item for item in frozen.get("included", [])}
    actual_events: dict[str, set[str]] = {}
    records: dict[str, dict[str, dict[str, Any]]] = {}
    hash_match = True
    for candidate in CANDIDATES:
        sid = candidate["strategy_id"]
        directory = ROOT / "results/research" / candidate["run"]
        actual = {"daily_sha256": digest(directory / candidate["daily"]), "trades_sha256": digest(directory / candidate["trades"]), "events_sha256": digest(directory / candidate["events"])}
        hash_match = hash_match and sid in expected and all(actual[key] == expected[sid].get(key) for key in actual)
        actual_events[sid] = event_dates(directory / candidate["events"])
        records[sid] = execution_records(directory / candidate["trades"])
    common_events = actual_events.get("R078-A", set()) == actual_events.get("R079-A", set())
    same_trade_dates = set(records.get("R078-A", {})) == set(records.get("R079-A", {})) == actual_events.get("R078-A", set())
    opposite = same_trade_dates and all(
        records["R078-A"][day]["qty"] == records["R079-A"][day]["qty"] == 1
        and all(records["R078-A"][day][field] == records["R079-A"][day][field] for field in ("entry_signal_ts", "entry_ts", "exit_signal_ts", "exit_ts", "exit_reason"))
        and {records["R078-A"][day]["side"], records["R079-A"][day]["side"]} == {"long", "short"}
        for day in records["R078-A"]
    )
    no_pnl_outputs = not any((Q001 / name).exists() for name in PNL_ARTIFACTS)
    report = {
        "stage": "Q001 PnL-free reproduction before Q002 PnL access",
        "q001_required_artifacts_present": exists,
        "q001_decision_inconclusive": decision.get("status") == "INCONCLUSIVE",
        "q001_pnl_result_artifacts_absent": no_pnl_outputs,
        "q001_no_pnl_fields_parsed_by_q002_at_this_stage": True,
        "scheduled_axis": len(events),
        "current_night_unavailable": reasons["CURRENT_NIGHT_UNAVAILABLE"],
        "insufficient_valid_prior_nights": reasons["INSUFFICIENT_VALID_PRIOR_NIGHTS"],
        "q_ready": sum(row.get("state") is not None for row in events),
        "remaining_dates_all_prior_complete_below_100": bool(reference_counts) and all(value < 100 for value in reference_counts if value >= 0),
        "q001_pre_gate_counts_match": pre.get("counts", {}).get("scheduled_axis") == 1131 and pre.get("counts", {}).get("q_ready") == 0,
        "r078_r079_hash_match_q001_freeze": hash_match,
        "same_E_event_dates": common_events and same_trade_dates,
        "same_entry_exit_one_contract_opposite_side": opposite,
        "common_event_count": len(actual_events.get("R078-A", set())) if common_events else None,
    }
    required_true = (
        report["q001_required_artifacts_present"], report["q001_decision_inconclusive"], report["q001_pnl_result_artifacts_absent"],
        report["scheduled_axis"] == 1131, report["current_night_unavailable"] == 265, report["insufficient_valid_prior_nights"] == 866,
        report["q_ready"] == 0, report["remaining_dates_all_prior_complete_below_100"], report["q001_pre_gate_counts_match"],
        report["r078_r079_hash_match_q001_freeze"], report["same_E_event_dates"], report["same_entry_exit_one_contract_opposite_side"],
    )
    report["passed"] = all(required_true)
    return report["passed"], report, actual_events, records


def night_measure(classifier: CalendarClassifier, target: date, bars: list[Bar], isolated: set[tuple[date, Session]]) -> NightMeasure | None:
    value, _, complete = _night_range(classifier, target, bars, isolated=isolated)
    if value is None or complete is None or value <= 0:
        return None
    return NightMeasure(abs(complete[-1].close - complete[0].open) / value, value)


def regime(day: str) -> str:
    return "old" if day <= "2024-11-01" else "new"


def state_ledger(axis: list[str], rows: list[StateRow], measures: list[NightMeasure | None], counts: list[int]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        measure = measures[row.index]
        events.append({"trade_date": axis[row.index], "state": row.state, "v_band": row.v_band, "reason": row.reason, "latest_complete_reference_trade_dates": [axis[index] for index in row.references], "prior_complete_nights_in_180_scheduled_dates": counts[row.index], "efficiency": None if measure is None else measure.efficiency, "range_points": None if measure is None else measure.range_points})
    return events


def selected_trades(axis: list[str], rows: list[StateRow], event_map: dict[str, set[str]], records: dict[str, dict[str, dict[str, Any]]]) -> tuple[list[tuple[StateRow, str, dict[str, Any]]], bool]:
    complete: list[tuple[StateRow, str, dict[str, Any]]] = []
    unresolved = False
    for row in rows:
        sid = route(row.state, "A")
        if sid is None or axis[row.index] not in event_map[sid]:
            continue
        trade = records[sid].get(axis[row.index])
        if trade is None or trade.get("exit_ts") is None or trade.get("exit_reason") is None:
            unresolved = True
        else:
            complete.append((row, sid, trade))
    return complete, unresolved


def delta(rows: list[StateRow], g: NDArray[np.float64], sample: NDArray[np.int64] | None = None) -> float | None:
    indices = np.arange(len(rows), dtype=int) if sample is None else sample
    parts: list[float] = []
    for band in ("LE", "ME", "HE"):
        he = [g[index] for index in indices if rows[int(index)].state == "HE" and rows[int(index)].v_band == band]
        le = [g[index] for index in indices if rows[int(index)].state == "LE" and rows[int(index)].v_band == band]
        if not he or not le:
            return None
        parts.append(fmean(he) - fmean(le))
    return fmean(parts)


def pf(values: list[int]) -> float | None:
    losses = -sum(value for value in values if value < 0)
    return None if losses == 0 else sum(value for value in values if value > 0) / losses


def load_pnl() -> tuple[list[str], dict[str, list[int]], dict[str, dict[str, dict[str, Any]]]]:
    axes: dict[str, list[str]] = {}
    daily: dict[str, list[int]] = {}
    trades: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate in CANDIDATES:
        sid, directory = candidate["strategy_id"], ROOT / "results/research" / candidate["run"]
        raw_daily, raw_trades = read_json(directory / candidate["daily"]), read_json(directory / candidate["trades"])
        axes[sid], daily[sid] = list(raw_daily), [int(value) for value in raw_daily.values()]
        trades[sid] = {str(row["trade_date"]): row for row in raw_trades}
        if any(int(row["net_pnl_jpy"]) != raw_daily[str(row["trade_date"])] for row in raw_trades):
            raise ValueError(f"{sid} immutable trade/daily PnL reconciliation failed")
    axis = axes["R078-A"]
    if len(axis) != 1131 or axes["R079-A"] != axis or axis[-1] != "2025-06-30":
        raise ValueError("immutable R078/R079 daily axes mismatch")
    return axis, daily, trades


def diagnostic(rows: list[StateRow], axis: list[str], daily: dict[str, list[int]], event_map: dict[str, set[str]], trades: dict[str, dict[str, dict[str, Any]]], g: NDArray[np.float64], label: str) -> dict[str, Any]:
    complete, unresolved = selected_trades(axis, rows, event_map, {sid: {day: {key: row.get(key) for key in ("trade_date", "side", "qty", "entry_signal_ts", "entry_ts", "exit_signal_ts", "exit_ts", "exit_reason")} for day, row in source.items()} for sid, source in trades.items()})
    path = [0 if (sid := route(row.state, "A")) is None else daily[sid][row.index] for row in rows]
    interaction = delta(rows, g)
    return {"label": label, "A_net_pnl_jpy": int(sum(path)), "Delta": interaction, "complete_A_trades": len(complete), "unresolved": unresolved, "status": "EXECUTABLE" if interaction is not None and not unresolved else "INSUFFICIENT_SAMPLE_OR_EXECUTION"}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_task_r099_q002_night_efficiency_coverage_repair.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [Path("scripts/run_task_r099_q002_night_efficiency_coverage_repair.py"), Path("src/n225m_bt/research/r099_q002_night_efficiency.py"), Path("tests/test_r099_q002_night_efficiency.py")]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    prereg = {"task_id": "TASK-R099-Q002", "study_id": "R099-Q002", "family_id": "cash_first_hour_extreme_night_efficiency_switch", "run_id": RUN_ID, "status": "FROZEN_BEFORE_EVALUATION_PNL", "preregistration_document": str(PREREG), "preregistration_document_sha256": digest(ROOT / PREREG), "parent": "TASK-R099-Q001 PnL-free coverage failure", "change_scope": "state history only: latest 100 complete nights in strict prior 180 scheduled dates; no current-date use or backfill", "frozen_unchanged": "R078-A/R079-A, e/v, A/C/F/I/N, costs, event, entry, exit, one-contract execution", "bootstrap": {"seed": BOOTSTRAP_SEED, "block_length_trade_dates": BLOCK_LENGTH, "repetitions": BOOTSTRAP_REPETITIONS, "method": "common non-wrapping MBB, tail truncation, linear percentile"}, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED", "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(data_config.gold_root, "development")], "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files}, "config_hashes": {str(path): digest(ROOT / path) for path in config_files}}
    write_json(OUT / "preregistration.json", prereg)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(prereg), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / PREREG, OUT / "documentation_snapshot" / PREREG.name)
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {"pytest": [sys.executable, "-m", "pytest", "tests/test_r099_night_efficiency_switch.py", "tests/test_r099_q002_night_efficiency.py", "tests/test_r075_q001.py", "-q"], "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)], "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r099_q002_night_efficiency.py", str(source_files[0])]}
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        done = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=environment)
        validation[name] = {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
    validation["status"] = "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("pre-execution validation failed")

    reproduced, reproduction, event_map, records = reproduce_q001()
    write_json(OUT / "q001_pnl_free_reproduction_before_evaluation.json", reproduction)
    if not reproduced:
        write_json(OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "Q001_PNL_FREE_REPRODUCTION_FAILED", "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"})
        return

    _, sessions, _, _ = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    dates = [item.trade_date for item in calendar.trading_days() if date(2021, 1, 1) <= item.trade_date <= date(2025, 6, 30)]
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars: dict[date, list[Bar]] = defaultdict(list)
    for bar in view.bars:
        if bar.session is Session.NIGHT:
            bars[bar.trade_date].append(bar)
    measures = [night_measure(classifier, target, bars[target], isolated) for target in dates]
    rows = state_rows(measures)
    axis = [target.isoformat() for target in dates]
    prior_counts = [sum(measure is not None for measure in measures[max(0, index - BASE_SCHEDULED_WINDOW):index]) for index in range(len(measures))]
    events = state_ledger(axis, rows, measures, prior_counts)
    write_json(OUT / "night_efficiency_state_ledger_before_evaluation_pnl.json", {"definition": "complete official NIGHT through 05:29; e=abs(C-O)/(H-L), v=H-L; strict current-excluded prior 180 scheduled dates; latest 100 complete nights only", "availability_jst": "08:45", "quarantine": quarantine_audit, "events": events})
    unavailable_by_year_regime: dict[str, Any] = {}
    for key, predicate in (("year", lambda day: day[:4]), ("regime", regime)):
        grouped: dict[str, Counter[str]] = defaultdict(Counter)
        for row in events:
            grouped[predicate(str(row["trade_date"]))][str(row["reason"])] += 1
        unavailable_by_year_regime[key] = {name: dict(sorted(counts.items())) for name, counts in sorted(grouped.items())}
    distribution: dict[str, Any] = {}
    for key, predicate in (("year", lambda day: day[:4]), ("regime", regime)):
        distribution_groups: dict[str, list[int]] = defaultdict(list)
        for row in events:
            distribution_groups[predicate(str(row["trade_date"]))].append(int(row["prior_complete_nights_in_180_scheduled_dates"]))
        distribution[key] = {name: {"days": len(values), "min": min(values), "max": max(values), "at_least_100": sum(value >= 100 for value in values)} for name, values in sorted(distribution_groups.items())}
    complete, unresolved = selected_trades(axis, rows, event_map, records)
    state_counts = Counter(row.state for row in rows)
    legs = Counter((row.state, sid) for row, sid, _ in complete)
    sides = Counter(str(trade["side"]) for _, _, trade in complete)
    years = Counter(str(trade["trade_date"])[:4] for _, _, trade in complete)
    regimes = Counter(regime(str(trade["trade_date"])) for _, _, trade in complete)
    cross = Counter((str(row.state), str(row.v_band)) for row, _, _ in complete if row.state in {"HE", "LE"} and row.v_band is not None)
    strict_prior = all(all(index < row.index and row.index - index <= BASE_SCHEDULED_WINDOW for index in row.references) and len(row.references) == BASE_HISTORY_COUNT for row in rows if row.state is not None)
    coverage = {"prior_complete_night_count_distribution": distribution, "unavailable_reasons": unavailable_by_year_regime, "state_counts": {"null" if state is None else state: value for state, value in state_counts.items()}, "complete_A_trades": len(complete), "leg_counts": {f"{state}_{sid}": value for (state, sid), value in legs.items()}, "side_counts": dict(sides), "year_counts": dict(years), "regime_counts": dict(regimes), "cross_counts": {f"{state}_{band}": value for (state, band), value in cross.items()}}
    write_json(OUT / "coverage_audit_before_evaluation_pnl.json", coverage)
    pre = {"q001_reproduction": reproduced, "q_ready_at_least_650": sum(row.state is not None for row in rows) >= 650, "HE_LE_each_at_least_180": state_counts["HE"] >= 180 and state_counts["LE"] >= 180, "A_complete_at_least_80": len(complete) >= 80, "HE_R079_LE_R078_each_at_least_25": legs[("HE", "R079-A")] >= 25 and legs[("LE", "R078-A")] >= 25, "A_long_short_each_at_least_25": sides["long"] >= 25 and sides["short"] >= 25, "2022_2024_each_at_least_15": all(years[str(year)] >= 15 for year in range(2022, 2025)), "2025_h1_at_least_8": years["2025"] >= 8, "old_new_at_least_70_8": regimes["old"] >= 70 and regimes["new"] >= 8, "HE_LE_v_cross_common_event_each_at_least_5": all(cross[(state, band)] >= 5 for state in ("HE", "LE") for band in ("HE", "ME", "LE")), "no_future_reference": strict_prior, "no_same_day_availability_selection": True, "no_R004_contamination": all((dates[row.index], Session.NIGHT) not in isolated for row in rows if row.state is not None), "no_unexplained_exclusion": all(row.reason in {"STATE_AVAILABLE", "CURRENT_NIGHT_UNAVAILABLE", "INSUFFICIENT_COMPLETE_PRIOR_NIGHTS", "EFFICIENCY_QUANTILES_DEGENERATE"} for row in rows), "no_unresolved_filled_position": not unresolved, "counts": coverage}
    pre["passed"] = all(value is True for key, value in pre.items() if key not in {"counts", "passed"})
    write_json(OUT / "pre_pnl_gate.json", pre)
    write_json(OUT / "access_ledger.json", {"stage": "Q001 PnL-free reproduction, state-only coverage audit, then conditional immutable PnL evaluation", "split": "development", "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    if not pre["passed"]:
        write_json(OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "PRE_PNL_COVERAGE_GATE_FAILED", "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"})
        return

    pnl_axis, daily, trade_pnl = load_pnl()
    if pnl_axis != axis:
        raise ValueError("state and immutable PnL scheduled axes differ")
    paths = {name: [0 if (sid := route(row.state, name)) is None else daily[sid][row.index] for row in rows] for name in ("A", "C", "F", "I")}
    paths["N"] = [0] * len(axis)
    index = mbb_indices(len(axis))
    np.save(OUT / "bootstrap_common_indices.npy", index)
    arrays = {name: np.asarray(values, dtype=float) for name, values in paths.items()}
    g = np.asarray(daily["R079-A"], dtype=float) - np.asarray(daily["R078-A"], dtype=float)
    delta_samples = [delta(rows, g, sample) for sample in index]
    undefined = sum(value is None for value in delta_samples)
    bootstrap: dict[str, Any] = {"method": "common 20 trade_date non-wrapping MBB, tail truncation, linear percentile", "seed": BOOTSTRAP_SEED, "repetitions": BOOTSTRAP_REPETITIONS, "block_length_trade_dates": BLOCK_LENGTH}
    for name, values in (("A", arrays["A"]), ("A-C", arrays["A"] - arrays["C"]), ("A-F", arrays["A"] - arrays["F"]), ("A-I", arrays["A"] - arrays["I"])):
        bootstrap[name] = {"estimate": float(values.mean()), "ci95_percentile_linear": percentile_ci(values[index].mean(axis=1))}
    bootstrap["Delta_efficiency_incremental_interaction"] = {"estimate": delta(rows, g), "undefined_repetitions": undefined, "ci95_percentile_linear": None if undefined else percentile_ci(np.asarray(delta_samples, dtype=float))}
    write_json(OUT / "bootstrap.json", bootstrap | {"index_file": "bootstrap_common_indices.npy"})
    primary = {"A_net_pnl_jpy": int(sum(paths["A"])), "A_profit_factor": pf(paths["A"]), "A_daily_net_ci": bootstrap["A"]["ci95_percentile_linear"], "A_minus_C_ci": bootstrap["A-C"]["ci95_percentile_linear"], "A_minus_F_ci": bootstrap["A-F"]["ci95_percentile_linear"], "A_minus_I_ci": bootstrap["A-I"]["ci95_percentile_linear"], "Delta": delta(rows, g), "Delta_ci": bootstrap["Delta_efficiency_incremental_interaction"]["ci95_percentile_linear"]}
    primary_and = {"A_net_positive": primary["A_net_pnl_jpy"] > 0, "A_pf_gt_one": primary["A_profit_factor"] is not None and primary["A_profit_factor"] > 1, "A_daily_ci_lower_positive": primary["A_daily_net_ci"][0] > 0, "A_minus_C_ci_lower_positive": primary["A_minus_C_ci"][0] > 0, "A_minus_F_ci_lower_positive": primary["A_minus_F_ci"][0] > 0, "A_minus_I_ci_lower_positive": primary["A_minus_I_ci"][0] > 0, "Delta_ci_lower_positive": primary["Delta_ci"] is not None and primary["Delta_ci"][0] > 0}
    write_json(OUT / "primary_results.json", {"primary": primary, "primary_and": primary_and, "daily_paths_jpy": paths})
    if not all(primary_and.values()):
        write_json(OUT / "decision.json", {"status": "REJECT", "reason": "PRIMARY_AND_FAILED", "decision_ceiling": "INVESTIGATE", "primary_and": primary_and, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "REJECT"})
        return

    profiles = ((25, 75, 180, 100, "q25_q75"), (40, 60, 180, 100, "q40_q60"), (33, 67, 180, 80, "history_80_in_180"), (33, 67, 180, 120, "history_120_in_180"), (33, 67, 150, 100, "history_100_max_150"), (33, 67, 210, 100, "history_100_max_210"))
    diagnostics = {label: diagnostic(state_rows(measures, low=low, high=high, scheduled_window=window, history_count=history), axis, daily, event_map, trade_pnl, g, label) for low, high, window, history, label in profiles}
    base_trade_count = len(complete)
    for label, deduction in (("cost_2tick", 1000), ("cost_3tick", 2000), ("fee_x2", 60)):
        diagnostics[label] = {"label": label, "A_net_pnl_jpy": primary["A_net_pnl_jpy"] - deduction * base_trade_count, "Delta": primary["Delta"], "complete_A_trades": base_trade_count, "unresolved": False, "status": "EXECUTABLE" if primary["Delta"] is not None else "INSUFFICIENT_SAMPLE_OR_EXECUTION"}
    legs_net = {"HE_R079": sum(daily["R079-A"][row.index] for row in rows if row.state == "HE"), "LE_R078": sum(daily["R078-A"][row.index] for row in rows if row.state == "LE")}
    by_year = {year: sum(value for day, value in zip(axis, paths["A"], strict=True) if day.startswith(year)) for year in ("2021", "2022", "2023", "2024", "2025")}
    by_regime = {name: sum(value for day, value in zip(axis, paths["A"], strict=True) if regime(day) == name) for name in ("old", "new")}
    winners = sorted((int(trade_pnl[sid][axis[row.index]]["net_pnl_jpy"]) for row, sid, _ in complete if int(trade_pnl[sid][axis[row.index]]["net_pnl_jpy"]) > 0), reverse=True)[:10]
    robustness = {"all_fixed_sensitivities_A_and_Delta_positive": all(item["status"] == "EXECUTABLE" and item["A_net_pnl_jpy"] > 0 and item["Delta"] is not None and item["Delta"] > 0 for item in diagnostics.values()), "HE_LE_legs_positive": all(value > 0 for value in legs_net.values()), "two_2022_2024_positive": sum(by_year[str(year)] > 0 for year in range(2022, 2025)) >= 2, "2025_h1_positive": by_year["2025"] > 0, "both_regimes_positive": all(value > 0 for value in by_regime.values()), "top10_winners_removed_positive": primary["A_net_pnl_jpy"] - sum(winners) > 0}
    write_json(OUT / "fixed_diagnostics.json", {"diagnostics": diagnostics, "leg_net": legs_net, "by_year": by_year, "by_regime": by_regime, "top10_winners_removed_net": primary["A_net_pnl_jpy"] - sum(winners), "robustness": robustness})
    write_json(OUT / "decision.json", {"status": "INVESTIGATE", "reason": "DEVELOPMENT_REUSE_DECISION_CEILING" if all(robustness.values()) else "ROBUSTNESS_FAILED_AFTER_PRIMARY_PASS", "decision_ceiling": "INVESTIGATE", "primary_and": primary_and, "robustness": robustness, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INVESTIGATE"})


if __name__ == "__main__":
    main()
