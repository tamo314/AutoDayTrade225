"""Preregister and execute TASK-R096-Q002 once, after its PnL-free audit."""

# mypy: disable-error-code=attr-defined
from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np
from run_r088_q001_cash_open_path_efficiency import (
    digest,
    engine_for,
    execute,
    orders_fills,
    quarantine,
    snapshot,
    write_json,
)
from run_r096_q001_opening_range_acceptance import profile_report

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.r088_cash_open_path_efficiency import scheduled_axis
from n225m_bt.research.r096_opening_range_acceptance import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    bootstrap,
    build_events,
)
from n225m_bt.research.r096_q002_complement_control import (
    build_q002_events,
    q002_causality_audit,
    q002_feasibility,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r096-q002-20260915-opening-range-acceptance-complement-control-04"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/99_r096_q002_opening_range_acceptance_complement_control.md")
Q001_OUT = ROOT / "results/research/r096-q001-20260915-opening-range-acceptance-continuation-01"


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _cross_counts(events: list[dict[str, object]]) -> dict[str, object]:
    """PnL-free cross-tab of every q-ready x>=qx50 observation."""
    rows = [
        row
        for row in events
        if "qx_cutoff" in row
        and bool(row.get("observation_valid"))
        and cast(float, row["x_abs_displacement_fraction"]) >= cast(float, row["qx_cutoff"])
    ]

    def group(row: dict[str, object]) -> str:
        z, q25, q50, q75 = (
            cast(float, row["z_directional_close_position"]),
            cast(float, row["qz_low"]),
            cast(float, row["qz50"]),
            cast(float, row["qz_high"]),
        )
        if z <= q25:
            return "Q1_z_lte_qz25_old_K"
        if z <= q50:
            return "Q2_qz25_lt_z_lte_qz50"
        if z < q75:
            return "Q3_qz50_lt_z_lt_qz75"
        return "Q4_z_gte_qz75_E"

    output: dict[str, object] = {"total_high_x": len(rows)}
    dimensions = {
        "z_quartile_group": lambda row: group(row),
        "displacement_direction": lambda row: (
            "up" if cast(float, row["displacement_points"]) > 0 else "down"
        ),
        "year": lambda row: str(date.fromisoformat(cast(str, row["trade_date"])).year),
        "tse_close_regime": lambda row: cast(str, row.get("tse_close_regime", "")),
        "x_band": lambda row: cast(str, row.get("x_band", "")),
    }
    for name, key in dimensions.items():
        output[name] = dict(sorted(Counter(key(row) for row in rows).items()))
    output["z_quartile_x_band"] = {
        group_name: {
            band: sum(group(row) == group_name and row.get("x_band") == band for row in rows)
            for band in ("X50_75", "X75_100")
        }
        for group_name in (
            "Q1_z_lte_qz25_old_K",
            "Q2_qz25_lt_z_lte_qz50",
            "Q3_qz50_lt_z_lt_qz75",
            "Q4_z_gte_qz75_E",
        )
    }
    return output


def _q001_reproduction_and_shortfall_audit(
    saved: list[dict[str, object]],
    replay: list[dict[str, object]],
    q002: list[dict[str, object]],
    isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Prove the old K shortage is label geometry, before PnL is accessed."""
    saved_gate = cast(dict[str, object], _load_json(Q001_OUT / "s2_feasibility.json"))["gate"]
    old_k = [
        row
        for row in replay
        if bool(row.get("control_condition")) and row["status"] == "EXECUTABLE"
    ]
    e = [
        row for row in q002 if bool(row.get("accepted_condition")) and row["status"] == "EXECUTABLE"
    ]
    kstar = [
        row for row in q002 if bool(row.get("kstar_condition")) and row["status"] == "EXECUTABLE"
    ]
    high_x = [
        row
        for row in q002
        if "qx_cutoff" in row
        and bool(row.get("observation_valid"))
        and cast(float, row["x_abs_displacement_fraction"]) >= cast(float, row["qx_cutoff"])
    ]
    old_k_dates = {cast(str, row["trade_date"]) for row in old_k}
    e_dates = {cast(str, row["trade_date"]) for row in e}
    kstar_dates = {cast(str, row["trade_date"]) for row in kstar}
    high_x_dates = {cast(str, row["trade_date"]) for row in high_x}
    def q_group(row: dict[str, object]) -> str:
        z, q25, q50, q75 = (
            cast(float, row["z_directional_close_position"]),
            cast(float, row["qz_low"]),
            cast(float, row["qz50"]),
            cast(float, row["qz_high"]),
        )
        if z <= q25:
            return "Q1"
        if z <= q50:
            return "Q2"
        if z < q75:
            return "Q3"
        return "Q4"

    groups = Counter(q_group(row) for row in high_x)
    selected = [
        row
        for row in q002
        if bool(row.get("accepted_condition")) or bool(row.get("kstar_condition"))
    ]
    statuses = Counter(cast(str, row["status"]) for row in selected)
    checks = {
        "q001_primary_events_byte_for_value": canonical_hash(saved) == canonical_hash(replay),
        "q001_qready_reproduced": sum(
            "qx_cutoff" in row and bool(row.get("observation_valid")) for row in replay
        )
        == cast(int, cast(dict[str, object], saved_gate)["q_ready_nonzero_days"]),
        "q001_e_reproduced": len(e)
        == cast(int, cast(dict[str, object], saved_gate)["e_completed"]),
        "q001_old_k_reproduced": len(old_k)
        == cast(int, cast(dict[str, object], saved_gate)["k_completed"]),
        "q001_e_signs_reproduced": Counter(
            "up" if cast(float, row["displacement_points"]) > 0 else "down" for row in e
        )
        == Counter(cast(dict[str, int], cast(dict[str, object], saved_gate)["e_direction_counts"])),
        "q001_e_years_reproduced": Counter(
            str(date.fromisoformat(cast(str, row["trade_date"])).year) for row in e
        )
        == Counter(cast(dict[str, int], cast(dict[str, object], saved_gate)["e_by_year"])),
        "q001_e_regimes_reproduced": Counter(
            cast(str, row.get("tse_close_regime", "")) for row in e
        )
        == Counter(
            cast(dict[str, int], cast(dict[str, object], saved_gate)["e_tse_regime_counts"])
        ),
        "q001_x_bands_reproduced": all(
            sum(row.get("x_band") == band for row in e)
            == cast(dict[str, int], cast(dict[str, object], saved_gate)["x_band_counts"])[band]["E"]
            and sum(row.get("x_band") == band for row in old_k)
            == cast(dict[str, int], cast(dict[str, object], saved_gate)["x_band_counts"])[band]["K"]
            for band in ("X50_75", "X75_100")
        ),
        "old_k_is_exact_q1_z_group": old_k_dates
        == {cast(str, row["trade_date"]) for row in high_x if q_group(row) == "Q1"},
        "e_is_exact_q4_z_group": e_dates
        == {cast(str, row["trade_date"]) for row in high_x if q_group(row) == "Q4"},
        "kstar_is_q1_q2_q3_complement": kstar_dates
        == {
            cast(str, row["trade_date"])
            for row in high_x
            if q_group(row) in {"Q1", "Q2", "Q3"}
        },
        "e_kstar_partition_all_high_x": e_dates.isdisjoint(kstar_dates)
        and e_dates | kstar_dates == high_x_dates,
        "q1_q4_and_partition_count_identity": groups["Q1"] == len(old_k)
        and groups["Q4"] == len(e)
        and groups["Q1"] + groups["Q2"] + groups["Q3"] + groups["Q4"] == len(high_x),
        "no_missing_or_future_availability_attrition": statuses
        == Counter({"EXECUTABLE": len(selected)})
        and len(selected) == len(high_x),
        "no_r004_in_qready_or_selected": all(
            (date.fromisoformat(cast(str, row["trade_date"])), Session.DAY) not in isolated
            for row in high_x
        )
        and all(
            not bool(row.get("accepted_condition")) and not bool(row.get("kstar_condition"))
            for row in q002
            if str(row.get("reason")) == "R004_DAY_SESSION_QUARANTINED"
        ),
        "no_schedule_mismatch_or_r004_reclassification": canonical_hash(saved)
        == canonical_hash(replay),
        "no_execution_processing_failure": not {
            status for status in statuses if status != "EXECUTABLE"
        },
    }
    return {
        "scope": "PnL-free Q001 reproduction and K shortfall explanation; no returns, PnL, orders, fills, trades, bootstrap, or sensitivity performance read.",
        "q001_target": {
            "axis_days": 1131,
            "q_ready": 990,
            "E": 197,
            "old_K": 49,
            "E_up_down": {"up": 100, "down": 97},
        },
        "observed": {
            "old_K": len(old_k),
            "E": len(e),
            "KSTAR": len(kstar),
            "high_x": len(high_x),
            "selected_statuses": dict(statuses),
        },
        "z_group_counts": dict(sorted(groups.items())),
        "cross_counts_high_x": _cross_counts(q002),
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r096_q002_opening_range_acceptance_complement_control.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r096_q002_opening_range_acceptance_complement_control.py"),
        Path("src/n225m_bt/research/r096_q002_complement_control.py"),
        Path("src/n225m_bt/research/r096_opening_range_acceptance.py"),
        Path("scripts/run_r096_q001_opening_range_acceptance.py"),
        Path("scripts/run_r088_q001_cash_open_path_efficiency.py"),
        Path("src/n225m_bt/strategies/r088_fixed_signal.py"),
        Path("tests/test_r096_q001.py"),
        Path("tests/test_r096_q002.py"),
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
        "task_id": "TASK-R096-Q002",
        "family_id": "cash_open_directional_range_end_acceptance_continuation",
        "study_id": "R096-Q002",
        "spec_version": "v1-complement-control",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_PNL",
        "preregistration_document": str(DOC),
        "preregistration_document_sha256": digest(ROOT / DOC),
        "parent_q001": "r096-q001-20260915-opening-range-acceptance-continuation-01",
        "q001_pnl_free_assertion": "Q001 has no PnL, PF, orders/fills/trades, bootstrap, or sensitivity-performance artifact; only its PnL-free state/event ledger is read.",
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_e_and_a_b_c_d": "Q001 verbatim: 60-minute x/z state, strict prior-120 quantiles, E=x>=qx50 and z>=qz75, entry/exit, fixed/paired directions and costs.",
        "sole_change": "M population KSTAR=x>=qx50 and z<qz75; qz75 equality belongs to E. No old lower-tail K economic result is obtained.",
        "s2_gate": "unexplained=0; unresolved=0; q-ready>=800; E>=100; KSTAR>=200; E signs>=35; KSTAR signs>=50; x bands each>=20; frozen E year/regime gates; KSTAR years 10/35/35/35/10 and old/new 170/15.",
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-wrapping MBB; tail truncation; linear percentile; common blocks",
        },
        "standardization": "equal weight 0.5/0.5 for current-day X50_75 and X75_100 bands",
        "sensitivities": [
            "z_q70_complement",
            "z_q80_complement",
            "x_q40",
            "x_q60",
            "window_30",
            "window_90",
            "entry_extra_1_bar",
            "exit_t_minus_20",
            "exit_1455",
            "cost_2tick",
            "fee_x2",
            "cost_3tick_diagnostic",
        ],
        "decision_ceiling": "INVESTIGATE",
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
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
            "tests/test_r096_q001.py",
            "tests/test_r096_q002.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "--follow-imports=skip",
            "src/n225m_bt/research/r096_q002_complement_control.py",
        ],
        "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files)],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        done = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env)
        validation[name] = {
            "returncode": done.returncode,
            "stdout": done.stdout,
            "stderr": done.stderr,
        }
    validation["status"] = (
        "PASS"
        if all(
            cast(dict[str, object], value)["returncode"] == 0
            for name, value in validation.items()
            if name != "status"
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

    replay = build_events(classifier, axis, bars, isolated)
    q002_events = build_q002_events(classifier, axis, bars, isolated)
    saved = cast(list[dict[str, object]], _load_json(Q001_OUT / "primary_events.json"))
    reproduction = _q001_reproduction_and_shortfall_audit(saved, replay, q002_events, isolated)
    s2, causal = q002_feasibility(q002_events), q002_causality_audit(q002_events, axis)
    profiles: dict[str, tuple[dict[str, object], int, int, str, str]] = {
        "a_continuation": ({}, 1, 30, "HE", "continuation"),
        "b_paired_fade": ({}, 1, 30, "HE", "fade"),
        "c_fixed_long": ({}, 1, 30, "HE", "long"),
        "d_fixed_short": ({}, 1, 30, "HE", "short"),
        "m_complement_control": ({}, 1, 30, "HL", "continuation"),
        "z_q70_complement": ({"z_upper": 70}, 1, 30, "HE", "continuation"),
        "z_q80_complement": ({"z_upper": 80}, 1, 30, "HE", "continuation"),
        "x_q40": ({"x_cutoff": 40}, 1, 30, "HE", "continuation"),
        "x_q60": ({"x_cutoff": 60}, 1, 30, "HE", "continuation"),
        "window_30": ({"window_minutes": 30}, 1, 30, "HE", "continuation"),
        "window_90": ({"window_minutes": 90}, 1, 30, "HE", "continuation"),
        "entry_extra_1_bar": ({"entry_extra_bars": 1}, 1, 30, "HE", "continuation"),
        "exit_t_minus_20": ({"exit_minus_minutes": 20}, 1, 30, "HE", "continuation"),
        "exit_1455": ({"fixed_exit_time": time(14, 55)}, 1, 30, "HE", "continuation"),
        "cost_2tick": ({}, 2, 30, "HE", "continuation"),
        "fee_x2": ({}, 1, 60, "HE", "continuation"),
        "cost_3tick_diagnostic": ({}, 3, 30, "HE", "continuation"),
    }
    profile_events = {
        name: q002_events
        if not overrides
        else build_q002_events(classifier, axis, bars, isolated, **overrides)
        for name, (overrides, _, _, _, _) in profiles.items()
    }
    unknown = {
        name: sorted(
            cast(str, row["trade_date"])
            for row in events
            if row["status"] == "ENTRY_FILLED_EXIT_UNKNOWN"
        )
        for name, events in profile_events.items()
    }
    unknown = {name: values for name, values in unknown.items() if values}
    for name, value in {
        "q001_reproduction_and_shortfall_audit.json": reproduction,
        "primary_events.json": q002_events,
        "s2_feasibility.json": s2,
        "causality_audit_before_pnl.json": causal,
    }.items():
        write_json(OUT / name, value)
    for name, events in profile_events.items():
        write_json(OUT / f"{name}_events.json", events)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "Q001 PnL-free reproduction/shortfall audit, then conditional Q002 S3",
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
    if (
        not bool(reproduction["passed"])
        or not bool(causal["passed"])
        or not bool(cast(dict[str, object], s2["gate"])["passed"])
        or unknown
    ):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "R096_Q002_PNL_FREE_REPRODUCTION_SHORTFALL_CAUSALITY_OR_GATE_FAILED",
            "reproduction": reproduction,
            "s2_feasibility": s2,
            "causality_audit": causal,
            "unknown_filled_exit_profiles": unknown,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return

    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int | None]] = {}
    for name, (_, ticks, fee, group, direction) in profiles.items():
        trades, pnl_daily, unresolved = execute(
            axis,
            profile_events[name],
            bars,
            engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee),
            profile=name,
            group=group,
            direction=direction,
        )
        if unresolved:
            raise ValueError(f"{name}: filled exit became unknown after PnL-free audit")
        ledgers[name], daily[name] = trades, pnl_daily
        reports[name] = profile_report(
            axis, trades, pnl_daily, profile_events[name], detailed=name == "a_continuation"
        )
        write_json(OUT / f"{name}_orders_fills.json", orders_fills(trades))
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", pnl_daily)
    by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in q002_events}
    bands = ("X50_75", "X75_100")
    a_band = [
        [
            int(
                bool(by_date[target].get("accepted_condition"))
                and by_date[target]["status"] == "EXECUTABLE"
                and by_date[target].get("x_band") == band
            )
            for band in bands
        ]
        for target in axis
    ]
    m_band = [
        [
            int(
                bool(by_date[target].get("kstar_condition"))
                and by_date[target]["status"] == "EXECUTABLE"
                and by_date[target].get("x_band") == band
            )
            for band in bands
        ]
        for target in axis
    ]
    support = {
        band: {
            "a_count": sum(row[index] for row in a_band),
            "m_count": sum(row[index] for row in m_band),
        }
        for index, band in enumerate(bands)
    }
    weights = [0.5, 0.5]
    arrays = {
        name: [cast(int, value) for value in daily[name].values()]
        for name in (
            "a_continuation",
            "b_paired_fade",
            "c_fixed_long",
            "d_fixed_short",
            "m_complement_control",
        )
    }
    boot, index = bootstrap(
        arrays["a_continuation"],
        arrays["b_paired_fade"],
        arrays["c_fixed_long"],
        arrays["d_fixed_short"],
        arrays["m_complement_control"],
        a_band,
        m_band,
        weights,
    )
    point = sum(
        weights[index]
        * (
            sum(
                arrays["a_continuation"][row_index]
                for row_index, row in enumerate(a_band)
                if row[index]
            )
            / support[band]["a_count"]
            - sum(
                arrays["m_complement_control"][row_index]
                for row_index, row in enumerate(m_band)
                if row[index]
            )
            / support[band]["m_count"]
        )
        for index, band in enumerate(bands)
    )
    cast(dict[str, object], boot["a_minus_m_x_band_standardized_net_jpy_per_trade"])["estimate"] = (
        point
    )
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    main_metrics = cast(dict[str, int | float | None], reports["a_continuation"]["metrics"])
    ci = {
        name: cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0]
        for name in (
            "a_scheduled_axis_mean_net_jpy_per_trade_date",
            "a_minus_b_paired_daily_net_jpy",
            "a_minus_c_paired_daily_net_jpy",
            "a_minus_d_paired_daily_net_jpy",
            "a_minus_m_x_band_standardized_net_jpy_per_trade",
        )
    }
    gates = {
        "net_positive": cast(int, main_metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(
            main_metrics["profit_factor"] and cast(float, main_metrics["profit_factor"]) > 1
        ),
        "a_daily_mbb_ci95_lower_gt_zero": ci["a_scheduled_axis_mean_net_jpy_per_trade_date"] > 0,
        "a_minus_b_ci95_lower_gt_zero": ci["a_minus_b_paired_daily_net_jpy"] > 0,
        "a_minus_c_ci95_lower_gt_zero": ci["a_minus_c_paired_daily_net_jpy"] > 0,
        "a_minus_d_ci95_lower_gt_zero": ci["a_minus_d_paired_daily_net_jpy"] > 0,
        "a_minus_m_standardized_ci95_lower_gt_zero": ci[
            "a_minus_m_x_band_standardized_net_jpy_per_trade"
        ]
        > 0,
    }
    detailed = cast(dict[str, dict[str, dict[str, int | float | None]]], reports["a_continuation"])
    fixed_sensitivity = [
        name
        for name in profiles
        if name
        not in {
            "a_continuation",
            "b_paired_fade",
            "c_fixed_long",
            "d_fixed_short",
            "m_complement_control",
            "cost_3tick_diagnostic",
        }
    ]
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_fixed_sensitivity_net_positive": all(
            cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])
            > 0
            for name in fixed_sensitivity
        ),
        "cost_3tick_diagnostic_net_positive": cast(
            int,
            cast(dict[str, int | float | None], reports["cost_3tick_diagnostic"]["metrics"])[
                "net_pnl_jpy"
            ],
        )
        > 0,
        "both_displacement_sign_net_positive": all(
            cast(int, detailed["by_displacement_sign"][name]["net_pnl_jpy"]) > 0
            for name in ("up", "down")
        ),
        "both_tse_regime_net_positive": all(
            cast(int, detailed["by_tse_close_regime"][name]["net_pnl_jpy"]) > 0
            for name in ("old", "new")
        ),
        "at_least_two_2022_2024_positive": sum(
            cast(int, detailed["by_year"][str(year)]["net_pnl_jpy"]) > 0
            for year in range(2022, 2025)
        )
        >= 2,
        "2025_h1_net_positive": cast(int, detailed["by_year"]["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(
            int,
            cast(dict[str, int | float | None], reports["a_continuation"]["profit_concentration"])[
                "net_excluding_top10_jpy"
            ],
        )
        > 0,
    }
    audit = {
        "causality_audit_before_pnl_passed": bool(causal["passed"]),
        "one_trade_per_trade_date": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in ledgers.values()
        ),
        "paired_b_same_event_entry_exit_opposite_side": {
            (trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value)
            for trade in ledgers["a_continuation"]
        }
        == {
            (
                trade.trade_date,
                trade.entry_ts,
                trade.exit_ts,
                "short" if trade.side is Side.LONG else "long",
            )
            for trade in ledgers["b_paired_fade"]
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
        raise ValueError("R096-Q002 execution/accounting audit failed")
    write_json(
        OUT / "profiles.json",
        reports
        | {
            "no_trade_control": {
                "daily_net_pnl_jpy": {target.isoformat(): 0 for target in axis},
                "net_pnl_jpy": 0,
            }
        },
    )
    write_json(OUT / "x_band_support.json", support)
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
