"""Execute TASK-R093-Q002 after its PnL-free warm-up-audit reproduction gate."""

# mypy: disable-error-code="arg-type,assignment,index,misc,no-untyped-call,type-arg"
# ruff: noqa: E701, E702, E731

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
from n225m_bt.research.r093_cash_night_reversal import (
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
    raise ImportError("cannot load R093-Q001 runner helpers")
q001 = module_from_spec(_Q001_SPEC)
_Q001_SPEC.loader.exec_module(q001)
RUN_ID = "task-r093-q002-tse-cash-night-reversal-warmup-audit-20260915-02"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = Path("docs/strategy/89_r093_q002_tse_cash_night_reversal_warmup_audit_repair.md")
Q001_OUT = ROOT / "results" / "research" / "task-r093-q001-tse-cash-night-reversal-20260915-01"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _cancel_reasons(events: list[dict[str, object]]) -> dict[str, int]:
    return dict(sorted(Counter(cast(str, row["reason"]) for row in events if row.get("status") == "ENTRY_CANCELLED").items()))


def _reproduction_gate(
    events: list[dict[str, object]], feasibility: dict[str, object], audit: dict[str, object]
) -> dict[str, object]:
    prior = cast(list[dict[str, object]], _load_json(Q001_OUT / "primary_events_pnl_free.json"))
    expected = {
        "scheduled_axis_trade_dates": 1099,
        "q_ready_nonzero_r_days": 957,
        "E_orders": 253,
        "complete_primary_paths": 246,
        "complete_by_r_sign": {"positive": 126, "negative": 120},
        "complete_by_year": {"2021": 27, "2022": 62, "2023": 69, "2024": 65, "2025": 23},
        "old_regime_complete": 215,
        "new_regime_complete": 31,
        "unexplained_exclusions": [],
        "unresolved_filled_positions": 0,
    }
    actual = {key: feasibility[key] for key in expected}
    cancellations = _cancel_reasons(events)
    expected_cancellations = {
        "FIRST_NORMAL_NIGHT_ENTRY_OPEN_MISSING_OR_INELIGIBLE": 6,
        "R004_FOLLOWING_NIGHT_SESSION_QUARANTINED": 1,
    }
    checks = {
        "q001_primary_pnl_free_ledger_exact_value_match": events == prior,
        "required_pnl_free_counts_exact": actual == expected,
        "seven_entry_cancellations_with_frozen_reasons": cancellations == expected_cancellations,
        "causality_audit_passed": bool(audit["passed"]),
    }
    return {"checks": checks, "actual": actual, "cancellations": cancellations, "passed": all(checks.values())}


def _trade(event: dict[str, object], bars: dict[tuple[date, Session], list[Bar]], instrument: Any, *, direction: str, ticks: int, fee: int, profile: str) -> Trade:
    night_day = date.fromisoformat(cast(str, event["night_trade_date"]))
    entry_stamp = datetime.fromisoformat(cast(str, event["entry_open_jst"]))
    exit_stamp = datetime.fromisoformat(cast(str, event["exit_open_jst"]))
    lookup = {bar.ts_jst: bar for bar in bars[(night_day, Session.NIGHT)]}
    entry, exit_bar = lookup[entry_stamp], lookup[exit_stamp]
    side, spec = Side(direction), instrument.instrument.to_spec()
    entry_fill = adverse_fill(entry.open, side is Side.LONG, ticks, spec)
    exit_fill = adverse_fill(exit_bar.open, side is Side.SHORT, ticks, spec)
    excursion = [bar for bar in bars[(night_day, Session.NIGHT)] if entry_stamp <= bar.ts_jst <= exit_stamp and bar.is_eligible]
    mae = max((entry_fill - bar.low if side is Side.LONG else bar.high - entry_fill) for bar in excursion)
    mfe = max((bar.high - entry_fill if side is Side.LONG else entry_fill - bar.low) for bar in excursion)
    gross = gross_pnl(entry_fill, exit_fill, side, 1, spec)
    return Trade(
        trade_id=f"r093-q002-{profile}-{event['trade_date']}", trade_date=night_day, side=side, qty=1,
        entry_signal_ts=datetime.fromisoformat(cast(str, event["submit_jst"])), entry_ts=entry_stamp,
        entry_reference_price=entry.open, entry_fill_price=entry_fill,
        exit_signal_ts=datetime.fromisoformat(cast(str, event["exit_signal_jst"])), exit_ts=exit_stamp,
        exit_reference_price=exit_bar.open, exit_fill_price=exit_fill, gross_pnl_jpy=gross, fees_jpy=2 * fee,
        slippage_cost_jpy=slippage_cost(entry.open, entry_fill, exit_bar.open, exit_fill, 1, spec),
        net_pnl_jpy=gross - 2 * fee, mae_jpy=mae * spec.multiplier, mfe_jpy=mfe * spec.multiplier,
        holding_minutes=int((exit_stamp - entry_stamp).total_seconds() // 60), entry_reason="r093_cash_close_scheduled_order",
        exit_reason=ExitReason.SIGNAL, strategy_id=f"r093_q002_{profile}", strategy_version="r093-q002-v1",
        parameter_hash=canonical_hash({"profile": profile, "event": event, "direction": direction, "ticks": ticks, "fee": fee}),
        metadata={"entry_session": Session.NIGHT.value, "cash_signal_trade_date": event["trade_date"], "night_schedule_version": event["night_schedule_version"]},
    )


def _execute(axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Bar]], instrument: Any, *, profile: str, direction: str, ticks: int, fee: int) -> tuple[tuple[Trade, ...], dict[str, int | None]]:
    trades = {date.fromisoformat(cast(str, row["trade_date"])): _trade(row, bars, instrument, direction=cast(str, row[f"{direction}_direction"]) if direction in {"reversal", "continuation"} else direction, ticks=ticks, fee=fee, profile=profile) for row in events if row.get("status") == "EXECUTABLE"}
    daily: dict[str, int | None] = {day.isoformat(): 0 for day in axis}
    for cash_day, trade in trades.items():
        daily[cash_day.isoformat()] = trade.net_pnl_jpy
    return tuple(trades[day] for day in axis if day in trades), daily


def _report(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], events: list[dict[str, object]]) -> dict[str, object]:
    by_cash = {cast(str, row["trade_date"]): row for row in events}
    event_for = lambda trade: by_cash[cast(str, trade.metadata["cash_signal_trade_date"])]
    return {
        "metrics": ledger_metrics(trades), "scheduled_axis_observations": len(axis), "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [],
        "by_year": {str(year): ledger_metrics(tuple(trade for trade in trades if date.fromisoformat(cast(str, event_for(trade)["trade_date"])).year == year)) for year in range(2021, 2026)},
        "by_r_sign": {name: ledger_metrics(tuple(trade for trade in trades if cast(int, cast(dict[str, object], event_for(trade)["observation"])["r_sign"]) == sign)) for name, sign in (("positive", 1), ("negative", -1))},
        "by_night_regime": {version: ledger_metrics(tuple(trade for trade in trades if event_for(trade)["night_schedule_version"] == version)) for version in sorted({cast(str, row["night_schedule_version"]) for row in events if row.get("status") == "EXECUTABLE"})},
        "profit_concentration": concentration(trades, axis),
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_task_r093_q002_tse_cash_night_reversal_warmup_audit.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [Path("scripts/run_task_r093_q002_tse_cash_night_reversal_warmup_audit.py"), Path("scripts/run_task_r093_q001_tse_cash_night_reversal.py"), Path("src/n225m_bt/research/r093_cash_night_reversal.py"), Path("tests/test_r093_q001.py"), Path("tests/test_r093_q002_warmup_audit.py")]
    config_files = [Path(f"config/{item}") for item in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    preregistration = {"task_id": "TASK-R093-Q002", "family_id": "tse_cash_night_reversal", "study_id": "R093-Q002", "spec_version": "v1-warmup-audit-repair", "run_id": RUN_ID, "protocol_revision": "RG-20260915-01", "preregistration_document": str(PREREG), "preregistration_document_sha256": q001.digest(ROOT / PREREG), "parent_q001": {"run_id": "task-r093-q001-tse-cash-night-reversal-20260915-01", "economic_results_accessed": False, "status": "INCONCLUSIVE_BEFORE_PNL"}, "change": "causality audit warm-up predicate only", "decision_ceiling": "INVESTIGATE", "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "profiles": ["A_reversal", "B_continuation", "C_fixed_long", "D_fixed_short", "A_q70", "A_q80", "A_entry_delay_1m", "A_exit_30m_early", "A_cost_2tick", "A_cost_3tick", "A_fee_2x"], "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10000, "method": "non-circular MBB; common index; tail truncation; linear percentile"}, "input_partitions": [{"path": str(item.resolve().relative_to(ROOT)), "sha256": q001.digest(item)} for item in partition_paths(data_config.gold_root, "development")], "implementation_hashes": {str(item): q001.digest(ROOT / item) for item in source_files}, "config_hashes": {str(item): q001.digest(ROOT / item) for item in config_files}, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    q001.snapshot(OUT / "source_snapshot", source_files)
    q001.snapshot(OUT / "config_snapshot", config_files)
    q001.snapshot(OUT / "documentation_snapshot", [PREREG])
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {"pytest": [sys.executable, "-m", "pytest", "tests/test_r093_q001.py", "tests/test_r093_q002_warmup_audit.py", "tests/test_execution.py", "-q"], "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)], "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r093_cash_night_reversal.py", str(source_files[0])], "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files)]}
    validation: dict[str, object] = {}
    for name, command in commands.items():
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env)
        validation[name] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    validation["status"] = "PASS" if all(cast(dict[str, object], item)["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("R093-Q002 pre-execution validation failed")
    instrument, sessions, data_config, _ = load_project_config(ROOT / "config")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml"))
    development = load_split(data_config.gold_root, "development")
    view, isolated, r004 = q001.quarantine(development)
    axis = q001.tse_axis(q001.cash_calendar())
    bars: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in set(axis):
            bars[(bar.trade_date, bar.session)].append(bar)
    primary = build_events(classifier, axis, bars, isolated)
    feasibility = q001.feasibility(primary)
    audit_checks = scheduled_reference_audit(primary, axis)
    audit = {"checks": audit_checks, "passed": all(audit_checks.values()), "pnl_not_accessed_before_audit": True}
    reproduction = _reproduction_gate(primary, feasibility, audit)
    write_json(OUT / "access_ledger.json", {"stage": "S2 then conditional S3", "physical_io": "Development normalized Parquet only", "data_version": development.data_version, "partitions": development.quality["partitions"], "r004": r004, "q001_economic_artifacts_read": False, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "scheduled_axis.json", {"trade_dates": [day.isoformat() for day in axis], "source": "frozen Cabinet Office TSE business-day evidence", "holiday_evidence_sha256": q001.digest(q001.HOLIDAYS)})
    write_json(OUT / "primary_events_pnl_free.json", primary)
    write_json(OUT / "s2_feasibility.json", feasibility)
    write_json(OUT / "causality_audit_before_pnl.json", audit)
    write_json(OUT / "q001_pnl_free_ledger_reproduction.json", reproduction)
    if not bool(reproduction["passed"]):
        decision = {"decision": "INCONCLUSIVE", "reason": "Q001_LEDGER_REPRODUCTION_OR_CAUSALITY_AUDIT_FAILED_BEFORE_PNL", "reproduction": reproduction, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision); write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision}); return
    profiles = {"A_reversal": (primary, "reversal", 1, 30), "B_continuation": (primary, "continuation", 1, 30), "C_fixed_long": (primary, "long", 1, 30), "D_fixed_short": (primary, "short", 1, 30), "A_q70": (build_events(classifier, axis, bars, isolated, percentile=70), "reversal", 1, 30), "A_q80": (build_events(classifier, axis, bars, isolated, percentile=80), "reversal", 1, 30), "A_entry_delay_1m": (build_events(classifier, axis, bars, isolated, entry_delay_minutes=1), "reversal", 1, 30), "A_exit_30m_early": (build_events(classifier, axis, bars, isolated, exit_early_minutes=30), "reversal", 1, 30), "A_cost_2tick": (primary, "reversal", 2, 30), "A_cost_3tick": (primary, "reversal", 3, 30), "A_fee_2x": (primary, "reversal", 1, 60)}
    unresolved = {name: [cast(str, row["trade_date"]) for row in events if row.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"] for name, (events, _, _, _) in profiles.items()}
    unresolved = {name: days for name, days in unresolved.items() if days}
    if unresolved:
        decision = {"decision": "INCONCLUSIVE", "reason": "ENTRY_FILLED_EXIT_UNRESOLVED_AFTER_S2", "unknown_profiles": unresolved, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision); write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision}); return
    reports: dict[str, dict[str, object]] = {}; trades_by: dict[str, tuple[Trade, ...]] = {}
    for name, (events, direction, ticks, fee) in profiles.items():
        trades, daily = _execute(axis, events, bars, instrument, profile=name, direction=direction, ticks=ticks, fee=fee)
        trades_by[name] = trades; reports[name] = _report(axis, trades, daily, events)
        write_json(OUT / f"{name}_events.json", events); write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades]); write_json(OUT / f"{name}_daily_axis.json", daily)
    a, b, c, d = (list(cast(dict[str, int], reports[name]["daily_net_pnl_jpy"]).values()) for name in ("A_reversal", "B_continuation", "C_fixed_long", "D_fixed_short"))
    boot, index = bootstrap(a, b, c, d); np.save(OUT / "bootstrap_common_day_indices.npy", index)
    metrics = cast(dict[str, int | float | None], reports["A_reversal"]["metrics"]); lower = lambda key: cast(list[float], cast(dict[str, object], boot[key])["ci95_percentile_linear"])[0]
    main = {"A_net_positive": cast(int, metrics["net_pnl_jpy"]) > 0, "A_pf_gt_one": metrics["profit_factor"] is not None and cast(float, metrics["profit_factor"]) > 1, "A_daily_net_ci_lower_positive": lower("A_reversal_daily_net_jpy_per_trade_date") > 0, "A_minus_B_ci_lower_positive": lower("A_minus_B_daily_net_jpy_per_trade_date") > 0, "A_minus_C_ci_lower_positive": lower("A_minus_C_daily_net_jpy_per_trade_date") > 0, "A_minus_D_ci_lower_positive": lower("A_minus_D_daily_net_jpy_per_trade_date") > 0}
    year = cast(dict[str, dict[str, int | float | None]], reports["A_reversal"]["by_year"]); sign = cast(dict[str, dict[str, int | float | None]], reports["A_reversal"]["by_r_sign"]); regime = cast(dict[str, dict[str, int | float | None]], reports["A_reversal"]["by_night_regime"]); concentration_a = cast(dict[str, int | float | None], reports["A_reversal"]["profit_concentration"])
    net = lambda name: cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])
    robustness = {"q70_net_positive": net("A_q70") > 0, "q80_net_positive": net("A_q80") > 0, "entry_delay_1m_net_positive": net("A_entry_delay_1m") > 0, "exit_30m_early_net_positive": net("A_exit_30m_early") > 0, "two_tick_net_positive": net("A_cost_2tick") > 0, "fee_2x_net_positive": net("A_fee_2x") > 0, "positive_r_net_positive": cast(int, sign["positive"]["net_pnl_jpy"]) > 0, "negative_r_net_positive": cast(int, sign["negative"]["net_pnl_jpy"]) > 0, "both_ose_regimes_net_positive": bool(regime) and all(cast(int, item["net_pnl_jpy"]) > 0 for item in regime.values()), "at_least_two_2022_2024_positive": sum(cast(int, year[str(item)]["net_pnl_jpy"]) > 0 for item in range(2022, 2025)) >= 2, "2025_h1_net_positive": cast(int, year["2025"]["net_pnl_jpy"]) > 0, "top10_winners_removed_net_positive": cast(int, concentration_a["net_excluding_top10_jpy"]) > 0, "three_tick_diagnostic_net_jpy": net("A_cost_3tick")}
    execution = {"A_B_C_D_same_E_entry_exit": all(tuple((row["trade_date"], row.get("entry_open_jst"), row.get("exit_open_jst")) for row in primary if row.get("status") == "EXECUTABLE") == tuple((row["trade_date"], row.get("entry_open_jst"), row.get("exit_open_jst")) for row in profiles[name][0] if row.get("status") == "EXECUTABLE") for name in ("B_continuation", "C_fixed_long", "D_fixed_short")), "A_B_opposite_side": all(left.side is not right.side for left, right in zip(trades_by["A_reversal"], trades_by["B_continuation"], strict=True)), "one_trade_one_position_per_cash_day": all(len(trades) == len({trade.metadata["cash_signal_trade_date"] for trade in trades}) for trades in trades_by.values()), "no_stop_or_target": all(trade.exit_reason is ExitReason.SIGNAL for trades in trades_by.values() for trade in trades), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades), "slippage_not_double_deducted": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades)}
    if not all(execution.values()): raise ValueError("R093-Q002 execution/accounting audit failed")
    decision = "REJECT" if not all(main.values()) else "INVESTIGATE"
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"}); write_json(OUT / "profiles.json", reports | {"N_no_trade": {"net_pnl_jpy": 0, "daily_net_pnl_jpy": {day.isoformat(): 0 for day in axis}}}); write_json(OUT / "execution_accounting_audit.json", execution); write_json(OUT / "s3_results.json", {"primary_and": main, "robustness": robustness, "three_tick_diagnostic_net_jpy": net("A_cost_3tick")}); write_json(OUT / "decision.json", {"decision": decision, "decision_ceiling": "INVESTIGATE", "primary_and": main, "robustness": robustness, "s2_gate": feasibility["gate"], "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}); write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "decision": decision, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__": main()
