"""Execute the single frozen Development-only TASK-R091-Q001 experiment."""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from sys import executable
from typing import Any, cast

import numpy as np

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r091_fixed_tse_short import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    ENTRY_TIME,
    MBB_SEED,
    bootstrap,
    feasibility,
    scheduled_event,
    tse_cash_close,
)
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r091-q001-tse-cash-hours-fixed-short-20260915-02"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG_DOC = Path("docs/strategy/81_r091_q001_tse_cash_hours_fixed_short.md")
OFFICIAL_AXIS = ROOT / "results/research/r021-q001-20260914-opening-cash-close-followthrough-05/institutional_evidence/tse_cash_calendar.json"
OFFICIAL_EVIDENCE = ROOT / "results/research/r021-q001-20260914-opening-cash-close-followthrough-05/institutional_evidence"
R004_EXPECTED_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def official_axis() -> list[date]:
    payload = json.loads(OFFICIAL_AXIS.read_text(encoding="utf-8"))
    axis = [date.fromisoformat(value) for value in payload["tse_open_trade_dates"]]
    selected = [value for value in axis if DEVELOPMENT_START <= value <= DEVELOPMENT_END]
    if len(selected) != len(axis) or len(selected) != len(set(selected)):
        raise ValueError("official TSE axis is outside Development or has duplicates")
    return selected


def r004_quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    """Reproduce the immutable whole-session R004 isolation without changing Gold."""
    groups = session_groups(data.bars)
    isolated = {
        key for key, bars in groups.items() if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in bars)
    }
    included = [bar for key, bars in groups.items() if key not in isolated for bar in bars]
    sessions = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(isolated),
        "quarantined_sessions_by_type": dict(sorted(Counter(session.value for _, session in isolated).items())),
        "included_bars": len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": sessions,
        "quarantined_session_list_hash": canonical_hash(sessions),
    }
    if audit["included_tick_grid_violations"] != 0:
        raise ValueError("R004 isolation left a tick-grid violation")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": sessions}), data.quality | {"quarantine": audit}), audit, isolated


def event_ledger(
    axis: list[date], groups: dict[tuple[date, Session], list[Bar]], isolated: set[tuple[date, Session]], *, entry: time = ENTRY_TIME, exit_minus_minutes: int = 0
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for target in axis:
        exit_time = tse_cash_close(target)
        if exit_minus_minutes:
            exit_time -= timedelta(minutes=exit_minus_minutes)
        events.append(
            scheduled_event(
                target,
                groups.get((target, Session.DAY), []),
                r004_day_quarantined=(target, Session.DAY) in isolated,
                entry_time=entry,
                exit_time=exit_time,
            )
        )
    return events


def serialize_trade(trade: Trade) -> dict[str, object]:
    return {
        "trade_id": trade.trade_id,
        "trade_date": trade.trade_date.isoformat(),
        "side": trade.side.value,
        "qty": trade.qty,
        "entry_signal_ts": trade.entry_signal_ts.isoformat(),
        "entry_ts": trade.entry_ts.isoformat(),
        "entry_reference_price": trade.entry_reference_price,
        "entry_fill_price": trade.entry_fill_price,
        "exit_signal_ts": trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None,
        "exit_ts": trade.exit_ts.isoformat(),
        "exit_reference_price": trade.exit_reference_price,
        "exit_fill_price": trade.exit_fill_price,
        "gross_pnl_jpy": trade.gross_pnl_jpy,
        "fees_jpy": trade.fees_jpy,
        "slippage_cost_jpy": trade.slippage_cost_jpy,
        "net_pnl_jpy": trade.net_pnl_jpy,
        "exit_reason": trade.exit_reason.value,
    }


def run_profile(
    name: str,
    side: str,
    events: list[dict[str, object]],
    groups: dict[tuple[date, Session], list[Bar]],
    engine: BacktestEngine,
) -> tuple[tuple[Trade, ...], dict[str, int], dict[str, int]]:
    """Execute exactly the pre-existing, complete scheduled orders; never repair an exit."""
    trades: list[Trade] = []
    daily = {str(event["trade_date"]): 0 for event in events}
    status: dict[str, int] = defaultdict(int)
    for event in events:
        target = date.fromisoformat(cast(str, event["trade_date"]))
        completion = cast(str, event["completion_status"])
        status[completion] += 1
        if completion != "COMPLETE":
            continue
        entry = datetime.fromisoformat(cast(str, event["entry_fill_planned_jst"]))
        exit_ = datetime.fromisoformat(cast(str, event["exit_fill_planned_jst"]))
        result = engine.run(
            groups[(target, Session.DAY)],
            R049FixedTimeStrategy(f"r091_{name}_{target.isoformat()}", entry, exit_, side),
            canonical_hash({"study": "R091-Q001", "profile": name, "side": side, "entry": entry.isoformat(), "exit": exit_.isoformat()}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: expected one completed trade, got {len(result.trades)} and {result.canceled_orders} cancellations")
        trade = result.trades[0]
        if (
            trade.qty != 1
            or trade.entry_ts != entry
            or trade.exit_ts != exit_
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.entry_signal_ts != entry - timedelta(minutes=1)
        ):
            raise ValueError(f"{name}/{target}: fixed schedule execution contract failed")
        trades.append(trade)
        daily[target.isoformat()] += trade.net_pnl_jpy
    return tuple(trades), daily, dict(sorted(status.items()))


def summary(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int]) -> dict[str, object]:
    regime = {
        "old_through_2024-11-01": sum(value for key, value in daily.items() if key <= "2024-11-01"),
        "new_from_2024-11-05": sum(value for key, value in daily.items() if key >= "2024-11-05"),
    }
    by_year = {str(year): sum(value for key, value in daily.items() if key.startswith(str(year))) for year in range(2021, 2026)}
    return {
        "metrics": ledger_metrics(trades),
        "daily_net_pnl_jpy": daily,
        "net_by_year_jpy": by_year,
        "net_by_tse_close_regime_jpy": regime,
        "profit_concentration": concentration(trades, axis),
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_task_r091_q001_tse_cash_hours_fixed_short.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    source_files = [
        Path("scripts/run_task_r091_q001_tse_cash_hours_fixed_short.py"),
        Path("src/n225m_bt/research/r091_fixed_tse_short.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r091_fixed_tse_short.py"),
    ]
    config_files = [Path("config/backtest.yaml"), Path("config/data.yaml"), Path("config/instrument.yaml"), Path("config/sessions.yaml"), Path("config/local_calendar.yaml"), Path("config/research.yaml")]
    OUT.mkdir(parents=True)
    document_snapshot = OUT / "documentation_snapshot"
    document_snapshot.mkdir()
    shutil.copy2(ROOT / PREREG_DOC, document_snapshot / PREREG_DOC.name)
    source_snapshot = OUT / "source_snapshot"
    source_snapshot.mkdir()
    for path in source_files:
        shutil.copy2(ROOT / path, source_snapshot / path.name)
    config_snapshot = OUT / "config_snapshot"
    config_snapshot.mkdir()
    for path in config_files:
        shutil.copy2(ROOT / path, config_snapshot / path.name)
    evidence_snapshot = OUT / "institutional_evidence"
    evidence_snapshot.mkdir()
    for name in ("tse_cash_calendar.json", "evidence_manifest.json", "r020_cabinet_office_public_holidays.csv", "jpx_tse_trading_hours.pdf", "jpx_trading_strengthening.html"):
        shutil.copy2(OFFICIAL_EVIDENCE / name, evidence_snapshot / name)

    _, _, data_config, _ = load_project_config(ROOT / "config")
    partitions = partition_paths(data_config.gold_root, "development")
    preregistration = {
        "task_id": "TASK-R091-Q001",
        "study_id": "R091-Q001",
        "family_id": "unconditional_tse_cash_hours_direction",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PNL_ACCESS",
        "prior_immutable_attempt": "...-01 stopped before preregistration completion, Development load, event availability, orders, fills, trades, returns, PnL, bootstrap, or decision because a relative input partition path was passed to Path.relative_to(ROOT). This -02 changes only manifest path serialization.",
        "seed": MBB_SEED,
        "hypothesis": "During official TSE cash-market hours, unconditional futures return is negative and a fixed short has positive post-cost expectancy.",
        "scheduled_axis": "official TSE business-day table only, Development trade_date 2021-01-01..2025-06-30; T=15:00 through 2024-11-01 and 15:30 from 2024-11-05, never inferred from observed futures rows.",
        "orders": "At 08:59 submit/decide a schedule-only order for 09:00 open; fixed T-1 exit decision and T open exit fill; one contract, max one position, no price/weekday/Stop/Target/re-entry/updates.",
        "e_exec": "entry eligibility depends solely on 08:59 and 09:00 availability. Later exit availability is E_analysis/settlement only; an unavailable exit retains its submitted entry and is null, never a zero or skipped trade.",
        "conditions": {"A": "fixed short", "B": "same-day same-entry/exit fixed long", "C": "no trade JPY0"},
        "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "diagnostic": ["2 ticks", "3 ticks", "fee x2"]},
        "s2_gate": "unexplained exclusions=0; unresolved filled positions=0; A/B >=900; 2021-2024 each >=180; 2025H1 >=80; old regime >=700; new regime >=100. Any failure is INCONCLUSIVE before PnL.",
        "s3_primary_and": "A Net>0, PF>1, A daily-Net MBB lower CI>0, paired A-B daily difference MBB lower CI>0; any failure REJECT.",
        "s3_sensitivities": "fixed 1-minute entry delay (exit unchanged), 09:05 entry, T-5 exit, 2/3 tick per side, fee x2; 3 tick is stored diagnostic.",
        "decision_ceiling": "INVESTIGATE",
        "prior_information_seen": {
            "development_reuse": "Development has been repeatedly used; this is not independent confirmation.",
            "r001_r090_duplicate_review": "No exact 09:00-to-T fixed-short study found. R066-Q001 is related fixed long but exits 14:30; R021/R030/R044 use cash-close schedules with price/event conditions. Direction or holding-time changes remain the same related family, not an independent family.",
            "r070_q001": "Known night fixed-long result: REJECT; its 16:30-to-05:30 schedule is not this day-only schedule.",
        },
        "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partitions],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "official_axis_sha256": digest(OFFICIAL_AXIS),
        "official_evidence_manifest_sha256": digest(OFFICIAL_EVIDENCE / "evidence_manifest.json"),
        "oos": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "created_at_utc": datetime.now(timezone.utc).isoformat(), "preregistration_hash": canonical_hash(preregistration), "status": "S0_S1_FROZEN"})

    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r091_fixed_tse_short.py", "tests/test_r021_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r091_fixed_tse_short.py", str(source_files[0])],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION"})
        raise ValueError("R091 pre-execution validation failed")

    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    axis = official_axis()
    development = load_split(data_config.gold_root, "development")
    view, r004_audit, isolated = r004_quarantine(development)
    if r004_audit["quarantined_session_list_hash"] != R004_EXPECTED_HASH:
        raise ValueError("R091 R004 quarantine hash mismatch")
    groups = session_groups(view.bars)
    events = event_ledger(axis, groups, isolated)
    s2 = feasibility(events)
    causality = {
        "official_tse_axis_only": all(DEVELOPMENT_START <= date.fromisoformat(cast(str, event["trade_date"])) <= DEVELOPMENT_END for event in events),
        "unique_trade_date_axis": len(axis) == len(set(axis)) == len(events),
        "schedule_not_observed_row_inferred": all(datetime.fromisoformat(cast(str, event["tse_cash_close_jst"])) == tse_cash_close(date.fromisoformat(cast(str, event["trade_date"]))) for event in events),
        "old_new_tse_rules": all((str(event["tse_cash_close_jst"]).endswith("15:00:00+09:00") if str(event["trade_date"]) <= "2024-11-01" else str(event["tse_cash_close_jst"]).endswith("15:30:00+09:00")) for event in events),
        "entry_e_exec_independent_of_exit": all(not bool(event["entry_eligible"]) or bool(event["order_submitted"]) for event in events),
        "r004_isolation_recorded": all((target, Session.DAY) not in isolated or event["completion_status"] == "NO_ORDER_R004_DAY_SESSION_QUARANTINED" for target, event in zip(axis, events, strict=True)),
        "trade_date_not_calendar_date": True,
    }
    write_json(OUT / "access_ledger.json", {"stage": "S2", "physical_io": "Development normalized Parquet only", "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "price_performance_access": "NO; availability/event statuses only", "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "r004_quarantine_audit.json", r004_audit)
    write_json(OUT / "scheduled_axis.json", {"trade_dates": [target.isoformat() for target in axis], "source": str(OFFICIAL_AXIS.relative_to(ROOT)), "sha256": digest(OFFICIAL_AXIS)})
    write_json(OUT / "primary_events_pnl_free.json", events)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(OUT / "causality_audit_before_pnl.json", {"checks": causality, "passed": all(causality.values()), "pnl_not_accessed_before_audit": True})
    s2_gate = cast(dict[str, object], s2["gate"])
    if not bool(s2_gate["passed"]) or not all(causality.values()):
        pre_decision = {"decision": "INCONCLUSIVE", "reason": "S2_OR_CAUSALITY_GATE_FAILED_BEFORE_PNL", "s2_gate": s2_gate, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", pre_decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **pre_decision})
        return

    profiles = {
        "A_fixed_short": ("short", time(9, 0), 0, 1, 30),
        "B_fixed_long": ("long", time(9, 0), 0, 1, 30),
        "A_entry_delay_1m": ("short", time(9, 1), 0, 1, 30),
        "A_entry_0905": ("short", time(9, 5), 0, 1, 30),
        "A_exit_T_minus_5m": ("short", time(9, 0), 5, 1, 30),
        "A_cost_2tick": ("short", time(9, 0), 0, 2, 30),
        "A_cost_3tick": ("short", time(9, 0), 0, 3, 30),
        "A_fee_2x": ("short", time(9, 0), 0, 1, 60),
    }
    output: dict[str, dict[str, object]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    event_by: dict[str, list[dict[str, object]]] = {}
    for name, (side, entry, exit_minus, ticks, fee) in profiles.items():
        profile_events = event_ledger(axis, groups, isolated, entry=entry, exit_minus_minutes=exit_minus)
        if any(event["completion_status"] != "COMPLETE" and event["entry_eligible"] for event in profile_events):
            raise ValueError(f"{name}: unresolved filled position during S3 sensitivity")
        runtime = baseline.model_copy(update={"mode": "day_only", "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}), "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee})})
        filled, daily, status = run_profile(name, side, profile_events, groups, BacktestEngine(instrument.instrument.to_spec(), runtime, CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml"))))
        trades_by[name], daily_by[name], event_by[name] = filled, daily, profile_events
        output[name] = summary(axis, filled, daily) | {"execution_status_counts": status, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": fee}}
        write_json(OUT / f"{name}_events.json", profile_events)
        write_json(OUT / f"{name}_trades.json", [serialize_trade(trade) for trade in filled])

    short_daily = list(daily_by["A_fixed_short"].values())
    long_daily = list(daily_by["B_fixed_long"].values())
    boot, index = bootstrap(short_daily, long_daily)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    primary = output["A_fixed_short"]
    primary_metrics = cast(dict[str, object], primary["metrics"])
    concentration_metrics = cast(dict[str, object], primary["profit_concentration"])
    primary_ci = cast(list[float], cast(dict[str, object], boot["A_short_daily_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    paired_ci = cast(list[float], cast(dict[str, object], boot["A_minus_B_daily_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    years = cast(dict[str, int], primary["net_by_year_jpy"])
    regimes = cast(dict[str, int], primary["net_by_tse_close_regime_jpy"])
    def profile_net(name: str) -> int:
        metrics = cast(dict[str, object], output[name]["metrics"])
        return cast(int, metrics["net_pnl_jpy"])

    gates = {
        "primary_A_net_positive": cast(int, primary_metrics["net_pnl_jpy"]) > 0,
        "primary_A_pf_gt_one": cast(float | None, primary_metrics["profit_factor"]) is not None and cast(float, primary_metrics["profit_factor"]) > 1,
        "primary_A_daily_net_ci_lower_positive": primary_ci[0] > 0,
        "primary_paired_A_minus_B_ci_lower_positive": paired_ci[0] > 0,
        "two_tick_net_positive": profile_net("A_cost_2tick") > 0,
        "fee_2x_net_positive": profile_net("A_fee_2x") > 0,
        "entry_delay_1m_net_positive": profile_net("A_entry_delay_1m") > 0,
        "entry_0905_net_positive": profile_net("A_entry_0905") > 0,
        "exit_T_minus_5m_net_positive": profile_net("A_exit_T_minus_5m") > 0,
        "old_tse_regime_net_positive": regimes["old_through_2024-11-01"] > 0,
        "new_tse_regime_net_positive": regimes["new_from_2024-11-05"] > 0,
        "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3,
        "positive_2025_h1": years["2025"] > 0,
        "top10_winners_removed_net_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0,
    }
    primary_keys = tuple(key for key in gates if key.startswith("primary_"))
    robust_keys = tuple(key for key in gates if key not in primary_keys)
    decision = "REJECT" if not all(gates[key] for key in primary_keys) else "INVESTIGATE"
    audit = {
        "submit_decision_fill_schedule_exact": all(datetime.fromisoformat(cast(str, event["submit_jst"])) == datetime.fromisoformat(cast(str, event["entry_decision_jst"])) for events in event_by.values() for event in events),
        "a_b_same_event_entry_exit": all((a["trade_date"], a["entry_fill_planned_jst"], a["exit_fill_planned_jst"]) == (b["trade_date"], b["entry_fill_planned_jst"], b["exit_fill_planned_jst"]) for a, b in zip(event_by["A_fixed_short"], event_by["B_fixed_long"], strict=True)),
        "a_b_opposite_side": all(a.side.value != b.side.value for a, b in zip(trades_by["A_fixed_short"], trades_by["B_fixed_long"], strict=True)),
        "one_trade_per_completed_day": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in trades_by.values()),
        "signal_exits_only": all(trade.exit_reason is ExitReason.SIGNAL for trades in trades_by.values() for trade in trades),
        "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in trades_by.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades),
        "slippage_not_double_deducted": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades),
        "no_unresolved_primary_position": bool(s2_gate["unresolved_filled_positions_equal_zero"]),
    }
    if not all(audit.values()):
        raise ValueError("R091 execution/accounting audit failed")
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    write_json(OUT / "s3_results.json", {"conditions": output, "C_no_trade": {"scheduled_axis_observations": len(axis), "net_pnl_jpy": 0, "daily_net_pnl_jpy": {target.isoformat(): 0 for target in axis}}, "bootstrap": boot, "gates": gates, "robustness_all_passed": all(gates[key] for key in robust_keys), "scope": "Development only; no WFA, OOS, or Final Holdout"})
    write_json(OUT / "decision.json", {"decision": decision, "decision_ceiling": "INVESTIGATE", "primary_and": {key: gates[key] for key in primary_keys}, "robustness": {key: gates[key] for key in robust_keys}, "s2_gate": s2_gate, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "decision": decision, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
