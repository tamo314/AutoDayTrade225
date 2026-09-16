"""Execute the one-time PnL-blind definition repair for TASK-R100-Q002."""

# mypy: disable-error-code=attr-defined
from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
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
from run_task_r100_q001_morning_efficiency_fixed_switch import (
    delta_samples,
)

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import ledger_metrics
from n225m_bt.research.r088_cash_open_path_efficiency import scheduled_axis
from n225m_bt.research.r100_morning_efficiency_switch import (
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
RUN_ID = "task-r100-q002-morning-range-efficiency-switch-20260916-03"
OUT = ROOT / "results" / "research" / RUN_ID
Q001 = ROOT / "results" / "research" / "task-r100-q001-morning-efficiency-fixed-switch-20260916-01"
DOC = Path("docs/strategy/112_r100_q002_morning_range_efficiency_switch.md")


def q001_pnl_free_reproduction() -> dict[str, object]:
    """Reconstruct only the recorded Q001 S2 facts from PnL-free artifacts."""
    gate = json.loads((Q001 / "pre_pnl_gate.json").read_text(encoding="utf-8"))
    causal = json.loads((Q001 / "causality_audit_before_pnl.json").read_text(encoding="utf-8"))
    states = json.loads((Q001 / "morning_state_ledger_before_evaluation_pnl.json").read_text(encoding="utf-8"))["ledger"]
    events = json.loads((Q001 / "primary_events_before_evaluation_pnl.json").read_text(encoding="utf-8"))
    economic_result_files = (
        "bootstrap",
        "fixed_diagnostics",
        "primary_results",
        "orders_fills",
        "sensitivity_",
        "_trades.json",
    )
    files = [path.name.lower() for path in Q001.iterdir() if path.is_file()]
    counts = Counter(item.get("state") for item in states)
    axis = [item["trade_date"] for item in states]
    checks = {
        "q001_has_no_economic_result_artifacts": not any(
            token in name for name in files for token in economic_result_files
        ),
        "counts_match_recorded": len(axis) == 1131 and sum(bool(item["q_ready"]) for item in states) == 1011
        and counts["HE"] == 319 and counts["LE"] == 11,
        "daily_axis_complete_unique": len(axis) == len(events) == len(set(axis))
        and axis == [item["trade_date"] for item in events],
        "no_future_reference": all(
            item["trade_date"] not in item["reference_valid_trade_dates"]
            and all(day < item["trade_date"] for day in item["reference_valid_trade_dates"])
            for item in states
        ),
        "r004_isolation": all(
            item.get("state") not in {"HE", "LE"} or item.get("reason") != "R004_DAY_SESSION_QUARANTINED"
            for item in events
        ) and bool(gate["no_R004_contamination"]),
        "selection_fixed_at_1129": all(
            item.get("status") != "EXECUTABLE"
            or datetime.fromisoformat(item["selection_fixed_at_jst"]).time() == time(11, 29)
            for item in events
        ),
        "unresolved_positions_zero": not any(
            item.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN" for item in events
        ),
        "q001_causality_artifact_passed": bool(causal["passed"]),
    }
    return {
        "source_run_id": "task-r100-q001-morning-efficiency-fixed-switch-20260916-01",
        "pnl_access": "NONE",
        "recorded_counts": {"q_ready": 1011, "HE": 319, "LE": 11},
        "reproduced_counts": {"scheduled_axis": len(axis), "q_ready": sum(bool(item["q_ready"]) for item in states), "HE": counts["HE"], "LE": counts["LE"]},
        "checks": checks,
        "passed": all(checks.values()),
    }


def known_reason(reason: object) -> bool:
    return str(reason).startswith(("VALID_", "NO_", "R004_", "P0_", "MORNING_", "NONPOSITIVE_", "CURRENT_", "INSUFFICIENT_", "EFFICIENCY_", "EVENT_", "STATE_", "FIXED_"))


def pre_pnl_gate(axis: list[date], events: list[dict[str, object]], isolated: set[tuple[date, Session]]) -> dict[str, object]:
    selected = [item for item in events if item.get("state") in {"HE", "LE"}]
    complete = [item for item in selected if item["status"] == "EXECUTABLE"]
    states, legs = Counter(item.get("state") for item in events), Counter(item["state"] for item in complete)
    directions = Counter(item["direction"] for item in complete)
    years = Counter(item["trade_date"][:4] for item in complete)
    regimes = Counter(item["tse_regime"] for item in complete)
    cross = Counter((item["state"], item["r_band"], item["direction"]) for item in complete)
    unexplained = [item["trade_date"] for item in events if not known_reason(item.get("reason"))]
    checks: dict[str, object] = {
        "q_ready_at_least_700": sum(bool(item["q_ready"]) for item in events) >= 700,
        "HE_LE_event_each_at_least_120": states["HE"] >= 120 and states["LE"] >= 120,
        "A_completed_at_least_180": len(complete) >= 180,
        "HE_continuation_LE_reversal_each_at_least_70": legs["HE"] >= 70 and legs["LE"] >= 70,
        "morning_direction_up_down_each_at_least_70": directions["up"] >= 70 and directions["down"] >= 70,
        "2021_initialization_at_least_15": years["2021"] >= 15,
        "2022_2024_each_at_least_35": all(years[str(year)] >= 35 for year in range(2022, 2025)),
        "2025_h1_at_least_15": years["2025"] >= 15,
        "old_new_regime_at_least_150_15": regimes["old"] >= 150 and regimes["new"] >= 15,
        "HE_LE_v_band_direction_common_event_each_at_least_8": all(
            cross[(state, band, direction)] >= 8
            for state in ("HE", "LE") for band in ("R50_75", "R75_100") for direction in ("up", "down")
        ),
        "no_future_reference": all(
            item["trade_date"] not in item["reference_valid_trade_dates"]
            and all(day < item["trade_date"] for day in item["reference_valid_trade_dates"])
            for item in events
        ),
        "no_same_day_event_fill_exit_selection": all(
            item["state_reason"] == "STATE_AVAILABLE" or item.get("state") is None for item in events
        ),
        "scheduled_axis_matches_events": [item["trade_date"] for item in events] == [day.isoformat() for day in axis],
        "no_R004_contamination": all((date.fromisoformat(item["trade_date"]), Session.DAY) not in isolated for item in selected),
        "unexplained_exclusions_equal_zero": not unexplained,
        "unresolved_filled_positions_equal_zero": not any(item["status"] == "ENTRY_FILLED_EXIT_UNKNOWN" for item in events),
    }
    checks["counts"] = {
        "scheduled_axis": len(axis), "q_ready": sum(bool(item["q_ready"]) for item in events),
        "state": dict(states), "A_completed": len(complete), "legs": dict(legs),
        "directions": dict(directions), "years": dict(years), "regimes": dict(regimes),
        "common_event_cross_v_band": {"|".join(key).replace("R", "V"): value for key, value in cross.items()},
        "unexplained": unexplained,
    }
    checks["passed"] = all(value is True for key, value in checks.items() if key not in {"counts", "passed"})
    return checks


def causality_audit(axis: list[date], events: list[dict[str, object]]) -> dict[str, object]:
    executable = [item for item in events if item["status"] == "EXECUTABLE"]
    checks = {
        "axis_complete_unique_trade_date": [item["trade_date"] for item in events] == [day.isoformat() for day in axis] and len(events) == len({item["trade_date"] for item in events}),
        "exact_0900_1129_150_bar_measurement": all(datetime.fromisoformat(item["window_start_jst"]).time() == time(9) and datetime.fromisoformat(item["window_end_jst"]).time() == time(11, 29) and item["scheduled_bar_count"] == 150 for item in events),
        "strict_prior_current_excluded_no_backfill": all(item["reference_scheduled_trade_dates"] == [day.isoformat() for day in axis[max(0, index - 120):index]] and item["trade_date"] not in item["reference_valid_trade_dates"] for index, item in enumerate(events)),
        "v_and_e_formula": all(abs(item["v_range_points"] - (item["h_points"] - item["l_points"])) < 1e-12 and abs(item["e_efficiency"] - abs(item["c_close_points"] - item["o_open_points"]) / item["v_range_points"]) < 1e-12 for item in events if item["observation_valid"]),
        "selection_1129_first_eligible_after_1230_fixed_exit": all(datetime.fromisoformat(item["selection_fixed_at_jst"]).time() == time(11, 29) and datetime.fromisoformat(item["entry_signal_jst"]) + timedelta(minutes=1) == datetime.fromisoformat(item["entry_open_jst"]) and datetime.fromisoformat(item["entry_open_jst"]).time() >= time(12, 30) and datetime.fromisoformat(item["exit_open_jst"]).time() == time(14, 55) for item in executable),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def execute_route(
    axis: list[date],
    events: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Any]],
    engine: Any,
    name: str,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    """Submit at the prior scheduled minute so the frozen first eligible open fills."""
    by_date = {date.fromisoformat(item["trade_date"]): item for item in events}
    trades: list[Trade] = []
    unknown: set[date] = set()
    for target in axis:
        event = by_date[target]
        direction = route(cast(str | None, event.get("state")), name)
        if direction is None:
            continue
        if event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            continue
        if event["status"] != "EXECUTABLE":
            continue
        side = cast(str, event[f"{direction}_direction"])
        result = engine.run(
            bars[(target, Session.DAY)],
            R088FixedSignalStrategy(
                f"r100_q002_{name}_{target.isoformat()}",
                datetime.fromisoformat(cast(str, event["entry_signal_jst"])),
                datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
                side,
            ),
            parameter_hash=canonical_hash({"study": "R100-Q002", "route": name, "event": event}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable R100-Q002 event did not fill exactly once")
        trade = result.trades[0]
        if (
            trade.entry_ts.isoformat() != event["entry_open_jst"]
            or trade.exit_ts.isoformat() != event["exit_open_jst"]
            or trade.side is not Side(side)
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{name}/{target}: event/execution mismatch")
        trades.append(trade)
    daily: dict[str, int | None] = {target.isoformat(): None if target in unknown else 0 for target in axis}
    for trade in trades:
        key = trade.trade_date.isoformat()
        if daily[key] is None or daily[key] != 0:
            raise ValueError(f"{name}/{key}: duplicate or unresolved daily result")
        daily[key] = trade.net_pnl_jpy
    return tuple(sorted(trades, key=lambda item: item.trade_date)), daily, unknown


def run_profile(axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Any]], instrument: object, baseline: Any, classifier: CalendarClassifier, *, ticks: int = 1, fee: int = 30) -> tuple[dict[str, tuple[Trade, ...]], dict[str, list[int]], set[date]]:
    results: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, list[int]] = {}
    unresolved: set[date] = set()
    for name in ("A", "C", "F", "I"):
        trades, path, unknown = execute_route(axis, events, bars, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name)
        results[name], unresolved = trades, unresolved | unknown
        if not any(value is None for value in path.values()):
            daily[name] = [cast(int, value) for value in path.values()]
    return results, daily, unresolved


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_task_r100_q002_morning_range_efficiency_switch.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [Path("scripts/run_task_r100_q002_morning_range_efficiency_switch.py"), Path("src/n225m_bt/research/r100_morning_efficiency_switch.py"), Path("src/n225m_bt/strategies/r088_fixed_signal.py"), Path("scripts/run_task_r100_q001_morning_efficiency_fixed_switch.py"), Path("scripts/run_r088_q001_cash_open_path_efficiency.py"), Path("tests/test_r100_morning_efficiency_switch.py")]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    prereg = {"task_id": "TASK-R100-Q002", "study_id": "R100-Q002", "parent": "TASK-R100-Q001", "spec_version": "v1", "run_id": RUN_ID, "status": "FROZEN_BEFORE_EVALUATION_PNL", "preregistration_document": str(DOC), "preregistration_document_sha256": digest(ROOT / DOC), "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "q001_pnl_free_fact": "Q001 q_ready=1011, HE=319, LE=11, with no PnL/PF/bootstrap/Delta/sensitivity artifacts; high r=abs(C-O)=e*(H-L) structurally thinned low-e state.", "frozen_rule": "Q001 unchanged except E is v=H-L >= strict-prior current-excluded q50(v); HE=E&e>=q67, LE=E&e<=q33; v bands q50-q75/q75+.", "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10000, "method": "common non-wrapping MBB, tail truncation, linear percentile"}, "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(data_config.gold_root, "development")], "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files}, "config_hashes": {str(path): digest(ROOT / path) for path in config_files}, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "preregistration.json", prereg)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(prereg), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / DOC, OUT / "documentation_snapshot" / DOC.name)
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {"pytest": [sys.executable, "-m", "pytest", "tests/test_r100_morning_efficiency_switch.py", "tests/test_execution.py", "-q"], "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)], "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r100_morning_efficiency_switch.py"], "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files)]}
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        done = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env)
        validation[name] = {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
    validation["status"] = "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("R100-Q002 validation failed")
    q001 = q001_pnl_free_reproduction()
    write_json(OUT / "q001_pnl_free_reproduction.json", q001)
    if not q001["passed"]:
        write_json(OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "Q001_PNL_FREE_REPRODUCTION_MISMATCH", "reproduction": q001})
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"})
        return
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier, axis = CalendarClassifier(sessions, calendar), scheduled_axis(calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in set(axis) and bar.session is Session.DAY:
            bars[(bar.trade_date, bar.session)].append(bar)
    ledger = state_ledger(classifier, axis, bars, isolated, event_metric="v")
    events = build_events(ledger, bars)
    gate, causal = pre_pnl_gate(axis, events, isolated), causality_audit(axis, events)
    write_json(OUT / "morning_state_ledger_before_evaluation_pnl.json", {"definition": "09:00-11:29 O/C/H/L; e=abs(C-O)/(H-L); E=v=H-L >= q50(v); state at 11:29", "quarantine": quarantine_audit, "ledger": ledger})
    write_json(OUT / "primary_events_before_evaluation_pnl.json", events)
    write_json(OUT / "pre_pnl_gate.json", gate)
    write_json(OUT / "causality_audit_before_pnl.json", causal)
    write_json(OUT / "access_ledger.json", {"stage": "Q001 PnL-free artifact reproduction then Q002 state/event causality audit", "split": "development", "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    if not gate["passed"] or not causal["passed"]:
        write_json(OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "PRE_PNL_GATE_OR_CAUSALITY_AUDIT_FAILED", "gate": gate, "causality": causal})
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"})
        return
    primary_trades, primary_daily, unresolved = run_profile(axis, events, bars, instrument, baseline, classifier)
    if unresolved or len(primary_daily) != 4:
        raise ValueError("R100-Q002 unresolved primary position after PnL-free gate")
    for name, trades in primary_trades.items():
        write_json(OUT / f"primary_{name}_orders_fills.json", orders_fills(trades))
        write_json(OUT / f"primary_{name}_trades.json", [asdict(item) for item in trades])
        write_json(OUT / f"primary_{name}_daily_axis.json", dict(zip((day.isoformat() for day in axis), primary_daily[name], strict=True)))
    arrays = {name: np.asarray(values, dtype=float) for name, values in primary_daily.items()}
    index = mbb_indices(len(axis))
    np.save(OUT / "bootstrap_common_indices.npy", index)
    delta, empty = delta_samples(events, arrays["C"], arrays["F"], index)
    boot: dict[str, object] = {"A": {"ci95_percentile_linear": percentile_ci(arrays["A"][index].mean(axis=1))}, "A-C": {"ci95_percentile_linear": percentile_ci((arrays["A"][index] - arrays["C"][index]).mean(axis=1))}, "A-F": {"ci95_percentile_linear": percentile_ci((arrays["A"][index] - arrays["F"][index]).mean(axis=1))}, "A-I": {"ci95_percentile_linear": percentile_ci((arrays["A"][index] - arrays["I"][index]).mean(axis=1))}, "Delta": {"ci95_percentile_linear": None if empty else percentile_ci(delta), "empty_stratum_resamples": empty}}
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_indices.npy"})
    a_metrics = ledger_metrics(primary_trades["A"])
    ci = {name: value["ci95_percentile_linear"] for name, value in boot.items()}
    primary = {"A_net_pnl_jpy": a_metrics["net_pnl_jpy"], "A_profit_factor": a_metrics["profit_factor"], "A_daily_net_ci": ci["A"], "A_minus_C_ci": ci["A-C"], "A_minus_F_ci": ci["A-F"], "A_minus_I_ci": ci["A-I"], "Delta_point": None if empty else float(delta.mean()), "Delta_ci": ci["Delta"]}
    primary_and = {"A_net_positive": cast(int, primary["A_net_pnl_jpy"]) > 0, "A_pf_gt_one": primary["A_profit_factor"] is not None and cast(float, primary["A_profit_factor"]) > 1, "A_daily_ci_lower_positive": cast(list[float], ci["A"])[0] > 0, "A_minus_C_ci_lower_positive": cast(list[float], ci["A-C"])[0] > 0, "A_minus_F_ci_lower_positive": cast(list[float], ci["A-F"])[0] > 0, "A_minus_I_ci_lower_positive": cast(list[float], ci["A-I"])[0] > 0, "Delta_ci_lower_positive": ci["Delta"] is not None and cast(list[float], ci["Delta"])[0] > 0}
    write_json(OUT / "primary_results.json", {"primary": primary, "primary_and": primary_and, "daily_paths_jpy": primary_daily})
    profiles: dict[str, tuple[dict[str, int | str], int, int, int, time]] = {"e_q25_q75": ({"e_low": 25, "e_high": 75, "event_metric": "v"}, 1, 30, 0, time(14, 55)), "e_q40_q60": ({"e_low": 40, "e_high": 60, "event_metric": "v"}, 1, 30, 0, time(14, 55)), "v_q40": ({"r_cutoff": 40, "event_metric": "v"}, 1, 30, 0, time(14, 55)), "v_q60": ({"r_cutoff": 60, "event_metric": "v"}, 1, 30, 0, time(14, 55)), "prior60_valid50": ({"lookback": 60, "min_valid": 50, "event_metric": "v"}, 1, 30, 0, time(14, 55)), "prior240_valid200": ({"lookback": 240, "min_valid": 200, "event_metric": "v"}, 1, 30, 0, time(14, 55)), "entry_plus_1": ({"event_metric": "v"}, 1, 30, 1, time(14, 55)), "exit_1430": ({"event_metric": "v"}, 1, 30, 0, time(14, 30)), "cost_2tick": ({"event_metric": "v"}, 2, 30, 0, time(14, 55)), "cost_3tick": ({"event_metric": "v"}, 3, 30, 0, time(14, 55)), "fee_x2": ({"event_metric": "v"}, 1, 60, 0, time(14, 55))}
    diagnostics: dict[str, object] = {}
    for name, (kwargs, ticks, fee, delay, exit_time) in profiles.items():
        alternate = build_events(state_ledger(classifier, axis, bars, isolated, **kwargs), bars, entry_extra_bars=delay, exit_time=exit_time)
        trades, daily, unknown = run_profile(axis, alternate, bars, instrument, baseline, classifier, ticks=ticks, fee=fee)
        if unknown or len(daily) != 4:
            diagnostics[name] = {"status": "UNRESOLVED_POSITION", "unknown": [day.isoformat() for day in sorted(unknown)]}
            continue
        points, empty_draws = delta_samples(alternate, np.asarray(daily["C"], dtype=float), np.asarray(daily["F"], dtype=float), index)
        diagnostics[name] = {"status": "COMPLETE", "A_net_pnl_jpy": ledger_metrics(trades["A"])["net_pnl_jpy"], "Delta_point": None if empty_draws else float(points.mean()), "empty_stratum_resamples": empty_draws, "state_counts": dict(Counter(item.get("state") for item in alternate))}
        write_json(OUT / f"sensitivity_{name}_events.json", alternate)
        write_json(OUT / f"sensitivity_{name}_A_trades.json", [asdict(item) for item in trades["A"]])
    by_event = {item["trade_date"]: item for item in events}
    he_leg = sum(trade.net_pnl_jpy for trade in primary_trades["A"] if by_event[trade.trade_date.isoformat()]["state"] == "HE")
    le_leg = sum(trade.net_pnl_jpy for trade in primary_trades["A"] if by_event[trade.trade_date.isoformat()]["state"] == "LE")
    by_year = {str(year): sum(trade.net_pnl_jpy for trade in primary_trades["A"] if trade.trade_date.year == year) for year in range(2021, 2026)}
    by_regime = {regime: sum(trade.net_pnl_jpy for trade in primary_trades["A"] if by_event[trade.trade_date.isoformat()]["tse_regime"] == regime) for regime in ("old", "new")}
    winners = sorted((trade.net_pnl_jpy for trade in primary_trades["A"] if trade.net_pnl_jpy > 0), reverse=True)[:10]
    robustness = {"all_fixed_sensitivities_A_and_Delta_positive": all(item.get("status") == "COMPLETE" and cast(int, item["A_net_pnl_jpy"]) > 0 and cast(float, item["Delta_point"]) > 0 for item in diagnostics.values()), "HE_continuation_LE_reversal_legs_positive": he_leg > 0 and le_leg > 0, "two_2022_2024_positive": sum(by_year[str(year)] > 0 for year in range(2022, 2025)) >= 2, "2025_h1_positive": by_year["2025"] > 0, "both_regimes_positive": all(value > 0 for value in by_regime.values()), "top10_winners_removed_positive": cast(int, primary["A_net_pnl_jpy"]) - sum(winners) > 0}
    write_json(OUT / "fixed_diagnostics.json", {"diagnostics": diagnostics, "HE_continuation_leg_net": he_leg, "LE_reversal_leg_net": le_leg, "by_year": by_year, "by_regime": by_regime, "top10_winners_removed_net": cast(int, primary["A_net_pnl_jpy"]) - sum(winners), "robustness": robustness})
    decision = "REJECT" if not all(primary_and.values()) else "INVESTIGATE"
    reason = "PRIMARY_AND_FAILED" if decision == "REJECT" else ("DEVELOPMENT_REUSE_DECISION_CEILING" if all(robustness.values()) else "ROBUSTNESS_FAILED_AFTER_PRIMARY_PASS")
    write_json(OUT / "decision.json", {"status": decision, "reason": reason, "decision_ceiling": "INVESTIGATE", "primary_and": primary_and, "robustness": robustness, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": decision})


if __name__ == "__main__":
    main()
