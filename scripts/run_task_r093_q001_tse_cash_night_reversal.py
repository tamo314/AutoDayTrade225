"""Run the frozen Development-only TASK-R093-Q001 experiment exactly once."""

# mypy: disable-error-code="arg-type,assignment,index,misc,no-untyped-call,type-arg"

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
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
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r093_cash_night_reversal import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    bootstrap,
    build_events,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r093-q001-tse-cash-night-reversal-20260915-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = Path("docs/strategy/87_r093_q001_tse_cash_night_reversal.md")
HOLIDAYS = ROOT / "results/research/r045-q001-20260914-tse-lunch-placebo-reversal-03/institutional_evidence/cabinet_office_public_holidays.csv"
R004_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for source in files:
        shutil.copy2(ROOT / source, destination / source.name)


def cash_calendar() -> TSECashMarketCalendar:
    if not HOLIDAYS.exists():
        raise FileNotFoundError(f"frozen TSE holiday evidence unavailable: {HOLIDAYS}")
    return TSECashMarketCalendar.from_cabinet_office_csv(HOLIDAYS.read_text(encoding="cp932"))


def tse_axis(calendar: TSECashMarketCalendar) -> list[date]:
    current, values = DEVELOPMENT_START, []
    while current <= DEVELOPMENT_END:
        if calendar.is_open(current):
            values.append(current)
        current = current.fromordinal(current.toordinal() + 1)
    return values


def quarantine(data: ResearchData) -> tuple[ResearchData, set[tuple[date, Session]], dict[str, object]]:
    groups = session_groups(data.bars)
    isolated = {key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [bar for key, rows in groups.items() if key not in isolated for bar in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit = {
        "rule": "R004 isolate whole (trade_date, session) having TICK_GRID_VIOLATION",
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
    }
    if audit["quarantined_session_list_hash"] != R004_HASH:
        raise ValueError(f"R093 R004 isolation mismatch: {audit}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), isolated, audit


def feasibility(events: list[dict[str, object]]) -> dict[str, object]:
    complete = [row for row in events if row.get("status") == "EXECUTABLE"]
    orders = [row for row in events if bool(row.get("entry_order_submitted"))]
    unresolved = [row for row in events if row.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"]
    qready_nonzero = [
        row for row in events if "q75" in row and cast(int, cast(dict[str, object], row["observation"])["r_sign"]) != 0
    ]
    signs = Counter(cast(int, cast(dict[str, object], row["observation"])["r_sign"]) for row in complete)
    years = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in complete)
    regimes = Counter(cast(str, row.get("night_schedule_version")) for row in complete)
    old = sum(count for version, count in regimes.items() if "from_20241105" not in version)
    new = sum(count for version, count in regimes.items() if "from_20241105" in version)
    known_prefixes = (
        "VALID_", "HISTORY_", "R004_", "TSE_", "INSUFFICIENT_", "ZERO_", "X_BELOW_",
        "EXTREME_", "SCHEDULED_", "FOLLOWING_", "NONRECIPROCAL_", "FIRST_", "FINAL_",
        "ENTRY_NOT_", "night ",
    )
    unexplained = [row["trade_date"] for row in events if not str(row.get("reason", "")).startswith(known_prefixes)]
    share = len(orders) / len(qready_nonzero) if qready_nonzero else None
    gate = {
        "unexplained_exclusions_equal_zero": not unexplained,
        "unresolved_filled_positions_equal_zero": not unresolved,
        "q_ready_nonzero_r_days_at_least_800": len(qready_nonzero) >= 800,
        "E_order_share_qready_nonzero_r_20_to_30_percent": share is not None and 0.20 <= share <= 0.30,
        "A_B_C_D_complete_trades_at_least_200": len(complete) >= 200,
        "positive_r_complete_trades_at_least_80": signs[1] >= 80,
        "negative_r_complete_trades_at_least_80": signs[-1] >= 80,
        "2021_initialization_partial_at_least_15": years[2021] >= 15,
        "each_2022_to_2024_at_least_35": all(years[year] >= 35 for year in range(2022, 2025)),
        "2025_h1_at_least_15": years[2025] >= 15,
        "old_ose_regime_at_least_170": old >= 170,
        "new_ose_regime_at_least_20": new >= 20,
    }
    return {
        "scope": "PnL-free availability/count gate; no trade, fill price, return, PnL, PF, win/loss, ranking, or bootstrap output.",
        "scheduled_axis_trade_dates": len(events),
        "q_ready_nonzero_r_days": len(qready_nonzero),
        "E_orders": len(orders),
        "E_order_share_qready_nonzero_r": share,
        "complete_primary_paths": len(complete),
        "unresolved_filled_positions": len(unresolved),
        "complete_by_r_sign": {"positive": signs[1], "negative": signs[-1]},
        "complete_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "complete_by_night_regime": dict(sorted(regimes.items())),
        "old_regime_complete": old,
        "new_regime_complete": new,
        "unexplained_exclusions": unexplained,
        "status_counts": dict(sorted(Counter(cast(str, row["status"]) for row in events).items())),
        "gate": gate | {"passed": all(gate.values())},
    }


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    executable = [row for row in events if row.get("status") == "EXECUTABLE"]
    checks = {
        "scheduled_tse_axis_complete_unique": [row["trade_date"] for row in events] == [day.isoformat() for day in axis] and len(events) == len({row["trade_date"] for row in events}),
        "references_are_exactly_current_excluded_prior_120_when_q_ready": all(
            len(cast(list[str], row["reference_trade_dates_oldest_to_newest"])) in {0, 120}
            and cast(str, row["trade_date"]) not in cast(list[str], row["reference_trade_dates_oldest_to_newest"])
            and all(date.fromisoformat(item) < date.fromisoformat(cast(str, row["trade_date"])) for item in cast(list[str], row["reference_trade_dates_oldest_to_newest"]))
            for row in events
        ),
        "E_order_not_selected_by_future_night_availability": all(
            row.get("state") != "E" or bool(row.get("entry_order_submitted")) for row in events
        ),
        "cash_close_submit_precedes_mapped_night_entry": all(
            datetime.fromisoformat(cast(str, row["submit_jst"])) < datetime.fromisoformat(cast(str, row["entry_open_jst"]))
            for row in executable
        ),
        "cash_to_following_night_mapping_is_explicit_one_to_one": all(
            row.get("night_calendar_start_date") == row.get("trade_date") and row.get("night_trade_date")
            for row in executable
        ),
        "entry_exit_are_first_and_last_normal_scheduled_opens": all(
            row.get("entry_open_jst") == row.get("formal_night_open_jst")
            and row.get("exit_open_jst") == row.get("exit_open_planned_jst")
            for row in executable
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def _trade(
    event: dict[str, object], bars: dict[tuple[date, Session], list[Bar]], instrument: Any, *, direction: str, ticks: int, fee: int, profile: str
) -> Trade:
    night_day = date.fromisoformat(cast(str, event["night_trade_date"]))
    entry_stamp = datetime.fromisoformat(cast(str, event["entry_open_jst"]))
    exit_stamp = datetime.fromisoformat(cast(str, event["exit_open_jst"]))
    lookup = {bar.ts_jst: bar for bar in bars[(night_day, Session.NIGHT)]}
    entry, exit_bar = lookup[entry_stamp], lookup[exit_stamp]
    side = Side(direction)
    spec = instrument.instrument.to_spec()
    entry_fill = adverse_fill(entry.open, side is Side.LONG, ticks, spec)
    exit_fill = adverse_fill(exit_bar.open, side is Side.SHORT, ticks, spec)
    excursion = [bar for bar in bars[(night_day, Session.NIGHT)] if entry_stamp <= bar.ts_jst <= exit_stamp and bar.is_eligible]
    mae = max((entry_fill - bar.low if side is Side.LONG else bar.high - entry_fill) for bar in excursion)
    mfe = max((bar.high - entry_fill if side is Side.LONG else entry_fill - bar.low) for bar in excursion)
    gross = gross_pnl(entry_fill, exit_fill, side, 1, spec)
    return Trade(
        trade_id=f"r093-{profile}-{event['trade_date']}", trade_date=night_day, side=side, qty=1,
        entry_signal_ts=datetime.fromisoformat(cast(str, event["submit_jst"])), entry_ts=entry_stamp,
        entry_reference_price=entry.open, entry_fill_price=entry_fill,
        exit_signal_ts=datetime.fromisoformat(cast(str, event["exit_signal_jst"])), exit_ts=exit_stamp,
        exit_reference_price=exit_bar.open, exit_fill_price=exit_fill, gross_pnl_jpy=gross,
        fees_jpy=2 * fee, slippage_cost_jpy=slippage_cost(entry.open, entry_fill, exit_bar.open, exit_fill, 1, spec),
        net_pnl_jpy=gross - 2 * fee, mae_jpy=mae * spec.multiplier, mfe_jpy=mfe * spec.multiplier,
        holding_minutes=int((exit_stamp - entry_stamp).total_seconds() // 60), entry_reason="r093_cash_close_scheduled_order",
        exit_reason=ExitReason.SIGNAL, strategy_id=f"r093_{profile}", strategy_version="r093-q001-v1",
        parameter_hash=canonical_hash({"profile": profile, "event": event, "direction": direction, "ticks": ticks, "fee": fee}),
        metadata={"entry_session": Session.NIGHT.value, "cash_signal_trade_date": event["trade_date"], "night_schedule_version": event["night_schedule_version"]},
    )


def execute_profile(axis: list[date], events: list[dict[str, object]], bars: dict[tuple[date, Session], list[Bar]], instrument: Any, *, profile: str, direction: str, ticks: int, fee: int) -> tuple[tuple[Trade, ...], dict[str, int | None], list[str]]:
    trades: dict[date, Trade] = {}
    unknown: list[str] = []
    for event in events:
        cash_day = date.fromisoformat(cast(str, event["trade_date"]))
        if event.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.append(cash_day.isoformat())
        elif event.get("status") == "EXECUTABLE":
            resolved_direction = cast(str, event[f"{direction}_direction"]) if direction in {"reversal", "continuation"} else direction
            trades[cash_day] = _trade(event, bars, instrument, direction=resolved_direction, ticks=ticks, fee=fee, profile=profile)
    daily: dict[str, int | None] = {day.isoformat(): None if day.isoformat() in unknown else 0 for day in axis}
    for cash_day, trade in trades.items():
        if daily[cash_day.isoformat()] is not None:
            daily[cash_day.isoformat()] = trade.net_pnl_jpy
    return tuple(trades[day] for day in axis if day in trades), daily, unknown


def report(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], events: list[dict[str, object]]) -> dict[str, object]:
    by_cash = {cast(str, row["trade_date"]): row for row in events}

    def event_for(trade: Trade) -> dict[str, object]:
        return by_cash[cast(str, trade.metadata["cash_signal_trade_date"])]
    return {
        "metrics": ledger_metrics(trades), "scheduled_axis_observations": len(axis), "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [day for day, value in daily.items() if value is None],
        "by_year": {str(year): ledger_metrics(tuple(trade for trade in trades if date.fromisoformat(cast(str, event_for(trade)["trade_date"])).year == year)) for year in range(2021, 2026)},
        "by_r_sign": {name: ledger_metrics(tuple(trade for trade in trades if cast(int, cast(dict[str, object], event_for(trade)["observation"])["r_sign"]) == sign)) for name, sign in (("positive", 1), ("negative", -1))},
        "by_night_regime": {version: ledger_metrics(tuple(trade for trade in trades if event_for(trade)["night_schedule_version"] == version)) for version in sorted({cast(str, row.get("night_schedule_version")) for row in events if row.get("status") == "EXECUTABLE"})},
        "profit_concentration": concentration(trades, axis),
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [Path("scripts/run_task_r093_q001_tse_cash_night_reversal.py"), Path("src/n225m_bt/research/r093_cash_night_reversal.py"), Path("tests/test_r093_q001.py")]
    config_files = [Path(f"config/{item}") for item in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    preregistration = {
        "task_id": "TASK-R093-Q001", "family_id": "tse_cash_night_reversal", "study_id": "R093-Q001", "spec_version": "v1", "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01", "preregistration_document": str(PREREG), "preregistration_document_sha256": digest(ROOT / PREREG),
        "prior_information_seen": True, "development_reuse": True, "decision_ceiling": "INVESTIGATE",
        "historical_linkage": {"R058": "closing-pressure to night reversal", "R070": "unconditional night risk premium", "R073": "official night to day reversal", "R085": "day-close to night-reopen gap fade", "R092": "TSE rolling extreme"},
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "TSE 09:00 open to final scheduled minute immediately before official cash T return; exact current-excluded 120 scheduled TSE days, >=100 valid x, q75 nearest rank equality upper; submit after c at T; fill first following mapped OSE night normal open; exit final normal one-minute open.",
        "s2_gate": "unexplained=0; unresolved=0; q-ready nonzero r>=800; E/q-ready 20-30%; A/B/C/D>=200; r signs>=80; 2021>=15; 2022-24>=35; 2025H1>=15; old/new night>=170/20.",
        "profiles": ["A_reversal", "B_continuation", "C_fixed_long", "D_fixed_short", "A_q70", "A_q80", "A_entry_delay_1m", "A_exit_30m_early", "A_cost_2tick", "A_cost_3tick", "A_fee_2x"],
        "bootstrap": {"seed": MBB_SEED, "block_length_trade_dates": 20, "repetitions": 10000, "method": "non-circular MBB; common index; tail truncation; linear percentile"},
        "input_partitions": [{"path": str(item.resolve().relative_to(ROOT)), "sha256": digest(item)} for item in partition_paths(data_config.gold_root, "development")],
        "implementation_hashes": {str(item): digest(ROOT / item) for item in source_files}, "config_hashes": {str(item): digest(ROOT / item) for item in config_files},
        "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    snapshot(OUT / "documentation_snapshot", [PREREG])
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r093_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r093_cash_night_reversal.py", str(source_files[0])],
        "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files)],
    }
    validation: dict[str, object] = {}
    for name, command in commands.items():
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=environment)
        validation[name] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    validation["status"] = "PASS" if all(cast(dict[str, object], item)["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("R093 pre-execution validation failed")
    instrument, sessions, data_config, _ = load_project_config(ROOT / "config")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml"))
    development = load_split(data_config.gold_root, "development")
    view, isolated, r004 = quarantine(development)
    axis = tse_axis(cash_calendar())
    axis_set = set(axis)
    bars: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in axis_set:
            bars[(bar.trade_date, bar.session)].append(bar)
    primary = build_events(classifier, axis, bars, isolated)
    s2, audit = feasibility(primary), causality_audit(primary, axis)
    write_json(OUT / "access_ledger.json", {"stage": "S2 then conditional S3", "physical_io": "Development normalized Parquet only", "data_version": development.data_version, "partitions": development.quality["partitions"], "r004": r004, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "scheduled_axis.json", {"trade_dates": [day.isoformat() for day in axis], "source": "frozen Cabinet Office TSE business-day evidence", "holiday_evidence_sha256": digest(HOLIDAYS)})
    write_json(OUT / "primary_events_pnl_free.json", primary)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_audit_before_pnl.json", audit)
    if not bool(cast(dict[str, object], s2["gate"])["passed"]) or not bool(audit["passed"]):
        decision = {"decision": "INCONCLUSIVE", "reason": "S2_GATE_OR_CAUSALITY_AUDIT_FAILED_BEFORE_PNL", "s2_gate": s2["gate"], "causality": audit, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return
    profiles = {
        "A_reversal": (primary, "reversal", 1, 30), "B_continuation": (primary, "continuation", 1, 30), "C_fixed_long": (primary, "long", 1, 30), "D_fixed_short": (primary, "short", 1, 30),
        "A_q70": (build_events(classifier, axis, bars, isolated, percentile=70), "reversal", 1, 30), "A_q80": (build_events(classifier, axis, bars, isolated, percentile=80), "reversal", 1, 30),
        "A_entry_delay_1m": (build_events(classifier, axis, bars, isolated, entry_delay_minutes=1), "reversal", 1, 30), "A_exit_30m_early": (build_events(classifier, axis, bars, isolated, exit_early_minutes=30), "reversal", 1, 30),
        "A_cost_2tick": (primary, "reversal", 2, 30), "A_cost_3tick": (primary, "reversal", 3, 30), "A_fee_2x": (primary, "reversal", 1, 60),
    }
    unknown_profiles: dict[str, list[str]] = {}
    for name, (events, _, _, _) in profiles.items():
        write_json(OUT / f"{name}_events.json", events)
        unknown = [cast(str, row["trade_date"]) for row in events if row.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"]
        if unknown:
            unknown_profiles[name] = unknown
    if unknown_profiles:
        decision = {"decision": "INCONCLUSIVE", "reason": "ENTRY_FILLED_EXIT_UNRESOLVED_AFTER_S2", "unknown_profiles": unknown_profiles, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return
    reports: dict[str, dict[str, object]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    for name, (events, direction, ticks, fee) in profiles.items():
        trades, daily, unknown = execute_profile(axis, events, bars, instrument, profile=name, direction=direction, ticks=ticks, fee=fee)
        if unknown:
            raise ValueError(f"R093 {name} became unknown after preflight")
        trades_by[name] = trades
        reports[name] = report(axis, trades, daily, events)
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    a, b, c, d = (list(cast(dict[str, int], reports[name]["daily_net_pnl_jpy"]).values()) for name in ("A_reversal", "B_continuation", "C_fixed_long", "D_fixed_short"))
    boot, index = bootstrap(a, b, c, d)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    a_metrics = cast(dict[str, int | float | None], reports["A_reversal"]["metrics"])
    def lower(key: str) -> float:
        return cast(list[float], cast(dict[str, object], boot[key])["ci95_percentile_linear"])[0]
    main = {
        "A_net_positive": cast(int, a_metrics["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": a_metrics["profit_factor"] is not None and cast(float, a_metrics["profit_factor"]) > 1,
        "A_daily_net_ci_lower_positive": lower("A_reversal_daily_net_jpy_per_trade_date") > 0,
        "A_minus_B_ci_lower_positive": lower("A_minus_B_daily_net_jpy_per_trade_date") > 0,
        "A_minus_C_ci_lower_positive": lower("A_minus_C_daily_net_jpy_per_trade_date") > 0,
        "A_minus_D_ci_lower_positive": lower("A_minus_D_daily_net_jpy_per_trade_date") > 0,
    }
    a_report = reports["A_reversal"]
    year = cast(dict[str, dict[str, int | float | None]], a_report["by_year"])
    sign = cast(dict[str, dict[str, int | float | None]], a_report["by_r_sign"])
    regime = cast(dict[str, dict[str, int | float | None]], a_report["by_night_regime"])
    concentration_a = cast(dict[str, int | float | None], a_report["profit_concentration"])
    def net(name: str) -> int:
        return cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])
    robustness = {
        "q70_net_positive": net("A_q70") > 0, "q80_net_positive": net("A_q80") > 0, "entry_delay_1m_net_positive": net("A_entry_delay_1m") > 0,
        "exit_30m_early_net_positive": net("A_exit_30m_early") > 0, "two_tick_net_positive": net("A_cost_2tick") > 0, "fee_2x_net_positive": net("A_fee_2x") > 0,
        "positive_r_net_positive": cast(int, sign["positive"]["net_pnl_jpy"]) > 0, "negative_r_net_positive": cast(int, sign["negative"]["net_pnl_jpy"]) > 0,
        "both_ose_regimes_net_positive": bool(regime) and all(cast(int, item["net_pnl_jpy"]) > 0 for item in regime.values()),
        "at_least_two_2022_2024_positive": sum(cast(int, year[str(item)]["net_pnl_jpy"]) > 0 for item in range(2022, 2025)) >= 2,
        "2025_h1_net_positive": cast(int, year["2025"]["net_pnl_jpy"]) > 0,
        "top10_winners_removed_net_positive": cast(int, concentration_a["net_excluding_top10_jpy"]) > 0,
        "three_tick_diagnostic_net_jpy": net("A_cost_3tick"),
    }
    execution = {
        "A_B_C_D_same_E_entry_exit": all(tuple((row["trade_date"], row.get("entry_open_jst"), row.get("exit_open_jst")) for row in primary if row.get("status") == "EXECUTABLE") == tuple((row["trade_date"], row.get("entry_open_jst"), row.get("exit_open_jst")) for row in profiles[name][0] if row.get("status") == "EXECUTABLE") for name in ("B_continuation", "C_fixed_long", "D_fixed_short")),
        "A_B_opposite_side": all(left.side is not right.side for left, right in zip(trades_by["A_reversal"], trades_by["B_continuation"], strict=True)),
        "one_trade_one_position_per_cash_day": all(len(trades) == len({trade.metadata["cash_signal_trade_date"] for trade in trades}) for trades in trades_by.values()),
        "no_stop_or_target": all(trade.exit_reason is ExitReason.SIGNAL for trades in trades_by.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades),
        "slippage_not_double_deducted": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades),
    }
    if not all(execution.values()):
        raise ValueError("R093 execution/accounting audit failed")
    decision = "REJECT" if not all(main.values()) else "INVESTIGATE"
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "profiles.json", reports | {"N_no_trade": {"net_pnl_jpy": 0, "daily_net_pnl_jpy": {day.isoformat(): 0 for day in axis}}})
    write_json(OUT / "execution_accounting_audit.json", execution)
    write_json(OUT / "s3_results.json", {"primary_and": main, "robustness": robustness, "three_tick_diagnostic_net_jpy": net("A_cost_3tick")})
    write_json(OUT / "decision.json", {"decision": decision, "decision_ceiling": "INVESTIGATE", "primary_and": main, "robustness": robustness, "s2_gate": s2["gate"], "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "decision": decision, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
