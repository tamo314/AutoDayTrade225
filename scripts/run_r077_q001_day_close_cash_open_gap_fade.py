"""Execute the frozen Development-only TASK-R077-Q001 experiment."""

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
from n225m_bt.research.r077_day_close_cash_open_gap_fade import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    bootstrap,
    feasibility,
    r077_event,
    scheduled_axis,
    state_ledger,
)
from n225m_bt.strategies.r077_fixed_signal import R077FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r077-q001-20260915-day-close-cash-open-gap-fade-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREGISTRATION_DOCUMENT = Path("docs/strategy/42_r077_q001_day_close_cash_open_gap_fade.md")
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


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    """Reproduce R004 whole-session isolation without changing normalized Gold."""
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
        raise ValueError(f"BLOCKED: R004 fixed quarantine mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def build_events(
    classifier: CalendarClassifier,
    axis: list[date],
    bars_by_session: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = 60,
    upper_percentile: int = 80,
    entry_delay_minutes: int = 0,
    exit_time: time = time(10),
) -> list[dict[str, object]]:
    states = state_ledger(
        classifier,
        axis,
        bars_by_session,
        isolated,
        lookback=lookback,
        upper_percentile=upper_percentile,
    )
    return [r077_event(state, bars_by_session, entry_delay_minutes=entry_delay_minutes, exit_time=exit_time) for state in states]


def engine_for(instrument: object, baseline: Any, classifier: CalendarClassifier, *, ticks: int = 1, fee: int = 30) -> BacktestEngine:
    config = baseline.model_copy(
        update={
            "mode": "day_only",
            "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
            "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
            "risk": baseline.risk.model_copy(update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}),
        }
    )
    return BacktestEngine(cast(Any, instrument).instrument.to_spec(), config, classifier)


def execute_profile(
    axis: list[date],
    events: list[dict[str, object]],
    bars_by_session: dict[tuple[date, Session], list[Any]],
    engine: BacktestEngine,
    *,
    name: str,
    state: str,
    direction: str,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    by_date = {date.fromisoformat(cast(str, event["trade_date"])): event for event in events}
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
        actual_direction = cast(str, event[f"{direction}_direction"])
        entry_signal = datetime.fromisoformat(cast(str, event["entry_signal_jst"]))
        exit_signal = datetime.fromisoformat(cast(str, event["exit_signal_jst"]))
        result = engine.run(
            bars_by_session[(target, Session.DAY)],
            R077FixedSignalStrategy(f"r077_{name}_{target.isoformat()}", entry_signal, exit_signal, actual_direction),
            parameter_hash=canonical_hash({"profile": name, "event": event, "direction": direction}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
        trade = result.trades[0]
        if (
            trade.entry_ts.isoformat() != event["entry_open_jst"]
            or trade.exit_ts.isoformat() != event["exit_open_jst"]
            or trade.side is not Side(actual_direction)
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{name}/{target}: event/execution mismatch")
        trades.append(trade)
    ordered = tuple(sorted(trades, key=lambda item: item.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), unknown


def report(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], events: list[dict[str, object]]) -> dict[str, object]:
    by_date = {date.fromisoformat(cast(str, event["trade_date"])): event for event in events}
    by_sign = {
        sign: tuple(trade for trade in trades if (cast(int, by_date[trade.trade_date]["g_points"]) > 0) == (sign == "positive"))
        for sign in ("positive", "negative")
    }
    return {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [target for target, value in daily.items() if value is None],
        "by_year": {str(year): ledger_metrics(tuple(trade for trade in trades if trade.trade_date.year == year)) for year in range(2021, 2026)},
        "by_g_sign": {sign: ledger_metrics(by_sign[sign]) for sign in ("positive", "negative")},
        "profit_concentration": concentration(trades, axis),
    }


def net_positive(value: dict[str, object]) -> bool:
    net = cast(dict[str, int | float | None], value["metrics"])["net_pnl_jpy"]
    return bool(net is not None and net > 0)


def _copy_snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for path in files:
        shutil.copy2(ROOT / path, destination / path.name)


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r077_q001_day_close_cash_open_gap_fade.py"),
        Path("src/n225m_bt/research/r077_day_close_cash_open_gap_fade.py"),
        Path("src/n225m_bt/strategies/r077_fixed_signal.py"),
        Path("tests/test_r077_q001.py"),
    ]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    partitions = partition_paths(data_config.gold_root, "development")
    preregistration = {
        "task_id": "TASK-R077-Q001",
        "family_id": "prior_day_session_close_cash_open_gap_fade",
        "study_id": "R077-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "preregistration_document": str(PREREGISTRATION_DOCUMENT),
        "preregistration_document_sha256": digest(ROOT / PREREGISTRATION_DOCUMENT),
        "prior_information_seen": True,
        "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10_000, "method": "non-wrapping MBB; tail truncation; linear percentile"},
        "frozen_rule": "C=prior versioned day-session close bar close; O=current 09:00 bar open; G=O-C; prior 60 valid nonzero G values; E abs(G)>=q80 fades from next eligible open after 09:00 observation through 10:00 open.",
        "controls": "scheduled-axis JPY0; same E event/entry/exit G-direction continuation; M q20<abs(G)<q80 same fade rule.",
        "s2_gate": "unexplained exclusions=0; E executable>=150; E G positive/negative>=50 each; 2021-2024 E>=20 each; 2025H1 E>=10; otherwise INCONCLUSIVE before PnL.",
        "primary_gate": "E-fade Net>0; PF>1; E daily MBB lower>0; E-fade-continuation paired daily lower>0; E-fade-M-fade per-trade block-bootstrap lower>0.",
        "sensitivities": ["q70", "q90", "lookback40", "lookback80", "exit0945", "exit1030", "entry_delay_1m", "cost_2tick", "cost_3tick", "fee_x2"],
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE.",
        "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partitions],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    _copy_snapshot(OUT / "source_snapshot", source_files)
    _copy_snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / PREREGISTRATION_DOCUMENT, OUT / "documentation_snapshot" / PREREGISTRATION_DOCUMENT.name)

    command_env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r077_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r077_day_close_cash_open_gap_fade.py", "src/n225m_bt/strategies/r077_fixed_signal.py", str(source_files[0])],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=command_env)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("pre-execution validation failed")

    instrument, sessions, _, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    axis = scheduled_axis(calendar)
    bars_by_session: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in set(axis) and bar.session is Session.DAY:
            bars_by_session[(bar.trade_date, bar.session)].append(bar)
    primary_events = build_events(classifier, axis, bars_by_session, isolated)
    s2 = feasibility(primary_events)
    write_json(OUT / "access_ledger.json", {"stage": "S2 then conditional S3", "split": "development", "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "quarantine": quarantine_audit, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "primary_events.json", primary_events)
    gate = cast(dict[str, object], s2["gate"])
    if not bool(gate["passed"]):
        decision = {"status": "INCONCLUSIVE", "reason": "S2_GATE_FAILED", "s2_gate": gate, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return

    profiles: dict[str, tuple[dict[str, object], int, int, str, str]] = {
        "extreme_fade": ({}, 1, 30, "E", "fade"),
        "extreme_continuation": ({}, 1, 30, "E", "continuation"),
        "middle_fade": ({}, 1, 30, "M", "fade"),
        "q70": ({"upper_percentile": 70}, 1, 30, "E", "fade"),
        "q90": ({"upper_percentile": 90}, 1, 30, "E", "fade"),
        "lookback40": ({"lookback": 40}, 1, 30, "E", "fade"),
        "lookback80": ({"lookback": 80}, 1, 30, "E", "fade"),
        "exit0945": ({"exit_time": time(9, 45)}, 1, 30, "E", "fade"),
        "exit1030": ({"exit_time": time(10, 30)}, 1, 30, "E", "fade"),
        "entry_delay_1m": ({"entry_delay_minutes": 1}, 1, 30, "E", "fade"),
        "cost_2tick": ({}, 2, 30, "E", "fade"),
        "cost_3tick": ({}, 3, 30, "E", "fade"),
        "fee_x2": ({}, 1, 60, "E", "fade"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    event_sets: dict[str, list[dict[str, object]]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (overrides, ticks, fee, state, direction) in profiles.items():
        events = primary_events if name in {"extreme_fade", "extreme_continuation", "middle_fade", "cost_2tick", "cost_3tick", "fee_x2"} else build_events(classifier, axis, bars_by_session, isolated, **cast(Any, overrides))
        trades, daily, unknown = execute_profile(axis, events, bars_by_session, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name=name, state=state, direction=direction)
        ledgers[name], event_sets[name] = trades, events
        reports[name] = report(axis, trades, daily, events)
        if unknown:
            unknown_profiles[name] = sorted(target.isoformat() for target in unknown)
        write_json(OUT / f"{name}_events.json", events)
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    if unknown_profiles:
        decision = {"status": "INCONCLUSIVE", "reason": "UNKNOWN_FILLED_EXIT", "unknown_profiles": unknown_profiles, "s2_gate": gate, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "profiles.json", reports)
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return

    extreme_daily = list(cast(dict[str, int], reports["extreme_fade"]["daily_net_pnl_jpy"]).values())
    continuation_daily = list(cast(dict[str, int], reports["extreme_continuation"]["daily_net_pnl_jpy"]).values())
    middle_daily = list(cast(dict[str, int], reports["middle_fade"]["daily_net_pnl_jpy"]).values())
    extreme_counts = [int(event.get("state") == "E" and event.get("status") == "EXECUTABLE") for event in primary_events]
    middle_counts = [int(event.get("state") == "M" and event.get("status") == "EXECUTABLE") for event in primary_events]
    boot, index = bootstrap(extreme_daily, continuation_daily, extreme_counts, middle_daily, middle_counts)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    primary_metrics = cast(dict[str, int | float | None], reports["extreme_fade"]["metrics"])
    primary_ci = cast(list[float] | None, cast(dict[str, object], boot["extreme_fade_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    paired_ci = cast(list[float] | None, cast(dict[str, object], boot["extreme_fade_minus_continuation_paired_daily_net_jpy"])["ci95_percentile_linear"])
    middle_ci = cast(list[float] | None, cast(dict[str, object], boot["extreme_fade_minus_middle_fade_net_jpy_per_trade"])["ci95_percentile_linear"])
    gates = {
        "net_positive": cast(int, primary_metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(primary_metrics["profit_factor"] and cast(float, primary_metrics["profit_factor"]) > 1),
        "extreme_fade_mbb_ci95_lower_gt_zero": primary_ci is not None and primary_ci[0] > 0,
        "extreme_fade_minus_continuation_ci95_lower_gt_zero": paired_ci is not None and paired_ci[0] > 0,
        "extreme_fade_minus_middle_fade_ci95_lower_gt_zero": middle_ci is not None and middle_ci[0] > 0,
    }
    sensitivity_names = ["q70", "q90", "lookback40", "lookback80", "exit0945", "exit1030", "entry_delay_1m", "cost_2tick", "cost_3tick", "fee_x2"]
    g_metrics = cast(dict[str, dict[str, int | float | None]], reports["extreme_fade"]["by_g_sign"])
    year_metrics = cast(dict[str, dict[str, int | float | None]], reports["extreme_fade"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], reports["extreme_fade"]["profit_concentration"])
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_ten_sensitivity_net_positive": all(net_positive(reports[name]) for name in sensitivity_names),
        "both_g_sign_net_positive": all(cast(int, g_metrics[sign]["net_pnl_jpy"]) > 0 for sign in ("positive", "negative")),
        "at_least_three_2021_2024_positive": sum(cast(int, year_metrics[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)) >= 3,
        "2025_h1_net_positive": cast(int, year_metrics["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0,
    }
    audit = {
        "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()),
        "extreme_fade_continuation_same_event_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["extreme_fade"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["extreme_continuation"]},
        "extreme_and_middle_are_disjoint": {trade.trade_date for trade in ledgers["extreme_fade"]}.isdisjoint({trade.trade_date for trade in ledgers["middle_fade"]}),
        "fixed_0900_selection_next_open_entry_and_exit": all(trade.entry_signal_ts.time() == time(9) and trade.entry_ts.time() == time(9, 1) and trade.exit_signal_ts is not None and trade.exit_signal_ts.time() == time(9, 59) and trade.exit_ts.time() == time(10) for trade in ledgers["extreme_fade"]),
        "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades),
    }
    if not all(audit.values()):
        raise ValueError("R077 execution/accounting/causality audit failed")
    write_json(OUT / "profiles.json", reports)
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {"status": "INVESTIGATE" if all(gates.values()) else "REJECT", "decision_ceiling": "INVESTIGATE", "s2_gate": gate, "s3_gates": gates, "candidate_checks": candidate_checks, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
