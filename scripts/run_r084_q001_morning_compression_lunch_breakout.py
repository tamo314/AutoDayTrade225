"""Execute the frozen Development-only TASK-R084-Q001 experiment exactly once."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r084_morning_compression_lunch_breakout import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    bootstrap,
    build_events,
    feasibility,
    scheduled_axis,
)
from n225m_bt.strategies.r084_fixed_signal import R084FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r084-q001-20260915-morning-compression-lunch-breakout-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREGISTRATION_DOCUMENT = Path("docs/strategy/58_r084_q001_morning_compression_lunch_breakout.md")
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for path in files:
        shutil.copy2(ROOT / path, destination / path.name)


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    """Reproduce the fixed R004 whole-session isolation without changing Gold."""
    grouped = session_groups(data.bars)
    isolated = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [row for key, rows in grouped.items() if key not in isolated for row in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included),
    }
    expected = {
        "parent_data_version": PARENT_HASH, "quarantined_sessions": 45, "quarantined_bars": 27345,
        "included_sessions": 2216, "included_bars": 1326086,
        "quarantined_session_list_hash": QUARANTINE_HASH, "included_tick_grid_violations": 0,
    }
    mismatches = {key: {"actual": audit[key], "expected": wanted} for key, wanted in expected.items() if audit[key] != wanted}
    audit.update(expected_match=not mismatches, mismatches=mismatches)
    if mismatches:
        raise ValueError(f"BLOCKED: R004 fixed quarantine mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def engine_for(instrument: object, baseline: Any, classifier: CalendarClassifier, *, ticks: int = 1, fee: int = 30) -> BacktestEngine:
    config = baseline.model_copy(update={
        "mode": "day_only",
        "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
        "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
        "risk": baseline.risk.model_copy(update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}),
    })
    return BacktestEngine(cast(Any, instrument).instrument.to_spec(), config, classifier)


def execute_profile(
    axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Any]],
    engine: BacktestEngine, *, name: str, state: str, fade: bool = False,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
    trades: list[Trade] = []
    unknown: set[date] = set()
    for target in axis:
        event = by_date[target]
        if event.get("state") != state:
            continue
        if event.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            continue
        if event.get("status") != "EXECUTABLE":
            continue
        breakout_direction = cast(str, event["breakout_direction"])
        direction = ("short" if breakout_direction == "long" else "long") if fade else breakout_direction
        result = engine.run(
            bars[(target, Session.DAY)],
            R084FixedSignalStrategy(
                f"r084_{name}_{target.isoformat()}",
                datetime.fromisoformat(cast(str, event["entry_signal_jst"])),
                datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
                direction,
            ),
            parameter_hash=canonical_hash({"profile": name, "event": event, "direction": direction}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
        trade = result.trades[0]
        if (trade.entry_ts.isoformat() != event["entry_open_jst"] or trade.exit_ts.isoformat() != event["exit_open_jst"] or trade.side is not Side(direction) or trade.exit_reason is not ExitReason.SIGNAL or trade.qty != 1):
            raise ValueError(f"{name}/{target}: event/execution mismatch")
        trades.append(trade)
    ordered = tuple(sorted(trades, key=lambda item: item.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), unknown


def assign_w_quintiles(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Label C executable events descriptively; labels never affect selection."""
    ranked = sorted((row for row in events if row.get("state") == "C" and row.get("status") == "EXECUTABLE"), key=lambda row: (cast(float, row["w_bps"]), cast(str, row["trade_date"])))
    labels = {cast(str, row["trade_date"]): min(5, index * 5 // len(ranked) + 1) for index, row in enumerate(ranked)}
    return [dict(row, compressed_w_quintile=labels.get(cast(str, row["trade_date"]))) for row in events]


def _breakout_band(value: str) -> str:
    clock = datetime.fromisoformat(value).time()
    if clock < time(12, 50):
        return "12:30-12:49"
    if clock < time(13, 10):
        return "12:50-13:09"
    return "13:10-13:29"


def report(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], events: list[dict[str, object]], *, detailed: bool = False) -> dict[str, object]:
    by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
    result: dict[str, object] = {
        "metrics": ledger_metrics(trades), "scheduled_axis_observations": len(axis), "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [target for target, value in daily.items() if value is None],
        "trade_occurrence_rate_per_scheduled_trade_date": len(trades) / len(axis),
        "by_year": {str(year): ledger_metrics(tuple(trade for trade in trades if trade.trade_date.year == year)) for year in range(2021, 2026)},
        "by_breakout_direction": {direction: ledger_metrics(tuple(trade for trade in trades if cast(str, by_date[trade.trade_date]["breakout_direction"]) == direction)) for direction in ("long", "short")},
        "profit_concentration": concentration(trades, axis),
    }
    if detailed:
        result["by_breakout_time_band"] = {band: ledger_metrics(tuple(trade for trade in trades if _breakout_band(cast(str, by_date[trade.trade_date]["breakout_close_jst"])) == band)) for band in ("12:30-12:49", "12:50-13:09", "13:10-13:29")}
        result["by_compressed_w_quintile"] = {str(quintile): ledger_metrics(tuple(trade for trade in trades if by_date[trade.trade_date].get("compressed_w_quintile") == quintile)) for quintile in range(1, 6)}
    return result


def order_fill_ledger(trades: tuple[Trade, ...]) -> dict[str, list[dict[str, object]]]:
    orders: list[dict[str, object]] = []
    fills: list[dict[str, object]] = []
    for trade in trades:
        orders.extend([
            {"trade_id": trade.trade_id, "action": "ENTRY", "signal_ts": trade.entry_signal_ts, "planned_fill_ts": trade.entry_ts, "side": trade.side.value},
            {"trade_id": trade.trade_id, "action": "EXIT", "signal_ts": trade.exit_signal_ts, "planned_fill_ts": trade.exit_ts, "side": "sell" if trade.side is Side.LONG else "buy"},
        ])
        fills.extend([
            {"trade_id": trade.trade_id, "action": "ENTRY", "fill_ts": trade.entry_ts, "reference_price": trade.entry_reference_price, "fill_price": trade.entry_fill_price},
            {"trade_id": trade.trade_id, "action": "EXIT", "fill_ts": trade.exit_ts, "reference_price": trade.exit_reference_price, "fill_price": trade.exit_fill_price},
        ])
    return {"orders": orders, "fills": fills}


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    ids = [cast(str, row["trade_date"]) for row in events]
    valid = [row for row in events if row.get("w_valid")]
    executable = [row for row in events if row.get("status") == "EXECUTABLE"]
    checks = {
        "scheduled_axis_complete_unique": ids == [target.isoformat() for target in axis] and len(ids) == len(set(ids)),
        "valid_w_has_150_registered_morning_bars_and_h_l_a": all(row.get("morning_expected_bar_count") == 150 and cast(int, row["a_0900_open_points"]) > 0 and cast(int, row["h_0900_1129_points"]) >= cast(int, row["l_0900_1129_points"]) for row in valid),
        "references_are_strictly_prior_and_current_excluded": all(all(date.fromisoformat(reference) < date.fromisoformat(cast(str, row["trade_date"])) for reference in cast(list[str], row["reference_valid_trade_dates_oldest_to_newest"])) and cast(str, row["trade_date"]) not in cast(list[str], row["reference_valid_trade_dates_oldest_to_newest"]) for row in events),
        "quantile_states_have_exact_prior_lookback": all(row.get("state") not in {"C", "M"} or row.get("reference_valid_count") == row.get("lookback_valid_trade_dates_required") for row in events),
        "breakouts_are_strict_in_registered_window_then_next_eligible_open": all(time(12, 30) <= datetime.fromisoformat(cast(str, row["breakout_close_jst"])).time() <= time(13, 29) and datetime.fromisoformat(cast(str, row["entry_signal_jst"])) >= datetime.fromisoformat(cast(str, row["breakout_close_jst"])) and datetime.fromisoformat(cast(str, row["entry_open_jst"])) > datetime.fromisoformat(cast(str, row["breakout_close_jst"])) for row in executable),
        "primary_exit_is_1430_open": all(datetime.fromisoformat(cast(str, row["exit_open_jst"])).time() == time(14, 30) for row in executable),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r084_q001_morning_compression_lunch_breakout.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [Path("scripts/run_r084_q001_morning_compression_lunch_breakout.py"), Path("src/n225m_bt/research/r084_morning_compression_lunch_breakout.py"), Path("src/n225m_bt/strategies/r084_fixed_signal.py"), Path("tests/test_r084_q001.py")]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    preregistration = {
        "task_id": "TASK-R084-Q001", "family_id": "morning_volatility_compression_lunch_breakout", "study_id": "R084-Q001", "spec_version": "v1", "run_id": RUN_ID, "protocol_revision": "RG-20260915-01", "status": "FROZEN_BEFORE_ADDITIONAL_PNL",
        "preregistration_document": str(PREREGISTRATION_DOCUMENT), "preregistration_document_sha256": digest(ROOT / PREREGISTRATION_DOCUMENT), "prior_information_seen": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "W=10000*(H(09:00-11:29)-L(09:00-11:29))/A(09:00 open); current-excluded prior 60 valid W nearest-rank q30/q70; C W<=q30; first strict 12:30-13:29 close breakout enters next eligible open in breakout direction and exits 14:30 open.",
        "s2_gate": "unexplained exclusions=0; C state eligible days>=280; C executable breakout>=80; long/short>=25 each; 2021-2024>=10 each; 2025H1>=5; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis JPY0; same C event/entry/exit opposite-side fade; M q30<W<q70 uses identical breakout rule and full common scheduled axis with no-trade JPY0.",
        "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10_000, "method": "non-wrapping MBB; tail truncation; linear percentile; common index"},
        "primary_gate": "C-breakout Net>0; PF>1; C daily MBB lower>0; C-breakout-C-fade paired daily lower>0; C-M group mean daily lower>0.",
        "sensitivities": ["compression_q20", "compression_q40", "lookback40", "lookback80", "entry_extra_1_bar", "exit1415", "exit1445", "cost_2tick", "cost_3tick", "fee_x2"],
        "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(data_config.gold_root, "development")],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files}, "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE.", "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / PREREGISTRATION_DOCUMENT, OUT / "documentation_snapshot" / PREREGISTRATION_DOCUMENT.name)
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r084_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r084_morning_compression_lunch_breakout.py", "src/n225m_bt/strategies/r084_fixed_signal.py", str(source_files[0])],
        "py_compile": [sys.executable, "-m", "py_compile", str(source_files[0])],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=environment)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("pre-execution validation failed")
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    axis = scheduled_axis(calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    axis_set = set(axis)
    for bar in view.bars:
        if bar.trade_date in axis_set and bar.session is Session.DAY:
            bars[(bar.trade_date, bar.session)].append(bar)
    primary_events = assign_w_quintiles(build_events(classifier, axis, bars, isolated))
    s2 = feasibility(primary_events)
    audit = causality_audit(primary_events, axis)
    write_json(OUT / "access_ledger.json", {"stage": "S2 then conditional S3", "split": "development", "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "quarantine": quarantine_audit, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "primary_events.json", primary_events)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_s2_audit.json", audit)
    gate = cast(dict[str, object], s2["gate"])
    if not bool(gate["passed"]) or not bool(audit["passed"]):
        decision = {"status": "INCONCLUSIVE", "reason": "S2_GATE_OR_CAUSALITY_AUDIT_FAILED", "s2_gate": gate, "causality_audit": audit, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    profiles: dict[str, tuple[list[dict[str, object]], int, int, str, bool]] = {
        "compressed_breakout": (primary_events, 1, 30, "C", False), "compressed_fade": (primary_events, 1, 30, "C", True), "middle_breakout": (primary_events, 1, 30, "M", False),
        "compression_q20": (build_events(classifier, axis, bars, isolated, compression_percentile=20), 1, 30, "C", False),
        "compression_q40": (build_events(classifier, axis, bars, isolated, compression_percentile=40), 1, 30, "C", False),
        "lookback40": (build_events(classifier, axis, bars, isolated, lookback=40), 1, 30, "C", False), "lookback80": (build_events(classifier, axis, bars, isolated, lookback=80), 1, 30, "C", False),
        "entry_extra_1_bar": (build_events(classifier, axis, bars, isolated, extra_entry_delay_bars=1), 1, 30, "C", False),
        "exit1415": (build_events(classifier, axis, bars, isolated, exit_time=time(14, 15)), 1, 30, "C", False), "exit1445": (build_events(classifier, axis, bars, isolated, exit_time=time(14, 45)), 1, 30, "C", False),
        "cost_2tick": (primary_events, 2, 30, "C", False), "cost_3tick": (primary_events, 3, 30, "C", False), "fee_x2": (primary_events, 1, 60, "C", False),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily_profiles: dict[str, dict[str, int | None]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (events, ticks, fee, state, fade) in profiles.items():
        trades, daily, unknown = execute_profile(axis, events, bars, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name=name, state=state, fade=fade)
        ledgers[name], daily_profiles[name] = trades, daily
        reports[name] = report(axis, trades, daily, events, detailed=name == "compressed_breakout")
        if unknown:
            unknown_profiles[name] = sorted(target.isoformat() for target in unknown)
        write_json(OUT / f"{name}_events.json", events)
        write_json(OUT / f"{name}_orders_fills.json", order_fill_ledger(trades))
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    no_trade = {target.isoformat(): 0 for target in axis}
    write_json(OUT / "no_trade_control_daily_axis.json", no_trade)
    if unknown_profiles:
        decision = {"status": "INCONCLUSIVE", "reason": "UNKNOWN_FILLED_EXIT", "unknown_profiles": unknown_profiles, "s2_gate": gate, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}})
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    compressed_daily = [cast(int, value) for value in daily_profiles["compressed_breakout"].values()]
    fade_daily = [cast(int, value) for value in daily_profiles["compressed_fade"].values()]
    middle_daily = [cast(int, value) for value in daily_profiles["middle_breakout"].values()]
    boot, index = bootstrap(compressed_daily, fade_daily, middle_daily)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    primary_metrics = cast(dict[str, int | float | None], reports["compressed_breakout"]["metrics"])
    primary_ci = cast(list[float] | None, cast(dict[str, object], boot["compressed_breakout_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    fade_ci = cast(list[float] | None, cast(dict[str, object], boot["compressed_breakout_minus_fade_paired_daily_net_jpy"])["ci95_percentile_linear"])
    middle_ci = cast(list[float] | None, cast(dict[str, object], boot["compressed_minus_middle_group_mean_daily_net_jpy"])["ci95_percentile_linear"])
    gates = {"net_positive": cast(int, primary_metrics["net_pnl_jpy"]) > 0, "pf_gt_one": bool(primary_metrics["profit_factor"] and cast(float, primary_metrics["profit_factor"]) > 1), "compressed_breakout_mbb_ci95_lower_gt_zero": primary_ci is not None and primary_ci[0] > 0, "compressed_breakout_minus_fade_ci95_lower_gt_zero": fade_ci is not None and fade_ci[0] > 0, "compressed_minus_middle_ci95_lower_gt_zero": middle_ci is not None and middle_ci[0] > 0}
    sensitivity_names = ["compression_q20", "compression_q40", "lookback40", "lookback80", "entry_extra_1_bar", "exit1415", "exit1445", "cost_2tick", "cost_3tick", "fee_x2"]
    direction_metrics = cast(dict[str, dict[str, int | float | None]], reports["compressed_breakout"]["by_breakout_direction"])
    year_metrics = cast(dict[str, dict[str, int | float | None]], reports["compressed_breakout"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], reports["compressed_breakout"]["profit_concentration"])
    candidate_checks = {"primary_gate": all(gates.values()), "all_ten_sensitivity_net_positive": all(cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"]) > 0 for name in sensitivity_names), "both_breakout_directions_net_positive": all(cast(int, direction_metrics[side]["net_pnl_jpy"]) > 0 for side in ("long", "short")), "at_least_three_2021_2024_positive": sum(cast(int, year_metrics[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)) >= 3, "2025_h1_net_positive": cast(int, year_metrics["2025"]["net_pnl_jpy"]) > 0, "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0}
    execution_audit = {
        "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()),
        "compressed_breakout_fade_same_event_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["compressed_breakout"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["compressed_fade"]},
        "compressed_and_middle_are_disjoint": {trade.trade_date for trade in ledgers["compressed_breakout"]}.isdisjoint({trade.trade_date for trade in ledgers["middle_breakout"]}),
        "primary_event_breakout_then_next_eligible_open_and_1430_exit": all(trade.entry_signal_ts < trade.entry_ts and trade.exit_signal_ts is not None and trade.exit_ts.time() == time(14, 30) for trade in ledgers["compressed_breakout"]),
        "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades),
    }
    if not all(execution_audit.values()):
        raise ValueError("R084 execution/accounting/causality audit failed")
    write_json(OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}})
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", execution_audit)
    decision = {"status": "INVESTIGATE" if all(gates.values()) else "REJECT", "decision_ceiling": "INVESTIGATE", "s2_gate": gate, "s3_gates": gates, "candidate_checks": candidate_checks, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
