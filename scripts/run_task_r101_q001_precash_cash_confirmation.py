"""Execute the frozen Development-only TASK-R101-Q001 once."""

# ruff: noqa: E701, E702

# mypy: disable-error-code=attr-defined
from __future__ import annotations

import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np
from run_r088_q001_cash_open_path_efficiency import (
    digest,
    engine_for,
    orders_fills,
    quarantine,
    snapshot,
    write_json,
)

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r088_cash_open_path_efficiency import scheduled_axis
from n225m_bt.research.r101_precash_cash_confirmation import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    build_events,
    mbb_indices,
    percentile_ci,
    route,
    state_ledger,
)
from n225m_bt.strategies.r088_fixed_signal import R088FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r101-q001-precash-cash-confirmation-20260916-02"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/114_r101_q001_precash_cash_open_confirmation.md")


def known_reason(reason: object) -> bool:
    return str(reason).startswith(("VALID_", "NO_", "R004_", "WINDOW_", "ZERO_", "INSUFFICIENT_", "DEGENERATE_", "EMPTY_", "STATE_", "FIXED_"))


def execute_route(axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Any]], engine: Any, name: str) -> tuple[tuple[Trade, ...], list[int], set[date]]:
    by_date = {date.fromisoformat(cast(str, item["trade_date"])): item for item in events}
    trades: list[Trade] = []
    unknown: set[date] = set()
    for target in axis:
        event = by_date[target]
        choice = route(cast(str, event["event"]), name)
        if choice is None:
            continue
        if event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            continue
        if event["status"] != "EXECUTABLE":
            continue
        base_side = cast(str, event["trade_direction"])
        side = base_side if choice == "c" else ("short" if base_side == "long" else "long")
        result = engine.run(bars[(target, Session.DAY)], R088FixedSignalStrategy(f"r101_{name}_{target.isoformat()}", datetime.fromisoformat(cast(str, event["entry_signal_jst"])), datetime.fromisoformat(cast(str, event["exit_signal_jst"])), side), parameter_hash=canonical_hash({"study": "R101-Q001", "route": name, "event": event}))
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
        trade = result.trades[0]
        if trade.entry_ts.isoformat() != event["entry_open_jst"] or trade.exit_ts.isoformat() != event["exit_open_jst"] or trade.side is not Side(side) or trade.exit_reason is not ExitReason.SIGNAL or trade.qty != 1:
            raise ValueError(f"{name}/{target}: signal/fill contract mismatch")
        trades.append(trade)
    daily = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if daily[trade.trade_date] is None or daily[trade.trade_date] != 0:
            raise ValueError("duplicate or unobservable daily route result")
        daily[trade.trade_date] = trade.net_pnl_jpy
    if unknown:
        return tuple(trades), [], unknown
    return tuple(trades), [cast(int, daily[target]) for target in axis], unknown


def gate(axis: list[date], events: list[dict[str, object]], isolated: set[tuple[date, Session]]) -> dict[str, object]:
    selected = [item for item in events if item["event"] in {"K", "D"}]
    complete_k = [item for item in events if item["event"] == "K" and item["status"] == "EXECUTABLE"]
    labels, years, regimes, signs = Counter(item["event"] for item in events), Counter(item["trade_date"][:4] for item in complete_k), Counter(item["tse_regime"] for item in complete_k), Counter(item["c_direction"] for item in complete_k)
    cells = Counter((item["event"], item.get("w_band"), item["c_direction"]) for item in selected)
    unexplained = [item["trade_date"] for item in events if not known_reason(item.get("reason"))]
    checks: dict[str, object] = {
        "q_ready_at_least_700": sum(bool(item["q_ready"]) for item in events) >= 700,
        "E_at_least_220": labels["K"] + labels["D"] >= 220,
        "K_D_each_at_least_90": labels["K"] >= 90 and labels["D"] >= 90,
        "A_completed_at_least_90": len(complete_k) >= 90,
        "A_c_direction_each_at_least_30": signs["up"] >= 30 and signs["down"] >= 30,
        "2021_initialization_at_least_8": years["2021"] >= 8,
        "2022_2024_each_at_least_18": all(years[str(year)] >= 18 for year in range(2022, 2025)),
        "2025_h1_at_least_8": years["2025"] >= 8,
        "old_new_regime_at_least_75_8": regimes["old"] >= 75 and regimes["new"] >= 8,
        "K_D_w_band_c_direction_common_event_each_at_least_6": all(cells[(event, band, direction)] >= 6 for event in ("K", "D") for band in ("LOW", "HIGH") for direction in ("up", "down")),
        "no_future_reference": all(item["trade_date"] not in item["reference_valid_trade_dates"] and all(day < item["trade_date"] for day in item["reference_valid_trade_dates"]) for item in events),
        "no_same_day_event_fill_exit_selection": all(item.get("selection_fixed_at_jst") is None or datetime.fromisoformat(cast(str, item["selection_fixed_at_jst"])).time() == datetime.fromisoformat(cast(str, item["second_window_end_jst"])).time() for item in events),
        "scheduled_axis_matches_events": [item["trade_date"] for item in events] == [day.isoformat() for day in axis],
        "no_R004_contamination": all((date.fromisoformat(cast(str, item["trade_date"])), Session.DAY) not in isolated for item in selected),
        "unexplained_exclusions_equal_zero": not unexplained,
        "unresolved_filled_positions_equal_zero": not any(item["status"] == "ENTRY_FILLED_EXIT_UNKNOWN" for item in events),
    }
    checks["counts"] = {"scheduled_axis": len(axis), "q_ready": sum(bool(item["q_ready"]) for item in events), "events": dict(labels), "A_completed": len(complete_k), "c_directions": dict(signs), "years": dict(years), "regimes": dict(regimes), "common_event_cells": {"|".join(key): value for key, value in cells.items()}, "unexplained": unexplained}
    checks["passed"] = all(value is True for key, value in checks.items() if key not in {"counts", "passed"})
    return checks


def causality(axis: list[date], events: list[dict[str, object]]) -> dict[str, object]:
    executable = [item for item in events if item["status"] == "EXECUTABLE"]
    checks = {
        "axis_complete_unique_trade_date": len(events) == len(axis) == len({item["trade_date"] for item in events}),
        "exact_windows": all(datetime.fromisoformat(cast(str, item["first_window_start_jst"])).time() == time(8, 45) and datetime.fromisoformat(cast(str, item["first_window_end_jst"])).time() == time(8, 59) and item["first_scheduled_bar_count"] == 15 and item["second_scheduled_bar_count"] in {10, 15, 20} for item in events),
        "strict_prior_current_excluded_no_backfill": all(cast(list[str], item["reference_scheduled_trade_dates"]) == [day.isoformat() for day in axis[max(0, index - len(cast(list[str], item["reference_scheduled_trade_dates"]))):index]] and item["trade_date"] not in item["reference_valid_trade_dates"] for index, item in enumerate(events)),
        "selection_then_next_eligible_entry_and_fixed_exit": all(datetime.fromisoformat(cast(str, item["entry_open_jst"])) > datetime.fromisoformat(cast(str, item["entry_signal_jst"])) and datetime.fromisoformat(cast(str, item["exit_open_jst"])) > datetime.fromisoformat(cast(str, item["exit_signal_jst"])) for item in executable),
        "w_uses_strict_prior_E_distribution": all(not item["q_ready"] or cast(int, item["strict_prior_e_count"]) > 0 for item in events),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def delta_samples(events: list[dict[str, object]], k_daily: np.ndarray, d_daily: np.ndarray, index: np.ndarray) -> tuple[np.ndarray, int]:
    samples, empty = np.full(len(index), np.nan), 0
    for iteration, draw in enumerate(index):
        parts: list[float] = []
        for band in ("LOW", "HIGH"):
            for direction in ("up", "down"):
                k = [position for position, original in enumerate(draw) if events[original]["event"] == "K" and events[original].get("w_band") == band and events[original]["c_direction"] == direction]
                d = [position for position, original in enumerate(draw) if events[original]["event"] == "D" and events[original].get("w_band") == band and events[original]["c_direction"] == direction]
                if not k or not d:
                    empty += 1
                    break
                parts.append(float(k_daily[draw[k]].sum() / len(k) - d_daily[draw[d]].sum() / len(d)))
            else:
                continue
            break
        else:
            samples[iteration] = sum(parts) / 4
    return samples, empty


def profile(axis: list[date], bars: dict[tuple[date, Session], list[Any]], instrument: Any, baseline: Any, classifier: CalendarClassifier, ledger: list[dict[str, object]], *, ticks: int = 1, fee: int = 30, exit_time: time = time(10, 30), entry_delay: int = 0) -> tuple[list[dict[str, object]], dict[str, tuple[Trade, ...]], dict[str, list[int]]]:
    events = build_events(ledger, bars, exit_time=exit_time, entry_delay_bars=entry_delay)
    trades, daily = {}, {}
    for name in ("A", "U", "R", "D_control"):
        result, path, unknown = execute_route(axis, events, bars, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name)
        if unknown:
            raise ValueError("unresolved position during registered profile")
        trades[name], daily[name] = result, path
    return events, trades, daily


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    sources = [Path("scripts/run_task_r101_q001_precash_cash_confirmation.py"), Path("src/n225m_bt/research/r101_precash_cash_confirmation.py"), Path("src/n225m_bt/strategies/r088_fixed_signal.py"), Path("tests/test_r101_precash_cash_confirmation.py")]
    configs = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    prereg = {"task_id": "TASK-R101-Q001", "family_id": "precash_cash_open_two_stage_confirmation", "study_id": "R101-Q001", "spec_version": "v1", "run_id": RUN_ID, "status": "FROZEN_BEFORE_EVALUATION_PNL", "preregistration_document": str(DOC), "preregistration_document_sha256": digest(ROOT / DOC), "prior_information_seen": True, "learned_from": ["R100"], "duplicate_review": "Before PnL R001-R100: R024 uses 09:00-09:04 unthresholded confirmation and 09:05-09:30; no valid test has two independent 15m p/c q50 filters, 09:14 lock, K/D comparison, and 09:15-10:30 execution.", "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10000, "method": "common non-wrapping MBB, tail truncation, linear percentile"}, "decision_ceiling": "INVESTIGATE", "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(data_config.gold_root, "development")], "implementation_hashes": {str(path): digest(ROOT / path) for path in sources}, "config_hashes": {str(path): digest(ROOT / path) for path in configs}, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "preregistration.json", prereg)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(prereg), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot(OUT / "source_snapshot", sources); snapshot(OUT / "config_snapshot", configs)
    (OUT / "documentation_snapshot").mkdir(); shutil.copy2(ROOT / DOC, OUT / "documentation_snapshot" / DOC.name)
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {"pytest": [sys.executable, "-m", "pytest", "tests/test_r101_precash_cash_confirmation.py", "tests/test_execution.py", "-q"], "ruff": [sys.executable, "-m", "ruff", "check", *map(str, sources)], "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r101_precash_cash_confirmation.py"], "py_compile": [sys.executable, "-m", "py_compile", *map(str, sources)]}
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        done = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env); validation[name] = {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
    validation["status"] = "PASS" if all(cast(dict[str, int], item)["returncode"] == 0 for item in validation.values()) else "FAIL"; write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}); raise ValueError("R101 validation failed")
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml"); classifier, axis = CalendarClassifier(sessions, calendar), scheduled_axis(calendar)
    development = load_split(data_config.gold_root, "development"); view, quarantine_audit, isolated = quarantine(development)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in set(axis) and bar.session is Session.DAY: bars[(bar.trade_date, bar.session)].append(bar)
    ledger = state_ledger(classifier, axis, bars, isolated); events = build_events(ledger, bars); before_gate, before_causality = gate(axis, events, isolated), causality(axis, events)
    write_json(OUT / "duplicate_review_before_evaluation_pnl.json", {"reviewed_range": "R001-R100", "related_not_equivalent": {"R024": "5m unthresholded cash confirmation, 09:05-09:30", "R031_R042": "night-including confirmation", "R057_R096": "gap/range acceptance", "R100": "09:00-11:29 state"}, "conclusion": "NO_MATERIALLY_EQUIVALENT_VALID_TEST; POST_HOC_DEVELOPMENT_REUSE; CEILING_INVESTIGATE"})
    write_json(OUT / "two_stage_ledger_before_evaluation_pnl.json", {"quarantine": quarantine_audit, "ledger": ledger}); write_json(OUT / "primary_events_before_evaluation_pnl.json", events); write_json(OUT / "pre_pnl_gate.json", before_gate); write_json(OUT / "causality_audit_before_pnl.json", before_causality); write_json(OUT / "access_ledger.json", {"stage": "state/event availability and causality audit before PnL", "split": "development", "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    if not bool(before_gate["passed"]) or not bool(before_causality["passed"]):
        write_json(OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "PRE_PNL_GATE_OR_CAUSALITY_AUDIT_FAILED", "gate": before_gate, "causality": before_causality}); write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"}); return
    events, trades, daily = profile(axis, bars, instrument, baseline, classifier, ledger)
    for name, values in trades.items():
        write_json(OUT / f"primary_{name}_orders_fills.json", orders_fills(values)); write_json(OUT / f"primary_{name}_trades.json", [asdict(item) for item in values]); write_json(OUT / f"primary_{name}_daily_axis.json", dict(zip((day.isoformat() for day in axis), daily[name], strict=True)))
    arrays = {name: np.asarray(values, dtype=float) for name, values in daily.items()}; index = mbb_indices(len(axis)); np.save(OUT / "bootstrap_common_indices.npy", index)
    delta, empty = delta_samples(events, arrays["A"], arrays["D_control"], index)
    a_mean, u_mean = float(np.mean([trade.net_pnl_jpy for trade in trades["A"]])), float(np.mean([trade.net_pnl_jpy for trade in trades["U"]]))
    diff_samples = np.asarray([float(arrays["A"][draw][arrays["A"][draw] != 0].mean() - arrays["U"][draw][arrays["U"][draw] != 0].mean()) if np.any(arrays["A"][draw] != 0) and np.any(arrays["U"][draw] != 0) else np.nan for draw in index])
    boot = {"A_daily_net": {"estimate": float(arrays["A"].mean()), "ci95_percentile_linear": percentile_ci(arrays["A"][index].mean(axis=1))}, "A_minus_R_paired_daily_net": {"estimate": float((arrays["A"] - arrays["R"]).mean()), "ci95_percentile_linear": percentile_ci((arrays["A"][index] - arrays["R"][index]).mean(axis=1))}, "A_trade_net_minus_U_E_trade_net": {"estimate": a_mean-u_mean, "ci95_percentile_linear": percentile_ci(cast(np.ndarray[Any, np.dtype[np.float64]], diff_samples))}, "Delta_K_minus_D_equal_weighted": {"estimate": None, "ci95_percentile_linear": None if empty else percentile_ci(delta), "empty_stratum_resamples": empty}, "index_file": "bootstrap_common_indices.npy"}; write_json(OUT / "bootstrap.json", boot)
    metrics = ledger_metrics(trades["A"]); lower = {key: cast(dict[str, list[float] | None], value)["ci95_percentile_linear"] for key, value in boot.items() if isinstance(value, dict)}
    primary_checks = {"A_net_positive": cast(int, metrics["net_pnl_jpy"]) > 0, "A_PF_gt_1": cast(float | None, metrics["profit_factor"]) is not None and cast(float, metrics["profit_factor"]) > 1, "A_daily_CI_lower_gt_0": cast(list[float], lower["A_daily_net"])[0] > 0, "A_minus_R_CI_lower_gt_0": cast(list[float], lower["A_minus_R_paired_daily_net"])[0] > 0, "A_trade_minus_U_CI_lower_gt_0": cast(list[float], lower["A_trade_net_minus_U_E_trade_net"])[0] > 0, "Delta_CI_lower_gt_0": lower["Delta_K_minus_D_equal_weighted"] is not None and cast(list[float], lower["Delta_K_minus_D_equal_weighted"])[0] > 0}
    write_json(OUT / "primary_results.json", {"A_metrics": metrics, "A_concentration": concentration(trades["A"], axis), "route_metrics": {name: ledger_metrics(value) for name, value in trades.items()}, "primary_checks": primary_checks})
    profiles = {"q40": {"percentile": 40}, "q60": {"percentile": 60}, "second_0909": {"second_end": time(9, 9)}, "second_0919": {"second_end": time(9, 19)}, "prior60": {"lookback": 60, "min_valid": 50}, "prior240": {"lookback": 240, "min_valid": 200}, "entry_plus_1": {"entry_delay": 1}, "exit_1000": {"exit_time": time(10)}, "exit_1100": {"exit_time": time(11)}, "cost_2tick": {"ticks": 2}, "cost_3tick": {"ticks": 3}, "fee_x2": {"fee": 60}}
    sensitivity: dict[str, object] = {}
    for name, setting in profiles.items():
        alternate_ledger = state_ledger(classifier, axis, bars, isolated, lookback=cast(int, setting.get("lookback", 120)), min_valid=cast(int, setting.get("min_valid", 100)), percentile=cast(int, setting.get("percentile", 50)), second_end=cast(time, setting.get("second_end", time(9, 14))))
        alternate_events, alternate_trades, alternate_daily = profile(axis, bars, instrument, baseline, classifier, alternate_ledger, ticks=cast(int, setting.get("ticks", 1)), fee=cast(int, setting.get("fee", 30)), exit_time=cast(time, setting.get("exit_time", time(10, 30))), entry_delay=cast(int, setting.get("entry_delay", 0)))
        alternate_index = index
        alternate_delta, alternate_empty = delta_samples(alternate_events, np.asarray(alternate_daily["A"], dtype=float), np.asarray(alternate_daily["D_control"], dtype=float), alternate_index)
        sensitivity[name] = {"settings": {key: str(value) for key, value in setting.items()}, "A_metrics": ledger_metrics(alternate_trades["A"]), "Delta_estimate": None if alternate_empty else float(np.mean(alternate_delta)), "Delta_ci95": None if alternate_empty else percentile_ci(alternate_delta), "event_counts": dict(Counter(item["event"] for item in alternate_events))}
    write_json(OUT / "sensitivities.json", sensitivity)
    decision = "INVESTIGATE" if all(primary_checks.values()) else "REJECT"
    write_json(OUT / "decision.json", {"status": decision, "reason": "PRIMARY_AND_PASSED_WITH_HARD_CEILING" if decision == "INVESTIGATE" else "PRIMARY_AND_FAILED", "primary_checks": primary_checks, "ceiling": "INVESTIGATE"}); write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": decision})


if __name__ == "__main__": main()
