"""Run the frozen Development-only TASK-R094-Q001 experiment exactly once."""

# mypy: disable-error-code="arg-type,assignment,index,misc,no-untyped-call,type-arg"

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np

from n225m_bt.backtest.costs import adverse_fill, gross_pnl, slippage_cost
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r094_cash_path_efficiency_night import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    bootstrap,
    build_events,
    scheduled_reference_audit,
)

ROOT = Path(__file__).resolve().parents[1]
_Q001_SPEC = spec_from_file_location(
    "r093_q001_runner", ROOT / "scripts" / "run_task_r093_q001_tse_cash_night_reversal.py"
)
if _Q001_SPEC is None or _Q001_SPEC.loader is None:
    raise ImportError("cannot load frozen R093 schedule/R004 helpers")
q001 = module_from_spec(_Q001_SPEC)
_Q001_SPEC.loader.exec_module(q001)
RUN_ID = "task-r094-q001-cash-path-efficiency-night-continuation-20260915-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = Path("docs/strategy/91_r094_q001_cash_path_efficiency_night_continuation.md")


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _trade(
    event: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    instrument: Any,
    *,
    direction: str,
    ticks: int,
    fee: int,
    profile: str,
) -> Trade:
    night_day = date.fromisoformat(cast(str, event["night_trade_date"]))
    entry_stamp, exit_stamp = (
        datetime.fromisoformat(cast(str, event[key])) for key in ("entry_open_jst", "exit_open_jst")
    )
    lookup = {bar.ts_jst: bar for bar in bars[(night_day, Session.NIGHT)]}
    entry, exit_bar = lookup[entry_stamp], lookup[exit_stamp]
    side, spec = Side(direction), instrument.instrument.to_spec()
    entry_fill = adverse_fill(entry.open, side is Side.LONG, ticks, spec)
    exit_fill = adverse_fill(exit_bar.open, side is Side.SHORT, ticks, spec)
    excursion = [
        bar
        for bar in bars[(night_day, Session.NIGHT)]
        if entry_stamp <= bar.ts_jst <= exit_stamp and bar.is_eligible
    ]
    mae = max(
        (entry_fill - bar.low if side is Side.LONG else bar.high - entry_fill) for bar in excursion
    )
    mfe = max(
        (bar.high - entry_fill if side is Side.LONG else entry_fill - bar.low) for bar in excursion
    )
    gross = gross_pnl(entry_fill, exit_fill, side, 1, spec)
    return Trade(
        trade_id=f"r094-{profile}-{event['trade_date']}",
        trade_date=night_day,
        side=side,
        qty=1,
        entry_signal_ts=datetime.fromisoformat(cast(str, event["submit_jst"])),
        entry_ts=entry_stamp,
        entry_reference_price=entry.open,
        entry_fill_price=entry_fill,
        exit_signal_ts=datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
        exit_ts=exit_stamp,
        exit_reference_price=exit_bar.open,
        exit_fill_price=exit_fill,
        gross_pnl_jpy=gross,
        fees_jpy=2 * fee,
        slippage_cost_jpy=slippage_cost(entry.open, entry_fill, exit_bar.open, exit_fill, 1, spec),
        net_pnl_jpy=gross - 2 * fee,
        mae_jpy=mae * spec.multiplier,
        mfe_jpy=mfe * spec.multiplier,
        holding_minutes=int((exit_stamp - entry_stamp).total_seconds() // 60),
        entry_reason="r094_cash_close_scheduled_order",
        exit_reason=ExitReason.SIGNAL,
        strategy_id=f"r094_{profile}",
        strategy_version="r094-q001-v1",
        parameter_hash=canonical_hash(
            {"profile": profile, "event": event, "direction": direction, "ticks": ticks, "fee": fee}
        ),
        metadata={
            "entry_session": Session.NIGHT.value,
            "cash_signal_trade_date": event["trade_date"],
            "night_schedule_version": event["night_schedule_version"],
            "state": event["state"],
            "x_rolling_percentile": event["current_x_rolling_percentile"],
        },
    )


def execute(
    axis: list[date],
    events: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Bar]],
    instrument: Any,
    *,
    state: str,
    profile: str,
    direction: str,
    ticks: int,
    fee: int,
) -> tuple[tuple[Trade, ...], dict[str, int], list[str]]:
    trades: dict[date, Trade] = {}
    unknown = [
        cast(str, row["trade_date"])
        for row in events
        if row.get("state") == state and row.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"
    ]
    for event in events:
        if event.get("state") == state and event.get("status") == "EXECUTABLE":
            cash_day = date.fromisoformat(cast(str, event["trade_date"]))
            resolved = (
                cast(str, event[f"{direction}_direction"])
                if direction in {"continuation", "reversal"}
                else direction
            )
            trades[cash_day] = _trade(
                event, bars, instrument, direction=resolved, ticks=ticks, fee=fee, profile=profile
            )
    daily = {day.isoformat(): 0 for day in axis}
    for cash_day, trade in trades.items():
        daily[cash_day.isoformat()] = trade.net_pnl_jpy
    return tuple(trades[day] for day in axis if day in trades), daily, unknown


def report(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int],
    events: list[dict[str, object]],
) -> dict[str, object]:
    by_cash = {cast(str, row["trade_date"]): row for row in events}

    def event_for(trade: Trade) -> dict[str, object]:
        return by_cash[cast(str, trade.metadata["cash_signal_trade_date"])]

    return {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
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
        "by_r_sign": {
            name: ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if cast(int, cast(dict[str, object], event_for(trade)["observation"])["r_sign"])
                    == value
                )
            )
            for name, value in (("positive", 1), ("negative", -1))
        },
        "by_night_regime": {
            version: ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if event_for(trade)["night_schedule_version"] == version
                )
            )
            for version in sorted(
                {
                    cast(str, row["night_schedule_version"])
                    for row in events
                    if row.get("status") == "EXECUTABLE" and row.get("state") == "H"
                }
            )
        },
        "profit_concentration": concentration(trades, axis),
    }


def feasibility(events: list[dict[str, object]]) -> dict[str, object]:
    complete_h = [
        row for row in events if row.get("state") == "H" and row.get("status") == "EXECUTABLE"
    ]
    complete_k = [
        row for row in events if row.get("state") == "K" and row.get("status") == "EXECUTABLE"
    ]
    unresolved = [row for row in events if row.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"]
    qready = [
        row
        for row in events
        if "qx" in row and bool(cast(dict[str, object], row["observation"])["valid"])
    ]
    signs = Counter(
        cast(int, cast(dict[str, object], row["observation"])["r_sign"]) for row in complete_h
    )
    years = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in complete_h)
    regimes = Counter(cast(str, row["night_schedule_version"]) for row in complete_h)
    old = sum(count for version, count in regimes.items() if "from_20241105" not in version)
    new = sum(count for version, count in regimes.items() if "from_20241105" in version)
    bands: dict[str, dict[str, int]] = {
        "0.50_to_0.75": {"H": 0, "M": 0},
        "0.75_to_1.00": {"H": 0, "M": 0},
    }
    for row in complete_h + complete_k:
        percentile = float(cast(float, row["current_x_rolling_percentile"]))
        band = (
            "0.50_to_0.75"
            if 0.5 <= percentile < 0.75
            else "0.75_to_1.00"
            if 0.75 <= percentile <= 1
            else None
        )
        if band is not None:
            bands[band]["H" if row["state"] == "H" else "M"] += 1
    known = (
        "VALID_",
        "HISTORY_",
        "R004_",
        "TSE_",
        "ZERO_",
        "INSUFFICIENT_",
        "X_BELOW_",
        "HIGH_",
        "SCHEDULED_",
        "FOLLOWING_",
        "NONRECIPROCAL_",
        "FIRST_",
        "FINAL_",
        "ENTRY_NOT_",
        "night ",
    )
    unexplained = [
        cast(str, row["trade_date"])
        for row in events
        if not str(row.get("reason", "")).startswith(known)
    ]
    gate = {
        "unexplained_exclusions_equal_zero": not unexplained,
        "unresolved_filled_positions_equal_zero": not unresolved,
        "q_ready_nonzero_days_at_least_800": len(qready) >= 800,
        "H_completed_at_least_100": len(complete_h) >= 100,
        "H_positive_cash_at_least_35": signs[1] >= 35,
        "H_negative_cash_at_least_35": signs[-1] >= 35,
        "K_completed_at_least_250": len(complete_k) >= 250,
        "H_2021_initialization_at_least_8": years[2021] >= 8,
        "H_each_2022_to_2024_at_least_18": all(years[year] >= 18 for year in range(2022, 2025)),
        "H_2025_h1_at_least_8": years[2025] >= 8,
        "H_old_ose_regime_at_least_85": old >= 85,
        "H_new_ose_regime_at_least_8": new >= 8,
        "each_x_band_H_and_M_at_least_20": all(
            count >= 20 for item in bands.values() for count in item.values()
        ),
    }
    return {
        "scope": "PnL-free state/availability/count gate",
        "scheduled_axis_trade_dates": len(events),
        "q_ready_nonzero_days": len(qready),
        "H_completed": len(complete_h),
        "K_completed": len(complete_k),
        "H_complete_by_r_sign": {"positive": signs[1], "negative": signs[-1]},
        "H_complete_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "H_complete_by_night_regime": dict(sorted(regimes.items())),
        "H_old_regime_complete": old,
        "H_new_regime_complete": new,
        "conditional_x_band_completed_counts": bands,
        "unresolved_filled_positions": len(unresolved),
        "unexplained_exclusions": unexplained,
        "status_counts": dict(sorted(Counter(cast(str, row["status"]) for row in events).items())),
        "gate": gate | {"passed": all(gate.values())},
    }


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    executable = [row for row in events if row.get("status") == "EXECUTABLE"]
    checks = scheduled_reference_audit(events, axis) | {
        "H_K_not_selected_by_future_night_availability": all(
            row.get("state") not in {"H", "K"} or bool(row.get("entry_order_submitted"))
            for row in events
        ),
        "cash_close_submit_precedes_mapped_night_entry": all(
            datetime.fromisoformat(cast(str, row["submit_jst"]))
            < datetime.fromisoformat(cast(str, row["entry_open_jst"]))
            for row in executable
        ),
        "cash_to_following_night_mapping_explicit_one_to_one": all(
            row.get("night_calendar_start_date") == row.get("trade_date")
            and row.get("night_trade_date")
            for row in executable
        ),
        "entry_exit_are_first_and_last_normal_scheduled_opens": all(
            row.get("entry_open_jst") == row.get("formal_night_open_jst")
            and row.get("exit_open_jst") == row.get("exit_open_planned_jst")
            for row in executable
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def standardized_a_minus_m(
    axis: list[date], a_trades: tuple[Trade, ...], m_trades: tuple[Trade, ...], index: np.ndarray
) -> dict[str, object]:
    labels = ("0.50_to_0.75", "0.75_to_1.00")
    series: dict[str, dict[str, np.ndarray]] = {}
    for label in labels:
        lower, upper = (0.5, 0.75) if label == labels[0] else (0.75, 1.0)

        def values(
            trades: tuple[Trade, ...],
            lower: float = lower,
            upper: float = upper,
            label: str = label,
        ) -> tuple[np.ndarray, np.ndarray]:
            pnl, count = np.zeros(len(axis), dtype=float), np.zeros(len(axis), dtype=float)
            positions = {day: position for position, day in enumerate(axis)}
            for trade in trades:
                percentile = float(cast(float, trade.metadata["x_rolling_percentile"]))
                inside = (
                    lower <= percentile < upper
                    if label == labels[0]
                    else lower <= percentile <= upper
                )
                if inside:
                    pos = positions[
                        date.fromisoformat(cast(str, trade.metadata["cash_signal_trade_date"]))
                    ]
                    pnl[pos], count[pos] = trade.net_pnl_jpy, 1
            return pnl, count

        h_pnl, h_count = values(a_trades)
        m_pnl, m_count = values(m_trades)
        series[label] = {"h_pnl": h_pnl, "h_count": h_count, "m_pnl": m_pnl, "m_count": m_count}

    def difference(sample_index: np.ndarray) -> np.ndarray:
        parts = []
        for item in series.values():
            h_count, m_count = (
                item["h_count"][sample_index].sum(axis=1),
                item["m_count"][sample_index].sum(axis=1),
            )
            if np.any(h_count == 0) or np.any(m_count == 0):
                raise ValueError("R094 bootstrap draw lacks a required conditional state")
            parts.append(
                item["h_pnl"][sample_index].sum(axis=1) / h_count
                - item["m_pnl"][sample_index].sum(axis=1) / m_count
            )
        return cast(np.ndarray, np.mean(np.stack(parts), axis=0))

    samples = difference(index)
    point = float(difference(np.arange(len(axis), dtype=int)[None, :])[0])
    ci = np.quantile(samples, (0.025, 0.975), method="linear")
    counts = {
        label: {
            name: int(item[f"{prefix}_count"].sum()) for name, prefix in (("H", "h"), ("M", "m"))
        }
        for label, item in series.items()
    }
    return {
        "method": "within-band conditional Net per trade, H minus M; equal weight across x-percentile bands; common MBB index",
        "estimate": point,
        "ci95_percentile_linear": [float(ci[0]), float(ci[1])],
        "completed_counts": counts,
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_task_r094_q001_cash_path_efficiency_night_continuation.py"),
        Path("src/n225m_bt/research/r094_cash_path_efficiency_night.py"),
        Path("tests/test_r094_q001.py"),
    ]
    config_files = [
        Path(f"config/{item}")
        for item in (
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
        "task_id": "TASK-R094-Q001",
        "family_id": "cash_path_efficiency_following_night",
        "study_id": "R094-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "preregistration_document": str(PREREG),
        "preregistration_document_sha256": q001.digest(ROOT / PREREG),
        "prior_information_seen": True,
        "post_r093_hypothesis": True,
        "development_reuse": True,
        "decision_ceiling": "INVESTIGATE",
        "historical_linkage": {
            "R009": "short in-session path efficiency, INCONCLUSIVE",
            "R046": "opening efficiency, REJECT with posthoc-universe limitation",
            "R079": "first-hour extreme continuation",
            "R088": "cash-open efficiency, INCONCLUSIVE availability",
            "R089": "late concentration continuation, REJECT",
            "R093-Q002": "cash-to-night reversal, REJECT; continuation estimate not used",
        },
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "full official cash x/e, exact prior-120 qx50 qe75, H continuation to following OSE night; K low-efficiency ablation",
        "s2_gate": "unexplained=0; unresolved=0; qready>=800; H>=100 and signs>=35; K>=250; H years 8/18/18/18/8; old/new=85/8; each x band H/M>=20",
        "profiles": [
            "A_continuation",
            "B_reversal",
            "C_fixed_long",
            "D_fixed_short",
            "M_K_continuation",
            "N_no_trade",
            "A_qe70",
            "A_qe80",
            "A_qx40",
            "A_qx60",
            "A_entry_delay_1m",
            "A_exit_30m_early",
            "A_cost_2tick",
            "A_cost_3tick",
            "A_fee_2x",
        ],
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-circular MBB; common index; tail truncation; linear percentile",
        },
        "input_partitions": [
            {"path": str(item.resolve().relative_to(ROOT)), "sha256": q001.digest(item)}
            for item in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(item): q001.digest(ROOT / item) for item in source_files},
        "config_hashes": {str(item): q001.digest(ROOT / item) for item in config_files},
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
    q001.snapshot(OUT / "source_snapshot", source_files)
    q001.snapshot(OUT / "config_snapshot", config_files)
    q001.snapshot(OUT / "documentation_snapshot", [PREREG])
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_r094_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r094_cash_path_efficiency_night.py",
            str(source_files[0]),
        ],
        "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files)],
    }
    validation: dict[str, object] = {}
    for name, command in commands.items():
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env)
        validation[name] = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, object], item)["returncode"] == 0 for item in validation.values())
        else "FAIL"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(
            OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}
        )
        raise ValueError("R094 pre-execution validation failed")
    instrument, sessions, data_config, _ = load_project_config(ROOT / "config")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    )
    development = load_split(data_config.gold_root, "development")
    view, isolated, r004 = q001.quarantine(development)
    axis = q001.tse_axis(q001.cash_calendar())
    axis_set = set(axis)
    bars: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in axis_set:
            bars[(bar.trade_date, bar.session)].append(bar)
    primary = build_events(classifier, axis, bars, isolated)
    s2, audit = feasibility(primary), causality_audit(primary, axis)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 then conditional S3",
            "physical_io": "Development normalized Parquet only",
            "data_version": development.data_version,
            "partitions": development.quality["partitions"],
            "r004": r004,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "scheduled_axis.json",
        {
            "trade_dates": [day.isoformat() for day in axis],
            "source": "frozen Cabinet Office TSE business-day evidence",
            "holiday_evidence_sha256": q001.digest(q001.HOLIDAYS),
        },
    )
    write_json(OUT / "primary_events_pnl_free.json", primary)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_audit_before_pnl.json", audit)
    if not bool(cast(dict[str, object], s2["gate"])["passed"]) or not bool(audit["passed"]):
        decision = {
            "decision": "INCONCLUSIVE",
            "reason": "S2_GATE_OR_CAUSALITY_AUDIT_FAILED_BEFORE_PNL",
            "s2_gate": s2["gate"],
            "causality": audit,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return
    profiles = {
        "A_continuation": (primary, "H", "continuation", 1, 30),
        "B_reversal": (primary, "H", "reversal", 1, 30),
        "C_fixed_long": (primary, "H", "long", 1, 30),
        "D_fixed_short": (primary, "H", "short", 1, 30),
        "M_K_continuation": (primary, "K", "continuation", 1, 30),
        "A_qe70": (
            build_events(classifier, axis, bars, isolated, qe_percentile=70),
            "H",
            "continuation",
            1,
            30,
        ),
        "A_qe80": (
            build_events(classifier, axis, bars, isolated, qe_percentile=80),
            "H",
            "continuation",
            1,
            30,
        ),
        "A_qx40": (
            build_events(classifier, axis, bars, isolated, qx_percentile=40),
            "H",
            "continuation",
            1,
            30,
        ),
        "A_qx60": (
            build_events(classifier, axis, bars, isolated, qx_percentile=60),
            "H",
            "continuation",
            1,
            30,
        ),
        "A_entry_delay_1m": (
            build_events(classifier, axis, bars, isolated, entry_delay_minutes=1),
            "H",
            "continuation",
            1,
            30,
        ),
        "A_exit_30m_early": (
            build_events(classifier, axis, bars, isolated, exit_early_minutes=30),
            "H",
            "continuation",
            1,
            30,
        ),
        "A_cost_2tick": (primary, "H", "continuation", 2, 30),
        "A_cost_3tick": (primary, "H", "continuation", 3, 30),
        "A_fee_2x": (primary, "H", "continuation", 1, 60),
    }
    unresolved = {
        name: days
        for name, (events, state, _, _, _) in profiles.items()
        if (
            days := [
                cast(str, row["trade_date"])
                for row in events
                if row.get("state") == state and row.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"
            ]
        )
    }
    for name, (events, _, _, _, _) in profiles.items():
        write_json(OUT / f"{name}_events.json", events)
    if unresolved:
        decision = {
            "decision": "INCONCLUSIVE",
            "reason": "ENTRY_FILLED_EXIT_UNRESOLVED_AFTER_S2",
            "unknown_profiles": unresolved,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return
    reports: dict[str, dict[str, object]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    for name, (events, state, direction, ticks, fee) in profiles.items():
        trades, daily, unknown = execute(
            axis,
            events,
            bars,
            instrument,
            state=state,
            profile=name,
            direction=direction,
            ticks=ticks,
            fee=fee,
        )
        if unknown:
            raise ValueError(f"R094 {name} became unresolved after preflight")
        trades_by[name], reports[name] = trades, report(axis, trades, daily, events)
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    a, b, c, d = (
        list(cast(dict[str, int], reports[name]["daily_net_pnl_jpy"]).values())
        for name in ("A_continuation", "B_reversal", "C_fixed_long", "D_fixed_short")
    )
    boot, index = bootstrap(a, b, c, d)
    conditional = standardized_a_minus_m(
        axis, trades_by["A_continuation"], trades_by["M_K_continuation"], index
    )
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    metrics = cast(dict[str, int | float | None], reports["A_continuation"]["metrics"])

    def lower(key: str) -> float:
        return cast(list[float], cast(dict[str, object], boot[key])["ci95_percentile_linear"])[0]

    main = {
        "A_net_positive": cast(int, metrics["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": metrics["profit_factor"] is not None
        and cast(float, metrics["profit_factor"]) > 1,
        "A_daily_net_ci_lower_positive": lower("A_continuation_daily_net_jpy_per_trade_date") > 0,
        "A_minus_B_ci_lower_positive": lower("A_minus_B_daily_net_jpy_per_trade_date") > 0,
        "A_minus_C_ci_lower_positive": lower("A_minus_C_daily_net_jpy_per_trade_date") > 0,
        "A_minus_D_ci_lower_positive": lower("A_minus_D_daily_net_jpy_per_trade_date") > 0,
        "standardized_A_minus_M_ci_lower_positive": cast(
            list[float], conditional["ci95_percentile_linear"]
        )[0]
        > 0,
    }
    year = cast(dict[str, dict[str, int | float | None]], reports["A_continuation"]["by_year"])
    sign = cast(dict[str, dict[str, int | float | None]], reports["A_continuation"]["by_r_sign"])
    regime = cast(
        dict[str, dict[str, int | float | None]], reports["A_continuation"]["by_night_regime"]
    )
    concentration_a = cast(
        dict[str, int | float | None], reports["A_continuation"]["profit_concentration"]
    )

    def net(name: str) -> int:
        return cast(
            int,
            cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"],
        )

    robustness = {
        "qe70_net_positive": net("A_qe70") > 0,
        "qe80_net_positive": net("A_qe80") > 0,
        "qx40_net_positive": net("A_qx40") > 0,
        "qx60_net_positive": net("A_qx60") > 0,
        "entry_delay_1m_net_positive": net("A_entry_delay_1m") > 0,
        "exit_30m_early_net_positive": net("A_exit_30m_early") > 0,
        "two_tick_net_positive": net("A_cost_2tick") > 0,
        "fee_2x_net_positive": net("A_fee_2x") > 0,
        "positive_r_net_positive": cast(int, sign["positive"]["net_pnl_jpy"]) > 0,
        "negative_r_net_positive": cast(int, sign["negative"]["net_pnl_jpy"]) > 0,
        "both_ose_regimes_net_positive": bool(regime)
        and all(cast(int, item["net_pnl_jpy"]) > 0 for item in regime.values()),
        "at_least_two_2022_2024_positive": sum(
            cast(int, year[str(value)]["net_pnl_jpy"]) > 0 for value in range(2022, 2025)
        )
        >= 2,
        "2025_h1_net_positive": cast(int, year["2025"]["net_pnl_jpy"]) > 0,
        "top10_winners_removed_net_positive": cast(int, concentration_a["net_excluding_top10_jpy"])
        > 0,
        "three_tick_diagnostic_net_jpy": net("A_cost_3tick"),
    }
    h_triplet = tuple(
        (row["trade_date"], row.get("entry_open_jst"), row.get("exit_open_jst"))
        for row in primary
        if row.get("state") == "H" and row.get("status") == "EXECUTABLE"
    )
    execution = {
        "A_B_C_D_same_H_entry_exit": all(
            h_triplet
            == tuple(
                (row["trade_date"], row.get("entry_open_jst"), row.get("exit_open_jst"))
                for row in profiles[name][0]
                if row.get("state") == "H" and row.get("status") == "EXECUTABLE"
            )
            for name in ("B_reversal", "C_fixed_long", "D_fixed_short")
        ),
        "A_B_opposite_side": all(
            left.side is not right.side
            for left, right in zip(
                trades_by["A_continuation"], trades_by["B_reversal"], strict=True
            )
        ),
        "one_trade_one_position_per_cash_day": all(
            len(trades) == len({trade.metadata["cash_signal_trade_date"] for trade in trades})
            for trades in trades_by.values()
        ),
        "no_stop_or_target": all(
            trade.exit_reason is ExitReason.SIGNAL
            for trades in trades_by.values()
            for trade in trades
        ),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
        "slippage_not_double_deducted": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
    }
    if not all(execution.values()):
        raise ValueError("R094 execution/accounting audit failed")
    decision = "REJECT" if not all(main.values()) else "INVESTIGATE"
    write_json(
        OUT / "bootstrap.json",
        boot
        | {
            "index_file": "bootstrap_common_day_indices.npy",
            "standardized_A_minus_M_conditional_net_per_trade": conditional,
        },
    )
    write_json(
        OUT / "profiles.json",
        reports
        | {
            "N_no_trade": {
                "net_pnl_jpy": 0,
                "daily_net_pnl_jpy": {day.isoformat(): 0 for day in axis},
            }
        },
    )
    write_json(OUT / "execution_accounting_audit.json", execution)
    write_json(
        OUT / "s3_results.json",
        {
            "primary_and": main,
            "robustness": robustness,
            "three_tick_diagnostic_net_jpy": net("A_cost_3tick"),
        },
    )
    write_json(
        OUT / "decision.json",
        {
            "decision": decision,
            "decision_ceiling": "INVESTIGATE",
            "primary_and": main,
            "robustness": robustness,
            "s2_gate": s2["gate"],
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "status": "COMPLETE",
            "decision": decision,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
