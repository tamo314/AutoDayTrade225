"""Execute the frozen Development-only TASK-R076-Q001 experiment."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r075_high_night_range_opening_breakout import select_state
from n225m_bt.research.r076_tse_opening_failed_auction import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    bootstrap,
    failed_auction_event,
    feasibility,
    scheduled_axis,
)
from n225m_bt.strategies.r076_fixed_signal import R076FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r076-q001-20260915-tse-opening-failed-auction-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREGISTRATION_DOCUMENT = Path("docs/strategy/40_r076_q001_tse_opening_failed_auction.md")
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
    """Reproduce fixed R004 whole-session isolation without mutating normalized Gold."""
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
    return (
        ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}),
        audit,
        isolated,
    )


def build_events(
    axis: list[date], bars_by_day: dict[date, list[Bar]], **overrides: object
) -> dict[date, dict[str, object]]:
    return {target: failed_auction_event(target, bars_by_day.get(target, []), **cast(Any, overrides)) for target in axis}


def engine_for(
    instrument: object, baseline: Any, classifier: CalendarClassifier, *, ticks: int = 1, fee: int = 30
) -> BacktestEngine:
    config = baseline.model_copy(
        update={
            "mode": "day_only",
            "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
            "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
            "risk": baseline.risk.model_copy(
                update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    return BacktestEngine(cast(Any, instrument).instrument.to_spec(), config, classifier)


def _profile_contract(event: dict[str, object], profile: str) -> tuple[str, str, str]:
    if profile == "primary":
        return (
            str(event["failed_fade_direction"]),
            str(event["confirmation_signal_jst"]),
            str(event["entry_open_jst"]),
        )
    if profile == "confirmed_continuation":
        return (
            str(event["initial_breakout_direction"]),
            str(event["confirmation_signal_jst"]),
            str(event["entry_open_jst"]),
        )
    if profile == "unconfirmed_fade":
        return (
            str(event["failed_fade_direction"]),
            str(event["breakout_bar_jst"]),
            str(event["unconfirmed_entry_open_jst"]),
        )
    raise ValueError("unknown R076 profile contract")


def execute_profile(
    axis: list[date],
    events: dict[date, dict[str, object]],
    bars_by_day: dict[date, list[Bar]],
    engine: BacktestEngine,
    *,
    name: str,
    contract: str,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    trades: list[Trade] = []
    unknown: set[date] = set()
    for target in axis:
        event = events[target]
        if event["status"] == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            continue
        if event["status"] != "EXECUTABLE":
            continue
        direction, entry_signal_raw, expected_entry_raw = _profile_contract(event, contract)
        entry_signal = datetime.fromisoformat(entry_signal_raw)
        exit_signal = datetime.fromisoformat(str(event["exit_signal_jst"]))
        result = engine.run(
            bars_by_day[target],
            R076FixedSignalStrategy(f"r076_{name}_{target.isoformat()}", entry_signal, exit_signal, direction),
            parameter_hash=canonical_hash({"profile": name, "event": event, "contract": contract}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
        trade = result.trades[0]
        if (
            trade.entry_ts.isoformat() != expected_entry_raw
            or trade.exit_ts.isoformat() != event["exit_open_jst"]
            or trade.side is not Side(direction)
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{name}/{target}: event/execution mismatch")
        trades.append(trade)
    ordered = tuple(sorted(trades, key=lambda item: item.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), unknown


def report(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int | None],
    events: dict[date, dict[str, object]],
) -> dict[str, object]:
    initial_direction = {
        direction: tuple(
            trade
            for trade in trades
            if events[trade.trade_date].get("initial_breakout_direction") == direction
        )
        for direction in ("long", "short")
    }
    return {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [target for target, value in daily.items() if value is None],
        "by_year": {
            str(year): ledger_metrics(tuple(trade for trade in trades if trade.trade_date.year == year))
            for year in range(2021, 2026)
        },
        "by_initial_breakout_direction": {
            direction: ledger_metrics(initial_direction[direction]) for direction in ("long", "short")
        },
        "profit_concentration": concentration(trades, axis),
    }


def net_positive(value: dict[str, object]) -> bool:
    net = cast(dict[str, int | float | None], value["metrics"])["net_pnl_jpy"]
    return bool(net is not None and net > 0)


def night_state_diagnostic(
    primary_trades: tuple[Trade, ...],
    primary_events: dict[date, dict[str, object]],
    bars_by_day: dict[date, list[Bar]],
    classifier: CalendarClassifier,
    isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Report R075-compatible night states without altering any R076 event or gate."""
    from n225m_bt.research.r075_high_night_range_opening_breakout import r075_event

    labels: dict[date, str] = {}
    unavailable: dict[str, str] = {}
    for target, event in primary_events.items():
        if event["status"] != "EXECUTABLE":
            continue
        probe = r075_event(classifier, target, bars_by_day, isolated=isolated, state_group="high")
        current, q25, q75 = probe.get("current_night_range_points"), probe.get("q25_points"), probe.get("q75_points")
        if isinstance(current, int) and isinstance(q25, int) and isinstance(q75, int):
            state, reason = select_state(current, q25=q25, q75=q75, high_percentile_value=q75)
            if state is not None:
                labels[target] = state
            else:
                unavailable[target.isoformat()] = str(reason)
        else:
            unavailable[target.isoformat()] = str(probe.get("reason", "NIGHT_STATE_UNAVAILABLE"))
    return {
        "diagnostic_only": True,
        "definition": "R075-compatible full night range with exact latest 20 calendar-eligible references: low<=q25, middle q25<R<q75, high>=q75; never used by R076 selection, execution, CI, or decision.",
        "state_counts_among_primary_events": {state: sum(value == state for value in labels.values()) for state in ("low", "middle", "high")},
        "unavailable_state_dates": unavailable,
        "primary_metrics_by_night_state": {
            state: ledger_metrics(tuple(trade for trade in primary_trades if labels.get(trade.trade_date) == state))
            for state in ("low", "middle", "high")
        },
    }


def _copy_snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for path in files:
        shutil.copy2(ROOT / path, destination / path.name)


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r076_q001_tse_opening_failed_auction.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r076_q001_tse_opening_failed_auction.py"),
        Path("src/n225m_bt/research/r076_tse_opening_failed_auction.py"),
        Path("src/n225m_bt/strategies/r076_fixed_signal.py"),
        Path("src/n225m_bt/research/r075_high_night_range_opening_breakout.py"),
        Path("src/n225m_bt/research/r074_low_night_range_opening_breakout.py"),
        Path("tests/test_r076_q001.py"),
    ]
    config_files = [
        Path(f"config/{name}")
        for name in (
            "backtest.yaml",
            "data.yaml",
            "instrument.yaml",
            "sessions.yaml",
            "local_calendar.yaml",
            "research.yaml",
        )
    ]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    partitions = partition_paths(data_config.gold_root, "development")
    preregistration = {
        "task_id": "TASK-R076-Q001",
        "family_id": "tse_opening_failed_auction",
        "study_id": "R076-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "preregistration_document": str(PREREGISTRATION_DOCUMENT),
        "preregistration_document_sha256": digest(ROOT / PREREGISTRATION_DOCUMENT),
        "prior_information_seen": True,
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10_000,
            "method": "non-wrapping MBB; tail truncation; linear percentile",
        },
        "frozen_rule": "No night selection. 09:00-09:29 range; first strict close breakout 09:30-10:30; first strict internal close after breakout within 30 bars and by 11:00; next eligible open reverse entry; 14:30 exit.",
        "strictness": "close equality is neither breakout nor return; return is strictly inside the opening range; primary confirmation uses one close.",
        "controls": "Scheduled-axis JPY0; identical failed event, confirmed entry and exit in initial breakout direction; same failed-event days only, immediate post-breakout opposite-side fade as a diagnostic ablation.",
        "s2_gate": "unexplained exclusions=0; executable failed events>=200; initial long/short>=60 each; 2021-2024>=25 each; 2025H1>=12; otherwise INCONCLUSIVE before PnL.",
        "primary_gate": "primary Net>0; PF>1; primary daily MBB lower>0; primary-confirmed-continuation paired daily difference lower>0; primary-unconfirmed-fade paired daily difference lower>0.",
        "sensitivities": ["return_15_bars", "return_45_bars", "opening_20", "opening_45", "two_internal_closes", "entry_delay_1m", "exit_1415", "exit_1445", "cost_2tick", "cost_3tick", "fee_x2"],
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE.",
        "night_state_diagnostic": "R075 q25/M/q75 only; not a primary condition, sensitivity, rescue, or selection input.",
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
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r076_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r076_tse_opening_failed_auction.py", "src/n225m_bt/strategies/r076_fixed_signal.py", str(source_files[0])],
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
    axis_set = set(axis)
    bars_by_day: dict[date, list[Bar]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in axis_set and bar.session in {Session.NIGHT, Session.DAY}:
            bars_by_day[bar.trade_date].append(bar)
    primary_events = build_events(axis, bars_by_day)
    s2 = feasibility(primary_events)
    write_json(OUT / "access_ledger.json", {"stage": "S2 then conditional S3", "split": "development", "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "quarantine": quarantine_audit, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "s2_events_primary.json", {target.isoformat(): event for target, event in primary_events.items()})
    s2_gate = cast(dict[str, object], s2["gate"])
    if not bool(s2_gate["passed"]):
        decision = {"status": "INCONCLUSIVE", "reason": "S2_GATE_FAILED", "s2_gate": s2_gate, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    unavailable_control = [
        target.isoformat()
        for target, event in primary_events.items()
        if event["status"] == "EXECUTABLE" and not bool(event["unconfirmed_entry_observable"])
    ]
    if unavailable_control:
        decision = {"status": "INCONCLUSIVE", "reason": "UNCONFIRMED_FADE_CONTROL_ENTRY_UNAVAILABLE", "trade_dates": unavailable_control, "s2_gate": s2_gate, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return

    profiles: dict[str, tuple[dict[str, object], int, int, str]] = {
        "primary": ({}, 1, 30, "primary"),
        "confirmed_continuation": ({}, 1, 30, "confirmed_continuation"),
        "unconfirmed_fade": ({}, 1, 30, "unconfirmed_fade"),
        "return_15_bars": ({"return_deadline_bars": 15}, 1, 30, "primary"),
        "return_45_bars": ({"return_deadline_bars": 45}, 1, 30, "primary"),
        "opening_20": ({"opening_minutes": 20}, 1, 30, "primary"),
        "opening_45": ({"opening_minutes": 45}, 1, 30, "primary"),
        "two_internal_closes": ({"consecutive_internal_closes": 2}, 1, 30, "primary"),
        "entry_delay_1m": ({"entry_delay_minutes": 1}, 1, 30, "primary"),
        "exit_1415": ({"exit_time": time(14, 15)}, 1, 30, "primary"),
        "exit_1445": ({"exit_time": time(14, 45)}, 1, 30, "primary"),
        "cost_2tick": ({}, 2, 30, "primary"),
        "cost_3tick": ({}, 3, 30, "primary"),
        "fee_x2": ({}, 1, 60, "primary"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    events_by_profile: dict[str, dict[date, dict[str, object]]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (overrides, ticks, fee, contract) in profiles.items():
        events = primary_events if name in {"primary", "confirmed_continuation", "unconfirmed_fade"} else build_events(axis, bars_by_day, **cast(Any, overrides))
        trades, daily, unknown = execute_profile(axis, events, bars_by_day, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name=name, contract=contract)
        ledgers[name], events_by_profile[name] = trades, events
        reports[name] = report(axis, trades, daily, events)
        if unknown:
            unknown_profiles[name] = sorted(target.isoformat() for target in unknown)
        write_json(OUT / f"{name}_events.json", {target.isoformat(): event for target, event in events.items()})
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    if unknown_profiles:
        decision = {"status": "INCONCLUSIVE", "reason": "UNKNOWN_FILLED_EXIT", "unknown_profiles": unknown_profiles, "s2_gate": s2_gate, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "profiles.json", reports)
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return

    primary_daily = list(cast(dict[str, int], reports["primary"]["daily_net_pnl_jpy"]).values())
    continuation_daily = list(cast(dict[str, int], reports["confirmed_continuation"]["daily_net_pnl_jpy"]).values())
    unconfirmed_daily = list(cast(dict[str, int], reports["unconfirmed_fade"]["daily_net_pnl_jpy"]).values())
    boot, index = bootstrap(primary_daily, continuation_daily, unconfirmed_daily)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    primary_metrics = cast(dict[str, int | float | None], reports["primary"]["metrics"])
    primary_ci = cast(dict[str, object], boot["primary_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"]
    continuation_ci = cast(dict[str, object], boot["primary_minus_confirmed_continuation_daily_net_jpy"])["ci95_percentile_linear"]
    unconfirmed_ci = cast(dict[str, object], boot["primary_minus_unconfirmed_fade_daily_net_jpy"])["ci95_percentile_linear"]
    gates = {
        "net_positive": cast(int, primary_metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(primary_metrics["profit_factor"] and cast(float, primary_metrics["profit_factor"]) > 1),
        "primary_mbb_ci95_lower_gt_zero": cast(list[float], primary_ci)[0] > 0,
        "primary_minus_confirmed_continuation_ci95_lower_gt_zero": cast(list[float], continuation_ci)[0] > 0,
        "primary_minus_unconfirmed_fade_ci95_lower_gt_zero": cast(list[float], unconfirmed_ci)[0] > 0,
    }
    primary_pass = all(gates.values())
    sensitivity_names = ["return_15_bars", "return_45_bars", "opening_20", "opening_45", "two_internal_closes", "entry_delay_1m", "exit_1415", "exit_1445", "cost_2tick", "cost_3tick", "fee_x2"]
    direction_metrics = cast(dict[str, dict[str, int | float | None]], reports["primary"]["by_initial_breakout_direction"])
    year_metrics = cast(dict[str, dict[str, int | float | None]], reports["primary"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], reports["primary"]["profit_concentration"])
    candidate_checks = {
        "primary_gate": primary_pass,
        "all_eleven_sensitivity_net_positive": all(net_positive(reports[name]) for name in sensitivity_names),
        "both_initial_direction_net_positive": all(cast(int, direction_metrics[side]["net_pnl_jpy"]) > 0 for side in ("long", "short")),
        "at_least_three_2021_2024_positive": sum(cast(int, year_metrics[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)) >= 3,
        "2025_h1_net_positive": cast(int, year_metrics["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0,
    }
    audit = {
        "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()),
        "primary_continuation_same_event_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["primary"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["confirmed_continuation"]},
        "unconfirmed_fade_same_failed_event_and_exit": {trade.trade_date for trade in ledgers["primary"]} == {trade.trade_date for trade in ledgers["unconfirmed_fade"]} and {trade.exit_ts for trade in ledgers["primary"]} == {trade.exit_ts for trade in ledgers["unconfirmed_fade"]},
        "confirmation_is_after_breakout_and_before_registered_limit": all(datetime.fromisoformat(str(event["breakout_bar_jst"])) < datetime.fromisoformat(str(event["confirmation_bar_jst"])) <= min(datetime.fromisoformat(str(event["breakout_bar_jst"])) + timedelta(minutes=cast(int, event["return_deadline_bars"])), datetime.combine(target, time(11), datetime.fromisoformat(str(event["breakout_bar_jst"])).tzinfo)) for target, event in primary_events.items() if event["status"] == "EXECUTABLE"),
        "day_only_no_holiday_spanning_position": all(trade.entry_ts.date() == trade.exit_ts.date() == trade.trade_date for trades in ledgers.values() for trade in trades),
        "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades),
    }
    if not all(audit.values()):
        raise ValueError("R076 execution/accounting/causality audit failed")
    write_json(OUT / "profiles.json", reports)
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    write_json(OUT / "night_state_diagnostics.json", night_state_diagnostic(ledgers["primary"], primary_events, bars_by_day, classifier, isolated))
    decision = {"status": "INVESTIGATE" if primary_pass else "REJECT", "decision_ceiling": "INVESTIGATE", "s2_gate": s2_gate, "s3_gates": gates, "candidate_checks": candidate_checks, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
