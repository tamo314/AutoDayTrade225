"""Execute frozen Development-only TASK-R086-Q001 exactly once."""

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
from n225m_bt.research.r086_intraday_same_clock_shock_fade import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    bootstrap,
    build_events,
    feasibility,
    pre_shock_control_events,
    scheduled_axis,
)
from n225m_bt.strategies.r086_fixed_signal import R086FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r086-q001-20260915-intraday-same-clock-shock-fade-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREGISTRATION_DOCUMENT = Path("docs/strategy/65_r086_q001_intraday_same_clock_shock_fade.md")
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


def snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for path in files:
        shutil.copy2(ROOT / path, destination / path.name)


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in row.quality_flags for row in included
        ),
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


def execute_profile(
    axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Any]],
    engine: BacktestEngine, *, name: str, direction: str,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
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
        if direction == "pre":
            if event.get("pre_shock_sign_control_status") != "EXECUTABLE_COMMON_PREVIOUS_NONZERO_BLOCK":
                continue
            side = cast(str, event["pre_shock_direction"])
        else:
            side = cast(str, event[f"{direction}_direction"])
        result = engine.run(
            bars[(target, Session.DAY)],
            R086FixedSignalStrategy(
                f"r086_{name}_{target.isoformat()}",
                datetime.fromisoformat(cast(str, event["entry_signal_jst"])),
                datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
                side,
            ),
            parameter_hash=canonical_hash({"profile": name, "event": event, "direction": side}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
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
    ordered = tuple(sorted(trades, key=lambda trade: trade.trade_date))
    return ordered, aligned_daily_net(axis, {trade.trade_date: trade for trade in ordered}, unknown), unknown


def assign_abs_x_quintiles(events: list[dict[str, object]]) -> list[dict[str, object]]:
    ranked = sorted(
        (event for event in events if event.get("status") == "EXECUTABLE"),
        key=lambda event: (cast(float, event["abs_x_bps"]), cast(str, event["trade_date"])),
    )
    labels = {
        cast(str, event["trade_date"]): min(5, index * 5 // len(ranked) + 1)
        for index, event in enumerate(ranked)
    }
    return [dict(event, abs_x_quintile=labels.get(cast(str, event["trade_date"]))) for event in events]


def _half(event: dict[str, object]) -> str:
    return "morning" if datetime.fromisoformat(cast(str, event["block_end_jst"])).time() < time(12) else "afternoon"


def _time_band(event: dict[str, object]) -> str:
    stamp = datetime.fromisoformat(cast(str, event["block_end_jst"]))
    return stamp.strftime("%H:00-%H:59")


def report(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], events: list[dict[str, object]], *, detailed: bool = False) -> dict[str, object]:
    by_date = {date.fromisoformat(cast(str, event["trade_date"])): event for event in events}
    result: dict[str, object] = {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [target for target, value in daily.items() if value is None],
        "trade_occurrence_rate_per_scheduled_trade_date": len(trades) / len(axis),
        "by_year": {str(year): ledger_metrics(tuple(trade for trade in trades if trade.trade_date.year == year)) for year in range(2021, 2026)},
        "by_x_sign": {sign: ledger_metrics(tuple(trade for trade in trades if (cast(float, by_date[trade.trade_date]["x_bps"]) > 0) == (sign == "positive"))) for sign in ("positive", "negative")},
        "by_half": {half: ledger_metrics(tuple(trade for trade in trades if _half(by_date[trade.trade_date]) == half)) for half in ("morning", "afternoon")},
        "profit_concentration": concentration(trades, axis),
    }
    if detailed:
        bands = sorted({_time_band(by_date[trade.trade_date]) for trade in trades})
        versions = sorted({cast(str, by_date[trade.trade_date]["schedule_version"]) for trade in trades})
        result["by_event_time_band"] = {band: ledger_metrics(tuple(trade for trade in trades if _time_band(by_date[trade.trade_date]) == band)) for band in bands}
        result["by_schedule_version"] = {version: ledger_metrics(tuple(trade for trade in trades if by_date[trade.trade_date]["schedule_version"] == version)) for version in versions}
        result["by_abs_x_quintile"] = {str(quintile): ledger_metrics(tuple(trade for trade in trades if by_date[trade.trade_date].get("abs_x_quintile") == quintile)) for quintile in range(1, 6)}
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


def causality_audit(events: list[dict[str, object]], blocks: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    executable = [event for event in events if event.get("status") == "EXECUTABLE"]
    event_dates = [cast(str, event["trade_date"]) for event in events]
    checks = {
        "scheduled_axis_complete_unique": event_dates == [target.isoformat() for target in axis] and len(event_dates) == len(set(event_dates)),
        "fixed_nonoverlapping_blocks_with_30_minute_edges": all(datetime.fromisoformat(cast(str, row["block_start_jst"])) >= datetime.fromisoformat(cast(str, row["interval_open_jst"])) + np.timedelta64(30, "m").astype(object) and datetime.fromisoformat(cast(str, row["block_end_jst"])) <= datetime.fromisoformat(cast(str, row["interval_close_jst"])) - np.timedelta64(30, "m").astype(object) for row in blocks),
        "references_are_strictly_prior_current_excluded_same_offset": all(all(date.fromisoformat(reference) < date.fromisoformat(cast(str, row["trade_date"])) for reference in cast(list[str], row["reference_valid_trade_dates_oldest_to_newest"])) and cast(str, row["trade_date"]) not in cast(list[str], row["reference_valid_trade_dates_oldest_to_newest"]) for row in blocks),
        "first_candidate_only": all(cast(int, event.get("later_shock_candidates_ignored", 0)) == cast(int, event.get("candidate_block_count", 1)) - 1 for event in executable),
        "entry_exit_in_same_continuous_interval": all(datetime.fromisoformat(cast(str, event["interval_open_jst"])) < datetime.fromisoformat(cast(str, event["entry_open_jst"])) < datetime.fromisoformat(cast(str, event["exit_open_jst"])) < datetime.fromisoformat(cast(str, event["interval_close_jst"])) for event in executable),
        "next_eligible_entry_and_exact_hold_exit": all(datetime.fromisoformat(cast(str, event["entry_open_jst"])) > datetime.fromisoformat(cast(str, event["entry_signal_jst"])) and datetime.fromisoformat(cast(str, event["exit_open_jst"])) - datetime.fromisoformat(cast(str, event["entry_open_jst"])) == np.timedelta64(cast(int, event["hold_minutes"]), "m").astype(object) for event in executable),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r086_q001_intraday_same_clock_shock_fade.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r086_q001_intraday_same_clock_shock_fade.py"),
        Path("src/n225m_bt/research/r086_intraday_same_clock_shock_fade.py"),
        Path("src/n225m_bt/strategies/r086_fixed_signal.py"),
        Path("tests/test_r086_q001.py"),
    ]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    preregistration = {
        "task_id": "TASK-R086-Q001", "family_id": "intraday_same_clock_liquidity_shock_reversal", "study_id": "R086-Q001", "spec_version": "v1", "run_id": RUN_ID, "protocol_revision": "RG-20260915-01", "status": "FROZEN_BEFORE_ADDITIONAL_PNL", "preregistration_document": str(PREREGISTRATION_DOCUMENT), "preregistration_document_sha256": digest(ROOT / PREREGISTRATION_DOCUMENT), "prior_information_seen": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "DAY continuous interval, 5m non-overlapping blocks from session open+30 with endpoint<=session close-30; X=10000*(close_t-open_t-4)/open_t-4; same interval/start-offset prior 60 valid date |X| nearest-rank q90, current excluded; first nonzero |X|>=q90 fades at next eligible open and exits at entry+20m open.",
        "s2_gate": "unexplained exclusions=0; outcome-observable executable events>=300; X positive/negative>=100 each; 2021-2024>=45 each; 2025H1>=20; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis no-trade JPY0; same event/entry/exit X-direction continuation; on main executable events with a prior nonzero non-overlapping block only, opposite prior-block-sign control.",
        "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10_000, "method": "non-wrapping MBB; tail truncation; linear percentile; common index"},
        "primary_gate": "fade Net>0; PF>1; fade daily MBB lower>0; fade-continuation paired daily lower>0; fade-pre-shock-sign-control paired daily lower>0 on the common event axis.",
        "sensitivities": ["threshold_q80", "threshold_q95", "lookback40", "lookback80", "shock_3m", "shock_10m", "entry_extra_1_bar", "hold_10m", "hold_30m", "cost_2tick", "cost_3tick", "fee_x2"],
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
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r086_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r086_intraday_same_clock_shock_fade.py", "src/n225m_bt/strategies/r086_fixed_signal.py"],
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
    events, blocks = build_events(classifier, axis, bars, isolated)
    events = assign_abs_x_quintiles(events)
    controls = pre_shock_control_events(events, blocks)
    s2 = feasibility(events)
    causality = causality_audit(events, blocks, axis)
    write_json(OUT / "primary_events.json", events)
    write_json(OUT / "primary_block_ledger.json", blocks)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_audit_before_pnl.json", causality)
    write_json(OUT / "access_ledger.json", {"stage": "S2 PnL-free R086 feasibility and causality audit, then conditional S3", "split": "development", "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "quarantine": quarantine_audit, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    if not bool(cast(dict[str, object], s2["gate"])["passed"]) or not bool(causality["passed"]):
        decision = {"status": "INCONCLUSIVE", "reason": "R086_PNL_FREE_FEASIBILITY_OR_CAUSALITY_GATE_FAILED", "s2_feasibility": s2, "causality_audit": causality, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    profile_specs: dict[str, tuple[dict[str, int], int, int, str]] = {
        "shock_fade": ({}, 1, 30, "fade"), "continuation": ({}, 1, 30, "continuation"),
        "pre_shock_sign_control": ({}, 1, 30, "pre"), "threshold_q80": ({"percentile": 80}, 1, 30, "fade"), "threshold_q95": ({"percentile": 95}, 1, 30, "fade"),
        "lookback40": ({"lookback": 40}, 1, 30, "fade"), "lookback80": ({"lookback": 80}, 1, 30, "fade"),
        "shock_3m": ({"shock_minutes": 3}, 1, 30, "fade"), "shock_10m": ({"shock_minutes": 10}, 1, 30, "fade"),
        "entry_extra_1_bar": ({"entry_extra_bars": 1}, 1, 30, "fade"), "hold_10m": ({"hold_minutes": 10}, 1, 30, "fade"), "hold_30m": ({"hold_minutes": 30}, 1, 30, "fade"),
        "cost_2tick": ({}, 2, 30, "fade"), "cost_3tick": ({}, 3, 30, "fade"), "fee_x2": ({}, 1, 60, "fade"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily_profiles: dict[str, dict[str, int | None]] = {}
    events_by_profile: dict[str, list[dict[str, object]]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (arguments, ticks, fee, direction) in profile_specs.items():
        if name in {"shock_fade", "continuation"} or name.startswith("cost_") or name == "fee_x2":
            profile_events = events
        elif name == "pre_shock_sign_control":
            profile_events = controls
        else:
            profile_events, profile_blocks = build_events(classifier, axis, bars, isolated, **arguments)
            write_json(OUT / f"{name}_block_ledger.json", profile_blocks)
        trades, daily, unknown = execute_profile(axis, profile_events, bars, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name=name, direction=direction)
        events_by_profile[name], ledgers[name], daily_profiles[name] = profile_events, trades, daily
        reports[name] = report(axis, trades, daily, profile_events, detailed=name == "shock_fade")
        if unknown:
            unknown_profiles[name] = sorted(target.isoformat() for target in unknown)
        write_json(OUT / f"{name}_events.json", profile_events)
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
    common_dates = [date.fromisoformat(cast(str, event["trade_date"])) for event in controls if event.get("pre_shock_sign_control_status") == "EXECUTABLE_COMMON_PREVIOUS_NONZERO_BLOCK"]
    common_axis = [target.isoformat() for target in common_dates]
    common_fade = [cast(int, daily_profiles["shock_fade"][target]) for target in common_axis]
    common_pre = [cast(int, daily_profiles["pre_shock_sign_control"][target]) for target in common_axis]
    write_json(OUT / "pre_shock_sign_common_event_axis.json", {"trade_dates": common_axis, "count": len(common_axis), "shock_fade_daily_net_jpy": common_fade, "pre_shock_sign_control_daily_net_jpy": common_pre})
    fade_daily = [cast(int, value) for value in daily_profiles["shock_fade"].values()]
    continuation_daily = [cast(int, value) for value in daily_profiles["continuation"].values()]
    boot, primary_index, pre_index = bootstrap(fade_daily, continuation_daily, common_fade, common_pre)
    np.save(OUT / "bootstrap_primary_axis_indices.npy", primary_index)
    np.save(OUT / "bootstrap_pre_shock_common_axis_indices.npy", pre_index)
    metrics = cast(dict[str, int | float | None], reports["shock_fade"]["metrics"])
    main_ci = cast(list[float], cast(dict[str, object], boot["shock_fade_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    continuation_ci = cast(list[float], cast(dict[str, object], boot["shock_fade_minus_continuation_paired_daily_net_jpy"])["ci95_percentile_linear"])
    pre_ci = cast(list[float], cast(dict[str, object], boot["shock_fade_minus_pre_shock_sign_control_paired_daily_net_jpy_on_common_event_axis"])["ci95_percentile_linear"])
    gates = {"net_positive": cast(int, metrics["net_pnl_jpy"]) > 0, "pf_gt_one": bool(metrics["profit_factor"] and cast(float, metrics["profit_factor"]) > 1), "fade_mbb_ci95_lower_gt_zero": main_ci[0] > 0, "fade_minus_continuation_ci95_lower_gt_zero": continuation_ci[0] > 0, "fade_minus_pre_shock_control_ci95_lower_gt_zero": pre_ci[0] > 0}
    sensitivity_names = ["threshold_q80", "threshold_q95", "lookback40", "lookback80", "shock_3m", "shock_10m", "entry_extra_1_bar", "hold_10m", "hold_30m", "cost_2tick", "cost_3tick", "fee_x2"]
    x_metrics = cast(dict[str, dict[str, int | float | None]], reports["shock_fade"]["by_x_sign"])
    half_metrics = cast(dict[str, dict[str, int | float | None]], reports["shock_fade"]["by_half"])
    year_metrics = cast(dict[str, dict[str, int | float | None]], reports["shock_fade"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], reports["shock_fade"]["profit_concentration"])
    candidate_checks = {"primary_gate": all(gates.values()), "all_fixed_sensitivity_net_positive": all(cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"]) > 0 for name in sensitivity_names), "both_x_sign_net_positive": all(cast(int, x_metrics[sign]["net_pnl_jpy"]) > 0 for sign in ("positive", "negative")), "both_morning_afternoon_net_positive": all(cast(int, half_metrics[half]["net_pnl_jpy"]) > 0 for half in ("morning", "afternoon")), "at_least_three_2021_2024_positive": sum(cast(int, year_metrics[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)) >= 3, "2025_h1_net_positive": cast(int, year_metrics["2025"]["net_pnl_jpy"]) > 0, "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0}
    audit = {"causality_audit_before_pnl_passed": bool(causality["passed"]), "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()), "fade_continuation_same_event_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["shock_fade"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["continuation"]}, "pre_shock_control_uses_common_dates_entry_exit": {(trade.trade_date, trade.entry_ts, trade.exit_ts) for trade in ledgers["pre_shock_sign_control"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts) for trade in ledgers["shock_fade"] if trade.trade_date.isoformat() in set(common_axis)}, "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades)}
    if not all(audit.values()):
        raise ValueError("R086 execution/accounting/causality audit failed")
    write_json(OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}})
    write_json(OUT / "bootstrap.json", boot | {"primary_axis_index_file": "bootstrap_primary_axis_indices.npy", "pre_shock_common_axis_index_file": "bootstrap_pre_shock_common_axis_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {"status": "INVESTIGATE" if all(gates.values()) else "REJECT", "decision_ceiling": "INVESTIGATE", "s2_feasibility": s2, "s3_gates": gates, "candidate_checks": candidate_checks, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
