"""Executor for the one preregistered TASK-R066-Q001 fixed-TSE-long experiment."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from sys import executable
from typing import Any

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import ledger_metrics
from n225m_bt.research.r066_tse_drift import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    ENTRY_TIME,
    EXIT_TIME,
    MBB_SEED,
    aligned_daily_net,
    feasibility,
    mbb_mean_ci,
    path_status,
    scheduled_axis,
)
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r066-q001-tse-regular-hours-fixed-long-20260915-02"
OUT = ROOT / "results" / "research" / RUN_ID
SEED = MBB_SEED


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def run_profile(
    axis: list[date],
    bars_by_day: dict[date, list[Bar]],
    engine: BacktestEngine,
    *,
    name: str,
    direction: str,
    entry: time = ENTRY_TIME,
    exit_: time = EXIT_TIME,
) -> tuple[tuple[Trade, ...], dict[str, int | None], dict[str, object]]:
    """Run independent same-day paths; an unavailable filled exit is null, never flat-filled."""
    trades: list[Trade] = []
    unknown: set[date] = set()
    known_no_trade: set[date] = set()
    per_day: dict[str, str] = {}
    for target in axis:
        bars = bars_by_day.get(target, [])
        status = path_status(target, bars, entry, exit_)
        per_day[target.isoformat()] = status
        if status != "EXECUTABLE":
            # An entry path failure is a known no-trade.  Once the entry and
            # exit signal are observable but the exit fill is not, net PnL is
            # unknown by the frozen contract.
            if status in {"MISSING_EXIT", "INELIGIBLE_EXIT", "NONPOSITIVE_EXIT"}:
                unknown.add(target)
            else:
                known_no_trade.add(target)
            continue
        tz = bars[0].ts_jst.tzinfo
        if tz is None:
            raise ValueError("timezone-aware bars are required")
        strategy = R049FixedTimeStrategy(
            f"{name}_{target.isoformat()}",
            datetime.combine(target, entry, tz),
            datetime.combine(target, exit_, tz),
            direction,
        )
        result = engine.run(bars, strategy, parameter_hash=canonical_hash({"name": name, "entry": str(entry), "exit": str(exit_), "direction": direction}))
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{target}: expected one completed fixed-time trade, got {len(result.trades)} and {result.canceled_orders} cancellations")
        trade = result.trades[0]
        if (
            trade.entry_ts.time() != entry
            or trade.exit_ts.time() != exit_
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{target}: execution contract mismatch")
        trades.append(trade)
    ordered = tuple(sorted(trades, key=lambda trade: trade.trade_date))
    daily = aligned_daily_net(axis, ordered, unknown)
    return ordered, daily, {
        "condition": name,
        "direction": direction,
        "entry_jst": entry.isoformat(timespec="minutes"),
        "exit_jst": exit_.isoformat(timespec="minutes"),
        "known_no_trade_dates": len(known_no_trade),
        "unknown_pnl_dates": len(unknown),
        "per_trade_date_execution_status": per_day,
    }


def profile_summary(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], audit: dict[str, object]) -> dict[str, object]:
    values = list(daily.values())
    observed = [value for value in values if value is not None]
    years: dict[str, int | None] = {}
    for year in range(2021, 2026):
        member = [daily[target.isoformat()] for target in axis if target.year == year]
        years[str(year)] = None if any(value is None for value in member) else sum(member)
    return {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "observed_daily_outcomes": len(observed),
        "unknown_daily_outcomes": len(values) - len(observed),
        "partial_observed_net_jpy": sum(observed),
        "daily_net_pnl_jpy": daily,
        "net_pnl_by_year_jpy": years,
        "execution_audit": audit,
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_task_r066_q001_tse_drift.py"),
        Path("src/n225m_bt/research/r066_tse_drift.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r066_tse_drift.py"),
    ]
    config_files = [
        Path("config/backtest.yaml"),
        Path("config/data.yaml"),
        Path("config/instrument.yaml"),
        Path("config/sessions.yaml"),
        Path("config/local_calendar.yaml"),
        Path("config/research.yaml"),
    ]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    partitions = partition_paths(data_config.gold_root, "development")
    preregistration: dict[str, object] = {
        "task_id": "TASK-R066-Q001",
        "study_id": "TASK-R066-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "seed": SEED,
        "prior_immutable_attempt": "...-01 stopped before preregistration write, market-table parsing, availability output, orders, fills, trades, PnL, bootstrap, or decision because a relative input partition path was incorrectly passed to Path.relative_to(ROOT). This -02 changes only manifest path serialization.",
        "duplicate_review": "R001-R065 reviewed before price-performance access. This rule has no price condition, no prior-session input, one fixed TSE-normal-hours long entry at 09:00 and fixed exit at 14:30. Existing R066-Q001 is a separate previously recorded lower-tail-to-night study and is not modified or used.",
        "hypothesis": "During TSE regular hours, unconditional positive drift makes a fixed long position positive after costs.",
        "primary_rule": "After the 08:59 JST eligible bar closes, enter one long at the next eligible 1-minute open (scheduled 09:00 JST), and exit at the scheduled 14:30 JST eligible open. No price condition, Stop, Target, re-entry, or prior-session information.",
        "scheduled_axis": "version-controlled calendar trade_date axis 2021-01-01 through 2025-06-30; no-trade=JPY0 and filled position with unknown exit=null.",
        "execution_contract": "bar-close signal, next eligible bar open, maximum one position, one contract, fixed planned clocks, no exit extension, no slippage double count.",
        "costs": "JPY30 fee plus 1 tick adverse slippage per side; fixed stress: 2 and 3 ticks per side, and fee x2.",
        "controls": "primary no-trade JPY0; directional diagnostic control is identical-clock fixed short.",
        "s2_gate": "at least 900 executable dates and at least 180 executable dates in each 2021, 2022, 2023, 2024; non-PnL only. If failed: INCONCLUSIVE and no S3.",
        "s3_required": "primary Net>0, PF>1, and lower 95% CI>0 for scheduled daily mean Net using 20-trade_date non-wrap MBB, 10,000 replicates, tail truncation, linear percentile.",
        "sensitivities_only": "entry 09:15 (exit 14:30), exit 14:15, exit 14:45, one-minute entry delay (09:01 fill, fixed 14:30 exit), 2/3 tick, and fee x2.",
        "candidate_required": "2-tick Net>0; delayed-entry Net>0; all three clock sensitivities have average daily Net>0; Net>0 in 2025H1 and in at least three of 2021-2024.",
        "oos": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
        "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partitions],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    snapshot = OUT / "source_snapshot"
    snapshot.mkdir()
    for path in source_files:
        target = snapshot / path.name
        shutil.copy2(ROOT / path, target)
    config_snapshot = OUT / "config_snapshot"
    config_snapshot.mkdir()
    for path in config_files:
        shutil.copy2(ROOT / path, config_snapshot / path.name)
    validation_commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r066_tse_drift.py", "tests/test_r049_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r066_tse_drift.py"],
    }
    validation: dict[str, Any] = {}
    for name, command in validation_commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION"})
        raise ValueError("pre-execution validation failed")
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    development = load_split(data_config.gold_root, "development")
    axis = scheduled_axis(calendar)
    bars_by_day: dict[date, list[Bar]] = defaultdict(list)
    for bar in development.bars:
        if bar.session is Session.DAY and bar.trade_date in set(axis):
            bars_by_day[bar.trade_date].append(bar)
    s2 = feasibility(axis, bars_by_day)
    write_json(OUT / "access_ledger.json", {"stage": "S2", "split": "development", "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "physical_partitions": development.quality["partitions"], "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED", "s2_outputs": "availability only; no returns, orders, fills, trades, or PnL"})
    write_json(OUT / "s2_feasibility.json", s2)
    if not bool(s2["gate"]["passed"]):
        decision = {"status": "INCONCLUSIVE", "reason": "S2_EXECUTABLE_DATE_GATE_FAILED", "s2_gate": s2["gate"], "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return
    def config(ticks: int = 1, fee: int = 30) -> Any:
        return baseline.model_copy(update={"mode": "day_only", "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}), "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}), "risk": baseline.risk.model_copy(update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0})})
    profiles = {
        "primary_long": ("long", ENTRY_TIME, EXIT_TIME, 1, 30),
        "directional_fixed_short": ("short", ENTRY_TIME, EXIT_TIME, 1, 30),
        "entry_0915": ("long", time(9, 15), EXIT_TIME, 1, 30),
        "exit_1415": ("long", ENTRY_TIME, time(14, 15), 1, 30),
        "exit_1445": ("long", ENTRY_TIME, time(14, 45), 1, 30),
        "entry_delay_1m": ("long", time(9, 1), EXIT_TIME, 1, 30),
        "cost_2tick": ("long", ENTRY_TIME, EXIT_TIME, 2, 30),
        "cost_3tick": ("long", ENTRY_TIME, EXIT_TIME, 3, 30),
        "fee_2x": ("long", ENTRY_TIME, EXIT_TIME, 1, 60),
    }
    output: dict[str, object] = {}
    all_trades: dict[str, tuple[Trade, ...]] = {}
    for name, (side, entry, exit_, ticks, fee) in profiles.items():
        filled, daily, audit = run_profile(axis, bars_by_day, BacktestEngine(instrument.instrument.to_spec(), config(ticks, fee), classifier), name=name, direction=side, entry=entry, exit_=exit_)
        if any(value is None for value in daily.values()):
            raise ValueError(f"{name}: unknown PnL prevents S3 metrics")
        output[name] = profile_summary(axis, filled, daily, audit)
        all_trades[name] = filled
        write_json(OUT / f"{name}_trades.json", [trade.__dict__ if hasattr(trade, "__dict__") else {"trade_id": trade.trade_id, "trade_date": trade.trade_date.isoformat(), "side": trade.side.value, "entry_ts": trade.entry_ts.isoformat(), "exit_ts": trade.exit_ts.isoformat(), "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy, "exit_reason": trade.exit_reason.value} for trade in filled])
    primary = output["primary_long"]
    primary_metrics = primary["metrics"]
    daily_values = [value for value in primary["daily_net_pnl_jpy"].values() if value is not None]
    bootstrap = mbb_mean_ci(daily_values, seed=SEED)
    write_json(OUT / "bootstrap_primary_daily_mean.json", bootstrap)
    years = primary["net_pnl_by_year_jpy"]
    time_sensitivities = ("entry_0915", "exit_1415", "exit_1445")
    gates = {
        "primary_net_positive": primary_metrics["net_pnl_jpy"] > 0,
        "primary_pf_gt_one": (primary_metrics["profit_factor"] or 0) > 1,
        "primary_mbb_ci95_lower_gt_zero": bootstrap["ci95_percentile_linear"][0] > 0,
        "two_tick_net_positive": output["cost_2tick"]["metrics"]["net_pnl_jpy"] > 0,
        "delayed_entry_net_positive": output["entry_delay_1m"]["metrics"]["net_pnl_jpy"] > 0,
        "all_time_sensitivity_average_daily_net_positive": all(sum(value for value in output[name]["daily_net_pnl_jpy"].values() if value is not None) / len(axis) > 0 for name in time_sensitivities),
        "three_positive_2021_2024": sum((years[str(year)] or 0) > 0 for year in range(2021, 2025)) >= 3,
        "positive_2025_h1": (years["2025"] or 0) > 0,
    }
    primary_pass = all(gates[name] for name in ("primary_net_positive", "primary_pf_gt_one", "primary_mbb_ci95_lower_gt_zero"))
    candidate_pass = primary_pass and all(gates[name] for name in gates if name not in {"primary_net_positive", "primary_pf_gt_one", "primary_mbb_ci95_lower_gt_zero"})
    decision = "CANDIDATE" if candidate_pass else "INVESTIGATE" if primary_pass else "REJECT"
    execution_audit = {
        "one_trade_per_complete_scheduled_path": all(len(value) <= len(axis) for value in all_trades.values()),
        "primary_entry_exit_clocks": all(trade.entry_ts.time() == ENTRY_TIME and trade.exit_ts.time() == EXIT_TIME for trade in all_trades["primary_long"]),
        "primary_signal_exit_only": all(trade.exit_reason is ExitReason.SIGNAL for trade in all_trades["primary_long"]),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in all_trades.values() for trade in trades),
        "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in all_trades.values() for trade in trades),
    }
    if not all(execution_audit.values()):
        raise ValueError("execution/accounting audit failed")
    write_json(OUT / "execution_accounting_audit.json", execution_audit)
    write_json(OUT / "s3_results.json", {"profiles": output, "no_trade_control": {"scheduled_axis_observations": len(axis), "daily_net_pnl_jpy": 0, "net_pnl_jpy": 0}, "directional_control": "directional_fixed_short", "gates": gates, "decision": decision, "data_version": development.data_version, "data_quality": development.quality, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "decision.json", {"status": decision, "s2_gate": s2["gate"], "s3_gates": gates, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "decision": decision, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
