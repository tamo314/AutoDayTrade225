"""Preregister, audit, and conditionally execute TASK-R090-Q001 once."""

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
from n225m_bt.research.r090_lunch_rejection import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    bootstrap,
    build_events,
    causality_audit,
    feasibility,
    support_audit,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r090-q001-20260915-lunch-rejection-fade-02"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/77_r090_q001_lunch_rejection_fade.md")


def report(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int | None],
    events: list[dict[str, object]],
    *,
    detailed: bool = False,
) -> dict[str, object]:
    by_date = {date.fromisoformat(cast(str, x["trade_date"])): x for x in events}
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
            by_j_quintile={
                str(q): ledger_metrics(
                    tuple(
                        x
                        for x in trades
                        if min(
                            5,
                            max(1, int(cast(float, by_date[x.trade_date]["j_rejection"]) * 5) + 1),
                        )
                        == q
                    )
                )
                for q in range(1, 6)
            },
        )
    return result


def _arrays(daily: dict[str, int | None]) -> list[int]:
    if any(value is None for value in daily.values()):
        raise ValueError("unknown outcome cannot enter bootstrap")
    return [cast(int, value) for value in daily.values()]


def _bands(axis: list[date], events: list[dict[str, object]], flag: str) -> list[list[int]]:
    by_date = {date.fromisoformat(cast(str, x["trade_date"])): x for x in events}
    return [
        [
            int(
                bool(by_date[d].get(flag))
                and by_date[d].get("status") == "EXECUTABLE"
                and by_date[d].get("m_band") == band
            )
            for band in ("B1", "B2")
        ]
        for d in axis
    ]


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r090_q001_lunch_rejection_fade.py"),
        Path("src/n225m_bt/research/r090_lunch_rejection.py"),
        Path("src/n225m_bt/strategies/r088_fixed_signal.py"),
        Path("scripts/run_r088_q001_cash_open_path_efficiency.py"),
        Path("tests/test_r090_q001.py"),
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
        "task_id": "TASK-R090-Q001",
        "family_id": "cash_lunch_reopen_rejection_price_discovery",
        "study_id": "R090-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_ADDITIONAL_PNL",
        "preregistration_document": str(DOC),
        "preregistration_document_sha256": digest(ROOT / DOC),
        "prior_information_seen": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "Lunch p0=11:30 open,p1=12:29 close,p2=12:39 close,D=p1-p0,M=10000*abs(D)/p0,J=-sign(D)*(p2-p1)/abs(D); strict-prior 160 scheduled dates/140 valid; M q60/q80 bands and same-band J q30/q70; LR/LA fade after p2 to 14:55. Independent morning placebo p0=10:00,p1=10:59,p2=11:09, PR fade 11:10 to 13:25.",
        "s2_gate": "unexplained=0; LR/LA/PR>=90; LR up/down>=30; B1/B2 LR/LA>=25; LR 2022-2024>=15 and 2025H1>=8; per-band LR/LA M overlap plus >=10 each; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis JPY0; same LR event/entry/exit sign(D) paired continuation; LA same-time fade; independent morning PR fade; LR standard M-band weights.",
        "bootstrap": {
            "seed": 20260915,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-wrapping MBB; tail truncation; linear percentile; common blocks",
        },
        "sensitivities": [
            "confirmation_5m",
            "confirmation_15m",
            "m_q50",
            "m_q70",
            "j_q60",
            "j_q80",
            "reference_120",
            "reference_200",
            "entry_extra_1_bar",
            "exit1430",
            "exit1510",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partition_paths(data_config.gold_root, "development")
        ],
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
            "tests/test_r090_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r090_lunch_rejection.py"],
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
            cast(dict[str, int], item)["returncode"] == 0
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
    primary, placebo = (
        build_events(classifier, axis, bars, isolated),
        build_events(classifier, axis, bars, isolated, interval="placebo"),
    )
    s2, causal, support = (
        feasibility(primary, placebo),
        causality_audit(primary, placebo, axis),
        support_audit(primary),
    )
    for name, value in (
        ("primary_events.json", primary),
        ("placebo_events.json", placebo),
        ("s2_feasibility.json", s2),
        ("causality_audit_before_pnl.json", causal),
        ("m_distribution_support_before_pnl.json", support),
    ):
        write_json(OUT / name, value)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 PnL-free R090 availability, support and causality audit, then conditional S3",
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
            "reason": "R090_PNL_FREE_FEASIBILITY_SUPPORT_OR_CAUSALITY_GATE_FAILED",
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
    profiles: dict[str, tuple[list[dict[str, object]], int, int, str, str]] = {
        "lr_fade": (primary, 1, 30, "HE", "fade"),
        "lr_paired_continuation": (primary, 1, 30, "HE", "continuation"),
        "la_fade": (primary, 1, 30, "HL", "fade"),
        "pr_fade": (placebo, 1, 30, "HE", "fade"),
    }
    variations: dict[str, dict[str, int | time | str]] = {
        "confirmation_5m": {"confirmation_minutes": 5},
        "confirmation_15m": {"confirmation_minutes": 15},
        "m_q50": {"m_cutoff": 50},
        "m_q70": {"m_cutoff": 70},
        "j_q60": {"j_upper": 60},
        "j_q80": {"j_upper": 80},
        "reference_120": {"reference_window": 120},
        "reference_200": {"reference_window": 200},
        "entry_extra_1_bar": {"entry_extra_bars": 1},
        "exit1430": {"exit_time": time(14, 30)},
        "exit1510": {"exit_time": time(15, 10)},
    }
    for name, override in variations.items():
        primary_override = {
            key: value for key, value in override.items() if key != "confirmation_minutes"
        }
        placebo_override = dict(primary_override)
        confirmation = cast(int, override.get("confirmation_minutes", 10))
        if "confirmation_minutes" in override:
            primary_override["confirmation_minutes"] = confirmation
            placebo_override["confirmation_minutes"] = confirmation
        if name == "exit1430":
            placebo_override["exit_time"] = time(13, 0)
        if name == "exit1510":
            placebo_override["exit_time"] = time(13, 40)
        pe, pp = (
            build_events(classifier, axis, bars, isolated, **primary_override),
            build_events(classifier, axis, bars, isolated, interval="placebo", **placebo_override),
        )
        profiles[name] = (pe, 1, 30, "HE", "fade")
        profiles[f"{name}_placebo_time_matched"] = (pp, 1, 30, "HE", "fade")
    profiles.update(
        {
            "cost_2tick": (primary, 2, 30, "HE", "fade"),
            "cost_3tick": (primary, 3, 30, "HE", "fade"),
            "fee_x2": (primary, 1, 60, "HE", "fade"),
        }
    )
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int | None]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (events, ticks, fee, group, direction) in profiles.items():
        trades, aligned, unknown = execute(
            axis,
            events,
            bars,
            engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee),
            profile=name,
            group=group,
            direction=direction,
        )
        ledgers[name], daily[name] = trades, aligned
        reports[name] = report(axis, trades, aligned, events, detailed=name == "lr_fade")
        if unknown:
            unknown_profiles[name] = sorted(day.isoformat() for day in unknown)
        for suffix, value in (
            ("events", events),
            ("orders_fills", orders_fills(trades)),
            ("trades", [asdict(x) for x in trades]),
            ("daily_axis", aligned),
        ):
            write_json(OUT / f"{name}_{suffix}.json", value)
    no_trade = {d.isoformat(): 0 for d in axis}
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
    lr_band, la_band, pr_band = (
        _bands(axis, primary, "lr_condition"),
        _bands(axis, primary, "la_condition"),
        _bands(axis, placebo, "lr_condition"),
    )
    counts = {
        band: {
            "lr_count": sum(row[i] for row in lr_band),
            "la_count": sum(row[i] for row in la_band),
            "pr_count": sum(row[i] for row in pr_band),
        }
        for i, band in enumerate(("B1", "B2"))
    }
    weights = [
        counts[band]["lr_count"] / sum(x["lr_count"] for x in counts.values())
        for band in ("B1", "B2")
    ]
    boot, index = bootstrap(
        _arrays(daily["lr_fade"]),
        _arrays(daily["lr_paired_continuation"]),
        _arrays(daily["la_fade"]),
        _arrays(daily["pr_fade"]),
        lr_band,
        la_band,
        pr_band,
        weights,
    )
    for key, other, other_band in (
        ("lr_minus_la_band_standardized_net_jpy_per_trade", _arrays(daily["la_fade"]), la_band),
        ("lr_minus_pr_band_standardized_net_jpy_per_trade", _arrays(daily["pr_fade"]), pr_band),
    ):
        estimate = sum(
            weights[i]
            * (
                sum(_arrays(daily["lr_fade"])[j] for j, row in enumerate(lr_band) if row[i])
                / counts[band]["lr_count"]
                - sum(other[j] for j, row in enumerate(other_band) if row[i])
                / counts[band][
                    "la_count"
                    if key.endswith("la_band_standardized_net_jpy_per_trade")
                    else "pr_count"
                ]
            )
            for i, band in enumerate(("B1", "B2"))
        )
        cast(dict[str, object], boot[key])["estimate"] = estimate
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    write_json(OUT / "m_band_support.json", counts)
    main_metrics = cast(dict[str, int | float | None], reports["lr_fade"]["metrics"])
    gates = {
        "net_positive": cast(int, main_metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(
            main_metrics["profit_factor"] and cast(float, main_metrics["profit_factor"]) > 1
        ),
        **{
            f"{key}_ci95_lower_gt_zero": cast(
                list[float], cast(dict[str, object], boot[key])["ci95_percentile_linear"]
            )[0]
            > 0
            for key in (
                "lr_scheduled_axis_mean_net_jpy_per_trade_date",
                "lr_minus_paired_continuation_paired_daily_net_jpy",
                "lr_minus_la_band_standardized_net_jpy_per_trade",
                "lr_minus_pr_band_standardized_net_jpy_per_trade",
            )
        },
    }
    detail = cast(dict[str, dict[str, int | float | None]], reports["lr_fade"])
    sensitivity = [name for name in variations] + ["cost_2tick", "cost_3tick", "fee_x2"]
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_fixed_sensitivity_net_positive": all(
            cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])
            > 0
            for name in sensitivity
        ),
        "both_displacement_sign_net_positive": all(
            cast(int, detail["by_displacement_sign"][x]["net_pnl_jpy"]) > 0 for x in ("up", "down")
        ),
        "both_m_band_net_positive": all(
            cast(int, detail["by_m_band"][x]["net_pnl_jpy"]) > 0 for x in ("B1", "B2")
        ),
        "at_least_two_2022_2024_positive": sum(
            cast(int, detail["by_year"][str(y)]["net_pnl_jpy"]) > 0 for y in range(2022, 2025)
        )
        >= 2,
        "2025_h1_net_positive": cast(int, detail["by_year"]["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(
            int, detail["profit_concentration"]["net_excluding_top10_jpy"]
        )
        > 0,
    }
    audit = {
        "causality_audit_before_pnl_passed": bool(causal["passed"]),
        "one_trade_per_trade_date": all(
            len({x.trade_date for x in trades}) == len(trades) for trades in ledgers.values()
        ),
        "paired_continuation_same_lr_event_entry_exit_opposite_side": {
            (x.trade_date, x.entry_ts, x.exit_ts, x.side.value) for x in ledgers["lr_fade"]
        }
        == {
            (x.trade_date, x.entry_ts, x.exit_ts, "short" if x.side is Side.LONG else "long")
            for x in ledgers["lr_paired_continuation"]
        },
        "primary_times": all(
            x.entry_signal_ts.time() == time(12, 39)
            and x.entry_ts.time() == time(12, 40)
            and x.exit_ts.time() == time(14, 55)
            for x in ledgers["lr_fade"]
        ),
        "placebo_hold_time_match": all(
            x.entry_signal_ts.time() == time(11, 9)
            and x.entry_ts.time() == time(11, 10)
            and x.exit_ts.time() == time(13, 25)
            for x in ledgers["pr_fade"]
        ),
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
        raise ValueError("R090 execution/accounting/causality audit failed")
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
