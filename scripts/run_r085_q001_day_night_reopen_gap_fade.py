"""Execute the frozen Development-only TASK-R085-Q001 experiment exactly once."""

# mypy: disable-error-code="attr-defined,no-untyped-call"

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
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
from n225m_bt.research.r085_day_night_reopen_gap_fade import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    bootstrap,
    build_events,
    feasibility,
    scheduled_axis,
)
from n225m_bt.strategies.r085_fixed_signal import R085FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r085-q001-20260915-day-night-reopen-gap-fade-04"
OUT = ROOT / "results" / "research" / RUN_ID
PREREGISTRATION_DOCUMENT = Path("docs/strategy/60_r085_q001_day_night_reopen_gap_fade.md")
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


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


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    """Reproduce fixed R004 whole-session isolation without mutating Gold."""
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [row for key, rows in grouped.items() if key not in isolated for row in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listed,
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
    mismatches = {
        key: {"actual": audit[key], "expected": wanted}
        for key, wanted in expected.items()
        if audit[key] != wanted
    }
    audit.update(expected_match=not mismatches, mismatches=mismatches)
    if mismatches:
        raise ValueError(f"BLOCKED: R004 fixed quarantine mismatch: {mismatches}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def engine_for(
    instrument: object,
    baseline: Any,
    classifier: CalendarClassifier,
    *,
    ticks: int = 1,
    fee: int = 30,
) -> BacktestEngine:
    config = baseline.model_copy(
        update={
            "mode": "night_only",
            "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
            "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
            "risk": baseline.risk.model_copy(
                update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    return BacktestEngine(cast(Any, instrument).instrument.to_spec(), config, classifier)


def execute_profile(
    axis: list[date],
    events: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Any]],
    engine: BacktestEngine,
    *,
    name: str,
    state: str,
    fade: bool,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    by_day = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
    trade_by_day: dict[date, Trade] = {}
    unknown: set[date] = set()
    for target in axis:
        event = by_day[target]
        if event.get("state") != state:
            continue
        if event.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            continue
        if event.get("status") != "EXECUTABLE":
            continue
        gap = cast(int, event["j_points"])
        direction = ("short" if gap > 0 else "long") if fade else ("long" if gap > 0 else "short")
        night_target = date.fromisoformat(cast(str, event["night_trade_date"]))
        result = engine.run(
            bars[(night_target, Session.NIGHT)],
            R085FixedSignalStrategy(
                f"r085_{name}_{target.isoformat()}",
                datetime.fromisoformat(cast(str, event["entry_signal_jst"])),
                datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
                direction,
            ),
            parameter_hash=canonical_hash(
                {"profile": name, "event": event, "direction": direction}
            ),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
        trade = result.trades[0]
        if (
            trade.entry_ts.isoformat() != event["entry_open_jst"]
            or trade.exit_ts.isoformat() != event["exit_open_jst"]
            or trade.side is not Side(direction)
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{name}/{target}: event/execution mismatch")
        trade_by_day[target] = trade
    return (
        tuple(sorted(trade_by_day.values(), key=lambda item: item.entry_ts)),
        aligned_daily_net(axis, trade_by_day, unknown),
        unknown,
    )


def assign_abs_j_quintiles(events: list[dict[str, object]]) -> list[dict[str, object]]:
    ranked = sorted(
        (row for row in events if row.get("state") == "E" and row.get("status") == "EXECUTABLE"),
        key=lambda row: (cast(int, row["abs_j_points"]), cast(str, row["trade_date"])),
    )
    labels = {
        cast(str, row["trade_date"]): min(5, index * 5 // len(ranked) + 1)
        for index, row in enumerate(ranked)
    }
    return [
        dict(row, extreme_abs_j_quintile=labels.get(cast(str, row["trade_date"]))) for row in events
    ]


def report(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int | None],
    events: list[dict[str, object]],
    *,
    detailed: bool = False,
) -> dict[str, object]:
    by_night = {
        date.fromisoformat(cast(str, row["night_trade_date"])): row
        for row in events
        if row.get("night_trade_date")
    }

    def event_for(trade: Trade) -> dict[str, object]:
        return by_night[trade.trade_date]

    result: dict[str, object] = {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [target for target, value in daily.items() if value is None],
        "trade_occurrence_rate_per_scheduled_trade_date": len(trades) / len(axis),
        "by_year": {
            str(year): ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if date.fromisoformat(cast(str, event_for(trade)["trade_date"])).year == year
                )
            )
            for year in range(2021, 2026)
        },
        "by_j_sign": {
            side: ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if (cast(int, event_for(trade)["j_points"]) > 0) == (side == "positive")
                )
            )
            for side in ("positive", "negative")
        },
        "profit_concentration": concentration(trades, axis),
    }
    if detailed:
        result["by_night_schedule_version"] = {
            version: ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if event_for(trade).get("night_schedule_version") == version
                )
            )
            for version in sorted(
                {cast(str, event_for(trade).get("night_schedule_version")) for trade in trades}
            )
        }
        result["by_extreme_abs_j_quintile"] = {
            str(quintile): ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if event_for(trade).get("extreme_abs_j_quintile") == quintile
                )
            )
            for quintile in range(1, 6)
        }
    return result


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    executable = [row for row in events if row.get("status") == "EXECUTABLE"]
    checks = {
        "scheduled_axis_complete_unique": [row["trade_date"] for row in events]
        == [target.isoformat() for target in axis]
        and len(events) == len({row["trade_date"] for row in events}),
        "day_to_night_mapping_is_one_to_one_and_calendar_explicit": all(
            row.get("night_trade_date")
            and row.get("night_trade_date") == row.get("trade_date")
            and row.get("night_calendar_start_date") == row.get("day_trade_date")
            for row in events
            if row.get("j_valid")
        ),
        "formal_boundaries_use_versioned_session_open_close": all(
            row.get("d_final_day_close_jst")
            and row.get("n_formal_open_jst")
            and row.get("day_schedule_version")
            and row.get("night_schedule_version")
            for row in events
            if row.get("j_valid")
        ),
        "references_are_strictly_prior_and_current_excluded": all(
            all(
                date.fromisoformat(reference) < date.fromisoformat(cast(str, row["trade_date"]))
                for reference in cast(
                    list[str], row["reference_valid_trade_dates_oldest_to_newest"]
                )
            )
            and cast(str, row["trade_date"])
            not in cast(list[str], row["reference_valid_trade_dates_oldest_to_newest"])
            for row in events
        ),
        "quantile_states_have_exact_prior_lookback": all(
            row.get("state") not in {"E", "M"}
            or row.get("reference_valid_count") == row.get("lookback_valid_sessions_required")
            for row in events
        ),
        "entry_is_after_formal_night_open_and_exit_is_first_eligible_at_or_after_open_plus_60": all(
            datetime.fromisoformat(cast(str, row["entry_open_jst"]))
            > datetime.fromisoformat(cast(str, row["n_formal_open_jst"]))
            and datetime.fromisoformat(cast(str, row["exit_open_jst"]))
            >= datetime.fromisoformat(cast(str, row["exit_floor_jst"]))
            for row in executable
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def net_positive(value: dict[str, object]) -> bool:
    net = cast(dict[str, int | float | None], value["metrics"])["net_pnl_jpy"]
    return bool(net is not None and net > 0)


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r085_q001_day_night_reopen_gap_fade.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r085_q001_day_night_reopen_gap_fade.py"),
        Path("src/n225m_bt/research/r085_day_night_reopen_gap_fade.py"),
        Path("src/n225m_bt/strategies/r085_fixed_signal.py"),
        Path("tests/test_r085_q001.py"),
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
    preregistration = {
        "task_id": "TASK-R085-Q001",
        "family_id": "day_night_reopen_gap_fade",
        "study_id": "R085-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_ADDITIONAL_PNL",
        "preregistration_document": str(PREREGISTRATION_DOCUMENT),
        "preregistration_document_sha256": digest(ROOT / PREREGISTRATION_DOCUMENT),
        "prior_information_seen": True,
        "technical_rerun": {
            "prior_failed_run": "r085-q001-20260915-day-night-reopen-gap-fade-03",
            "record": "docs/strategy/63_r085_q001_s2_reason_technical_rerun_record.md",
            "economic_rule_or_pnl_behavior_changed": False,
        },
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "The scheduled axis is official NIGHT trade_date; D=its mapped prior formal final eligible DAY close; N=formal NIGHT open; J=N-D; current-excluded prior 60 valid nonzero |J| nearest-rank q20/q80; E fades J at next eligible NIGHT open through first eligible open at formal NIGHT open+60 minutes.",
        "s2_gate": "unexplained exclusions=0; E executable>=180; E J positive/negative>=60 each; 2021-2024>=25 each; 2025H1>=12; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis JPY0; same E event/entry/exit J-direction continuation; M q20<|J|<q80 same fade and shared scheduled axis.",
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10_000,
            "method": "non-wrapping MBB; tail truncation; linear percentile; common index",
        },
        "primary_gate": "E-fade Net>0; PF>1; daily MBB lower>0; E-fade-E-continuation paired daily lower>0; E-M group daily lower>0.",
        "sensitivities": [
            "q70",
            "q90",
            "lookback40",
            "lookback80",
            "entry_open_plus_5",
            "exit_open_plus_30",
            "exit_open_plus_90",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE.",
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partition_paths(data_config.gold_root, "development")
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
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(
        ROOT / PREREGISTRATION_DOCUMENT,
        OUT / "documentation_snapshot" / PREREGISTRATION_DOCUMENT.name,
    )
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_r085_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r085_day_night_reopen_gap_fade.py",
            "src/n225m_bt/strategies/r085_fixed_signal.py",
            str(source_files[0]),
        ],
        "py_compile": [sys.executable, "-m", "py_compile", str(source_files[0])],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(
            command, cwd=ROOT, capture_output=True, text=True, check=False, env=environment
        )
        validation[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    validation["status"] = (
        "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(
            OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}
        )
        raise ValueError("pre-execution validation failed")
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    axis = scheduled_axis(calendar)
    axis_set = set(axis)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in axis_set and bar.session in {Session.DAY, Session.NIGHT}:
            bars[(bar.trade_date, bar.session)].append(bar)
    primary_events = assign_abs_j_quintiles(build_events(classifier, axis, bars, isolated))
    s2 = feasibility(primary_events)
    audit = causality_audit(primary_events, axis)
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
    write_json(OUT / "primary_events.json", primary_events)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_s2_audit.json", audit)
    gate = cast(dict[str, object], s2["gate"])
    if not bool(gate["passed"]) or not bool(audit["passed"]):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "S2_GATE_OR_CAUSALITY_AUDIT_FAILED",
            "s2_gate": gate,
            "causality_audit": audit,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    profiles: dict[str, tuple[list[dict[str, object]], int, int, str, bool]] = {
        "extreme_fade": (primary_events, 1, 30, "E", True),
        "extreme_continuation": (primary_events, 1, 30, "E", False),
        "middle_fade": (primary_events, 1, 30, "M", True),
        "q70": (
            build_events(classifier, axis, bars, isolated, upper_percentile=70),
            1,
            30,
            "E",
            True,
        ),
        "q90": (
            build_events(classifier, axis, bars, isolated, upper_percentile=90),
            1,
            30,
            "E",
            True,
        ),
        "lookback40": (
            build_events(classifier, axis, bars, isolated, lookback=40),
            1,
            30,
            "E",
            True,
        ),
        "lookback80": (
            build_events(classifier, axis, bars, isolated, lookback=80),
            1,
            30,
            "E",
            True,
        ),
        "entry_open_plus_5": (
            build_events(classifier, axis, bars, isolated, entry_not_before_minutes=5),
            1,
            30,
            "E",
            True,
        ),
        "exit_open_plus_30": (
            build_events(classifier, axis, bars, isolated, exit_minutes=30),
            1,
            30,
            "E",
            True,
        ),
        "exit_open_plus_90": (
            build_events(classifier, axis, bars, isolated, exit_minutes=90),
            1,
            30,
            "E",
            True,
        ),
        "cost_2tick": (primary_events, 2, 30, "E", True),
        "cost_3tick": (primary_events, 3, 30, "E", True),
        "fee_x2": (primary_events, 1, 60, "E", True),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (events, ticks, fee, state, fade) in profiles.items():
        trades, daily, unknown = execute_profile(
            axis,
            events,
            bars,
            engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee),
            name=name,
            state=state,
            fade=fade,
        )
        ledgers[name] = trades
        reports[name] = report(axis, trades, daily, events, detailed=name == "extreme_fade")
        if unknown:
            unknown_profiles[name] = sorted(target.isoformat() for target in unknown)
        write_json(OUT / f"{name}_events.json", events)
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    if unknown_profiles:
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "UNKNOWN_FILLED_EXIT",
            "unknown_profiles": unknown_profiles,
            "s2_gate": gate,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "profiles.json", reports)
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    extreme_daily = list(
        cast(dict[str, int], reports["extreme_fade"]["daily_net_pnl_jpy"]).values()
    )
    continuation_daily = list(
        cast(dict[str, int], reports["extreme_continuation"]["daily_net_pnl_jpy"]).values()
    )
    middle_daily = list(cast(dict[str, int], reports["middle_fade"]["daily_net_pnl_jpy"]).values())
    boot, index = bootstrap(extreme_daily, continuation_daily, middle_daily)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    primary_metrics = cast(dict[str, int | float | None], reports["extreme_fade"]["metrics"])
    primary_ci = cast(
        list[float] | None,
        cast(dict[str, object], boot["extreme_fade_scheduled_axis_mean_net_jpy_per_trade_date"])[
            "ci95_percentile_linear"
        ],
    )
    paired_ci = cast(
        list[float] | None,
        cast(dict[str, object], boot["extreme_fade_minus_continuation_paired_daily_net_jpy"])[
            "ci95_percentile_linear"
        ],
    )
    middle_ci = cast(
        list[float] | None,
        cast(dict[str, object], boot["extreme_minus_middle_group_mean_daily_net_jpy"])[
            "ci95_percentile_linear"
        ],
    )
    gates = {
        "net_positive": cast(int, primary_metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(
            primary_metrics["profit_factor"] and cast(float, primary_metrics["profit_factor"]) > 1
        ),
        "extreme_fade_mbb_ci95_lower_gt_zero": primary_ci is not None and primary_ci[0] > 0,
        "extreme_fade_minus_continuation_ci95_lower_gt_zero": paired_ci is not None
        and paired_ci[0] > 0,
        "extreme_minus_middle_fade_ci95_lower_gt_zero": middle_ci is not None and middle_ci[0] > 0,
    }
    sensitivity_names = [
        "q70",
        "q90",
        "lookback40",
        "lookback80",
        "entry_open_plus_5",
        "exit_open_plus_30",
        "exit_open_plus_90",
        "cost_2tick",
        "cost_3tick",
        "fee_x2",
    ]
    j_metrics = cast(dict[str, dict[str, int | float | None]], reports["extreme_fade"]["by_j_sign"])
    year_metrics = cast(
        dict[str, dict[str, int | float | None]], reports["extreme_fade"]["by_year"]
    )
    concentration_metrics = cast(
        dict[str, int | float | None], reports["extreme_fade"]["profit_concentration"]
    )
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_ten_sensitivity_net_positive": all(
            net_positive(reports[name]) for name in sensitivity_names
        ),
        "both_j_sign_net_positive": all(
            cast(int, j_metrics[side]["net_pnl_jpy"]) > 0 for side in ("positive", "negative")
        ),
        "major_night_versions_sign_consistent": all(
            cast(int, metrics["net_pnl_jpy"]) > 0
            for metrics in cast(
                dict[str, dict[str, int | float | None]],
                reports["extreme_fade"].get("by_night_schedule_version", {}),
            ).values()
        ),
        "at_least_three_2021_2024_positive": sum(
            cast(int, year_metrics[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)
        )
        >= 3,
        "2025_h1_net_positive": cast(int, year_metrics["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(
            int, concentration_metrics["net_excluding_top10_jpy"]
        )
        > 0,
    }
    execution_audit = {
        "one_trade_per_day_event": all(
            len(trades) == len({trade.trade_date for trade in trades})
            for trades in ledgers.values()
        ),
        "extreme_fade_continuation_same_event_entry_exit_opposite_side": {
            (trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value)
            for trade in ledgers["extreme_fade"]
        }
        == {
            (
                trade.trade_date,
                trade.entry_ts,
                trade.exit_ts,
                "short" if trade.side is Side.LONG else "long",
            )
            for trade in ledgers["extreme_continuation"]
        },
        "extreme_and_middle_are_disjoint": {
            trade.trade_date for trade in ledgers["extreme_fade"]
        }.isdisjoint({trade.trade_date for trade in ledgers["middle_fade"]}),
        "no_stop_or_target": all(
            trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET}
            for trades in ledgers.values()
            for trade in trades
        ),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in ledgers.values()
            for trade in trades
        ),
    }
    if not all(execution_audit.values()):
        raise ValueError("R085 execution/accounting audit failed")
    write_json(OUT / "profiles.json", reports)
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", execution_audit)
    decision = {
        "status": "INVESTIGATE" if all(gates.values()) else "REJECT",
        "decision_ceiling": "INVESTIGATE",
        "s2_gate": gate,
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
