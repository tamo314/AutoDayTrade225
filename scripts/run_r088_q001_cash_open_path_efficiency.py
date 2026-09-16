"""Execute frozen Development-only TASK-R088-Q001 exactly once."""

# mypy: disable-error-code=attr-defined

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
from n225m_bt.research.r088_cash_open_path_efficiency import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    bootstrap,
    build_events,
    causality_audit,
    feasibility,
    scheduled_axis,
)
from n225m_bt.strategies.r088_fixed_signal import R088FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r088-q001-20260915-cash-open-path-efficiency-continuation-01"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/71_r088_q001_cash_open_path_efficiency_continuation.md")
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for file in files:
        shutil.copy2(ROOT / file, destination / file.name)


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
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
        raise ValueError(f"BLOCKED: fixed R004 quarantine mismatch: {mismatches}")
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
            "mode": "day_only",
            "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
            "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
            "risk": baseline.risk.model_copy(
                update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    return BacktestEngine(cast(Any, instrument).instrument.to_spec(), config, classifier)


def execute(
    axis: list[date],
    events: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Any]],
    engine: BacktestEngine,
    *,
    profile: str,
    group: str,
    direction: str,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
    trades: list[Trade] = []
    unknown: set[date] = set()
    condition_key = "high_efficiency_condition" if group == "HE" else "low_efficiency_condition"
    for target in axis:
        event = by_date[target]
        if not bool(event.get(condition_key)):
            continue
        if event.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            continue
        if event.get("status") != "EXECUTABLE":
            continue
        side = cast(str, event[f"{direction}_direction"])
        result = engine.run(
            bars[(target, Session.DAY)],
            R088FixedSignalStrategy(
                f"r088_{profile}_{target.isoformat()}",
                datetime.fromisoformat(cast(str, event["entry_signal_jst"])),
                datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
                side,
            ),
            parameter_hash=canonical_hash({"profile": profile, "event": event, "direction": side}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{profile}/{target}: executable event did not fill exactly once")
        trade = result.trades[0]
        if (
            trade.entry_ts.isoformat() != event["entry_open_jst"]
            or trade.exit_ts.isoformat() != event["exit_open_jst"]
            or trade.side is not Side(side)
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{profile}/{target}: event/execution mismatch")
        trades.append(trade)
    ordered = tuple(sorted(trades, key=lambda trade: trade.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), unknown


def report(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int | None],
    events: list[dict[str, object]],
    *,
    detailed: bool = False,
) -> dict[str, object]:
    by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
    result: dict[str, object] = {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [day for day, value in daily.items() if value is None],
        "trade_occurrence_rate_per_scheduled_trade_date": len(trades) / len(axis),
        "by_year": {
            str(year): ledger_metrics(
                tuple(trade for trade in trades if trade.trade_date.year == year)
            )
            for year in range(2021, 2026)
        },
        "profit_concentration": concentration(trades, axis),
    }
    if detailed:
        result["by_displacement_sign"] = {
            name: ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if (cast(float, by_date[trade.trade_date]["displacement_points"]) > 0)
                    == (name == "up")
                )
            )
            for name in ("up", "down")
        }
        result["by_e_quintile"] = {
            str(q): ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if int(cast(float, by_date[trade.trade_date]["e_efficiency"]) * 5) + 1 == q
                )
            )
            for q in range(1, 6)
        }
        result["by_m_quintile"] = {
            str(q): ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if by_date[trade.trade_date].get("causal_m_quintile") == q
                )
            )
            for q in range(1, 6)
        }
    return result


def orders_fills(trades: tuple[Trade, ...]) -> dict[str, list[dict[str, object]]]:
    orders: list[dict[str, object]] = []
    fills: list[dict[str, object]] = []
    for trade in trades:
        orders.extend(
            [
                {
                    "trade_id": trade.trade_id,
                    "action": "ENTRY",
                    "signal_ts": trade.entry_signal_ts,
                    "planned_fill_ts": trade.entry_ts,
                    "side": trade.side.value,
                },
                {
                    "trade_id": trade.trade_id,
                    "action": "EXIT",
                    "signal_ts": trade.exit_signal_ts,
                    "planned_fill_ts": trade.exit_ts,
                    "side": "sell" if trade.side is Side.LONG else "buy",
                },
            ]
        )
        fills.extend(
            [
                {
                    "trade_id": trade.trade_id,
                    "action": "ENTRY",
                    "fill_ts": trade.entry_ts,
                    "reference_price": trade.entry_reference_price,
                    "fill_price": trade.entry_fill_price,
                },
                {
                    "trade_id": trade.trade_id,
                    "action": "EXIT",
                    "fill_ts": trade.exit_ts,
                    "reference_price": trade.exit_reference_price,
                    "fill_price": trade.exit_fill_price,
                },
            ]
        )
    return {"orders": orders, "fills": fills}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r088_q001_cash_open_path_efficiency.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r088_q001_cash_open_path_efficiency.py"),
        Path("src/n225m_bt/research/r088_cash_open_path_efficiency.py"),
        Path("src/n225m_bt/strategies/r088_fixed_signal.py"),
        Path("tests/test_r088_q001.py"),
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
        "task_id": "TASK-R088-Q001",
        "family_id": "cash_open_path_efficiency_order_flow_continuation",
        "study_id": "R088-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_ADDITIONAL_PNL",
        "preregistration_document": str(DOC),
        "preregistration_document_sha256": digest(ROOT / DOC),
        "prior_information_seen": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "09:00 open and each 09:00--09:59 close form p0..p60; M=absolute displacement bps and E=absolute displacement/path length. Exact prior 120 scheduled dates, current excluded, need 100 valid, provide causal q60(M), q30/q70(E); HE/HL as preregistered; next eligible open after 09:59 and fixed 14:55 exit.",
        "s2_gate": "unexplained=0; HE/HL >=90 each; HE up/down >=30 each; HE 2021-2024 >=12 each; 2025H1 >=6; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis JPY0; same HE event/entry/exit paired fade; HL magnitude control in displacement direction; fixed HE M-quintile standard weights.",
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-wrapping MBB; tail truncation; linear percentile",
        },
        "sensitivities": [
            "window_30",
            "window_90",
            "m_q50",
            "m_q70",
            "e_q60",
            "e_q80",
            "reference_80",
            "reference_160",
            "entry_extra_1_bar",
            "exit1430",
            "exit1510",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE",
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
    shutil.copy2(ROOT / DOC, OUT / "documentation_snapshot" / DOC.name)
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_r088_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r088_cash_open_path_efficiency.py",
            "src/n225m_bt/strategies/r088_fixed_signal.py",
        ],
        "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files[:3])],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env)
        validation[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    validation["status"] = (
        "PASS"
        if all(
            cast(dict[str, object], item)["returncode"] == 0
            for key, item in validation.items()
            if key != "status"
        )
        else "FAIL"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(
            OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}
        )
        raise ValueError("pre-execution validation failed")
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier, axis = CalendarClassifier(sessions, calendar), scheduled_axis(calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in set(axis) and bar.session is Session.DAY:
            bars[(bar.trade_date, bar.session)].append(bar)
    events = build_events(classifier, axis, bars, isolated)
    s2, causal = feasibility(events), causality_audit(events, axis)
    write_json(OUT / "primary_events.json", events)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_audit_before_pnl.json", causal)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 PnL-free R088 feasibility and causality audit, then conditional S3",
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
    if not bool(cast(dict[str, object], s2["gate"])["passed"]) or not bool(causal["passed"]):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "R088_PNL_FREE_FEASIBILITY_OR_CAUSALITY_GATE_FAILED",
            "s2_feasibility": s2,
            "causality_audit": causal,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    profiles: dict[str, tuple[dict[str, int | time], int, int, str, str]] = {
        "he_continuation": ({}, 1, 30, "HE", "continuation"),
        "he_paired_fade": ({}, 1, 30, "HE", "fade"),
        "hl_magnitude_control": ({}, 1, 30, "HL", "continuation"),
        "window_30": ({"window_minutes": 30}, 1, 30, "HE", "continuation"),
        "window_90": ({"window_minutes": 90}, 1, 30, "HE", "continuation"),
        "m_q50": ({"m_cutoff": 50}, 1, 30, "HE", "continuation"),
        "m_q70": ({"m_cutoff": 70}, 1, 30, "HE", "continuation"),
        "e_q60": ({"e_upper": 60}, 1, 30, "HE", "continuation"),
        "e_q80": ({"e_upper": 80}, 1, 30, "HE", "continuation"),
        "reference_80": ({"reference_window": 80}, 1, 30, "HE", "continuation"),
        "reference_160": ({"reference_window": 160}, 1, 30, "HE", "continuation"),
        "entry_extra_1_bar": ({"entry_extra_bars": 1}, 1, 30, "HE", "continuation"),
        "exit1430": ({"exit_time": time(14, 30)}, 1, 30, "HE", "continuation"),
        "exit1510": ({"exit_time": time(15, 10)}, 1, 30, "HE", "continuation"),
        "cost_2tick": ({}, 2, 30, "HE", "continuation"),
        "cost_3tick": ({}, 3, 30, "HE", "continuation"),
        "fee_x2": ({}, 1, 60, "HE", "continuation"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int | None]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    base_names = {
        "he_continuation",
        "he_paired_fade",
        "hl_magnitude_control",
        "cost_2tick",
        "cost_3tick",
        "fee_x2",
    }
    for name, (overrides, ticks, fee, group, direction) in profiles.items():
        profile_events = (
            events
            if name in base_names
            else build_events(classifier, axis, bars, isolated, **overrides)
        )
        trades, profile_daily, unknown = execute(
            axis,
            profile_events,
            bars,
            engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee),
            profile=name,
            group=group,
            direction=direction,
        )
        ledgers[name], daily[name], reports[name] = (
            trades,
            profile_daily,
            report(axis, trades, profile_daily, profile_events, detailed=name == "he_continuation"),
        )
        if unknown:
            unknown_profiles[name] = sorted(item.isoformat() for item in unknown)
        write_json(OUT / f"{name}_events.json", profile_events)
        write_json(OUT / f"{name}_orders_fills.json", orders_fills(trades))
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", profile_daily)
    no_trade = {target.isoformat(): 0 for target in axis}
    write_json(OUT / "no_trade_control_daily_axis.json", no_trade)
    if unknown_profiles:
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "UNKNOWN_FILLED_EXIT",
            "unknown_profiles": unknown_profiles,
        }
        write_json(
            OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade}}
        )
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    arrays = {
        name: [cast(int, value) for value in daily[name].values()]
        for name in ("he_continuation", "he_paired_fade", "hl_magnitude_control")
    }
    by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
    he_q_counts = [
        [
            int(
                bool(by_date[target].get("high_efficiency_condition"))
                and by_date[target].get("status") == "EXECUTABLE"
                and by_date[target].get("causal_m_quintile") == q
            )
            for q in range(1, 6)
        ]
        for target in axis
    ]
    hl_q_counts = [
        [
            int(
                bool(by_date[target].get("low_efficiency_condition"))
                and by_date[target].get("status") == "EXECUTABLE"
                and by_date[target].get("causal_m_quintile") == q
            )
            for q in range(1, 6)
        ]
        for target in axis
    ]
    he_total = sum(sum(row) for row in he_q_counts)
    weights = [sum(row[q] for row in he_q_counts) / he_total for q in range(5)]
    support = {
        str(q + 1): {
            "he_count": sum(row[q] for row in he_q_counts),
            "hl_count": sum(row[q] for row in hl_q_counts),
            "he_weight": weights[q],
            "common_support": bool(
                sum(row[q] for row in he_q_counts) and sum(row[q] for row in hl_q_counts)
            ),
        }
        for q in range(5)
    }
    boot, index = bootstrap(
        arrays["he_continuation"],
        arrays["he_paired_fade"],
        arrays["hl_magnitude_control"],
        he_q_counts,
        hl_q_counts,
        weights,
    )
    point = sum(
        weights[q]
        * (
            sum(arrays["he_continuation"][i] for i, row in enumerate(he_q_counts) if row[q])
            / support[str(q + 1)]["he_count"]
            - sum(arrays["hl_magnitude_control"][i] for i, row in enumerate(hl_q_counts) if row[q])
            / support[str(q + 1)]["hl_count"]
        )
        for q in range(5)
        if support[str(q + 1)]["common_support"]
    )
    cast(dict[str, object], boot["he_minus_hl_standardized_net_jpy_per_trade"])["estimate"] = point
    cast(dict[str, object], boot["he_minus_hl_standardized_net_jpy_per_trade"])[
        "common_support_all_he_quintiles"
    ] = all(cast(bool, value["common_support"]) for value in support.values())
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    write_json(OUT / "m_quintile_support.json", support)
    main = cast(dict[str, int | float | None], reports["he_continuation"]["metrics"])
    main_ci = cast(
        list[float],
        cast(dict[str, object], boot["he_scheduled_axis_mean_net_jpy_per_trade_date"])[
            "ci95_percentile_linear"
        ],
    )
    fade_ci = cast(
        list[float],
        cast(dict[str, object], boot["he_minus_paired_fade_paired_daily_net_jpy"])[
            "ci95_percentile_linear"
        ],
    )
    hl_ci = cast(
        list[float],
        cast(dict[str, object], boot["he_minus_hl_standardized_net_jpy_per_trade"])[
            "ci95_percentile_linear"
        ],
    )
    gates = {
        "net_positive": cast(int, main["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(main["profit_factor"] and cast(float, main["profit_factor"]) > 1),
        "he_daily_mbb_ci95_lower_gt_zero": main_ci[0] > 0,
        "he_minus_paired_fade_ci95_lower_gt_zero": fade_ci[0] > 0,
        "he_minus_hl_standardized_ci95_lower_gt_zero": hl_ci is not None and hl_ci[0] > 0,
    }
    sensitivity_names = [
        name
        for name in profiles
        if name not in {"he_continuation", "he_paired_fade", "hl_magnitude_control"}
    ]
    details = cast(
        dict[str, dict[str, int | float | None]], reports["he_continuation"]["by_displacement_sign"]
    )
    years = cast(dict[str, dict[str, int | float | None]], reports["he_continuation"]["by_year"])
    concentration_metrics = cast(
        dict[str, int | float | None], reports["he_continuation"]["profit_concentration"]
    )
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_fixed_sensitivity_net_positive": all(
            cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])
            > 0
            for name in sensitivity_names
        ),
        "both_displacement_sign_net_positive": all(
            cast(int, details[name]["net_pnl_jpy"]) > 0 for name in ("up", "down")
        ),
        "at_least_three_2021_2024_positive": sum(
            cast(int, years[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)
        )
        >= 3,
        "2025_h1_net_positive": cast(int, years["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(
            int, concentration_metrics["net_excluding_top10_jpy"]
        )
        > 0,
    }
    audit = {
        "causality_audit_before_pnl_passed": bool(causal["passed"]),
        "one_trade_per_trade_date": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in ledgers.values()
        ),
        "paired_fade_same_event_entry_exit_opposite_side": {
            (trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value)
            for trade in ledgers["he_continuation"]
        }
        == {
            (
                trade.trade_date,
                trade.entry_ts,
                trade.exit_ts,
                "short" if trade.side is Side.LONG else "long",
            )
            for trade in ledgers["he_paired_fade"]
        },
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
    if not all(audit.values()):
        raise ValueError("R088 execution/accounting/causality audit failed")
    write_json(
        OUT / "profiles.json",
        reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}},
    )
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {
        "status": "INVESTIGATE" if all(gates.values()) else "REJECT",
        "decision_ceiling": "INVESTIGATE",
        "s2_feasibility": s2,
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
