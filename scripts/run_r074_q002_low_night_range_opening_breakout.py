"""Execute the separately frozen Development-only TASK-R074-Q002 experiment."""

from __future__ import annotations

import os
import shutil
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np
from run_r074_q001_low_night_range_opening_breakout import (
    ROOT,
    build_events,
    digest,
    engine_for,
    execute_profile,
    net_positive,
    quarantine,
    report,
    write_json,
)

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.r074_low_night_range_opening_breakout import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    bootstrap,
    scheduled_axis,
)
from n225m_bt.research.r074_q002_s2 import feasibility_q002

RUN_ID = "r074-q002-20260915-low-night-range-opening-breakout-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREGISTRATION_DOCUMENT = Path("docs/strategy/34_r074_q002_low_night_range_opening_breakout.md")


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r074_q002_low_night_range_opening_breakout.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r074_q002_low_night_range_opening_breakout.py"),
        Path("scripts/run_r074_q001_low_night_range_opening_breakout.py"),
        Path("src/n225m_bt/research/r074_q002_s2.py"),
        Path("src/n225m_bt/research/r074_low_night_range_opening_breakout.py"),
        Path("src/n225m_bt/strategies/r074_fixed_signal.py"),
        Path("tests/test_r074_q002.py"),
        Path("tests/test_r074_q001.py"),
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
        "task_id": "TASK-R074-Q002",
        "parent_study": "R074-Q001",
        "allowed_change_from_parent": "S2 annual gate only",
        "family_id": "night_range_state_opening_breakout",
        "study_id": "R074-Q002",
        "spec_version": "v2",
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
        "frozen_rule": "Official-night full high-low through 05:29; exact latest 20 prior calendar-eligible nights within 60 calendar days; nearest-rank q25 low state; TSE 09:00-09:29 range; first strict close breakout through 11:00; next eligible open entry; 14:30 exit.",
        "tie_rule": "R_current <= q25 is compression; q25 < R_current <= q75 is middle control; strict close only; equality is no breakout.",
        "history_missingness": "Any selected reference missing/ineligible/R004-isolated invalidates the target; no older valid range replaces it.",
        "controls": "Scheduled-axis JPY0; identical-event opposite side; q25<R<=q75 middle-state same breakout with trade-average difference.",
        "s2_gate": "A>=150; A long/short>=45 each; middle>=300; unexplained exclusions=0; for each 2021-2024 year, A occurrences among valid 20-reference N >= Binomial(N,0.25) lower 5% inverse-CDF quantile and A executable rate>=75%; otherwise INCONCLUSIVE before PnL.",
        "annual_s2_interpretation": "This binomial criterion is a nominal-state availability check, not a return test. 2021 performance is descriptive only and cannot independently support significance.",
        "primary_gate": "A Net>0; PF>1; primary daily mean MBB lower>0; A-reverse daily difference lower>0; A-middle trade-average difference lower>0.",
        "sensitivities": [
            "q20",
            "q33",
            "opening20",
            "opening45",
            "entry_delay_1m",
            "exit_1415",
            "exit_1445",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "candidate_gate": "Primary plus all ten sensitivity Net>0, both side Net>0, every 2021-2024 Net>0, and Net excluding top10 winners>0.",
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partitions
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(
        OUT / "run_manifest.json",
        {
            "run_id": RUN_ID,
            "preregistration_hash": canonical_hash(preregistration),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "S0_S1_FROZEN",
        },
    )
    for directory, files in ((OUT / "source_snapshot", source_files), (OUT / "config_snapshot", config_files)):
        directory.mkdir()
        for path in files:
            shutil.copy2(ROOT / path, directory / path.name)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / PREREGISTRATION_DOCUMENT, OUT / "documentation_snapshot" / PREREGISTRATION_DOCUMENT.name)
    command_env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_r074_q001.py",
            "tests/test_r074_q002.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r074_low_night_range_opening_breakout.py",
            "src/n225m_bt/research/r074_q002_s2.py",
            "src/n225m_bt/strategies/r074_fixed_signal.py",
        ],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=command_env)
        validation[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
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
    primary_events = build_events(axis, bars_by_day, classifier, isolated)
    middle_events = build_events(axis, bars_by_day, classifier, isolated, state_group="middle")
    s2 = feasibility_q002(primary_events, middle_events)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 then conditional S3",
            "split": "development",
            "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
            "physical_partitions": development.quality["partitions"],
            "data_version": development.data_version,
            "quarantine": quarantine_audit,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "s2_events_primary.json", {target.isoformat(): event for target, event in primary_events.items()})
    write_json(OUT / "s2_events_middle.json", {target.isoformat(): event for target, event in middle_events.items()})
    s2_gate = cast(dict[str, object], s2["gate"])
    if not bool(s2_gate["passed"]):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "S2_GATE_FAILED",
            "s2_gate": s2_gate,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return

    profiles: dict[str, tuple[dict[str, object], int, int, bool]] = {
        "primary": ({}, 1, 30, False),
        "reverse_control": ({}, 1, 30, True),
        "middle_control": ({"state_group": "middle"}, 1, 30, False),
        "threshold_q20": ({"compression_percentile": 20}, 1, 30, False),
        "threshold_q33": ({"compression_percentile": 33}, 1, 30, False),
        "opening_20": ({"opening_minutes": 20}, 1, 30, False),
        "opening_45": ({"opening_minutes": 45}, 1, 30, False),
        "entry_delay_1m": ({"entry_delay_minutes": 1}, 1, 30, False),
        "exit_1415": ({"exit_time": time(14, 15)}, 1, 30, False),
        "exit_1445": ({"exit_time": time(14, 45)}, 1, 30, False),
        "cost_2tick": ({}, 2, 30, False),
        "cost_3tick": ({}, 3, 30, False),
        "fee_x2": ({}, 1, 60, False),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (overrides, ticks, fee, reverse) in profiles.items():
        events = (
            primary_events
            if name in {"primary", "reverse_control"}
            else middle_events
            if name == "middle_control"
            else build_events(axis, bars_by_day, classifier, isolated, **cast(Any, overrides))
        )
        trades, daily, unknown = execute_profile(
            axis,
            events,
            bars_by_day,
            engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee),
            name=name,
            reverse=reverse,
        )
        ledgers[name] = trades
        reports[name] = report(axis, trades, daily)
        if unknown:
            unknown_profiles[name] = sorted(item.isoformat() for item in unknown)
        write_json(OUT / f"{name}_events.json", {target.isoformat(): event for target, event in events.items()})
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    if unknown_profiles:
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "UNKNOWN_FILLED_EXIT",
            "unknown_profiles": unknown_profiles,
            "s2_gate": s2_gate,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "profiles.json", reports)
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    primary_daily = list(cast(dict[str, int], reports["primary"]["daily_net_pnl_jpy"]).values())
    reverse_daily = list(cast(dict[str, int], reports["reverse_control"]["daily_net_pnl_jpy"]).values())
    middle_daily = list(cast(dict[str, int], reports["middle_control"]["daily_net_pnl_jpy"]).values())
    primary_trade_dates = {trade.trade_date.isoformat() for trade in ledgers["primary"]}
    middle_trade_dates = {trade.trade_date.isoformat() for trade in ledgers["middle_control"]}
    primary_axis = cast(dict[str, int], reports["primary"]["daily_net_pnl_jpy"])
    middle_axis = cast(dict[str, int], reports["middle_control"]["daily_net_pnl_jpy"])
    primary_counts = [1 if target in primary_trade_dates else 0 for target in primary_axis]
    middle_counts = [1 if target in middle_trade_dates else 0 for target in middle_axis]
    boot, index = bootstrap(primary_daily, reverse_daily, primary_counts, middle_daily, middle_counts)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    primary_metrics = cast(dict[str, int | float | None], reports["primary"]["metrics"])
    primary_ci = cast(dict[str, object], boot["primary_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"]
    reverse_ci = cast(dict[str, object], boot["primary_minus_reverse_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"]
    state_ci = cast(dict[str, object], boot["primary_minus_middle_net_jpy_per_trade"])["ci95_percentile_linear"]
    gates = {
        "net_positive": cast(int, primary_metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(primary_metrics["profit_factor"] and cast(float, primary_metrics["profit_factor"]) > 1),
        "primary_mbb_ci95_lower_gt_zero": cast(list[float], primary_ci)[0] > 0,
        "primary_minus_reverse_ci95_lower_gt_zero": cast(list[float], reverse_ci)[0] > 0,
        "primary_minus_middle_trade_average_ci95_lower_gt_zero": cast(list[float], state_ci)[0] > 0,
    }
    primary_pass = all(gates.values())
    sensitivity_names = ["threshold_q20", "threshold_q33", "opening_20", "opening_45", "entry_delay_1m", "exit_1415", "exit_1445", "cost_2tick", "cost_3tick", "fee_x2"]
    side_metrics = cast(dict[str, dict[str, int | float | None]], reports["primary"]["by_direction"])
    year_metrics = cast(dict[str, dict[str, int | float | None]], reports["primary"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], reports["primary"]["profit_concentration"])
    candidate_checks = {
        "primary_gate": primary_pass,
        "all_ten_sensitivity_net_positive": all(net_positive(reports[name]) for name in sensitivity_names),
        "both_direction_net_positive": all(cast(int, side_metrics[side]["net_pnl_jpy"]) > 0 for side in ("long", "short")),
        "every_2021_2024_net_positive": all(cast(int, year_metrics[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)),
        "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0,
    }
    decision_status = "CANDIDATE" if all(candidate_checks.values()) else "INVESTIGATE" if primary_pass else "REJECT"
    audit = {
        "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()),
        "primary_reverse_same_date_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["primary"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["reverse_control"]},
        "day_only_no_holiday_spanning_position": all(trade.entry_ts.date() == trade.exit_ts.date() == trade.trade_date for trades in ledgers.values() for trade in trades),
        "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades),
    }
    if not all(audit.values()):
        raise ValueError("R074 execution/accounting audit failed")
    write_json(OUT / "profiles.json", reports)
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {
        "status": decision_status,
        "s2_gate": s2_gate,
        "s3_gates": gates,
        "candidate_checks": candidate_checks,
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
