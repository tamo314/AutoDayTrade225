"""Execute the frozen Development-only TASK-R102-Q001 once."""

# ruff: noqa: E701, E702, I001
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
from run_r088_q001_cash_open_path_efficiency import digest, engine_for, orders_fills, quarantine, snapshot, write_json

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r088_cash_open_path_efficiency import scheduled_axis
from n225m_bt.research.r102_opening_range_false_break import (
    DEVELOPMENT_END, DEVELOPMENT_START, MBB_SEED, build_events, mbb_indices, percentile_ci,
    route, state_ledger,
)
from n225m_bt.strategies.r088_fixed_signal import R088FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r102-q001-opening-range-false-break-reentry-20260916-01"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/116_r102_q001_opening_range_false_break_reentry.md")


def known_reason(reason: object) -> bool:
    return str(reason).startswith(("VALID_", "NO_", "R004_", "WINDOW_", "NONPOSITIVE_", "BOTH_", "INSUFFICIENT_", "STATE_", "FIXED_"))


def execute_route(axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Any]], engine: Any, name: str) -> tuple[tuple[Trade, ...], list[int], set[date]]:
    by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
    trades: list[Trade] = []; unknown: set[date] = set()
    for target in axis:
        event = by_date[target]; choice = route(cast(str | None, event.get("classification")), name)
        if choice is None: continue
        if event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN": unknown.add(target); continue
        if event["status"] != "EXECUTABLE": continue
        b = cast(int, event["b"])
        side = "long" if (b > 0) == (choice == "break") else "short"
        result = engine.run(bars[(target, Session.DAY)], R088FixedSignalStrategy(f"r102_{name}_{target.isoformat()}", datetime.fromisoformat(cast(str, event["entry_signal_jst"])), datetime.fromisoformat(cast(str, event["exit_signal_jst"])), side), parameter_hash=canonical_hash({"study": "R102-Q001", "route": name, "event": event}))
        if result.canceled_orders or len(result.trades) != 1: raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
        trade = result.trades[0]
        if trade.entry_ts.isoformat() != event["entry_open_jst"] or trade.exit_ts.isoformat() != event["exit_open_jst"] or trade.side is not Side(side) or trade.exit_reason is not ExitReason.SIGNAL or trade.qty != 1:
            raise ValueError(f"{name}/{target}: signal/fill contract mismatch")
        trades.append(trade)
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if daily[trade.trade_date] is None or daily[trade.trade_date] != 0: raise ValueError("duplicate or unobservable daily result")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return tuple(trades), ([] if unknown else [cast(int, daily[target]) for target in axis]), unknown


def gate(axis: list[date], events: list[dict[str, object]], isolated: set[tuple[date, Session]]) -> dict[str, object]:
    uni = [row for row in events if row.get("event") == "U"]
    q, t = [row for row in uni if row.get("classification") == "Q"], [row for row in uni if row.get("classification") == "T"]
    complete_q = [row for row in q if row["status"] == "EXECUTABLE"]
    years, regimes, signs = Counter(row["trade_date"][:4] for row in complete_q), Counter(row["tse_regime"] for row in complete_q), Counter(row["b_direction"] for row in complete_q)
    cells = Counter((cast(str, row["classification"]), cast(str | None, row.get("x_band")), cast(str, row["b_direction"])) for row in uni if row.get("classification") in {"Q", "T"} and row.get("q_ready"))
    unexplained = [row["trade_date"] for row in events if not known_reason(row.get("reason"))]
    checks: dict[str, object] = {
        "q_ready_at_least_700": sum(bool(row.get("q_ready")) for row in events) >= 700,
        "unidirectional_events_at_least_250": len(uni) >= 250,
        "Q_T_each_at_least_90": len(q) >= 90 and len(t) >= 90,
        "S_completed_at_least_90": len(complete_q) >= 90,
        "Q_b_direction_each_at_least_30": signs["up"] >= 30 and signs["down"] >= 30,
        "2021_initialization_at_least_8": years["2021"] >= 8,
        "2022_2024_each_at_least_18": all(years[str(year)] >= 18 for year in range(2022, 2025)),
        "2025_h1_at_least_8": years["2025"] >= 8,
        "old_new_regime_at_least_75_8": regimes["old"] >= 75 and regimes["new"] >= 8,
        "Q_T_x_band_b_direction_common_event_each_at_least_6": all(cells[(label, band, direction)] >= 6 for label in ("Q", "T") for band in ("LOW", "HIGH") for direction in ("up", "down")),
        "no_future_reference": all(row["trade_date"] not in row["reference_unidirectional_trade_dates"] and all(day < row["trade_date"] for day in row["reference_unidirectional_trade_dates"]) for row in events),
        "no_same_day_event_fill_exit_selection": all(row.get("selection_fixed_at_jst") is None or datetime.fromisoformat(cast(str, row["selection_fixed_at_jst"])).time() == datetime.fromisoformat(cast(str, row["confirmation_end_jst"])).time() for row in events),
        "scheduled_axis_matches_events": [row["trade_date"] for row in events] == [day.isoformat() for day in axis],
        "no_R004_contamination": all((date.fromisoformat(cast(str, row["trade_date"])), Session.DAY) not in isolated for row in uni),
        "unexplained_exclusions_equal_zero": not unexplained,
        "unresolved_filled_positions_equal_zero": not any(row["status"] == "ENTRY_FILLED_EXIT_UNKNOWN" for row in uni),
    }
    checks["counts"] = {"scheduled_axis": len(axis), "q_ready": sum(bool(row.get("q_ready")) for row in events), "unidirectional": len(uni), "Q": len(q), "T": len(t), "Z": sum(row.get("classification") == "Z" for row in uni), "S_completed": len(complete_q), "b_directions": dict(signs), "years": dict(years), "regimes": dict(regimes), "common_event_cells": {"|".join(key): value for key, value in cells.items()}, "unexplained": unexplained}
    checks["passed"] = all(value is True for key, value in checks.items() if key not in {"counts", "passed"})
    return checks


def causality(axis: list[date], events: list[dict[str, object]]) -> dict[str, object]:
    executable = [row for row in events if row["status"] == "EXECUTABLE"]
    checks = {
        "axis_complete_unique_trade_date": len(events) == len(axis) == len({row["trade_date"] for row in events}),
        "primary_windows_exact": all(datetime.fromisoformat(cast(str, row["opening_start_jst"])).time() == time(9) and datetime.fromisoformat(cast(str, row["opening_end_jst"])).time() == time(9, 14) and datetime.fromisoformat(cast(str, row["confirmation_start_jst"])).time() == time(9, 15) and datetime.fromisoformat(cast(str, row["confirmation_end_jst"])).time() == time(9, 29) for row in events if row.get("observation_valid")),
        "strict_prior_current_excluded_no_backfill": all(cast(list[str], row["reference_scheduled_trade_dates"]) == [day.isoformat() for day in axis[max(0, index - len(cast(list[str], row["reference_scheduled_trade_dates"]))):index]] and row["trade_date"] not in row["reference_unidirectional_trade_dates"] for index, row in enumerate(events)),
        "selection_then_next_eligible_entry_and_fixed_exit": all(datetime.fromisoformat(cast(str, row["entry_open_jst"])) > datetime.fromisoformat(cast(str, row["entry_signal_jst"])) and datetime.fromisoformat(cast(str, row["exit_open_jst"])) > datetime.fromisoformat(cast(str, row["exit_signal_jst"])) for row in executable),
        "x_band_uses_strict_prior_unidirectional_distribution": all(not row.get("q_ready") or cast(int, row["reference_unidirectional_count"]) >= 50 for row in events),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def selected_mean_samples(events: list[dict[str, object]], values: np.ndarray, index: np.ndarray, classifications: set[str]) -> np.ndarray:
    result = np.full(len(index), np.nan)
    for iteration, draw in enumerate(index):
        selected = [original for original in draw if events[original].get("classification") in classifications and events[original].get("event") == "U"]
        if selected: result[iteration] = float(values[selected].sum() / len(selected))
    return result


def delta_samples(events: list[dict[str, object]], s_daily: np.ndarray, t_daily: np.ndarray, index: np.ndarray) -> tuple[np.ndarray, int]:
    samples, empty = np.full(len(index), np.nan), 0
    for iteration, draw in enumerate(index):
        parts: list[float] = []
        for band in ("LOW", "HIGH"):
            for direction in ("up", "down"):
                q = [original for original in draw if events[original].get("classification") == "Q" and events[original].get("x_band") == band and events[original].get("b_direction") == direction]
                t = [original for original in draw if events[original].get("classification") == "T" and events[original].get("x_band") == band and events[original].get("b_direction") == direction]
                if not q or not t: empty += 1; break
                parts.append(float(s_daily[q].sum() / len(q) - t_daily[t].sum() / len(t)))
            else: continue
            break
        else: samples[iteration] = sum(parts) / 4
    return samples, empty


def profile(axis: list[date], bars: dict[tuple[date, Session], list[Any]], instrument: Any, baseline: Any, classifier: CalendarClassifier, ledger: list[dict[str, object]], *, ticks: int = 1, fee: int = 30, entry_delay: int = 0, exit_time: time = time(10, 30)) -> tuple[list[dict[str, object]], dict[str, tuple[Trade, ...]], dict[str, list[int]]]:
    events = build_events(ledger, bars, entry_delay_bars=entry_delay, exit_time=exit_time); trades: dict[str, tuple[Trade, ...]] = {}; daily: dict[str, list[int]] = {}
    for name in ("S", "B", "U", "T"):
        result, path, unknown = execute_route(axis, events, bars, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name)
        if unknown: raise ValueError("unresolved position during registered profile")
        trades[name], daily[name] = result, path
    return events, trades, daily


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_task_r102_q001_opening_range_false_break_reentry.py')

    if OUT.exists(): raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    sources = [Path("scripts/run_task_r102_q001_opening_range_false_break_reentry.py"), Path("src/n225m_bt/research/r102_opening_range_false_break.py"), Path("src/n225m_bt/strategies/r088_fixed_signal.py"), Path("tests/test_r102_opening_range_false_break.py")]
    configs = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    prereg = {"task_id": "TASK-R102-Q001", "family_id": "opening_range_false_break_reentry", "study_id": "R102-Q001", "spec_version": "v1", "run_id": RUN_ID, "status": "FROZEN_BEFORE_EVALUATION_PNL", "preregistration_document": str(DOC), "preregistration_document_sha256": digest(ROOT / DOC), "prior_information_seen": True, "learned_from": ["R100", "R101"], "duplicate_review": "Before PnL R001-R101: R054 is 30m range then 31--90m close break/b+5/30m hold; R076 breaks 30m range after 09:30 and re-enters later for a 14:30 exit; R096 is 60m acceptance/afternoon. No valid test has 09:00--09:14 H/L, 09:15--09:29 high/low unique break, 09:29 close Q/T/Z, and 09:30--10:30 reverse rule.", "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10000, "method": "common non-wrapping MBB, tail truncation, linear percentile"}, "decision_ceiling": "INVESTIGATE", "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(data_config.gold_root, "development")], "implementation_hashes": {str(path): digest(ROOT / path) for path in sources}, "config_hashes": {str(path): digest(ROOT / path) for path in configs}, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "preregistration.json", prereg); write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(prereg), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"}); snapshot(OUT / "source_snapshot", sources); snapshot(OUT / "config_snapshot", configs); (OUT / "documentation_snapshot").mkdir(); shutil.copy2(ROOT / DOC, OUT / "documentation_snapshot" / DOC.name)
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}; commands = {"pytest": [sys.executable, "-m", "pytest", "tests/test_r102_opening_range_false_break.py", "tests/test_execution.py", "-q"], "ruff": [sys.executable, "-m", "ruff", "check", *map(str, sources)], "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r102_opening_range_false_break.py"], "py_compile": [sys.executable, "-m", "py_compile", *map(str, sources)]}
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        done = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env); validation[name] = {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
    validation["status"] = "PASS" if all(cast(dict[str, int], item)["returncode"] == 0 for item in validation.values()) else "FAIL"; write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS": write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}); raise ValueError("R102 validation failed")
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config"); calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml"); classifier, axis = CalendarClassifier(sessions, calendar), scheduled_axis(calendar); development = load_split(data_config.gold_root, "development"); view, quarantine_audit, isolated = quarantine(development)
    axis_set = set(axis); bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in axis_set and bar.session is Session.DAY: bars[(bar.trade_date, bar.session)].append(bar)
    tick_size = float(instrument.instrument.tick_size); ledger = state_ledger(classifier, axis, bars, isolated, tick_size=tick_size); events = build_events(ledger, bars); before_gate, before_causality = gate(axis, events, isolated), causality(axis, events)
    write_json(OUT / "duplicate_review_before_evaluation_pnl.json", {"reviewed_range": "R001-R101", "related_not_equivalent": {"R054": "30m range / later first close breakout / b+5 / 30m hold", "R076": "30m range / post-0930 close break and return / 1430 exit", "R096": "60m range acceptance / afternoon", "R100_R101": "efficiency or two-stage directional states"}, "conclusion": "NO_MATERIALLY_EQUIVALENT_VALID_TEST; POST_HOC_DEVELOPMENT_REUSE; CEILING_INVESTIGATE"}); write_json(OUT / "opening_range_ledger_before_evaluation_pnl.json", {"quarantine": quarantine_audit, "ledger": ledger}); write_json(OUT / "primary_events_before_evaluation_pnl.json", events); write_json(OUT / "pre_pnl_gate.json", before_gate); write_json(OUT / "causality_audit_before_pnl.json", before_causality); write_json(OUT / "access_ledger.json", {"stage": "state/event availability and causality audit before PnL", "split": "development", "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    if not bool(before_gate["passed"]) or not bool(before_causality["passed"]): write_json(OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "PRE_PNL_GATE_OR_CAUSALITY_AUDIT_FAILED", "gate": before_gate, "causality": before_causality}); write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"}); return
    events, trades, daily = profile(axis, bars, instrument, baseline, classifier, ledger)
    for name, values in trades.items(): write_json(OUT / f"primary_{name}_orders_fills.json", orders_fills(values)); write_json(OUT / f"primary_{name}_trades.json", [asdict(item) for item in values]); write_json(OUT / f"primary_{name}_daily_axis.json", dict(zip((day.isoformat() for day in axis), daily[name], strict=True)))
    arrays = {name: np.asarray(values, dtype=float) for name, values in daily.items()}; index = mbb_indices(len(axis)); np.save(OUT / "bootstrap_common_indices.npy", index)
    q_mean, u_mean = selected_mean_samples(events, arrays["S"], index, {"Q"}), selected_mean_samples(events, arrays["U"], index, {"Q", "T", "Z"}); delta, empty = delta_samples(events, arrays["S"], arrays["T"], index)
    q_events, u_events = [i for i, row in enumerate(events) if row.get("classification") == "Q"], [i for i, row in enumerate(events) if row.get("event") == "U"]
    boot = {"S_daily_net": {"estimate": float(arrays["S"].mean()), "ci95_percentile_linear": percentile_ci(arrays["S"][index].mean(axis=1))}, "S_minus_B_paired_daily_net": {"estimate": float((arrays["S"] - arrays["B"]).mean()), "ci95_percentile_linear": percentile_ci((arrays["S"][index] - arrays["B"][index]).mean(axis=1))}, "S_Q_trade_net_minus_U_unidirectional_event_net": {"estimate": float(arrays["S"][q_events].sum() / len(q_events) - arrays["U"][u_events].sum() / len(u_events)), "ci95_percentile_linear": percentile_ci(q_mean - u_mean)}, "Delta_Q_minus_T_equal_weighted": {"estimate": None if empty else float(np.mean(delta)), "ci95_percentile_linear": None if empty else percentile_ci(delta), "empty_stratum_resamples": empty}, "index_file": "bootstrap_common_indices.npy"}; write_json(OUT / "bootstrap.json", boot)
    metrics = ledger_metrics(trades["S"]); lower = {key: cast(dict[str, list[float] | None], value)["ci95_percentile_linear"] for key, value in boot.items() if isinstance(value, dict)}; checks = {"S_net_positive": cast(int, metrics["net_pnl_jpy"]) > 0, "S_PF_gt_1": cast(float | None, metrics["profit_factor"]) is not None and cast(float, metrics["profit_factor"]) > 1, "S_daily_CI_lower_gt_0": cast(list[float], lower["S_daily_net"])[0] > 0, "S_minus_B_CI_lower_gt_0": cast(list[float], lower["S_minus_B_paired_daily_net"])[0] > 0, "S_Q_trade_minus_U_event_CI_lower_gt_0": cast(list[float], lower["S_Q_trade_net_minus_U_unidirectional_event_net"])[0] > 0, "Delta_CI_lower_gt_0": lower["Delta_Q_minus_T_equal_weighted"] is not None and cast(list[float], lower["Delta_Q_minus_T_equal_weighted"])[0] > 0}; write_json(OUT / "primary_results.json", {"S_metrics": metrics, "S_concentration": concentration(trades["S"], axis), "route_metrics": {name: ledger_metrics(value) for name, value in trades.items()}, "primary_checks": checks})
    profiles: dict[str, dict[str, object]] = {"threshold_2tick": {"threshold_ticks": 2}, "threshold_max_1tick_or_10pct_w": {"relative_threshold": True}, "opening_0900_0909": {"opening_end": time(9, 9)}, "opening_0900_0919": {"opening_end": time(9, 19)}, "confirmation_10m": {"confirmation_minutes": 10}, "confirmation_20m": {"confirmation_minutes": 20}, "entry_plus_1": {"entry_delay": 1}, "exit_1000": {"exit_time": time(10)}, "exit_1100": {"exit_time": time(11)}, "cost_2tick": {"ticks": 2}, "cost_3tick": {"ticks": 3}, "fee_x2": {"fee": 60}}
    sensitivity: dict[str, object] = {}
    for name, setting in profiles.items():
        alternate_ledger = state_ledger(classifier, axis, bars, isolated, opening_end=cast(time, setting.get("opening_end", time(9, 14))), confirmation_minutes=cast(int, setting.get("confirmation_minutes", 15)), threshold_ticks=cast(int, setting.get("threshold_ticks", 1)), relative_threshold=cast(bool, setting.get("relative_threshold", False)), tick_size=tick_size)
        alternate_events, alternate_trades, alternate_daily = profile(axis, bars, instrument, baseline, classifier, alternate_ledger, ticks=cast(int, setting.get("ticks", 1)), fee=cast(int, setting.get("fee", 30)), entry_delay=cast(int, setting.get("entry_delay", 0)), exit_time=cast(time, setting.get("exit_time", time(10, 30))))
        alternate_delta, alternate_empty = delta_samples(alternate_events, np.asarray(alternate_daily["S"], dtype=float), np.asarray(alternate_daily["T"], dtype=float), index)
        sensitivity[name] = {"settings": {key: str(value) for key, value in setting.items()}, "S_metrics": ledger_metrics(alternate_trades["S"]), "Delta_estimate": None if alternate_empty else float(np.mean(alternate_delta)), "Delta_ci95": None if alternate_empty else percentile_ci(alternate_delta), "event_counts": dict(Counter(row.get("classification", "NONE") for row in alternate_events))}
    write_json(OUT / "sensitivities.json", sensitivity)
    decision = "INVESTIGATE" if all(checks.values()) else "REJECT"; write_json(OUT / "decision.json", {"status": decision, "reason": "PRIMARY_AND_PASSED_WITH_HARD_CEILING" if decision == "INVESTIGATE" else "PRIMARY_AND_FAILED", "primary_checks": checks, "ceiling": "INVESTIGATE"}); write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": decision})


if __name__ == "__main__": main()
