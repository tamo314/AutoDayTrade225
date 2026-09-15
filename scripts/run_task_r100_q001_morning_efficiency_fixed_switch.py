"""Execute the frozen Development-only TASK-R100-Q001 exactly once."""

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
RUN_ID = "task-r100-q001-morning-efficiency-fixed-switch-20260916-01"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/110_r100_q001_morning_efficiency_fixed_switch.md")
AXIS_SET: set[date] = set()


def execute_route(
    axis: list[date],
    events: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Any]],
    engine: Any,
    name: str,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    by_date = {date.fromisoformat(cast(str, item["trade_date"])): item for item in events}
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
                f"r100_{name}_{target.isoformat()}",
                datetime.fromisoformat(cast(str, event["entry_open_jst"])),
                datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
                side,
            ),
            parameter_hash=canonical_hash({"study": "R100-Q001", "route": name, "event": event}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable R100 event did not fill exactly once")
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


def known_reason(reason: object) -> bool:
    return str(reason).startswith(
        (
            "VALID_",
            "NO_",
            "R004_",
            "P0_",
            "MORNING_",
            "NONPOSITIVE_",
            "CURRENT_",
            "INSUFFICIENT_",
            "EFFICIENCY_",
            "STATE_",
            "NO_ELIGIBLE_",
            "FIXED_",
        )
    )


def pnl_free_gate(
    axis: list[date], events: list[dict[str, object]], isolated: set[tuple[date, Session]]
) -> dict[str, object]:
    selected = [item for item in events if item.get("state") in {"HE", "LE"}]
    complete = [item for item in selected if item["status"] == "EXECUTABLE"]
    states, directions = Counter(cast(str | None, item.get("state")) for item in events), Counter(
        cast(str, item["direction"]) for item in complete
    )
    years = Counter(cast(str, item["trade_date"])[:4] for item in complete)
    regimes = Counter(cast(str, item["tse_regime"]) for item in complete)
    legs = Counter(cast(str, item["state"]) for item in complete)
    cross = Counter(
        (cast(str, item["state"]), cast(str, item["r_band"]), cast(str, item["direction"]))
        for item in complete
    )
    strict_prior = all(
        all(index < cast(int, item["index"]) and cast(int, item["index"]) - index <= 120 for index in range(max(0, cast(int, item["index"]) - 120), cast(int, item["index"])))
        and cast(str, item["trade_date"]) not in cast(list[str], item["reference_valid_trade_dates"])
        for item in events
    )
    unexplained = [cast(str, item["trade_date"]) for item in events if not known_reason(item.get("reason"))]
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
        "HE_LE_r_band_direction_common_event_each_at_least_8": all(
            cross[(state, band, direction)] >= 8
            for state in ("HE", "LE")
            for band in ("R50_75", "R75_100")
            for direction in ("up", "down")
        ),
        "no_future_reference": strict_prior,
        "no_same_day_event_fill_exit_selection": all(
            item["state_reason"] == "STATE_AVAILABLE" or item.get("state") is None for item in events
        ),
        "scheduled_axis_matches_events": [item["trade_date"] for item in events] == [item.isoformat() for item in axis],
        "no_R004_contamination": all(
            (date.fromisoformat(cast(str, item["trade_date"])), Session.DAY) not in isolated
            for item in selected
        ),
        "unexplained_exclusions_equal_zero": not unexplained,
        "unresolved_filled_positions_equal_zero": not any(item["status"] == "ENTRY_FILLED_EXIT_UNKNOWN" for item in events),
    }
    checks["counts"] = {
        "scheduled_axis": len(axis), "q_ready": sum(bool(item["q_ready"]) for item in events),
        "state": dict(states), "A_completed": len(complete), "legs": dict(legs),
        "directions": dict(directions), "years": dict(years), "regimes": dict(regimes),
        "common_event_cross": {"|".join(key): value for key, value in cross.items()},
        "unexplained": unexplained,
    }
    checks["passed"] = all(value is True for key, value in checks.items() if key not in {"counts", "passed"})
    return checks


def causality_audit(axis: list[date], events: list[dict[str, object]]) -> dict[str, object]:
    executable = [item for item in events if item["status"] == "EXECUTABLE"]
    checks = {
        "axis_complete_unique_trade_date": [item["trade_date"] for item in events] == [day.isoformat() for day in axis] and len(events) == len({item["trade_date"] for item in events}),
        "exact_0900_1129_150_bar_measurement": all(datetime.fromisoformat(cast(str, item["window_start_jst"])).time() == time(9) and datetime.fromisoformat(cast(str, item["window_end_jst"])).time() == time(11, 29) and item["scheduled_bar_count"] == 150 for item in events),
        "strict_prior_current_excluded_no_backfill": all(cast(list[str], item["reference_scheduled_trade_dates"]) == [day.isoformat() for day in axis[max(0, index - 120):index]] and cast(str, item["trade_date"]) not in cast(list[str], item["reference_valid_trade_dates"]) for index, item in enumerate(events)),
        "selection_1129_first_eligible_after_1230_fixed_exit": all(datetime.fromisoformat(cast(str, item["selection_fixed_at_jst"])).time() == time(11, 29) and datetime.fromisoformat(cast(str, item["entry_open_jst"])).time() >= time(12, 30) and datetime.fromisoformat(cast(str, item["exit_open_jst"])).time() == time(14, 55) for item in executable),
        "r_and_e_formula": all(abs(cast(float, item["r_abs_points"]) - abs(cast(float, item["c_close_points"]) - cast(float, item["o_open_points"]))) < 1e-12 and abs(cast(float, item["e_efficiency"]) - cast(float, item["r_abs_points"]) / (cast(float, item["h_points"]) - cast(float, item["l_points"]))) < 1e-12 for item in events if bool(item["observation_valid"])),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def delta_samples(
    states: list[dict[str, object]], continuation: np.ndarray, reversal: np.ndarray, index: np.ndarray
) -> tuple[np.ndarray, int]:
    g, samples, empty = continuation - reversal, np.full(len(index), np.nan), 0
    for iteration, draw in enumerate(index):
        parts: list[float] = []
        for band in ("R50_75", "R75_100"):
            for direction in ("up", "down"):
                he = [position for position, original in enumerate(draw) if states[original].get("state") == "HE" and states[original].get("r_band") == band and states[original].get("direction") == direction]
                le = [position for position, original in enumerate(draw) if states[original].get("state") == "LE" and states[original].get("r_band") == band and states[original].get("direction") == direction]
                if not he or not le:
                    empty += 1
                    break
                parts.append(float(g[draw[he]].mean() - g[draw[le]].mean()))
            else:
                continue
            break
        else:
            samples[iteration] = sum(parts) / 4
    return samples, empty


def run_profile(
    axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Any]], instrument: object, baseline: Any, classifier: CalendarClassifier, *, ticks: int = 1, fee: int = 30
) -> tuple[dict[str, tuple[Trade, ...]], dict[str, list[int]], set[date]]:
    results, daily, unknown = {}, {}, set()
    for name in ("A", "C", "F", "I"):
        trades, path, unresolved = execute_route(axis, events, bars, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name)
        results[name], unknown = trades, unknown | unresolved
        if any(value is None for value in path.values()):
            continue
        daily[name] = [cast(int, value) for value in path.values()]
    return results, daily, unknown


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [Path("scripts/run_task_r100_q001_morning_efficiency_fixed_switch.py"), Path("src/n225m_bt/research/r100_morning_efficiency_switch.py"), Path("src/n225m_bt/strategies/r088_fixed_signal.py"), Path("scripts/run_r088_q001_cash_open_path_efficiency.py"), Path("tests/test_r100_morning_efficiency_switch.py")]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    prereg = {"task_id": "TASK-R100-Q001", "family_id": "morning_path_efficiency_afternoon_fixed_switch", "study_id": "R100-Q001", "spec_version": "v1", "run_id": RUN_ID, "status": "FROZEN_BEFORE_EVALUATION_PNL", "preregistration_document": str(DOC), "preregistration_document_sha256": digest(ROOT / DOC), "prior_information_seen": True, "learned_from": ["R078", "R079", "R099"], "duplicate_review": "R001-R099 reviewed before PnL: R088/R096 use 09:00-09:59 and different controls; R099 uses complete night and immutable existing constituents; no materially equivalent 09:00-11:29 HE-continuation/LE-reversal fixed switch exists.", "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "frozen_rule": "09:00-11:29 150 normal eligible DAY bars; r=abs(C-O), e=abs(C-O)/(H-L); H<=L/incomplete/R004 unavailable; exact current-excluded prior 120 scheduled dates, valid>=100, nearest-rank q33/q67 e with q33<q67, q50 r; HE=E&e>=q67, LE=E&e<=q33; 11:29 fixed routes; first eligible >=12:30 entry, 14:55 exit.", "pre_pnl_gate": "q-ready>=700; HE/LE events>=120 each; A completed>=180; legs>=70; directions>=70; 2021>=15; 2022-2024>=35 each; 2025H1>=15; old/new>=150/15; HE/LE x r-band x direction common events>=8; no leakage, selection, axis mismatch, R004, unexplained exclusion, unresolved filled position.", "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10000, "method": "common non-wrapping MBB, tail truncation, linear percentile"}, "primary": "A Net>0, PF>1, lower CI A/A-C/A-F/A-I/Delta>0; Delta equal-weight HE-LE continuation-minus-reversal across two r bands and up/down directions.", "sensitivities": ["e_q25_q75", "e_q40_q60", "r_q40", "r_q60", "prior60_valid50", "prior240_valid200", "entry_plus_1", "exit_1430", "cost_2tick", "cost_3tick", "fee_x2"], "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(data_config.gold_root, "development")], "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files}, "config_hashes": {str(path): digest(ROOT / path) for path in config_files}, "decision_ceiling": "INVESTIGATE", "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
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
    validation["status"] = "PASS" if all(cast(dict[str, int], item)["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(
            OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}
        )
        raise ValueError("R100 validation failed")

    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier, axis = CalendarClassifier(sessions, calendar), scheduled_axis(calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in set(axis) and bar.session is Session.DAY:
            bars[(bar.trade_date, bar.session)].append(bar)
    ledger = state_ledger(classifier, axis, bars, isolated)
    events = build_events(ledger, bars)
    gate, causal = pnl_free_gate(axis, events, isolated), causality_audit(axis, events)
    write_json(OUT / "duplicate_review_before_evaluation_pnl.json", {"reviewed_range": "R001-R099", "related_not_equivalent": {"R088": "09:00-09:59 HE continuation versus low-efficiency continuation control", "R096": "09:00-09:59 directional range-end acceptance", "R099": "complete official NIGHT switch into existing cash entries", "R081_R095": "lunch-break displacement"}, "conclusion": "NO_MATERIALLY_EQUIVALENT_VALID_TEST; POST_HOC_DEVELOPMENT_REUSE; CEILING_INVESTIGATE"})
    write_json(OUT / "morning_state_ledger_before_evaluation_pnl.json", {"definition": "09:00-11:29 O/C/H/L r/e; state at 11:29", "quarantine": quarantine_audit, "ledger": ledger})
    write_json(OUT / "primary_events_before_evaluation_pnl.json", events)
    write_json(OUT / "pre_pnl_gate.json", gate)
    write_json(OUT / "causality_audit_before_pnl.json", causal)
    write_json(OUT / "access_ledger.json", {"stage": "state/event availability and causality audit before PnL", "split": "development", "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    if not bool(gate["passed"]) or not bool(causal["passed"]):
        write_json(OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "PRE_PNL_GATE_OR_CAUSALITY_AUDIT_FAILED", "gate": gate, "causality": causal})
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"})
        return

    primary_trades, primary_daily, unresolved = run_profile(axis, events, bars, instrument, baseline, classifier)
    if unresolved or len(primary_daily) != 4:
        raise ValueError("R100 unresolved primary position after PnL-free gate")
    for name, trades in primary_trades.items():
        write_json(OUT / f"primary_{name}_orders_fills.json", orders_fills(trades))
        write_json(OUT / f"primary_{name}_trades.json", [asdict(item) for item in trades])
        write_json(
            OUT / f"primary_{name}_daily_axis.json",
            dict(zip((item.isoformat() for item in axis), primary_daily[name], strict=True)),
        )
    arrays = {name: np.asarray(values, dtype=float) for name, values in primary_daily.items()}
    index = mbb_indices(len(axis))
    np.save(OUT / "bootstrap_common_indices.npy", index)
    delta, empty = delta_samples(events, arrays["C"], arrays["F"], index)
    boot: dict[str, object] = {"A": {"ci95_percentile_linear": percentile_ci(arrays["A"][index].mean(axis=1))}, "A-C": {"ci95_percentile_linear": percentile_ci((arrays["A"][index] - arrays["C"][index]).mean(axis=1))}, "A-F": {"ci95_percentile_linear": percentile_ci((arrays["A"][index] - arrays["F"][index]).mean(axis=1))}, "A-I": {"ci95_percentile_linear": percentile_ci((arrays["A"][index] - arrays["I"][index]).mean(axis=1))}, "Delta": {"ci95_percentile_linear": None if empty else percentile_ci(delta), "empty_stratum_resamples": empty}}
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_indices.npy"})
    a_metrics = ledger_metrics(primary_trades["A"])
    ci = {
        name: cast(dict[str, list[float] | None], value)["ci95_percentile_linear"]
        for name, value in boot.items()
    }
    primary = {"A_net_pnl_jpy": a_metrics["net_pnl_jpy"], "A_profit_factor": a_metrics["profit_factor"], "A_daily_net_ci": ci["A"], "A_minus_C_ci": ci["A-C"], "A_minus_F_ci": ci["A-F"], "A_minus_I_ci": ci["A-I"], "Delta_point": None if empty else float(delta.mean()), "Delta_ci": ci["Delta"]}
    primary_and = {"A_net_positive": cast(int, primary["A_net_pnl_jpy"]) > 0, "A_pf_gt_one": primary["A_profit_factor"] is not None and cast(float, primary["A_profit_factor"]) > 1, "A_daily_ci_lower_positive": cast(list[float], ci["A"])[0] > 0, "A_minus_C_ci_lower_positive": cast(list[float], ci["A-C"])[0] > 0, "A_minus_F_ci_lower_positive": cast(list[float], ci["A-F"])[0] > 0, "A_minus_I_ci_lower_positive": cast(list[float], ci["A-I"])[0] > 0, "Delta_ci_lower_positive": ci["Delta"] is not None and cast(list[float], ci["Delta"])[0] > 0}
    write_json(OUT / "primary_results.json", {"primary": primary, "primary_and": primary_and, "daily_paths_jpy": primary_daily})

    profiles: dict[str, tuple[dict[str, int], int, int, int, time]] = {"e_q25_q75": ({"e_low": 25, "e_high": 75}, 1, 30, 0, time(14, 55)), "e_q40_q60": ({"e_low": 40, "e_high": 60}, 1, 30, 0, time(14, 55)), "r_q40": ({"r_cutoff": 40}, 1, 30, 0, time(14, 55)), "r_q60": ({"r_cutoff": 60}, 1, 30, 0, time(14, 55)), "prior60_valid50": ({"lookback": 60, "min_valid": 50}, 1, 30, 0, time(14, 55)), "prior240_valid200": ({"lookback": 240, "min_valid": 200}, 1, 30, 0, time(14, 55)), "entry_plus_1": ({}, 1, 30, 1, time(14, 55)), "exit_1430": ({}, 1, 30, 0, time(14, 30)), "cost_2tick": ({}, 2, 30, 0, time(14, 55)), "cost_3tick": ({}, 3, 30, 0, time(14, 55)), "fee_x2": ({}, 1, 60, 0, time(14, 55))}
    diagnostics: dict[str, object] = {}
    for name, (state_kwargs, ticks, fee, delay, exit_time) in profiles.items():
        alternate_ledger = state_ledger(classifier, axis, bars, isolated, **state_kwargs)
        alternate_events = build_events(
            alternate_ledger, bars, entry_extra_bars=delay, exit_time=exit_time
        )
        trades, daily, unknown = run_profile(axis, alternate_events, bars, instrument, baseline, classifier, ticks=ticks, fee=fee)
        if unknown or len(daily) != 4:
            diagnostics[name] = {
                "status": "UNRESOLVED_POSITION",
                "unknown": [item.isoformat() for item in sorted(unknown)],
            }
            continue
        continuation, reversal = np.asarray(daily["C"], dtype=float), np.asarray(daily["F"], dtype=float)
        points, empty_draws = delta_samples(alternate_events, continuation, reversal, index)
        diagnostics[name] = {"status": "COMPLETE", "A_net_pnl_jpy": ledger_metrics(trades["A"])["net_pnl_jpy"], "Delta_point": None if empty_draws else float(points.mean()), "empty_stratum_resamples": empty_draws, "state_counts": dict(Counter(cast(str | None, item.get("state")) for item in alternate_events))}
        write_json(OUT / f"sensitivity_{name}_events.json", alternate_events)
        write_json(
            OUT / f"sensitivity_{name}_A_trades.json", [asdict(item) for item in trades["A"]]
        )
        write_json(
            OUT / f"sensitivity_{name}_A_daily_axis.json",
            dict(zip((item.isoformat() for item in axis), daily["A"], strict=True)),
        )
    he_leg = sum(item.net_pnl_jpy for item in primary_trades["A"] if next(event for event in events if event["trade_date"] == item.trade_date.isoformat())["state"] == "HE")
    le_leg = sum(item.net_pnl_jpy for item in primary_trades["A"] if next(event for event in events if event["trade_date"] == item.trade_date.isoformat())["state"] == "LE")
    by_year = {str(year): sum(item.net_pnl_jpy for item in primary_trades["A"] if item.trade_date.year == year) for year in range(2021, 2026)}
    by_regime = {regime: sum(item.net_pnl_jpy for item in primary_trades["A"] if next(event for event in events if event["trade_date"] == item.trade_date.isoformat())["tse_regime"] == regime) for regime in ("old", "new")}
    winners = sorted((item.net_pnl_jpy for item in primary_trades["A"] if item.net_pnl_jpy > 0), reverse=True)[:10]
    robustness = {"all_fixed_sensitivities_A_and_Delta_positive": all(item.get("status") == "COMPLETE" and cast(int, item["A_net_pnl_jpy"]) > 0 and cast(float, item["Delta_point"]) > 0 for item in diagnostics.values()), "HE_continuation_LE_reversal_legs_positive": he_leg > 0 and le_leg > 0, "two_2022_2024_positive": sum(by_year[str(year)] > 0 for year in range(2022, 2025)) >= 2, "2025_h1_positive": by_year["2025"] > 0, "both_regimes_positive": all(value > 0 for value in by_regime.values()), "top10_winners_removed_positive": cast(int, primary["A_net_pnl_jpy"]) - sum(winners) > 0}
    write_json(OUT / "fixed_diagnostics.json", {"diagnostics": diagnostics, "HE_continuation_leg_net": he_leg, "LE_reversal_leg_net": le_leg, "by_year": by_year, "by_regime": by_regime, "top10_winners_removed_net": cast(int, primary["A_net_pnl_jpy"]) - sum(winners), "robustness": robustness})
    decision = "REJECT" if not all(primary_and.values()) else "INVESTIGATE"
    reason = "PRIMARY_AND_FAILED" if decision == "REJECT" else ("DEVELOPMENT_REUSE_DECISION_CEILING" if all(robustness.values()) else "ROBUSTNESS_FAILED_AFTER_PRIMARY_PASS")
    write_json(OUT / "decision.json", {"status": decision, "reason": reason, "decision_ceiling": "INVESTIGATE", "primary_and": primary_and, "robustness": robustness, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": decision})


if __name__ == "__main__":
    main()
