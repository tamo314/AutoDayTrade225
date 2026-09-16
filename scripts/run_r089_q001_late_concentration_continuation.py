"""Preregister then execute TASK-R089-Q001 without accessing protected periods."""

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
from n225m_bt.research.r089_late_concentration import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    bootstrap,
    build_events,
    causality_audit,
    feasibility,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r089-q001-20260915-late-concentration-continuation-04"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/75_r089_q001_late_concentration_continuation.md")


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
            by_m_band={
                band: ledger_metrics(
                    tuple(x for x in trades if by_date[x.trade_date].get("m_band") == band)
                )
                for band in ("B1", "B2")
            },
            by_f_quintile={
                str(q): ledger_metrics(
                    tuple(
                        x
                        for x in trades
                        if min(
                            5,
                            max(
                                1,
                                int(cast(float, by_date[x.trade_date]["f_late_concentration"]) * 5)
                                + 1,
                            ),
                        )
                        == q
                    )
                )
                for q in range(1, 6)
            },
        )
    return result


def support_audit(events: list[dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for band in ("B1", "B2"):
        groups = {
            name: [
                cast(float, x["m_bps"])
                for x in events
                if x.get("status") == "EXECUTABLE" and x.get("m_band") == band and bool(x.get(key))
            ]
            for name, key in (
                ("LC", "late_concentration_condition"),
                ("EC", "early_completion_condition"),
            )
        }
        if not groups["LC"] or not groups["EC"]:
            result[band] = {
                "lc_m_bps": groups["LC"],
                "ec_m_bps": groups["EC"],
                "intersection": None,
                "lc_in_intersection": 0,
                "ec_in_intersection": 0,
                "passed": False,
            }
            continue
        low, high = (
            max(min(groups["LC"]), min(groups["EC"])),
            min(max(groups["LC"]), max(groups["EC"])),
        )
        result[band] = {
            "lc_m_bps": groups["LC"],
            "ec_m_bps": groups["EC"],
            "intersection": [low, high],
            "lc_in_intersection": sum(low <= x <= high for x in groups["LC"]),
            "ec_in_intersection": sum(low <= x <= high for x in groups["EC"]),
            "passed": low <= high
            and sum(low <= x <= high for x in groups["LC"]) >= 10
            and sum(low <= x <= high for x in groups["EC"]) >= 10,
        }
    return {
        "bands": result,
        "passed": all(bool(cast(dict[str, object], item)["passed"]) for item in result.values()),
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r089_q001_late_concentration_continuation.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r089_q001_late_concentration_continuation.py"),
        Path("src/n225m_bt/research/r089_late_concentration.py"),
        Path("src/n225m_bt/strategies/r088_fixed_signal.py"),
        Path("scripts/run_r088_q001_cash_open_path_efficiency.py"),
        Path("tests/test_r089_q001.py"),
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
        "task_id": "TASK-R089-Q001",
        "family_id": "cash_open_late_concentration_order_flow_continuation",
        "study_id": "R089-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_ADDITIONAL_PNL",
        "preregistration_document": str(DOC),
        "preregistration_document_sha256": digest(ROOT / DOC),
        "prior_information_seen": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "p0=09:00 open, p45=09:44 close, p60=09:59 close; D=p60-p0; M=10000*abs(D)/p0; F=sign(D)*(p60-p45)/abs(D). Exact prior 160 scheduled dates, current excluded, 140 valid. M>=q60, B1=[q60,q80), B2=[q80,infinity); same-current-threshold-band prior F q30/q70 defines EC/LC.",
        "s2_gate": "unexplained=0; LC/EC>=90 each; LC up/down>=30 each; B1/B2 LC/EC>=25 each; LC 2022-2024>=15 each; 2025H1>=8; per-band M overlap plus >=10 each; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis JPY0; same LC event/entry/exit paired fade; EC continuation; B1/B2 LC standard weights.",
        "bootstrap": {
            "seed": 20260915,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-wrapping MBB; tail truncation; linear percentile",
        },
        "sensitivities": [
            "window_30",
            "window_90",
            "recent_10",
            "recent_20",
            "m_q50",
            "m_q70",
            "f_q60",
            "f_q80",
            "reference_120",
            "reference_200",
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
            "tests/test_r089_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r089_late_concentration.py"],
        "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files)],
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
            cast(dict[str, int], x)["returncode"] == 0
            for key, x in validation.items()
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
    s2, causal, support = feasibility(events), causality_audit(events, axis), support_audit(events)
    write_json(OUT / "primary_events.json", events)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_audit_before_pnl.json", causal)
    write_json(OUT / "m_distribution_support_before_pnl.json", support)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 PnL-free R089 availability, support and causality audit, then conditional S3",
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
        or not bool(support["passed"])
    ):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "R089_PNL_FREE_FEASIBILITY_SUPPORT_OR_CAUSALITY_GATE_FAILED",
            "s2_feasibility": s2,
            "causality_audit": causal,
            "m_distribution_support": support,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    profiles: dict[str, tuple[dict[str, int | time], int, int, str, str]] = {
        "lc_continuation": ({}, 1, 30, "HE", "continuation"),
        "lc_paired_fade": ({}, 1, 30, "HE", "fade"),
        "ec_continuation": ({}, 1, 30, "HL", "continuation"),
        "window_30": ({"window_minutes": 30}, 1, 30, "HE", "continuation"),
        "window_90": ({"window_minutes": 90}, 1, 30, "HE", "continuation"),
        "recent_10": ({"recent_minutes": 10}, 1, 30, "HE", "continuation"),
        "recent_20": ({"recent_minutes": 20}, 1, 30, "HE", "continuation"),
        "m_q50": ({"m_cutoff": 50}, 1, 30, "HE", "continuation"),
        "m_q70": ({"m_cutoff": 70}, 1, 30, "HE", "continuation"),
        "f_q60": ({"f_upper": 60}, 1, 30, "HE", "continuation"),
        "f_q80": ({"f_upper": 80}, 1, 30, "HE", "continuation"),
        "reference_120": ({"reference_window": 120}, 1, 30, "HE", "continuation"),
        "reference_200": ({"reference_window": 200}, 1, 30, "HE", "continuation"),
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
    base = {
        "lc_continuation",
        "lc_paired_fade",
        "ec_continuation",
        "cost_2tick",
        "cost_3tick",
        "fee_x2",
    }
    for name, (overrides, ticks, fee, group, direction) in profiles.items():
        profile_events = (
            events if name in base else build_events(classifier, axis, bars, isolated, **overrides)
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
        ledgers[name], daily[name] = trades, profile_daily
        reports[name] = profile_report(
            axis, trades, profile_daily, profile_events, detailed=name == "lc_continuation"
        )
        if unknown:
            unknown_profiles[name] = sorted(x.isoformat() for x in unknown)
        write_json(OUT / f"{name}_events.json", profile_events)
        write_json(OUT / f"{name}_orders_fills.json", orders_fills(trades))
        write_json(OUT / f"{name}_trades.json", [asdict(x) for x in trades])
        write_json(OUT / f"{name}_daily_axis.json", profile_daily)
    no_trade = {x.isoformat(): 0 for x in axis}
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
        for name in ("lc_continuation", "lc_paired_fade", "ec_continuation")
    }
    by_date = {date.fromisoformat(cast(str, x["trade_date"])): x for x in events}
    lc_band = [
        [
            int(
                bool(by_date[d].get("late_concentration_condition"))
                and by_date[d].get("status") == "EXECUTABLE"
                and by_date[d].get("m_band") == band
            )
            for band in ("B1", "B2")
        ]
        for d in axis
    ]
    ec_band = [
        [
            int(
                bool(by_date[d].get("early_completion_condition"))
                and by_date[d].get("status") == "EXECUTABLE"
                and by_date[d].get("m_band") == band
            )
            for band in ("B1", "B2")
        ]
        for d in axis
    ]
    counts = {
        band: {
            "lc_count": sum(row[i] for row in lc_band),
            "ec_count": sum(row[i] for row in ec_band),
        }
        for i, band in enumerate(("B1", "B2"))
    }
    total = sum(x["lc_count"] for x in counts.values())
    weights = [counts[b]["lc_count"] / total for b in ("B1", "B2")]
    boot, index = bootstrap(
        arrays["lc_continuation"],
        arrays["lc_paired_fade"],
        arrays["ec_continuation"],
        lc_band,
        ec_band,
        weights,
    )
    point = sum(
        weights[i]
        * (
            sum(arrays["lc_continuation"][j] for j, row in enumerate(lc_band) if row[i])
            / counts[band]["lc_count"]
            - sum(arrays["ec_continuation"][j] for j, row in enumerate(ec_band) if row[i])
            / counts[band]["ec_count"]
        )
        for i, band in enumerate(("B1", "B2"))
    )
    cast(dict[str, object], boot["lc_minus_ec_band_standardized_net_jpy_per_trade"])["estimate"] = (
        point
    )
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    write_json(OUT / "m_band_support.json", counts)
    main_metrics = cast(dict[str, int | float | None], reports["lc_continuation"]["metrics"])
    main_ci = cast(
        list[float],
        cast(dict[str, object], boot["lc_scheduled_axis_mean_net_jpy_per_trade_date"])[
            "ci95_percentile_linear"
        ],
    )
    fade_ci = cast(
        list[float],
        cast(dict[str, object], boot["lc_minus_paired_fade_paired_daily_net_jpy"])[
            "ci95_percentile_linear"
        ],
    )
    ec_ci = cast(
        list[float],
        cast(dict[str, object], boot["lc_minus_ec_band_standardized_net_jpy_per_trade"])[
            "ci95_percentile_linear"
        ],
    )
    gates = {
        "net_positive": cast(int, main_metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(
            main_metrics["profit_factor"] and cast(float, main_metrics["profit_factor"]) > 1
        ),
        "lc_daily_mbb_ci95_lower_gt_zero": main_ci[0] > 0,
        "lc_minus_paired_fade_ci95_lower_gt_zero": fade_ci[0] > 0,
        "lc_minus_ec_band_standardized_ci95_lower_gt_zero": ec_ci[0] > 0,
    }
    details = cast(
        dict[str, dict[str, int | float | None]], reports["lc_continuation"]["by_displacement_sign"]
    )
    bands = cast(dict[str, dict[str, int | float | None]], reports["lc_continuation"]["by_m_band"])
    years = cast(dict[str, dict[str, int | float | None]], reports["lc_continuation"]["by_year"])
    concentration_metrics = cast(
        dict[str, int | float | None], reports["lc_continuation"]["profit_concentration"]
    )
    sensitivity = [
        x for x in profiles if x not in {"lc_continuation", "lc_paired_fade", "ec_continuation"}
    ]
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_fixed_sensitivity_net_positive": all(
            cast(int, cast(dict[str, int | float | None], reports[x]["metrics"])["net_pnl_jpy"]) > 0
            for x in sensitivity
        ),
        "both_displacement_sign_net_positive": all(
            cast(int, details[x]["net_pnl_jpy"]) > 0 for x in ("up", "down")
        ),
        "both_m_band_net_positive": all(
            cast(int, bands[x]["net_pnl_jpy"]) > 0 for x in ("B1", "B2")
        ),
        "at_least_two_2022_2024_positive": sum(
            cast(int, years[str(y)]["net_pnl_jpy"]) > 0 for y in range(2022, 2025)
        )
        >= 2,
        "2025_h1_net_positive": cast(int, years["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(
            int, concentration_metrics["net_excluding_top10_jpy"]
        )
        > 0,
    }
    audit = {
        "causality_audit_before_pnl_passed": bool(causal["passed"]),
        "one_trade_per_trade_date": all(
            len({x.trade_date for x in trades}) == len(trades) for trades in ledgers.values()
        ),
        "paired_fade_same_event_entry_exit_opposite_side": {
            (x.trade_date, x.entry_ts, x.exit_ts, x.side.value) for x in ledgers["lc_continuation"]
        }
        == {
            (x.trade_date, x.entry_ts, x.exit_ts, "short" if x.side is Side.LONG else "long")
            for x in ledgers["lc_paired_fade"]
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
        raise ValueError("R089 execution/accounting/causality audit failed")
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
        "m_distribution_support": support,
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
