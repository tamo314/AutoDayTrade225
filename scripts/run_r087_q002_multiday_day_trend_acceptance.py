"""Execute the PnL-blind-gated Development-only TASK-R087-Q002 once."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np
from run_r087_q001_multiday_day_trend_acceptance import (
    assign_abs_t_quintiles,
    engine_for,
    execute_profile,
    order_fill_ledger,
    quarantine,
    report,
)

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.r087_multiday_day_trend_acceptance import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    bootstrap,
    build_events,
    causality_audit,
    feasibility,
    scheduled_axis,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r087-q002-20260915-multiday-day-trend-acceptance-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREGISTRATION_DOCUMENT = Path("docs/strategy/69_r087_q002_multiday_day_trend_acceptance.md")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


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


def q002_pre_pnl_audit(
    events: list[dict[str, object]], data: ResearchData, isolated: set[tuple[date, Session]]
) -> dict[str, object]:
    """Record only causal state availability; this function never accesses PnL."""
    quantiled = [row for row in events if "q30_abs_t_bps" in row and "q_upper_abs_t_bps" in row]
    if not quantiled:
        return {"passed": False, "reason": "NO_CAUSALLY_DETERMINED_Q30_Q70"}
    first_r = next((row for row in events if bool(row.get("day_return_valid"))), None)
    first_t = next((row for row in events if bool(row.get("t_valid"))), None)
    first_q = quantiled[0]
    evaluation_start = date.fromisoformat(cast(str, first_q["trade_date"]))
    pre_q = [row for row in events if date.fromisoformat(cast(str, row["trade_date"])) < evaluation_start]
    expected_warmup = {
        "INSUFFICIENT_PRIOR_SCHEDULED_DAYS_FOR_T",
        "T_REQUIRES_ALL_PRIOR_SCHEDULED_DAY_RETURNS_VALID_NO_BACKFILL",
        "INSUFFICIENT_VALID_T_IN_EXACT_PRIOR_SCHEDULED_REFERENCE_WINDOW",
    }
    invalid_r = [row for row in events if not bool(row.get("day_return_valid"))]
    invalid_r_dates = {date.fromisoformat(cast(str, row["trade_date"])) for row in invalid_r}
    isolated_day_dates = {target for target, session in isolated if session is Session.DAY}
    warmup_only = (
        all(str(row.get("reason")) in expected_warmup for row in pre_q)
        and all(target in isolated_day_dates for target in invalid_r_dates)
        and int(first_q["reference_valid_t_count"]) == 100
        and not any("q30_abs_t_bps" in row for row in pre_q)
    )
    by_condition = {
        condition: [
            row["trade_date"] for row in events
            if row.get("condition") == condition and row.get("status") == "EXECUTABLE"
        ]
        for condition in ("EA", "EO", "MA")
    }
    earliest_bar = min(data.bars, key=lambda row: row.ts_jst)
    return {
        "scope": "PnL-free audit only; no return, win/loss, PF, ranking, bootstrap, or performance statistic accessed.",
        "available_data_start": {"timestamp_jst": earliest_bar.ts_jst, "trade_date": earliest_bar.trade_date},
        "first_valid_r": None if first_r is None else {
            "trade_date": first_r["trade_date"], "r_bps": first_r["r_bps"],
            "open_jst": first_r["o_first_eligible_open_jst"], "close_jst": first_r["c_last_eligible_close_jst"],
        },
        "first_valid_t": None if first_t is None else {"trade_date": first_t["trade_date"], "t_bps": first_t["t_bps"]},
        "first_causally_determined_q30_q70": {
            "trade_date": first_q["trade_date"], "reference_valid_t_count": first_q["reference_valid_t_count"],
            "q30_abs_t_bps": first_q["q30_abs_t_bps"], "q70_abs_t_bps": first_q["q_upper_abs_t_bps"],
        },
        "fixed_evaluation_trade_date_range": [evaluation_start.isoformat(), DEVELOPMENT_END.isoformat()],
        "executable_trade_dates_by_condition": by_condition,
        "ea_2021_count": sum(target.startswith("2021-") for target in by_condition["EA"]),
        "ea_2021_zero_cause": {
            "cause": "No q30/q70 existed on any 2021 scheduled trade_date under the current-excluded exact-prior-120, minimum-100-valid-T rule; no-backfill preserved the causal warm-up.",
            "pre_q_reason_counts": dict(sorted(Counter(str(row.get("reason")) for row in pre_q).items())),
            "invalid_r_trade_dates": sorted(target.isoformat() for target in invalid_r_dates),
            "all_invalid_r_dates_are_predeclared_r004_day_quarantines": all(target in isolated_day_dates for target in invalid_r_dates),
        },
        "unexplained_exclusions": feasibility(events)["gate"]["unexplained_exclusion_trade_dates"],
        "causal_warmup_only": warmup_only,
        "passed": bool(first_r and first_t and warmup_only and not feasibility(events)["gate"]["unexplained_exclusion_trade_dates"]),
    }


def q002_feasibility(events: list[dict[str, object]], evaluation_start: date) -> dict[str, object]:
    rows = [row for row in events if date.fromisoformat(cast(str, row["trade_date"])) >= evaluation_start]
    executable = {
        condition: [row for row in rows if row.get("condition") == condition and row.get("status") == "EXECUTABLE"]
        for condition in ("EA", "EO", "MA")
    }
    ea = executable["EA"]
    signs = Counter("positive" if cast(float, row["t_bps"]) > 0 else "negative" for row in ea)
    years = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in ea)
    unexplained = feasibility(rows)["gate"]["unexplained_exclusion_trade_dates"]
    gate: dict[str, object] = {
        "unexplained_exclusions_equal_zero": not unexplained,
        "ea_at_least_120": len(ea) >= 120,
        "eo_at_least_90": len(executable["EO"]) >= 90,
        "ma_at_least_140": len(executable["MA"]) >= 140,
        "ea_t_sign_counts": dict(sorted(signs.items())),
        "ea_t_positive_negative_at_least_35_each": signs["positive"] >= 35 and signs["negative"] >= 35,
        "ea_by_year": {str(year): years[year] for year in range(2022, 2026)},
        "ea_2022_2024_at_least_15_each": all(years[year] >= 15 for year in range(2022, 2025)),
        "ea_2025_h1_at_least_8": years[2025] >= 8,
    }
    gate["passed"] = all(bool(value) for key, value in gate.items() if key not in {"ea_t_sign_counts", "ea_by_year"})
    return {
        "scope": "PnL-free Q002 availability on fixed causal evaluation axis only.",
        "evaluation_start_trade_date": evaluation_start.isoformat(),
        "executable_by_condition": {key: len(value) for key, value in executable.items()},
        "unexplained_exclusion_trade_dates": unexplained,
        "gate": gate,
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r087_q002_multiday_day_trend_acceptance.py"),
        Path("scripts/run_r087_q001_multiday_day_trend_acceptance.py"),
        Path("src/n225m_bt/research/r087_multiday_day_trend_acceptance.py"),
        Path("src/n225m_bt/strategies/r087_fixed_signal.py"), Path("tests/test_r087_q001.py"),
    ]
    config_files = [Path(f"config/{name}") for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    preregistration = {
        "task_id": "TASK-R087-Q002", "parent_study_id": "R087-Q001", "study_id": "R087-Q002", "spec_version": "v2", "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_PNL", "preregistration_document": str(PREREGISTRATION_DOCUMENT), "preregistration_document_sha256": digest(ROOT / PREREGISTRATION_DOCUMENT),
        "pnl_blind_change_basis": "Q001 PnL-free labels only: EA=129, EO=126, MA=147; no PnL or performance statistic accessed.",
        "unchanged_q001_rule_controls_costs_bootstrap_sensitivities_primary_and": True,
        "s2_replacement": "Causal evaluation starts at first q30/q70 date. unexplained=0; EA>=120; EO>=90; MA>=140; EA T +/- >=35 each; EA 2022-2024 >=15 each; 2025H1 >=8.",
        "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10_000, "method": "non-wrapping MBB; tail truncation; linear percentile; same block ratio estimator for EA-EO/MA"},
        "primary_gate": "EA Net>0; PF>1; EA daily MBB lower>0; EA-paired-fade paired daily lower>0; EA-EO and EA-MA group per-trade ratio-estimator lower>0.",
        "sensitivities": ["cumulative_5", "cumulative_20", "q60", "q80", "reference_80", "reference_160", "confirmation_5m", "confirmation_30m", "entry_extra_1_bar", "exit1430", "exit1510", "cost_2tick", "cost_3tick", "fee_x2"],
        "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(data_config.gold_root, "development")],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files}, "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE.", "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / PREREGISTRATION_DOCUMENT, OUT / "documentation_snapshot" / PREREGISTRATION_DOCUMENT.name)
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r087_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r087_multiday_day_trend_acceptance.py", "src/n225m_bt/strategies/r087_fixed_signal.py"],
        "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files[:-1])],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=environment)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("pre-execution validation failed")
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    full_axis = scheduled_axis(calendar)
    classifier = CalendarClassifier(sessions, calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    full_set = set(full_axis)
    for bar in view.bars:
        if bar.trade_date in full_set and bar.session is Session.DAY:
            bars[(bar.trade_date, bar.session)].append(bar)
    events = assign_abs_t_quintiles(build_events(classifier, full_axis, bars, isolated))
    causal = causality_audit(events, full_axis)
    pre_pnl = q002_pre_pnl_audit(events, development, isolated)
    evaluation_start = date.fromisoformat(cast(str, cast(dict[str, object], pre_pnl["first_causally_determined_q30_q70"])["trade_date"])) if pre_pnl.get("passed") else DEVELOPMENT_END
    s2 = q002_feasibility(events, evaluation_start)
    evaluation_axis = [target for target in full_axis if target >= evaluation_start]
    evaluation_events = [row for row in events if date.fromisoformat(cast(str, row["trade_date"])) >= evaluation_start]
    write_json(OUT / "primary_events_before_pnl.json", events)
    write_json(OUT / "q002_pre_pnl_audit.json", pre_pnl)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_audit_before_pnl.json", causal)
    write_json(OUT / "access_ledger.json", {"stage": "S2 PnL-free R087-Q002 audit, then conditional S3", "state_construction_trade_dates": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "evaluation_trade_dates": [evaluation_start.isoformat(), DEVELOPMENT_END.isoformat()], "physical_partitions": development.quality["partitions"], "data_version": development.data_version, "quarantine": quarantine_audit, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    if not bool(pre_pnl.get("passed")) or not bool(causal["passed"]) or not bool(cast(dict[str, object], s2["gate"])["passed"]):
        decision = {"status": "INCONCLUSIVE", "reason": "R087_Q002_PNL_FREE_AUDIT_OR_FEASIBILITY_GATE_FAILED", "pre_pnl_audit": pre_pnl, "s2_feasibility": s2, "causality_audit": causal, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    profiles: dict[str, tuple[dict[str, int | time], int, int, str, str]] = {
        "ea_continuation": ({}, 1, 30, "EA", "continuation"), "ea_paired_fade": ({}, 1, 30, "EA", "fade"), "eo_alignment_control": ({}, 1, 30, "EO", "continuation"), "ma_trend_strength_control": ({}, 1, 30, "MA", "continuation"),
        "cumulative_5": ({"cumulative_period": 5}, 1, 30, "EA", "continuation"), "cumulative_20": ({"cumulative_period": 20}, 1, 30, "EA", "continuation"), "q60": ({"upper_percentile": 60}, 1, 30, "EA", "continuation"), "q80": ({"upper_percentile": 80}, 1, 30, "EA", "continuation"),
        "reference_80": ({"reference_window": 80}, 1, 30, "EA", "continuation"), "reference_160": ({"reference_window": 160}, 1, 30, "EA", "continuation"), "confirmation_5m": ({"confirmation_minutes": 5}, 1, 30, "EA", "continuation"), "confirmation_30m": ({"confirmation_minutes": 30}, 1, 30, "EA", "continuation"),
        "entry_extra_1_bar": ({"entry_extra_bars": 1}, 1, 30, "EA", "continuation"), "exit1430": ({"exit_time": time(14, 30)}, 1, 30, "EA", "continuation"), "exit1510": ({"exit_time": time(15, 10)}, 1, 30, "EA", "continuation"), "cost_2tick": ({}, 2, 30, "EA", "continuation"), "cost_3tick": ({}, 3, 30, "EA", "continuation"), "fee_x2": ({}, 1, 60, "EA", "continuation"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int | None]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    base_names = {"ea_continuation", "ea_paired_fade", "eo_alignment_control", "ma_trend_strength_control", "cost_2tick", "cost_3tick", "fee_x2"}
    for name, (overrides, ticks, fee, condition, direction) in profiles.items():
        profile_events = evaluation_events if name in base_names else [row for row in build_events(classifier, full_axis, bars, isolated, **overrides) if date.fromisoformat(cast(str, row["trade_date"])) >= evaluation_start]
        trades, profile_daily, unknown = execute_profile(evaluation_axis, profile_events, bars, engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee), name=name, condition=condition, direction=direction)
        ledgers[name], daily[name] = trades, profile_daily
        reports[name] = report(evaluation_axis, trades, profile_daily, profile_events, detailed_ea=name == "ea_continuation")
        if unknown:
            unknown_profiles[name] = sorted(target.isoformat() for target in unknown)
        write_json(OUT / f"{name}_events.json", profile_events)
        write_json(OUT / f"{name}_orders_fills.json", order_fill_ledger(trades))
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", profile_daily)
    no_trade = {target.isoformat(): 0 for target in evaluation_axis}
    write_json(OUT / "no_trade_control_daily_axis.json", no_trade)
    if unknown_profiles:
        decision = {"status": "INCONCLUSIVE", "reason": "UNKNOWN_FILLED_EXIT", "unknown_profiles": unknown_profiles, "s2_feasibility": s2, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade}})
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    arrays = {name: [cast(int, value) for value in daily[name].values()] for name in ("ea_continuation", "ea_paired_fade", "eo_alignment_control", "ma_trend_strength_control")}
    counts = {condition: [int(row.get("condition") == condition and row.get("status") == "EXECUTABLE") for row in evaluation_events] for condition in ("EA", "EO", "MA")}
    boot, index = bootstrap(arrays["ea_continuation"], arrays["ea_paired_fade"], arrays["eo_alignment_control"], counts["EO"], arrays["ma_trend_strength_control"], counts["MA"], counts["EA"])
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    main_metrics = cast(dict[str, int | float | None], reports["ea_continuation"]["metrics"])
    main_ci = cast(list[float], cast(dict[str, object], boot["ea_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    fade_ci = cast(list[float], cast(dict[str, object], boot["ea_minus_paired_fade_paired_daily_net_jpy"])["ci95_percentile_linear"])
    eo_ci = cast(list[float], cast(dict[str, object], boot["ea_minus_eo_net_jpy_per_trade_ratio_estimator"])["ci95_percentile_linear"])
    ma_ci = cast(list[float], cast(dict[str, object], boot["ea_minus_ma_net_jpy_per_trade_ratio_estimator"])["ci95_percentile_linear"])
    gates = {"net_positive": cast(int, main_metrics["net_pnl_jpy"]) > 0, "pf_gt_one": bool(main_metrics["profit_factor"] and cast(float, main_metrics["profit_factor"]) > 1), "ea_daily_mbb_ci95_lower_gt_zero": main_ci[0] > 0, "ea_minus_paired_fade_ci95_lower_gt_zero": fade_ci[0] > 0, "ea_minus_eo_ratio_ci95_lower_gt_zero": eo_ci[0] > 0, "ea_minus_ma_ratio_ci95_lower_gt_zero": ma_ci[0] > 0}
    sensitivity_names = ["cumulative_5", "cumulative_20", "q60", "q80", "reference_80", "reference_160", "confirmation_5m", "confirmation_30m", "entry_extra_1_bar", "exit1430", "exit1510", "cost_2tick", "cost_3tick", "fee_x2"]
    t_metrics = cast(dict[str, dict[str, int | float | None]], reports["ea_continuation"]["by_t_sign"])
    year_metrics = cast(dict[str, dict[str, int | float | None]], reports["ea_continuation"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], reports["ea_continuation"]["profit_concentration"])
    follow_up_checks = {"primary_gate": all(gates.values()), "all_fixed_sensitivity_net_positive": all(cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"]) > 0 for name in sensitivity_names), "both_t_sign_net_positive": all(cast(int, t_metrics[sign]["net_pnl_jpy"]) > 0 for sign in ("positive", "negative")), "at_least_two_2022_2024_positive": sum(cast(int, year_metrics[str(year)]["net_pnl_jpy"]) > 0 for year in range(2022, 2025)) >= 2, "2025_h1_net_positive": cast(int, year_metrics["2025"]["net_pnl_jpy"]) > 0, "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0}
    audit = {"causality_audit_before_pnl_passed": bool(causal["passed"]), "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()), "ea_paired_fade_same_event_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["ea_continuation"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["ea_paired_fade"]}, "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades)}
    if not all(audit.values()):
        raise ValueError("R087-Q002 execution/accounting/causality audit failed")
    write_json(OUT / "profiles.json", reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}})
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {"status": "INVESTIGATE" if all(gates.values()) else "REJECT", "decision_ceiling": "INVESTIGATE", "s2_feasibility": s2, "s3_gates": gates, "follow_up_checks": follow_up_checks, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
