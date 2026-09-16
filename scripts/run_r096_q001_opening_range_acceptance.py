"""Preregister and execute TASK-R096-Q001 once, Development only."""

# mypy: disable-error-code=attr-defined
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
from n225m_bt.research.r088_cash_open_path_efficiency import scheduled_axis
from n225m_bt.research.r096_opening_range_acceptance import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    bootstrap,
    build_events,
    causality_audit,
    feasibility,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r096-q001-20260915-opening-range-acceptance-continuation-01"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/97_r096_q001_opening_range_acceptance_continuation.md")


def profile_report(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int | None],
    events: list[dict[str, object]],
    *,
    detailed: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [day for day, value in daily.items() if value is None],
        "trade_occurrence_rate_per_scheduled_trade_date": len(trades) / len(axis),
        "by_year": {
            str(year): ledger_metrics(tuple(x for x in trades if x.trade_date.year == year))
            for year in range(2021, 2026)
        },
        "profit_concentration": concentration(trades, axis),
    }
    if detailed:
        by_date = {date.fromisoformat(cast(str, x["trade_date"])): x for x in events}
        result.update(
            by_displacement_sign={
                name: ledger_metrics(
                    tuple(
                        x
                        for x in trades
                        if (cast(float, by_date[x.trade_date]["displacement_points"]) > 0)
                        == (name == "up")
                    )
                )
                for name in ("up", "down")
            },
            by_tse_close_regime={
                regime: ledger_metrics(
                    tuple(
                        x for x in trades if by_date[x.trade_date].get("tse_close_regime") == regime
                    )
                )
                for regime in ("old", "new")
            },
            by_x_band={
                band: ledger_metrics(
                    tuple(x for x in trades if by_date[x.trade_date].get("x_band") == band)
                )
                for band in ("X50_75", "X75_100")
            },
        )
    return result


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r096_q001_opening_range_acceptance.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r096_q001_opening_range_acceptance.py"),
        Path("src/n225m_bt/research/r096_opening_range_acceptance.py"),
        Path("src/n225m_bt/strategies/r088_fixed_signal.py"),
        Path("scripts/run_r088_q001_cash_open_path_efficiency.py"),
        Path("tests/test_r096_q001.py"),
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
        "task_id": "TASK-R096-Q001",
        "family_id": "cash_open_directional_range_end_acceptance_continuation",
        "study_id": "R096-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_ADDITIONAL_PNL",
        "preregistration_document": str(DOC),
        "preregistration_document_sha256": digest(ROOT / DOC),
        "prior_information_seen": True,
        "learned_from_prior_development_results": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "duplicate_review": "R001-R095 reviewed before PnL; R022/R042/R056/R079/R080/R088/R089 are not materially equivalent: none use both 60-minute directional endpoint range position z, exact prior-120 x/z thresholding, and T-minus-5 versioned cash exit.",
        "frozen_rule": "60 scheduled 09:00..09:59 bars: O=open09:00,C=close09:59,H=max(high),L=min(low),D=C-O,x=abs(D)/O,z=(C-L)/(H-L) for D>0 else (H-C)/(H-L). Exact current-excluded prior 120 scheduled TSE days, >=100 valid x/z, nearest-rank qx50/qz25/qz75 with qz25<qz75; E=x>=qx50,z>=qz75; K=x>=qx50,z<=qz25. Signal after 09:59, next eligible normal open <=10 minutes, fixed versioned TSE close minus 5 minute open exit.",
        "s2_gate": "unexplained=0; q-ready nonzero>=800; E/K complete>=100 each; E up/down>=35 each; E 2021>=8, 2022-2024>=18 each, 2025H1>=8; old/new close regimes>=85/8; each rolling x [50,75)/[75,100] band E/K>=20; unknown filled exit=0; else INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis JPY0 N; same E event/entry/exit opposite B; same E fixed-long C and fixed-short D; K direction M; equal-weight x-band standardized A-M per-trade difference.",
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-wrapping MBB; tail truncation; linear percentile; common blocks",
        },
        "sensitivities": [
            "z_q70_q30",
            "z_q80_q20",
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
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "decision_ceiling": "INVESTIGATE due to post-R088/R089 Development reuse; never CANDIDATE",
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
            "tests/test_r096_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r096_opening_range_acceptance.py",
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
            cast(dict[str, object], x)["returncode"] == 0
            for k, x in validation.items()
            if k != "status"
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
    profiles: dict[str, tuple[dict[str, int | time], int, int, str, str]] = {
        "a_continuation": ({}, 1, 30, "HE", "continuation"),
        "b_paired_fade": ({}, 1, 30, "HE", "fade"),
        "c_fixed_long": ({}, 1, 30, "HE", "long"),
        "d_fixed_short": ({}, 1, 30, "HE", "short"),
        "m_control": ({}, 1, 30, "HL", "continuation"),
        "z_q70_q30": ({"z_upper": 70}, 1, 30, "HE", "continuation"),
        "z_q80_q20": ({"z_upper": 80}, 1, 30, "HE", "continuation"),
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
    primary_events = build_events(classifier, axis, bars, isolated)
    s2, causal = feasibility(primary_events), causality_audit(primary_events, axis)
    profile_events = {
        name: primary_events
        if not overrides
        else build_events(classifier, axis, bars, isolated, **overrides)
        for name, (overrides, _, _, _, _) in profiles.items()
    }
    unknown = {
        name: sorted(
            cast(str, x["trade_date"])
            for x in events
            if x.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"
        )
        for name, events in profile_events.items()
    }
    unknown = {name: values for name, values in unknown.items() if values}
    write_json(OUT / "primary_events.json", primary_events)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_audit_before_pnl.json", causal)
    for name, events in profile_events.items():
        write_json(OUT / f"{name}_events.json", events)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 PnL-free duplicate-reviewed availability and causality audit, then conditional S3",
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
        not bool(cast(dict[str, object], s2["gate"])["passed"])
        or not bool(causal["passed"])
        or unknown
    ):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "R096_PNL_FREE_FEASIBILITY_CAUSALITY_OR_UNRESOLVED_EXIT_GATE_FAILED",
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
        write_json(OUT / f"{name}_trades.json", [asdict(x) for x in trades])
        write_json(OUT / f"{name}_daily_axis.json", pnl_daily)
    no_trade = {d.isoformat(): 0 for d in axis}
    by_date = {date.fromisoformat(cast(str, x["trade_date"])): x for x in primary_events}
    a_band = [
        [
            int(
                bool(by_date[d].get("accepted_condition"))
                and by_date[d].get("status") == "EXECUTABLE"
                and by_date[d].get("x_band") == band
            )
            for band in ("X50_75", "X75_100")
        ]
        for d in axis
    ]
    m_band = [
        [
            int(
                bool(by_date[d].get("control_condition"))
                and by_date[d].get("status") == "EXECUTABLE"
                and by_date[d].get("x_band") == band
            )
            for band in ("X50_75", "X75_100")
        ]
        for d in axis
    ]
    support = {
        band: {"a_count": sum(row[i] for row in a_band), "m_count": sum(row[i] for row in m_band)}
        for i, band in enumerate(("X50_75", "X75_100"))
    }
    total = sum(item["a_count"] for item in support.values())
    weights = [support[band]["a_count"] / total for band in ("X50_75", "X75_100")]
    arrays = {
        name: [cast(int, value) for value in daily[name].values()]
        for name in (
            "a_continuation",
            "b_paired_fade",
            "c_fixed_long",
            "d_fixed_short",
            "m_control",
        )
    }
    boot, index = bootstrap(
        arrays["a_continuation"],
        arrays["b_paired_fade"],
        arrays["c_fixed_long"],
        arrays["d_fixed_short"],
        arrays["m_control"],
        a_band,
        m_band,
        weights,
    )
    point = sum(
        weights[i]
        * (
            sum(arrays["a_continuation"][j] for j, row in enumerate(a_band) if row[i])
            / support[band]["a_count"]
            - sum(arrays["m_control"][j] for j, row in enumerate(m_band) if row[i])
            / support[band]["m_count"]
        )
        for i, band in enumerate(("X50_75", "X75_100"))
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
    sensitivity = [
        name
        for name in profiles
        if name
        not in {"a_continuation", "b_paired_fade", "c_fixed_long", "d_fixed_short", "m_control"}
    ]
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_fixed_sensitivity_net_positive": all(
            cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])
            > 0
            for name in sensitivity
        ),
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
            len({x.trade_date for x in trades}) == len(trades) for trades in ledgers.values()
        ),
        "paired_b_same_event_entry_exit_opposite_side": {
            (x.trade_date, x.entry_ts, x.exit_ts, x.side.value) for x in ledgers["a_continuation"]
        }
        == {
            (x.trade_date, x.entry_ts, x.exit_ts, "short" if x.side is Side.LONG else "long")
            for x in ledgers["b_paired_fade"]
        },
        "no_stop_or_target": all(
            x.exit_reason not in {ExitReason.STOP, ExitReason.TARGET}
            for trades in ledgers.values()
            for x in trades
        ),
        "net_equals_gross_minus_fees": all(
            x.net_pnl_jpy == x.gross_pnl_jpy - x.fees_jpy
            for trades in ledgers.values()
            for x in trades
        ),
    }
    if not all(audit.values()):
        raise ValueError("R096 execution/accounting audit failed")
    write_json(
        OUT / "profiles.json",
        reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}},
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
