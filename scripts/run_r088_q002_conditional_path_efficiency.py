"""Audit Q001 then execute the one-time preregistered TASK-R088-Q002."""

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

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r088_cash_open_path_efficiency import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    nearest_rank,
    scheduled_axis,
)
from n225m_bt.research.r088_q002_conditional_path_efficiency import (
    bootstrap,
    build_events,
    causality_audit,
    feasibility,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r088-q002-20260915-conditional-path-efficiency-continuation-02"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/73_r088_q002_conditional_path_efficiency_continuation.md")
PARENT = ROOT / "results/research/r088-q001-20260915-cash-open-path-efficiency-continuation-01"
PARENT_EVENTS_HASH = "e8438e9d9c94fad757ccd48433e766fd34bc0db93868e9d5e0312ba4fb0c7edc"


def q001_audit() -> dict[str, object]:
    """Recompute Q001 labels/quantiles without loading post-09:59 prices or PnL."""
    source = PARENT / "primary_events.json"
    if digest(source) != PARENT_EVENTS_HASH:
        raise ValueError("Q001 primary event ledger hash mismatch")
    rows = cast(list[dict[str, object]], json.loads(source.read_text(encoding="utf-8")))
    decision = cast(
        dict[str, object], json.loads((PARENT / "decision.json").read_text(encoding="utf-8"))
    )
    parent_pnl_files = [
        path.name
        for path in PARENT.iterdir()
        if any(
            token in path.name for token in ("trades", "orders", "fills", "profiles", "bootstrap")
        )
    ]
    by_date = {cast(str, row["trade_date"]): row for row in rows}
    daily: list[dict[str, object]] = []
    quantiles_match = True
    bins: Counter[str] = Counter()
    high_m: list[dict[str, object]] = []
    for row in rows:
        label = {
            key: row.get(key)
            for key in (
                "trade_date",
                "observation_valid",
                "reason",
                "status",
                "m_bps",
                "e_efficiency",
                "q_m_bps",
                "q_e_low",
                "q_e_high",
                "high_efficiency_condition",
                "low_efficiency_condition",
                "reference_valid_count",
                "reference_valid_trade_dates",
            )
        }
        daily.append(label)
        if not row.get("observation_valid") or int(cast(int, row["reference_valid_count"])) < 100:
            continue
        refs = [
            by_date[cast(str, target)]
            for target in cast(list[str], row["reference_valid_trade_dates"])
        ]
        expected = (
            nearest_rank([cast(float, item["m_bps"]) for item in refs], 60),
            nearest_rank([cast(float, item["e_efficiency"]) for item in refs], 30),
            nearest_rank([cast(float, item["e_efficiency"]) for item in refs], 70),
        )
        actual = (row.get("q_m_bps"), row.get("q_e_low"), row.get("q_e_high"))
        quantiles_match &= all(
            float(left) == right for left, right in zip(actual, expected, strict=True)
        )
        m, e = cast(float, row["m_bps"]), cast(float, row["e_efficiency"])
        m_bin = "M_LT_Q60" if m < expected[0] else "M_GE_Q60"
        e_bin = "E_LE_Q30" if e <= expected[1] else "E_GE_Q70" if e >= expected[2] else "E_MIDDLE"
        bins[f"{m_bin}__{e_bin}"] += 1
        if m_bin == "M_GE_Q60" and cast(float, row["displacement_points"]) != 0:
            high_m.append(row)
    parent_audit = cast(dict[str, object], decision["causality_audit"])
    unexplained = cast(
        list[str],
        cast(dict[str, object], decision["s2_feasibility"])["gate"][
            "unexplained_exclusion_trade_dates"
        ],  # type: ignore[index]
    )
    high_m_e = [cast(float, row["e_efficiency"]) for row in high_m]
    high_m_q30 = [cast(float, row["q_e_low"]) for row in high_m]
    structural_only = (
        bool(parent_audit["passed"])
        and quantiles_match
        and not unexplained
        and not parent_pnl_files
        and len(high_m) == 411
        and bins["M_GE_Q60__E_LE_Q30"] == 1
    )
    return {
        "scope": "Q001 PnL-free root-cause audit; no PnL/return/performance data read or derived.",
        "q001_primary_events_sha256": digest(source),
        "q001_pnl_was_not_obtained": not parent_pnl_files,
        "q001_prohibited_pnl_artifacts": parent_pnl_files,
        "q001_parent_causality_audit": parent_audit,
        "quantile_recomputation_matches_daily_ledger": quantiles_match,
        "unexplained_exclusion_trade_dates": unexplained,
        "daily_labels": daily,
        "causal_m_e_joint_distribution": dict(sorted(bins.items())),
        "high_m_nonzero_count": len(high_m),
        "high_m_e_summary": {
            "min": min(high_m_e),
            "median": float(np.median(high_m_e)),
            "max": max(high_m_e),
            "q30_min": min(high_m_q30),
            "q30_median": float(np.median(high_m_q30)),
            "q30_max": max(high_m_q30),
        },
        "structural_dependency_only": structural_only,
        "conclusion": "HIGH_M_AND_E_DEPENDENCE_REMOVES_UNCONDITIONAL_HL_SUPPORT"
        if structural_only
        else "INCONCLUSIVE_DO_NOT_ACCESS_PNL",
    }


def profile_report(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int | None],
    events: list[dict[str, object]],
    *,
    detailed: bool,
) -> dict[str, object]:
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
        by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
        result.update(
            by_displacement_sign={
                name: ledger_metrics(
                    tuple(
                        trade
                        for trade in trades
                        if (cast(float, by_date[trade.trade_date]["displacement_points"]) > 0)
                        == (name == "up")
                    )
                )
                for name in ("up", "down")
            },
            by_m_band={
                band: ledger_metrics(
                    tuple(
                        trade for trade in trades if by_date[trade.trade_date].get("m_band") == band
                    )
                )
                for band in ("B1", "B2")
            },
            by_e_quintile={
                str(q): ledger_metrics(
                    tuple(
                        trade
                        for trade in trades
                        if int(cast(float, by_date[trade.trade_date]["e_efficiency"]) * 5) + 1 == q
                    )
                )
                for q in range(1, 6)
            },
        )
    return result


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r088_q002_conditional_path_efficiency.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r088_q002_conditional_path_efficiency.py"),
        Path("src/n225m_bt/research/r088_q002_conditional_path_efficiency.py"),
        Path("src/n225m_bt/strategies/r088_fixed_signal.py"),
        Path("scripts/run_r088_q001_cash_open_path_efficiency.py"),
        Path("src/n225m_bt/research/r088_cash_open_path_efficiency.py"),
        Path("tests/test_r088_q001.py"),
        Path("tests/test_r088_q002.py"),
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
        "task_id": "TASK-R088-Q002",
        "family_id": "cash_open_path_efficiency_order_flow_continuation",
        "study_id": "R088-Q002",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_PNL",
        "preregistration_document": str(DOC),
        "preregistration_document_sha256": digest(ROOT / DOC),
        "parent_q001_events_sha256": PARENT_EVENTS_HASH,
        "q001_pnl_not_obtained": True,
        "prior_information_seen": True,
        "learned_from_q001_pnl_free_hl_support_failure": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "Q001 p0..p60/M/L/E and execution unchanged. Prior exact 120 scheduled dates, minimum 100 valid. M>=q60; prior values nearest by abs(M_i-M_d), newest date ties, first 60; q30/q70 E define CHL/CHE.",
        "s2_gate": "unexplained=0; CHE/CHL >=90 each; CHE up/down >=30 each; CHE 2021-2024 >=12 each; 2025H1 >=6; B1/B2 CHE/CHL >=20 each; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis JPY0; same CHE event/entry/exit paired fade; CHL same direction; CHE B1/B2 fixed standard weights.",
        "bootstrap": {
            "seed": 20260915,
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
            "comparison_40",
            "comparison_80",
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
            "tests/test_r088_q002.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r088_cash_open_path_efficiency.py",
            "src/n225m_bt/research/r088_q002_conditional_path_efficiency.py",
            "src/n225m_bt/strategies/r088_fixed_signal.py",
        ],
        "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files[:5])],
    }
    validation: dict[str, object] = {}
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
            cast(dict[str, int], item)["returncode"] == 0
            for key, item in validation.items()
            if key != "status"
        )
        else "FAIL"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("pre-execution validation failed")
    parent_audit = q001_audit()
    write_json(OUT / "q001_hl_root_cause_audit.json", parent_audit)
    if not bool(parent_audit["structural_dependency_only"]):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "Q001_HL_CAUSE_NOT_SOLELY_STRUCTURAL",
            "q001_audit": parent_audit,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
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
            "stage": "Q001 PnL-free root-cause audit then Q002 S2 then conditional S3",
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
            "reason": "R088_Q002_PNL_FREE_FEASIBILITY_OR_CAUSALITY_GATE_FAILED",
            "q001_audit": parent_audit,
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
        "che_continuation": ({}, 1, 30, "CHE", "continuation"),
        "che_paired_fade": ({}, 1, 30, "CHE", "fade"),
        "chl_continuation": ({}, 1, 30, "CHL", "continuation"),
        "window_30": ({"window_minutes": 30}, 1, 30, "CHE", "continuation"),
        "window_90": ({"window_minutes": 90}, 1, 30, "CHE", "continuation"),
        "m_q50": ({"m_cutoff": 50}, 1, 30, "CHE", "continuation"),
        "m_q70": ({"m_cutoff": 70}, 1, 30, "CHE", "continuation"),
        "e_q60": ({"e_upper": 60}, 1, 30, "CHE", "continuation"),
        "e_q80": ({"e_upper": 80}, 1, 30, "CHE", "continuation"),
        "reference_80": ({"reference_window": 80}, 1, 30, "CHE", "continuation"),
        "reference_160": ({"reference_window": 160}, 1, 30, "CHE", "continuation"),
        "comparison_40": ({"comparison_size": 40}, 1, 30, "CHE", "continuation"),
        "comparison_80": ({"comparison_size": 80}, 1, 30, "CHE", "continuation"),
        "entry_extra_1_bar": ({"entry_extra_bars": 1}, 1, 30, "CHE", "continuation"),
        "exit1430": ({"exit_time": time(14, 30)}, 1, 30, "CHE", "continuation"),
        "exit1510": ({"exit_time": time(15, 10)}, 1, 30, "CHE", "continuation"),
        "cost_2tick": ({}, 2, 30, "CHE", "continuation"),
        "cost_3tick": ({}, 3, 30, "CHE", "continuation"),
        "fee_x2": ({}, 1, 60, "CHE", "continuation"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int | None]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    base = {
        "che_continuation",
        "che_paired_fade",
        "chl_continuation",
        "cost_2tick",
        "cost_3tick",
        "fee_x2",
    }
    for name, (overrides, ticks, fee, group, direction) in profiles.items():
        profile_events = (
            events if name in base else build_events(classifier, axis, bars, isolated, **overrides)
        )
        normalized_group = "HE" if group == "CHE" else "HL"
        trades, profile_daily, unknown = execute(
            axis,
            profile_events,
            bars,
            engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee),
            profile=name,
            group=normalized_group,
            direction=direction,
        )
        ledgers[name], daily[name] = trades, profile_daily
        reports[name] = profile_report(
            axis, trades, profile_daily, profile_events, detailed=name == "che_continuation"
        )
        if unknown:
            unknown_profiles[name] = sorted(day.isoformat() for day in unknown)
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
        for name in ("che_continuation", "che_paired_fade", "chl_continuation")
    }
    by_date = {date.fromisoformat(cast(str, row["trade_date"])): row for row in events}
    che_band = [
        [
            int(
                bool(by_date[day].get("conditional_high_efficiency_condition"))
                and by_date[day].get("status") == "EXECUTABLE"
                and by_date[day].get("m_band") == band
            )
            for band in ("B1", "B2")
        ]
        for day in axis
    ]
    chl_band = [
        [
            int(
                bool(by_date[day].get("conditional_low_efficiency_condition"))
                and by_date[day].get("status") == "EXECUTABLE"
                and by_date[day].get("m_band") == band
            )
            for band in ("B1", "B2")
        ]
        for day in axis
    ]
    counts = {
        band: {
            "che_count": sum(row[index] for row in che_band),
            "chl_count": sum(row[index] for row in chl_band),
        }
        for index, band in enumerate(("B1", "B2"))
    }
    total = sum(value["che_count"] for value in counts.values())
    weights = [counts[band]["che_count"] / total for band in ("B1", "B2")]
    boot, index = bootstrap(
        arrays["che_continuation"],
        arrays["che_paired_fade"],
        arrays["chl_continuation"],
        che_band,
        chl_band,
        weights,
    )
    point = sum(
        weights[index]
        * (
            sum(arrays["che_continuation"][i] for i, row in enumerate(che_band) if row[index])
            / counts[band]["che_count"]
            - sum(arrays["chl_continuation"][i] for i, row in enumerate(chl_band) if row[index])
            / counts[band]["chl_count"]
        )
        for index, band in enumerate(("B1", "B2"))
    )
    cast(dict[str, object], boot["che_minus_chl_band_standardized_net_jpy_per_trade"])[
        "estimate"
    ] = point
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    write_json(OUT / "m_band_support.json", counts)
    main = cast(dict[str, int | float | None], reports["che_continuation"]["metrics"])
    main_ci = cast(
        list[float],
        cast(dict[str, object], boot["che_scheduled_axis_mean_net_jpy_per_trade_date"])[
            "ci95_percentile_linear"
        ],
    )
    fade_ci = cast(
        list[float],
        cast(dict[str, object], boot["che_minus_paired_fade_paired_daily_net_jpy"])[
            "ci95_percentile_linear"
        ],
    )
    chl_ci = cast(
        list[float],
        cast(dict[str, object], boot["che_minus_chl_band_standardized_net_jpy_per_trade"])[
            "ci95_percentile_linear"
        ],
    )
    gates = {
        "net_positive": cast(int, main["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(main["profit_factor"] and cast(float, main["profit_factor"]) > 1),
        "che_daily_mbb_ci95_lower_gt_zero": main_ci[0] > 0,
        "che_minus_paired_fade_ci95_lower_gt_zero": fade_ci[0] > 0,
        "che_minus_chl_band_standardized_ci95_lower_gt_zero": chl_ci[0] > 0,
    }
    detailed = cast(
        dict[str, dict[str, int | float | None]],
        reports["che_continuation"]["by_displacement_sign"],
    )
    bands = cast(dict[str, dict[str, int | float | None]], reports["che_continuation"]["by_m_band"])
    years = cast(dict[str, dict[str, int | float | None]], reports["che_continuation"]["by_year"])
    concentration_metrics = cast(
        dict[str, int | float | None], reports["che_continuation"]["profit_concentration"]
    )
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_fixed_sensitivity_net_positive": all(
            cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])
            > 0
            for name in profiles
            if name not in {"che_continuation", "che_paired_fade", "chl_continuation"}
        ),
        "both_displacement_sign_net_positive": all(
            cast(int, detailed[name]["net_pnl_jpy"]) > 0 for name in ("up", "down")
        ),
        "both_m_band_net_positive": all(
            cast(int, bands[name]["net_pnl_jpy"]) > 0 for name in ("B1", "B2")
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
            for trade in ledgers["che_continuation"]
        }
        == {
            (
                trade.trade_date,
                trade.entry_ts,
                trade.exit_ts,
                "short" if trade.side is Side.LONG else "long",
            )
            for trade in ledgers["che_paired_fade"]
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
        raise ValueError("R088-Q002 execution/accounting/causality audit failed")
    write_json(
        OUT / "profiles.json",
        reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}},
    )
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {
        "status": "INVESTIGATE" if all(gates.values()) else "REJECT",
        "decision_ceiling": "INVESTIGATE",
        "q001_audit": {"structural_dependency_only": parent_audit["structural_dependency_only"]},
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
