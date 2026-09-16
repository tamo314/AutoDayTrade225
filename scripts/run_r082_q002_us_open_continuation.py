"""Execute the one permitted fixed-event PnL evaluation for TASK-R082-Q002."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np
from run_r082_q001_us_open_continuation import (
    engine_for,
    nyse_days,
    order_fill_ledger,
    quarantine,
    report,
    snapshot,
    write_json,
)

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split
from n225m_bt.research.r078_cash_first_hour_extreme_fade import scheduled_axis
from n225m_bt.research.r082_us_open_continuation import (
    _execution_event,
    bootstrap,
    nyse_open_jst,
    pre_open_sign_events,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r082-q002-20260915-us-open-continuation-01"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/54_r082_q002_us_open_continuation.md")
Q001 = ROOT / "results" / "research" / "r082-q001-20260915-us-open-continuation-02"
Q001_EVENTS = Q001 / "primary_events.json"
Q001_EVENTS_SHA256 = "c67345cc1f3ae390fd216464af6128ad7030c83414719e1c4cf18b6e2bc111f0"
FIXED_EXECUTABLE_IDS_SHA256 = "746efde7d3a66107d4b5193bb5b6c17dc4aa70d752d29caa652c660c06347616"
FIXED_EXECUTABLE_COUNT = 806


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def fixed_id_hash(events: list[dict[str, object]]) -> str:
    ids = [cast(str, event["trade_date"]) for event in events if event.get("status") == "EXECUTABLE"]
    return sha256("\n".join(ids).encode()).hexdigest()


def q001_pnl_absence_audit() -> dict[str, object]:
    expected_absent = [
        "profiles.json",
        "bootstrap.json",
        "continuation_trades.json",
        "continuation_orders_fills.json",
        "fade_trades.json",
        "pre_open_sign_control_trades.json",
    ]
    decision = json.loads((Q001 / "decision.json").read_text(encoding="utf-8"))
    completed = json.loads((Q001 / "COMPLETED.json").read_text(encoding="utf-8"))
    passed = (
        decision.get("status") == "INCONCLUSIVE"
        and decision.get("reason") == "R082_PNL_FREE_FEASIBILITY_GATE_FAILED"
        and completed.get("status") == "INCONCLUSIVE"
        and all(not (Q001 / name).exists() for name in expected_absent)
    )
    return {
        "q001_run_id": Q001.name,
        "decision_sha256": digest(Q001 / "decision.json"),
        "completed_sha256": digest(Q001 / "COMPLETED.json"),
        "decision_status": decision.get("status"),
        "decision_reason": decision.get("reason"),
        "completed_status": completed.get("status"),
        "pnl_artifacts_required_absent": expected_absent,
        "pnl_artifacts_absent": [name for name in expected_absent if not (Q001 / name).exists()],
        "passed": passed,
    }


def fixed_events() -> list[dict[str, object]]:
    if digest(Q001_EVENTS) != Q001_EVENTS_SHA256:
        raise ValueError("fixed Q001 event ledger SHA-256 mismatch")
    events = cast(list[dict[str, object]], json.loads(Q001_EVENTS.read_text(encoding="utf-8")))
    executable = [event for event in events if event.get("status") == "EXECUTABLE"]
    ids = [cast(str, event["trade_date"]) for event in executable]
    if (
        len(events) != 1131
        or len(executable) != FIXED_EXECUTABLE_COUNT
        or len(ids) != len(set(ids))
        or fixed_id_hash(events) != FIXED_EXECUTABLE_IDS_SHA256
    ):
        raise ValueError("fixed Q001 executable event IDs do not match the audited 806-event set")
    return events


def fixed_gate(events: list[dict[str, object]]) -> dict[str, object]:
    executable = [event for event in events if event.get("status") == "EXECUTABLE"]
    years = Counter(date.fromisoformat(cast(str, event["trade_date"])).year for event in executable)
    signs = Counter("positive" if cast(int, event["u_points"]) > 0 else "negative" for event in executable)
    known = ("NYSE_", "N225_", "R004_", "U_WINDOW_", "ZERO_", "VALID_", "NONZERO_", "ENTRY_", "FIXED_")
    unexplained = [
        cast(str, event["trade_date"])
        for event in events
        if not str(event.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "executable_trades": len(executable),
        "minimum_executable_trades": 800,
        "executable_at_least_800": len(executable) >= 800,
        "u_sign_counts": dict(sorted(signs.items())),
        "minimum_positive_and_negative_each": 300,
        "u_sign_minima_passed": signs["positive"] >= 300 and signs["negative"] >= 300,
        "by_year": {str(year): years[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 140,
        "annual_2021_2024_minima_passed": all(years[year] >= 140 for year in range(2021, 2025)),
        "minimum_2025_h1": 60,
        "year_2025_h1_minimum_passed": years[2025] >= 60,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[name])
        for name in (
            "executable_at_least_800",
            "u_sign_minima_passed",
            "annual_2021_2024_minima_passed",
            "year_2025_h1_minimum_passed",
            "unexplained_exclusions_equal_zero",
        )
    )
    return gate


def fixed_execution_universe(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Retain the fixed scheduled axis while allowing orders only on the audited 806 IDs."""
    result: list[dict[str, object]] = []
    for source in events:
        event = dict(source)
        if source.get("status") != "EXECUTABLE":
            event.update(status="SKIPPED", fixed_event_membership="OUTSIDE_FIXED_806")
        else:
            event["fixed_event_membership"] = "IN_FIXED_806"
        result.append(event)
    return result


def fixed_definition_audit(
    events: list[dict[str, object]], nyse_trading_days: set[date]
) -> dict[str, object]:
    executable = [event for event in events if event.get("status") == "EXECUTABLE"]
    checks: dict[str, bool] = {
        "event_file_sha256_exact": digest(Q001_EVENTS) == Q001_EVENTS_SHA256,
        "ordered_806_event_id_sha256_exact": fixed_id_hash(events) == FIXED_EXECUTABLE_IDS_SHA256,
        "all_executable_u_nonzero": all(cast(int, event["u_points"]) != 0 for event in executable),
        "all_executable_u_valid": all(bool(event.get("u_valid")) for event in executable),
        "all_executable_nyse_regular_days": all(
            date.fromisoformat(cast(str, event["nyse_trade_date"])) in nyse_trading_days
            for event in executable
        ),
        "all_executable_s_is_new_york_0930": all(
            datetime.fromisoformat(cast(str, event["s_jst"]))
            == nyse_open_jst(date.fromisoformat(cast(str, event["nyse_trade_date"])))
            for event in executable
        ),
        "all_executable_s_within_n225_night": all(
            datetime.fromisoformat(cast(str, event["n225_night_open_jst"]))
            <= datetime.fromisoformat(cast(str, event["s_jst"]))
            <= datetime.fromisoformat(cast(str, event["n225_night_close_jst"]))
            and bool(event.get("n225_night_contains_s"))
            for event in executable
        ),
        "all_executable_s_u_entry_exit_offsets_exact": all(
            datetime.fromisoformat(cast(str, event["signal_endpoint_jst"]))
            == datetime.fromisoformat(cast(str, event["s_jst"])) + timedelta(minutes=29)
            and datetime.fromisoformat(cast(str, event["entry_signal_jst"]))
            == datetime.fromisoformat(cast(str, event["s_jst"])) + timedelta(minutes=29)
            and datetime.fromisoformat(cast(str, event["entry_open_jst"]))
            == datetime.fromisoformat(cast(str, event["s_jst"])) + timedelta(minutes=30)
            and datetime.fromisoformat(cast(str, event["exit_signal_jst"]))
            == datetime.fromisoformat(cast(str, event["s_jst"])) + timedelta(minutes=179)
            and datetime.fromisoformat(cast(str, event["exit_open_jst"]))
            == datetime.fromisoformat(cast(str, event["s_jst"])) + timedelta(minutes=180)
            for event in executable
        ),
        "all_executable_direction_matches_u": all(
            cast(str, event["continuation_direction"])
            == ("long" if cast(int, event["u_points"]) > 0 else "short")
            and cast(str, event["fade_direction"])
            == ("short" if cast(int, event["u_points"]) > 0 else "long")
            for event in executable
        ),
    }
    return {
        "fixed_event_file": str(Q001_EVENTS.relative_to(ROOT)),
        "fixed_event_file_sha256": digest(Q001_EVENTS),
        "fixed_executable_count": len(executable),
        "fixed_executable_ids_sha256": fixed_id_hash(events),
        "checks": checks,
        "passed": all(checks.values()),
    }


def restricted_reexecution_events(
    events: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Any]],
    *,
    signal_minutes: int = 30,
    entry_delay_minutes: int = 0,
    exit_minutes: int = 180,
) -> list[dict[str, object]]:
    """Apply a registered sensitivity only to the frozen main IDs; never add dates."""
    result: list[dict[str, object]] = []
    for source in events:
        event = dict(source)
        if source.get("status") != "EXECUTABLE":
            event.update(status="SKIPPED", reason="NOT_IN_FIXED_806_EVENT_SET")
            result.append(event)
            continue
        if signal_minutes != 30:
            target = date.fromisoformat(cast(str, event["trade_date"]))
            s = datetime.fromisoformat(cast(str, event["s_jst"]))
            lookup = {bar.ts_jst: bar for bar in bars[(target, Session.NIGHT)]}
            endpoint = s + timedelta(minutes=signal_minutes - 1)
            a, b = lookup.get(s), lookup.get(endpoint)
            valid = bool(
                a is not None
                and b is not None
                and a.is_eligible
                and b.is_eligible
                and a.open > 0
                and b.close > 0
            )
            movement = b.close - a.open if valid and a is not None and b is not None else 0
            event.update(
                u_valid=valid and movement != 0,
                u_points=movement,
                abs_u_points=abs(movement),
                u_a_open_points=a.open if a is not None else None,
                u_b_close_points=b.close if b is not None else None,
            )
        result.append(
            _execution_event(
                event,
                bars,
                signal_minutes=signal_minutes,
                entry_delay_minutes=entry_delay_minutes,
                exit_minutes=exit_minutes,
            )
        )
    return result


def execute_profiles(
    axis: list[date],
    events: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Any]],
    instrument: object,
    baseline: Any,
    classifier: CalendarClassifier,
) -> tuple[dict[str, dict[str, object]], dict[str, tuple[Trade, ...]], dict[str, dict[str, int | None]]]:
    from run_r082_q001_us_open_continuation import execute_profile

    profiles: dict[str, tuple[list[dict[str, object]], int, int, str]] = {
        "continuation": (events, 1, 30, "continuation"),
        "fade": (events, 1, 30, "fade"),
        "pre_open_sign_control": (pre_open_sign_events(events), 1, 30, "pre_open"),
        "signal_15m": (restricted_reexecution_events(events, bars, signal_minutes=15), 1, 30, "continuation"),
        "signal_45m": (restricted_reexecution_events(events, bars, signal_minutes=45), 1, 30, "continuation"),
        "entry_delay_1m": (restricted_reexecution_events(events, bars, entry_delay_minutes=1), 1, 30, "continuation"),
        "exit_s_plus_120m": (restricted_reexecution_events(events, bars, exit_minutes=120), 1, 30, "continuation"),
        "exit_s_plus_240m": (restricted_reexecution_events(events, bars, exit_minutes=240), 1, 30, "continuation"),
        "cost_2tick": (events, 2, 30, "continuation"),
        "cost_3tick": (events, 3, 30, "continuation"),
        "fee_x2": (events, 1, 60, "continuation"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int | None]] = {}
    unknown: dict[str, list[str]] = {}
    for name, (profile_events, ticks, fee, direction) in profiles.items():
        trades, daily_axis, unknown_days = execute_profile(
            axis,
            profile_events,
            bars,
            engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee),
            name=name,
            direction=direction,
        )
        if unknown_days:
            unknown[name] = sorted(day.isoformat() for day in unknown_days)
        reports[name] = report(axis, trades, daily_axis, profile_events, primary=name == "continuation")
        ledgers[name], daily[name] = trades, daily_axis
        write_json(OUT / f"{name}_events.json", profile_events)
        write_json(OUT / f"{name}_orders_fills.json", order_fill_ledger(trades))
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily_axis)
    if unknown:
        raise ValueError(f"UNKNOWN_FILLED_EXIT: {unknown}")
    return reports, ledgers, daily


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r082_q002_us_open_continuation.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    q001_absence = q001_pnl_absence_audit()
    if not bool(q001_absence["passed"]):
        raise ValueError("Q001 PnL-absence audit failed")
    events = fixed_events()
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r082_q002_us_open_continuation.py"),
        Path("scripts/run_r082_q001_us_open_continuation.py"),
        Path("src/n225m_bt/research/r082_us_open_continuation.py"),
        Path("src/n225m_bt/strategies/r082_fixed_signal.py"),
        Path("tests/test_r082_q001.py"),
    ]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")] + [Path("config/calendars/nyse_regular_trading_days_2021_2025h1_v1.yaml")]
    preregistration = {
        "task_id": "TASK-R082-Q002",
        "family_id": "us_cash_open_n225_night_price_discovery",
        "study_id": "R082-Q002",
        "spec_version": "v2",
        "protocol_revision": "R082-Q002-count-gate-only-20260915",
        "status": "FROZEN_BEFORE_ADDITIONAL_PNL",
        "preregistration_document": str(DOC),
        "preregistration_document_sha256": digest(ROOT / DOC),
        "q001_pnl_absence_audit": q001_absence,
        "fixed_input": {"path": str(Q001_EVENTS.relative_to(ROOT)), "event_file_sha256": Q001_EVENTS_SHA256, "executable_event_count": FIXED_EXECUTABLE_COUNT, "ordered_event_ids_sha256": FIXED_EXECUTABLE_IDS_SHA256},
        "sole_protocol_change": "minimum executable count 850 -> 800; all other selection, calendar, S/U/P, entry/exit, exclusion, execution, cost, bootstrap, control, and decision rules unchanged",
        "information_gate": "fixed audited 806 IDs only; executable>=800; U positive/negative>=300 each; 2021-2024>=140 each; 2025H1>=60; unexplained exclusions=0; any failure is INCONCLUSIVE before PnL",
        "baseline": "one contract; 1 tick per side plus JPY30 fee per side; continuation U direction S+30 to S+180",
        "controls": "same-event opposite-side fade; U/P nonzero common-date pre-open P-sign control; no-trade scheduled axis",
        "bootstrap": {"seed": 20260915, "block_length_trade_dates": 20, "repetitions": 10000, "method": "non-wrapping MBB; tail truncation; linear percentile"},
        "sensitivities": ["signal_15m", "signal_45m", "entry_delay_1m", "exit_s_plus_120m", "exit_s_plus_240m", "cost_2tick", "cost_3tick", "fee_x2"],
        "prohibited": ["event re-extraction or addition", "missing-date rescue", "time/holding/strength selection", "result-dependent rescue", "OOS", "walk_forward", "final_holdout"],
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE",
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "q001_pnl_absence_audit.json", q001_absence)
    shutil.copy2(Q001_EVENTS, OUT / "fixed_q001_primary_events.json")
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / DOC, OUT / "documentation_snapshot" / DOC.name)
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r082_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r082_us_open_continuation.py", "src/n225m_bt/strategies/r082_fixed_signal.py"],
        "py_compile": [sys.executable, "-m", "py_compile", "scripts/run_r082_q001_us_open_continuation.py", "scripts/run_r082_q002_us_open_continuation.py"],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=environment)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(cast(dict[str, object], item)["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("pre-execution validation failed")
    nyse_trading_days, calendar_audit = nyse_days()
    definition_audit = fixed_definition_audit(events, nyse_trading_days)
    gate = fixed_gate(events)
    write_json(OUT / "nyse_calendar_audit.json", calendar_audit)
    write_json(OUT / "fixed_event_definition_audit.json", definition_audit)
    write_json(OUT / "s2_feasibility.json", {"scope": "fixed Q001 ledger only; no re-extraction", "gate": gate})
    if not bool(calendar_audit["passed"]) or not bool(definition_audit["passed"]) or not bool(gate["passed"]):
        reason = "NYSE_CALENDAR_AUDIT_FAILED" if not bool(calendar_audit["passed"]) else ("FIXED_EVENT_DEFINITION_AUDIT_FAILED" if not bool(definition_audit["passed"]) else "R082_Q002_PNL_FREE_GATE_FAILED")
        decision = {"status": "INCONCLUSIVE", "reason": reason, "q001_pnl_absence_audit": q001_absence, "nyse_calendar_audit": calendar_audit, "fixed_event_definition_audit": definition_audit, "s2_feasibility": gate, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier, axis = CalendarClassifier(sessions, calendar), scheduled_axis(calendar)
    if [day.isoformat() for day in axis] != [cast(str, event["trade_date"]) for event in events]:
        raise ValueError("fixed event ledger no longer matches the scheduled trade_date axis")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, _ = quarantine(development)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in set(axis) and bar.session is Session.NIGHT:
            bars[(bar.trade_date, bar.session)].append(bar)
    write_json(OUT / "access_ledger.json", {"stage": "fixed-806 S3 evaluation after PnL-free Q001 audit and Q002 preregistration", "split": "development", "trade_date_filter": ["2021-01-01", "2025-06-30"], "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "quarantine": quarantine_audit, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    execution_events = fixed_execution_universe(events)
    reports, ledgers, daily = execute_profiles(
        axis, execution_events, bars, instrument, baseline, classifier
    )
    no_trade = {target.isoformat(): 0 for target in axis}
    write_json(OUT / "no_trade_control_daily_axis.json", no_trade)
    common_axis = [
        event["trade_date"]
        for event in pre_open_sign_events(execution_events)
        if event.get("status") == "EXECUTABLE"
    ]
    common_cont = [cast(int, daily["continuation"][cast(str, target)]) for target in common_axis]
    common_pre = [cast(int, daily["pre_open_sign_control"][cast(str, target)]) for target in common_axis]
    write_json(OUT / "pre_open_common_event_axis.json", {"trade_dates": common_axis, "count": len(common_axis), "continuation_daily_net_jpy": common_cont, "pre_open_sign_control_daily_net_jpy": common_pre})
    boot, primary_index, common_index = bootstrap([cast(int, value) for value in daily["continuation"].values()], [cast(int, value) for value in daily["fade"].values()], common_cont, common_pre)
    np.save(OUT / "bootstrap_primary_axis_indices.npy", primary_index)
    np.save(OUT / "bootstrap_pre_open_common_axis_indices.npy", common_index)
    metrics = cast(dict[str, int | float | None], reports["continuation"]["metrics"])
    primary_ci = cast(list[float], cast(dict[str, object], boot["continuation_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    fade_ci = cast(list[float], cast(dict[str, object], boot["continuation_minus_fade_paired_daily_net_jpy"])["ci95_percentile_linear"])
    pre_ci = cast(list[float], cast(dict[str, object], boot["continuation_minus_pre_open_sign_control_paired_daily_net_jpy_on_common_u_p_axis"])["ci95_percentile_linear"])
    gates = {"net_positive": cast(int, metrics["net_pnl_jpy"]) > 0, "pf_gt_one": bool(metrics["profit_factor"] and cast(float, metrics["profit_factor"]) > 1), "continuation_mbb_ci95_lower_gt_zero": primary_ci[0] > 0, "continuation_minus_fade_ci95_lower_gt_zero": fade_ci[0] > 0, "continuation_minus_pre_open_sign_control_ci95_lower_gt_zero": pre_ci[0] > 0}
    u_metrics = cast(dict[str, dict[str, int | float | None]], reports["continuation"]["by_u_sign"])
    time_metrics = cast(dict[str, dict[str, int | float | None]], reports["continuation"]["by_us_time"])
    years = cast(dict[str, dict[str, int | float | None]], reports["continuation"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], reports["continuation"]["profit_concentration"])
    sensitivity_names = ["signal_15m", "signal_45m", "entry_delay_1m", "exit_s_plus_120m", "exit_s_plus_240m", "cost_2tick", "cost_3tick", "fee_x2"]
    candidate_checks = {"primary_gate": all(gates.values()), "all_fixed_sensitivity_net_positive": all(cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"]) > 0 for name in sensitivity_names), "both_u_sign_net_positive": all(cast(int, u_metrics[sign]["net_pnl_jpy"]) > 0 for sign in ("positive", "negative")), "both_us_time_net_positive": all(cast(int, time_metrics[state]["net_pnl_jpy"]) > 0 for state in ("dst", "standard")), "at_least_three_2021_2024_positive": sum(cast(int, years[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)) >= 3, "2025_h1_net_positive": cast(int, years["2025"]["net_pnl_jpy"]) > 0, "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0}
    audit = {"all_profiles_only_use_fixed_806_ids": all({trade.trade_date for trade in trades}.issubset({date.fromisoformat(cast(str, event["trade_date"])) for event in events if event.get("status") == "EXECUTABLE"}) for trades in ledgers.values()), "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()), "primary_continuation_fade_same_event_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["continuation"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["fade"]}, "pre_open_control_is_same_u_p_common_dates_and_entry_exit": {(trade.trade_date, trade.entry_ts, trade.exit_ts) for trade in ledgers["pre_open_sign_control"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts) for trade in ledgers["continuation"] if trade.trade_date.isoformat() in set(common_axis)}, "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades)}
    if not all(audit.values()):
        raise ValueError("R082-Q002 execution/accounting/causality audit failed")
    write_json(OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}})
    write_json(OUT / "bootstrap.json", boot | {"primary_axis_index_file": "bootstrap_primary_axis_indices.npy", "pre_open_common_axis_index_file": "bootstrap_pre_open_common_axis_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {"status": "INVESTIGATE" if all(gates.values()) else "REJECT", "decision_ceiling": "INVESTIGATE", "q001_pnl_absence_audit": q001_absence, "nyse_calendar_audit": calendar_audit, "fixed_event_definition_audit": definition_audit, "s2_feasibility": gate, "s3_gates": gates, "candidate_checks": candidate_checks, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
