"""Execute frozen Development-only R070-Q001; it has no OOS read path."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from sys import executable
from typing import Any, cast

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import ledger_metrics
from n225m_bt.research.r070_night_risk_premium import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    PRIMARY_ENTRY,
    PRIMARY_EXIT,
    aligned_daily_net,
    feasibility,
    fixed_night_event,
    mbb_mean_ci,
    scheduled_axis,
)
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r070-q001-20260915-night-unconditional-long-03"
OUT = ROOT / "results" / "research" / RUN_ID


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def serialise_trades(trades: tuple[Trade, ...]) -> list[dict[str, object]]:
    return [asdict(trade) for trade in trades]


def profile(
    axis: list[date],
    bars_by_day: dict[date, list[Bar]],
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    *,
    name: str,
    direction: str,
    entry_time: time = PRIMARY_ENTRY,
    exit_time: time = PRIMARY_EXIT,
    entry_delay_minutes: int = 0,
) -> tuple[tuple[Trade, ...], dict[str, int | None], dict[str, object], dict[str, dict[str, object]]]:
    """Replay an unconditional fixed order while preserving unavailable exits as null."""
    trades: list[Trade] = []
    unknown: set[date] = set()
    audit: dict[str, str] = {}
    events: dict[str, dict[str, object]] = {}
    for target in axis:
        bars = bars_by_day.get(target, [])
        event = fixed_night_event(
            target,
            bars,
            classifier,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_delay_minutes=entry_delay_minutes,
        )
        events[target.isoformat()] = event
        status = str(event["status"])
        if status in {"NO_SCHEDULED_FIXED_WINDOW", "ENTRY_CANCELLED"}:
            audit[target.isoformat()] = status
            continue
        if status == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            audit[target.isoformat()] = status
            continue
        if status != "EXECUTABLE":
            raise ValueError(f"{name}/{target}: unrecognized status {status}")
        entry = datetime.fromisoformat(str(event["scheduled_entry_open_jst"]))
        exit_ = datetime.fromisoformat(str(event["scheduled_exit_open_jst"]))
        result = engine.run(
            bars,
            R049FixedTimeStrategy(
                f"r070_{name}_{target.isoformat()}", entry, exit_, direction, entry_delay_minutes
            ),
            parameter_hash=canonical_hash({"profile": name, "event": event, "direction": direction}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: expected exactly one completed fixed-time trade")
        trade = result.trades[0]
        if (
            trade.entry_ts.isoformat() != event["entry_open_jst"]
            or trade.exit_ts != exit_
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
            or trade.net_pnl_jpy != trade.gross_pnl_jpy - trade.fees_jpy
        ):
            raise ValueError(f"{name}/{target}: execution/accounting contract mismatch")
        trades.append(trade)
        audit[target.isoformat()] = "TRADED"
    ordered = tuple(sorted(trades, key=lambda item: item.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), {"per_trade_date": audit}, events


def summary(
    axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], audit: dict[str, object]
) -> dict[str, object]:
    observed = [value for value in daily.values() if value is not None]
    years = {
        str(year): sum(value or 0 for key, value in daily.items() if key.startswith(str(year)))
        for year in range(2021, 2026)
    }
    return {
        "metrics": ledger_metrics(trades),
        "daily_net_pnl_jpy": daily,
        "daily_mean_net_jpy": sum(observed) / len(observed) if observed else None,
        "net_pnl_by_year_jpy": years,
        "scheduled_axis_observations": len(axis),
        "unknown_outcome_trade_dates": [key for key, value in daily.items() if value is None],
        "execution_audit": audit,
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r070_q001_night_risk_premium.py"),
        Path("src/n225m_bt/research/r070_night_risk_premium.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r070_q001.py"),
    ]
    config_files = [
        Path(f"config/{name}")
        for name in ("backtest.yaml", "data.yaml", "instrument.yaml", "sessions.yaml", "local_calendar.yaml", "research.yaml")
    ]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    partitions = partition_paths(data_config.gold_root, "development")
    preregistration = {
        "task_id": "TASK-R070-Q001",
        "family_id": "night_unconditional_risk_premium",
        "study_id": "R070-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "bootstrap_seed": MBB_SEED,
        "prior_information_seen": "Development is repeatedly used; this is exploratory, not independent validation.",
        "implementation_correction": "Run -02 completed after the same frozen specification but incorrectly classified every one-minute delayed entry as outside the scheduled window, yielding zero delayed trades. This -03 run changes only that schedule-boundary predicate, adds a synthetic delayed-entry regression test, retains -02 immutably, and reruns every preregistered profile; no strategy rule, input period, costs, or gate is changed.",
        "duplicate_review": "R001-R069 were reviewed before price results. Earlier night studies use price-derived conditions, prior/day session information, or shorter/other holding windows. None is an unconditional one-contract long from the fixed 16:31 next eligible open after the 16:30 bar through the same trade_date 05:30 open, with fixed short as its direction control.",
        "hypothesis": "Night sessions have a positive unconditional risk premium: an unconditional fixed long held within the same night session has positive after-cost expectancy.",
        "primary_rule": "For each version-controlled night session on the Development scheduled trade-date axis, after the fixed 16:30 bar close submit one long market order; fill at the next eligible one-minute open (normally 16:31, on the preceding calendar date when applicable), and exit at the same trade_date 05:30 eligible open. A schedule where these fixed clocks are not in the night session is a known no-trade. No price condition is used.",
        "prohibited": "No Stop, Target, price condition, day-session or prior-session information, holiday-spanning hold, re-entry, WFA, OOS, or Final Holdout.",
        "controls": "Primary control is no trade (JPY0 on the scheduled axis). Direction control is fixed short with identical entry and exit times.",
        "costs": "Baseline is one adverse tick plus JPY30 fee per side; gross contains fill slippage once and Net=gross-fees.",
        "s2_gate": "Entry/exit-executable trade dates >=900 and each 2021-2024 >=180. S2 reports availability only; failure is INCONCLUSIVE and stops before PnL.",
        "primary_gate": "Long Net>0, PF>1, and lower bound>0 for 20 trade-date non-wrapping MBB (10,000 repetitions) of scheduled-axis daily mean Net.",
        "sensitivities": "Only entry 17:00; exit 05:00; exit 05:45; primary entry delayed one minute without exit extension; 2/3 ticks per side; and fee x2.",
        "candidate_gate": "After passing the primary gate: 2-tick Net>0, delayed-entry Net>0, all three time sensitivities have positive daily mean Net, and Net>0 for 2025H1 plus at least three individual years in 2021-2024.",
        "scheduled_axis": "All version-controlled Development trade dates 2021-01-01..2025-06-30. Known nonexecution is JPY0; a filled entry with unavailable later exit is null, not zero.",
        "execution_contract": "Bar-close order decision, next eligible one-minute open, fixed scheduled exit, one contract/max one position. Calendar date and trade_date remain distinct. Missing exit data cannot retroactively cancel a filled entry.",
        "oos": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
        "input_partitions": [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partitions],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {"run_id": RUN_ID, "preregistration_hash": canonical_hash(preregistration), "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "S0_S1_FROZEN"})
    for directory, files in ((OUT / "source_snapshot", source_files), (OUT / "config_snapshot", config_files)):
        directory.mkdir()
        for path in files:
            shutil.copy2(ROOT / path, directory / path.name)
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r070_q001.py", "tests/test_r049_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r070_night_risk_premium.py", str(source_files[0])],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION"})
        raise ValueError("pre-execution validation failed")

    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    development = load_split(data_config.gold_root, "development")
    axis = scheduled_axis(calendar)
    bars_by_day: dict[date, list[Bar]] = defaultdict(list)
    for bar in development.bars:
        if bar.session.value == "night" and bar.trade_date in set(axis):
            bars_by_day[bar.trade_date].append(bar)
    write_json(OUT / "access_ledger.json", {"stage": "S2 then S3", "split": "development", "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()], "physical_partitions": development.quality["partitions"], "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    s2 = feasibility(axis, bars_by_day, classifier)
    write_json(OUT / "s2_feasibility.json", s2)
    s2_gate = cast(dict[str, object], s2["gate"])
    if not bool(s2_gate["passed"]):
        s2_decision = {"status": "INCONCLUSIVE", "reason": "S2_GATE_FAILED", "s2_gate": s2_gate, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", s2_decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **s2_decision})
        return

    def config(ticks: int = 1, fee: int = 30) -> Any:
        return baseline.model_copy(update={"mode": "night_only", "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}), "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}), "risk": baseline.risk.model_copy(update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0})})

    profiles: dict[str, tuple[str, time, time, int, int, int]] = {
        "long_primary": ("long", PRIMARY_ENTRY, PRIMARY_EXIT, 0, 1, 30),
        "short_direction_control": ("short", PRIMARY_ENTRY, PRIMARY_EXIT, 0, 1, 30),
        "entry_1700": ("long", time(17, 0), PRIMARY_EXIT, 0, 1, 30),
        "exit_0500": ("long", PRIMARY_ENTRY, time(5, 0), 0, 1, 30),
        "exit_0545": ("long", PRIMARY_ENTRY, time(5, 45), 0, 1, 30),
        "entry_delay_1m": ("long", PRIMARY_ENTRY, PRIMARY_EXIT, 1, 1, 30),
        "cost_2tick": ("long", PRIMARY_ENTRY, PRIMARY_EXIT, 0, 2, 30),
        "cost_3tick": ("long", PRIMARY_ENTRY, PRIMARY_EXIT, 0, 3, 30),
        "fee_x2": ("long", PRIMARY_ENTRY, PRIMARY_EXIT, 0, 1, 60),
    }
    reports: dict[str, dict[str, object]] = {}
    all_events: dict[str, dict[str, dict[str, object]]] = {}
    for name, (direction, entry, exit_, delay, ticks, fee) in profiles.items():
        engine = BacktestEngine(instrument.instrument.to_spec(), config(ticks, fee), classifier)
        trades, daily, audit, events = profile(axis, bars_by_day, classifier, engine, name=name, direction=direction, entry_time=entry, exit_time=exit_, entry_delay_minutes=delay)
        report = summary(axis, trades, daily, audit)
        reports[name] = report
        all_events[name] = events
        write_json(OUT / f"{name}_trades.json", serialise_trades(trades))
        write_json(OUT / f"{name}_daily_axis.json", daily)
    write_json(OUT / "events.json", all_events)
    write_json(OUT / "profiles.json", reports)
    primary = reports["long_primary"]
    decision: dict[str, object]
    if primary["unknown_outcome_trade_dates"]:
        decision = {"status": "INCONCLUSIVE", "reason": "UNKNOWN_FILLED_EXIT_OUTCOMES", "unknown_trade_dates": primary["unknown_outcome_trade_dates"]}
    else:
        primary_daily = cast(dict[str, int | None], primary["daily_net_pnl_jpy"])
        if any(value is None for value in primary_daily.values()):
            raise ValueError("unknown primary outcome bypassed R070 decision guard")
        daily_values = [int(cast(int, value)) for value in primary_daily.values()]
        bootstrap = mbb_mean_ci(daily_values)
        write_json(OUT / "primary_mbb.json", bootstrap)
        primary_metrics = cast(dict[str, int | float | None], primary["metrics"])
        primary_ci = cast(list[float], bootstrap["ci95_percentile_linear"])
        primary_pass = bool(primary_metrics["net_pnl_jpy"] and primary_metrics["net_pnl_jpy"] > 0 and (primary_metrics["profit_factor"] or 0) > 1 and primary_ci[0] > 0)
        time_names = ("entry_1700", "exit_0500", "exit_0545")
        year_net = cast(dict[str, int], primary["net_pnl_by_year_jpy"])
        cost_2_metrics = cast(dict[str, int | float | None], reports["cost_2tick"]["metrics"])
        delayed_metrics = cast(dict[str, int | float | None], reports["entry_delay_1m"]["metrics"])
        candidate_checks = {
            "primary_gate": primary_pass,
            "cost_2tick_net_positive": bool(cost_2_metrics["net_pnl_jpy"] and cost_2_metrics["net_pnl_jpy"] > 0),
            "delayed_entry_net_positive": bool(delayed_metrics["net_pnl_jpy"] and delayed_metrics["net_pnl_jpy"] > 0),
            "all_three_time_sensitivities_mean_positive": all(bool(cast(float | None, reports[name]["daily_mean_net_jpy"]) and cast(float, reports[name]["daily_mean_net_jpy"]) > 0) for name in time_names),
            "at_least_three_positive_2021_2024": sum(year_net[str(year)] > 0 for year in range(2021, 2025)) >= 3,
            "positive_2025_h1": year_net["2025"] > 0,
        }
        decision = {"status": "CANDIDATE" if all(candidate_checks.values()) else ("REJECT" if not primary_pass else "INVESTIGATE"), "primary_gate_passed": primary_pass, "candidate_checks": candidate_checks, "primary_mbb": bootstrap, "oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision, "holdout_read": False})


if __name__ == "__main__":
    main()
