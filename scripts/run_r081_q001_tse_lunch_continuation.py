"""Execute frozen Development-only TASK-R081-Q001 exactly once."""

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
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r078_cash_first_hour_extreme_fade import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    aligned_daily_net,
    scheduled_axis,
)
from n225m_bt.research.r081_tse_lunch_continuation import (
    MBB_SEED,
    TRIMMED_WINDOW,
    _execution_event,
    assign_abs_l_quintiles,
    bootstrap,
    build_events,
    feasibility,
    morning_sign_events,
)
from n225m_bt.strategies.r081_fixed_signal import R081FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r081-q001-20260915-tse-lunch-continuation-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREGISTRATION_DOCUMENT = Path("docs/strategy/50_r081_q001_tse_lunch_continuation.md")
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [row for key, rows in grouped.items() if key not in isolated for row in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included),
    }
    expected = {
        "parent_data_version": PARENT_HASH,
        "quarantined_sessions": 45,
        "quarantined_bars": 27345,
        "included_sessions": 2216,
        "included_bars": 1326086,
        "quarantined_session_list_hash": QUARANTINE_HASH,
        "included_tick_grid_violations": 0,
    }
    mismatches = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    audit.update(expected_match=not mismatches, mismatches=mismatches)
    if mismatches:
        raise ValueError(f"BLOCKED: fixed R004 quarantine mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def engine_for(instrument: object, baseline: Any, classifier: CalendarClassifier, *, ticks: int = 1, fee: int = 30) -> BacktestEngine:
    config = baseline.model_copy(update={
        "mode": "day_only",
        "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
        "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
        "risk": baseline.risk.model_copy(update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}),
    })
    return BacktestEngine(cast(Any, instrument).instrument.to_spec(), config, classifier)


def execute_profile(axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Any]], engine: BacktestEngine, *, name: str, direction: str) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    by_date = {date.fromisoformat(cast(str, event["trade_date"])): event for event in events}
    trades: list[Trade] = []
    unknown: set[date] = set()
    for target in axis:
        event = by_date[target]
        if event.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            continue
        if event.get("status") != "EXECUTABLE":
            continue
        actual_direction = cast(str, event[f"{direction}_direction"])
        result = engine.run(
            bars[(target, Session.DAY)],
            R081FixedSignalStrategy(
                f"r081_{name}_{target.isoformat()}",
                datetime.fromisoformat(cast(str, event["entry_signal_jst"])),
                datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
                actual_direction,
            ),
            parameter_hash=canonical_hash({"profile": name, "event": event, "direction": actual_direction}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
        trade = result.trades[0]
        if trade.entry_ts.isoformat() != event["entry_open_jst"] or trade.exit_ts.isoformat() != event["exit_open_jst"] or trade.side is not Side(actual_direction) or trade.exit_reason is not ExitReason.SIGNAL or trade.qty != 1:
            raise ValueError(f"{name}/{target}: event/execution mismatch")
        trades.append(trade)
    ordered = tuple(sorted(trades, key=lambda item: item.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), unknown


def report(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], events: list[dict[str, object]], *, include_quintiles: bool = False) -> dict[str, object]:
    by_date = {date.fromisoformat(cast(str, event["trade_date"])): event for event in events}
    by_l_sign = {sign: tuple(trade for trade in trades if (cast(int, by_date[trade.trade_date]["l_points"]) > 0) == (sign == "positive")) for sign in ("positive", "negative")}
    result: dict[str, object] = {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [target for target, value in daily.items() if value is None],
        "by_year": {str(year): ledger_metrics(tuple(trade for trade in trades if trade.trade_date.year == year)) for year in range(2021, 2026)},
        "by_l_sign": {sign: ledger_metrics(by_l_sign[sign]) for sign in ("positive", "negative")},
        "profit_concentration": concentration(trades, axis),
    }
    if include_quintiles:
        result["by_abs_l_quintile"] = {str(quintile): ledger_metrics(tuple(trade for trade in trades if by_date[trade.trade_date].get("abs_l_quintile") == quintile)) for quintile in range(1, 6)}
        result["by_l_m_sign_relation"] = {
            relation: ledger_metrics(tuple(trade for trade in trades if bool(by_date[trade.trade_date].get("m_valid")) and ((cast(int, by_date[trade.trade_date]["l_points"]) * cast(int, by_date[trade.trade_date]["m_points"]) > 0) == (relation == "same"))))
            for relation in ("same", "opposite")
        }
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


def snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for path in files:
        shutil.copy2(ROOT / path, destination / path.name)


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r081_q001_tse_lunch_continuation.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r081_q001_tse_lunch_continuation.py"),
        Path("src/n225m_bt/research/r081_tse_lunch_continuation.py"),
        Path("src/n225m_bt/strategies/r081_fixed_signal.py"),
        Path("tests/test_r081_q001.py"),
    ]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    preregistration = {
        "task_id": "TASK-R081-Q001", "family_id": "tse_cash_lunch_information_price_discovery", "study_id": "R081-Q001", "spec_version": "v1", "run_id": RUN_ID, "protocol_revision": "RG-20260915-01", "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "preregistration_document": str(PREREGISTRATION_DOCUMENT), "preregistration_document_sha256": digest(ROOT / PREREGISTRATION_DOCUMENT), "prior_information_seen": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "L=B(12:29 close)-A(11:30 open); every nonzero executable L enters in L direction at 12:30 open and exits at 14:30 open; no gap, night range, opening range, 09:00-09:59 direction, post-12:30 information, or abs(L) selection.",
        "s2_gate": "unexplained exclusions=0; executable>=900; L positive/negative>=350 each; 2021-2024>=150 each; 2025H1>=70; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis no-trade JPY0; same-day/same-entry/same-exit L fade; on same executable L!=0/M!=0 dates only, same-entry/same-exit morning M-sign control where M=11:29 close-09:00 open.",
        "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10_000, "method": "non-wrapping MBB; tail truncation; linear percentile"},
        "primary_gate": "continuation Net>0; PF>1; continuation daily MBB lower>0; continuation-fade paired daily lower>0; continuation-morning-sign-control paired daily lower>0 on the preregistered common L/M axis.",
        "sensitivities": ["exclude_lunch_window_ends_5m", "entry_delay_1m", "exit1415", "exit1445", "cost_2tick", "cost_3tick", "fee_x2"],
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE.", "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED",
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files}, "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / PREREGISTRATION_DOCUMENT, OUT / "documentation_snapshot" / PREREGISTRATION_DOCUMENT.name)
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r081_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r081_tse_lunch_continuation.py", "src/n225m_bt/strategies/r081_fixed_signal.py"],
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
    events = assign_abs_l_quintiles(build_events(classifier, axis, bars, isolated))
    s2 = feasibility(events)
    write_json(OUT / "primary_events.json", events)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "access_ledger.json", {"stage": "S2 PnL-free R081 feasibility then conditional S3", "split": "development", "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "quarantine": quarantine_audit, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    if not bool(cast(dict[str, object], s2["gate"])["passed"]):
        decision = {"status": "INCONCLUSIVE", "reason": "R081_PNL_FREE_FEASIBILITY_GATE_FAILED", "s2_feasibility": s2, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    profiles: dict[str, tuple[list[dict[str, object]], int, int, str]] = {
        "continuation": (events, 1, 30, "continuation"), "fade": (events, 1, 30, "fade"), "morning_sign_control": (morning_sign_events(events), 1, 30, "morning"),
        "exclude_lunch_window_ends_5m": (build_events(classifier, axis, bars, isolated, window=TRIMMED_WINDOW), 1, 30, "continuation"),
        "entry_delay_1m": ([_execution_event(event, bars, entry_delay_minutes=1) for event in events], 1, 30, "continuation"),
        "exit1415": ([_execution_event(event, bars, exit_time=time(14, 15)) for event in events], 1, 30, "continuation"),
        "exit1445": ([_execution_event(event, bars, exit_time=time(14, 45)) for event in events], 1, 30, "continuation"),
        "cost_2tick": (events, 2, 30, "continuation"), "cost_3tick": (events, 3, 30, "continuation"), "fee_x2": (events, 1, 60, "continuation"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily_profiles: dict[str, dict[str, int | None]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (events_for_profile, ticks, fee, direction) in profiles.items():
        trades, daily, unknown = execute_profile(axis, events_for_profile, bars, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name=name, direction=direction)
        ledgers[name], daily_profiles[name] = trades, daily
        reports[name] = report(axis, trades, daily, events_for_profile, include_quintiles=name == "continuation")
        if unknown:
            unknown_profiles[name] = sorted(target.isoformat() for target in unknown)
        write_json(OUT / f"{name}_events.json", events_for_profile)
        write_json(OUT / f"{name}_orders_fills.json", order_fill_ledger(trades))
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    no_trade = {target.isoformat(): 0 for target in axis}
    write_json(OUT / "no_trade_control_daily_axis.json", no_trade)
    if unknown_profiles:
        decision = {"status": "INCONCLUSIVE", "reason": "UNKNOWN_FILLED_EXIT", "unknown_profiles": unknown_profiles, "s2_feasibility": s2, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade}})
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    common_dates = [date.fromisoformat(cast(str, event["trade_date"])) for event in morning_sign_events(events) if event.get("status") == "EXECUTABLE"]
    common_axis = [target.isoformat() for target in common_dates]
    common_continuation = [cast(int, daily_profiles["continuation"][target]) for target in common_axis]
    common_morning = [cast(int, daily_profiles["morning_sign_control"][target]) for target in common_axis]
    write_json(OUT / "morning_sign_common_event_axis.json", {"trade_dates": common_axis, "count": len(common_axis), "continuation_daily_net_jpy": common_continuation, "morning_sign_control_daily_net_jpy": common_morning})
    continuation_daily = [cast(int, value) for value in daily_profiles["continuation"].values()]
    fade_daily = [cast(int, value) for value in daily_profiles["fade"].values()]
    boot, primary_index, morning_index = bootstrap(continuation_daily, fade_daily, common_continuation, common_morning)
    np.save(OUT / "bootstrap_primary_axis_indices.npy", primary_index)
    np.save(OUT / "bootstrap_morning_common_axis_indices.npy", morning_index)
    primary_metrics = cast(dict[str, int | float | None], reports["continuation"]["metrics"])
    primary_ci = cast(list[float], cast(dict[str, object], boot["continuation_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    fade_ci = cast(list[float], cast(dict[str, object], boot["continuation_minus_fade_paired_daily_net_jpy"])["ci95_percentile_linear"])
    morning_ci = cast(list[float], cast(dict[str, object], boot["continuation_minus_morning_sign_control_paired_daily_net_jpy_on_common_l_m_axis"])["ci95_percentile_linear"])
    gates = {"net_positive": cast(int, primary_metrics["net_pnl_jpy"]) > 0, "pf_gt_one": bool(primary_metrics["profit_factor"] and cast(float, primary_metrics["profit_factor"]) > 1), "continuation_mbb_ci95_lower_gt_zero": primary_ci[0] > 0, "continuation_minus_fade_ci95_lower_gt_zero": fade_ci[0] > 0, "continuation_minus_morning_sign_control_ci95_lower_gt_zero": morning_ci[0] > 0}
    sensitivity_names = ["exclude_lunch_window_ends_5m", "entry_delay_1m", "exit1415", "exit1445", "cost_2tick", "cost_3tick", "fee_x2"]
    l_metrics = cast(dict[str, dict[str, int | float | None]], reports["continuation"]["by_l_sign"])
    year_metrics = cast(dict[str, dict[str, int | float | None]], reports["continuation"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], reports["continuation"]["profit_concentration"])
    candidate_checks = {"primary_gate": all(gates.values()), "all_fixed_sensitivity_net_positive": all(cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"]) > 0 for name in sensitivity_names), "both_l_sign_net_positive": all(cast(int, l_metrics[sign]["net_pnl_jpy"]) > 0 for sign in ("positive", "negative")), "at_least_three_2021_2024_positive": sum(cast(int, year_metrics[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)) >= 3, "2025_h1_net_positive": cast(int, year_metrics["2025"]["net_pnl_jpy"]) > 0, "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0}
    audit = {
        "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()),
        "primary_continuation_fade_same_event_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["continuation"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["fade"]},
        "primary_selection_at_1229_then_next_open_entry_and_fixed_exit": all(trade.entry_signal_ts.time() == time(12, 29) and trade.entry_ts.time() == time(12, 30) and trade.exit_signal_ts is not None and trade.exit_signal_ts.time() == time(14, 29) and trade.exit_ts.time() == time(14, 30) for trade in ledgers["continuation"]),
        "morning_control_is_same_l_m_common_dates_and_entry_exit": {(trade.trade_date, trade.entry_ts, trade.exit_ts) for trade in ledgers["morning_sign_control"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts) for trade in ledgers["continuation"] if trade.trade_date.isoformat() in set(common_axis)},
        "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades),
    }
    if not all(audit.values()):
        raise ValueError("R081 execution/accounting/causality audit failed")
    write_json(OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}})
    write_json(OUT / "bootstrap.json", boot | {"primary_axis_index_file": "bootstrap_primary_axis_indices.npy", "morning_common_axis_index_file": "bootstrap_morning_common_axis_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {"status": "INVESTIGATE" if all(gates.values()) else "REJECT", "decision_ceiling": "INVESTIGATE", "s2_feasibility": s2, "s3_gates": gates, "candidate_checks": candidate_checks, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
