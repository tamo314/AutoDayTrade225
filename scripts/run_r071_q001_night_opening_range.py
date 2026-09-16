"""Execute the frozen Development-only R071-Q001 experiment."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from sys import executable
from typing import Any, cast

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import ledger_metrics
from n225m_bt.research.r071_night_opening_range import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    feasibility,
    mbb_mean_ci,
    night_opening_range_event,
    scheduled_axis,
)
from n225m_bt.strategies.r071_night_opening_range import R071NightOpeningRangeStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r071-q001-20260915-night-opening-range-continuation-02"
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
    bars_by_day: dict[date, list[Any]],
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    *,
    name: str,
    opening_minutes: int = 30,
    search_end_hour: int = 23,
    search_end_minute: int = 30,
    entry_delay_minutes: int = 0,
    reverse_direction: bool = False,
) -> tuple[tuple[Trade, ...], dict[str, int | None], dict[str, object], dict[str, dict[str, object]]]:
    """Replay causal signals, retaining filled-but-unknown exits as null."""
    from datetime import time

    trades: list[Trade] = []
    unknown: set[date] = set()
    audit: dict[str, str] = {}
    events: dict[str, dict[str, object]] = {}
    for target in axis:
        event = night_opening_range_event(
            target,
            bars_by_day.get(target, []),
            classifier,
            opening_minutes=opening_minutes,
            search_end=time(search_end_hour, search_end_minute),
            entry_delay_minutes=entry_delay_minutes,
            reverse_direction=reverse_direction,
        )
        events[target.isoformat()] = event
        status = str(event["status"])
        if status in {"NO_SCHEDULED_NIGHT_WINDOW", "SKIPPED", "ENTRY_CANCELLED"}:
            audit[target.isoformat()] = status
            continue
        if status == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            audit[target.isoformat()] = status
            continue
        if status != "EXECUTABLE":
            raise ValueError(f"{name}/{target}: unexpected event status {status}")
        strategy = R071NightOpeningRangeStrategy(
            f"r071_{name}_{target.isoformat()}",
            range_start=datetime.fromisoformat(str(event["opening_range_start_jst"])),
            opening_minutes=opening_minutes,
            search_end=datetime.fromisoformat(str(event["scheduled_search_end_jst"])),
            exit_open=datetime.fromisoformat(str(event["scheduled_exit_open_jst"])),
            entry_delay_minutes=entry_delay_minutes,
            reverse_direction=reverse_direction,
        )
        result = engine.run(
            bars_by_day.get(target, []),
            strategy,
            parameter_hash=canonical_hash({"profile": name, "event": event}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: expected exactly one completed trade")
        trade = result.trades[0]
        expected_side = Side(str(event["execution_direction"]))
        if (
            trade.entry_ts.isoformat() != event["entry_open_jst"]
            or trade.exit_ts.isoformat() != event["exit_open_jst"]
            or trade.side is not expected_side
            or trade.exit_reason is not ExitReason.SIGNAL
        ):
            raise ValueError(f"{name}/{target}: event/engine execution mismatch")
        trades.append(trade)
        audit[target.isoformat()] = "EXECUTABLE"
    ordered = tuple(sorted(trades, key=lambda trade: trade.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), {"per_trade_date": audit}, events


def summary(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None]) -> dict[str, object]:
    observed = [value for value in daily.values() if value is not None]
    year_net = {
        str(year): sum(value or 0 for key, value in daily.items() if key.startswith(str(year)))
        for year in range(2021, 2026)
    }
    unknown = [key for key, value in daily.items() if value is None]
    return {
        "partial_observed_metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "observed_axis_observations": len(observed),
        "unknown_outcome_trade_dates": unknown,
        "partial_observed_net_pnl_jpy": sum(observed),
        "partial_observed_daily_mean_net_jpy": sum(observed) / len(observed) if observed else None,
        "net_pnl_by_year_jpy_if_complete": year_net if not unknown else None,
        "daily_net_pnl_jpy": daily,
    }


def profile_mean(report: dict[str, object]) -> float | None:
    if cast(list[str], report["unknown_outcome_trade_dates"]):
        return None
    values = cast(dict[str, int | None], report["daily_net_pnl_jpy"]).values()
    return sum(cast(int, value) for value in values) / len(values)


def net_is_positive(report: dict[str, object]) -> bool:
    net = cast(dict[str, int | float | None], report["partial_observed_metrics"])["net_pnl_jpy"]
    return net is not None and net > 0


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r071_q001_night_opening_range.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r071_q001_night_opening_range.py"),
        Path("src/n225m_bt/research/r071_night_opening_range.py"),
        Path("src/n225m_bt/strategies/r071_night_opening_range.py"),
        Path("tests/test_r071_q001.py"),
    ]
    preregistration_document = Path("docs/strategy/22_r071_q001_night_opening_range_continuation.md")
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
        "task_id": "TASK-R071-Q001",
        "family_id": "night_opening_price_discovery",
        "study_id": "R071-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "preregistration_document": str(preregistration_document),
        "preregistration_document_sha256": digest(ROOT / preregistration_document),
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10_000,
            "method": "non-wrapping MBB; tail truncation; linear percentile",
        },
        "prior_information_seen": True,
        "implementation_correction": "The immutable -01 attempt stopped on 2021-01-04 before any aggregate PnL result because the adapter invalidated a filled position on a later gap and the event constructor admitted a multi-calendar-day holiday span. -02 adds the preregistered no-holiday-spanning schedule predicate, permits fixed exit after a filled entry despite an intervening non-exit gap, and adds a synthetic regression. The hypothesis, signal, costs, gates, profiles, data split, and seed are unchanged.",
        "non_overlap_review": "R070 is unconditional fixed-time night direction; R069 is day-session opening range. R071 alone selects the first strict 16:30-16:59 night opening-range close breakout through 23:30 and holds it to 05:30, with an opposite-direction paired mechanism control.",
        "hypothesis": "The first strict night opening-range breakout reflects overseas information discovery and continues in the breakout direction through the remaining night session.",
        "primary_rule": "Freeze high/low from eligible 16:30-16:59 bars, enter the first strict close breakout between 17:00 and 23:30 at the next eligible open, and exit at same-trade-date 05:30 eligible open.",
        "prohibited": "No stop, target, day/prior-session input, breakout-width threshold, re-entry, holiday-spanning hold, or unregistered profile.",
        "controls": "No trade JPY0 primary control; paired false-breakout is the same event and entry/exit with only execution direction reversed.",
        "costs": "One adverse tick plus JPY30 fee per side; Net=gross_fill-fees without double-counting slippage.",
        "s2_gate": ">=400 executable signal dates, >=60 in each 2021-2024 year, and >=120 each long/short; failure stops as INCONCLUSIVE before PnL.",
        "primary_gate": "Net>0; PF>1; primary daily mean MBB CI95 lower>0; paired primary-minus-false-breakout daily difference MBB CI95 lower>0.",
        "sensitivity_profiles": [
            "opening_20m",
            "opening_45m",
            "deadline_2230",
            "deadline_0030",
            "entry_delay_1m",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "candidate_gate": "Primary gate plus 2tick Net>0; delayed Net>0; all four window/deadline daily means>0; long and short group Net>0; >=3 of 2021-2024 and 2025H1 Net>0.",
        "scheduled_axis": "All version-controlled Development trade dates 2021-01-01..2025-06-30; known no-trade=0 and filled unknown exit=null.",
        "input_partitions": [str(path.resolve().relative_to(ROOT)) for path in partition_paths(data_config.gold_root, "development")],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "oos": "NOT_ACCESSED",
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
    for directory, files in ((OUT / "source_snapshot", source_files), (OUT / "config_snapshot", config_files)):
        directory.mkdir()
        for path in files:
            shutil.copy2(ROOT / path, directory / path.name)
    documentation_snapshot = OUT / "documentation_snapshot"
    documentation_snapshot.mkdir()
    shutil.copy2(ROOT / preregistration_document, documentation_snapshot / preregistration_document.name)
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r071_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r071_night_opening_range.py",
            "src/n225m_bt/strategies/r071_night_opening_range.py",
            str(source_files[0]),
        ],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
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
    bars_by_day: dict[date, list[Any]] = defaultdict(list)
    for bar in development.bars:
        if bar.session.value == "night" and bar.trade_date in set(axis):
            bars_by_day[bar.trade_date].append(bar)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 then conditional S3",
            "split": "development",
            "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
            "physical_partitions": development.quality["partitions"],
            "data_version": development.data_version,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    s2 = feasibility(axis, bars_by_day, classifier)
    write_json(OUT / "s2_feasibility.json", s2)
    s2_gate = cast(dict[str, object], s2["gate"])
    if not bool(s2_gate["passed"]):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "S2_GATE_FAILED",
            "s2_gate": s2_gate,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return

    def engine_for(ticks: int = 1, fee: int = 30) -> BacktestEngine:
        config = baseline.model_copy(
            update={
                "mode": "night_only",
                "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
                "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
                "risk": baseline.risk.model_copy(
                    update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
                ),
            }
        )
        return BacktestEngine(instrument.instrument.to_spec(), config, classifier)

    profiles = {
        "primary": (30, 23, 30, 0, False, 1, 30),
        "false_breakout_control": (30, 23, 30, 0, True, 1, 30),
        "opening_20m": (20, 23, 30, 0, False, 1, 30),
        "opening_45m": (45, 23, 30, 0, False, 1, 30),
        "deadline_2230": (30, 22, 30, 0, False, 1, 30),
        "deadline_0030": (30, 0, 30, 0, False, 1, 30),
        "entry_delay_1m": (30, 23, 30, 1, False, 1, 30),
        "cost_2tick": (30, 23, 30, 0, False, 2, 30),
        "cost_3tick": (30, 23, 30, 0, False, 3, 30),
        "fee_x2": (30, 23, 30, 0, False, 1, 60),
    }
    reports: dict[str, dict[str, object]] = {}
    all_events: dict[str, dict[str, dict[str, object]]] = {}
    trades_by_profile: dict[str, tuple[Trade, ...]] = {}
    for name, (minutes, hour, minute, delay, reverse, ticks, fee) in profiles.items():
        trades, daily, audit, events = profile(
            axis,
            bars_by_day,
            classifier,
            engine_for(ticks, fee),
            name=name,
            opening_minutes=minutes,
            search_end_hour=hour,
            search_end_minute=minute,
            entry_delay_minutes=delay,
            reverse_direction=reverse,
        )
        reports[name] = summary(axis, trades, daily) | {"execution_audit": audit}
        all_events[name] = events
        trades_by_profile[name] = trades
        write_json(OUT / f"{name}_trades.json", serialise_trades(trades))
        write_json(OUT / f"{name}_daily_axis.json", daily)
    write_json(OUT / "events.json", all_events)
    write_json(OUT / "profiles.json", reports)

    primary, control = reports["primary"], reports["false_breakout_control"]
    primary_unknown = cast(list[str], primary["unknown_outcome_trade_dates"])
    control_unknown = cast(list[str], control["unknown_outcome_trade_dates"])
    audit = {
        "one_trade_per_trade_date": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in trades_by_profile.values()
        ),
        "primary_control_same_dates_entry_exit_opposite_side": {
            trade.trade_date: (trade.entry_ts, trade.exit_ts, trade.side.value)
            for trade in trades_by_profile["primary"]
        }
        == {
            trade.trade_date: (
                trade.entry_ts,
                trade.exit_ts,
                "short" if trade.side.value == "long" else "long",
            )
            for trade in trades_by_profile["false_breakout_control"]
        },
        "no_stop_or_target": all(
            trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET}
            for trades in trades_by_profile.values()
            for trade in trades
        ),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by_profile.values()
            for trade in trades
        ),
    }
    if not all(audit.values()):
        raise ValueError("R071 execution/accounting audit failed")
    write_json(OUT / "execution_accounting_audit.json", audit)
    if primary_unknown or control_unknown:
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "UNKNOWN_FILLED_EXIT_OUTCOMES",
            "primary_unknown_trade_dates": primary_unknown,
            "control_unknown_trade_dates": control_unknown,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
    else:
        primary_daily = cast(dict[str, int | None], primary["daily_net_pnl_jpy"])
        control_daily = cast(dict[str, int | None], control["daily_net_pnl_jpy"])
        primary_values = [cast(int, value) for value in primary_daily.values()]
        contrast_values = [
            cast(int, primary_daily[key]) - cast(int, control_daily[key]) for key in primary_daily
        ]
        primary_mbb, contrast_mbb = mbb_mean_ci(primary_values), mbb_mean_ci(contrast_values)
        write_json(OUT / "primary_mbb.json", primary_mbb)
        write_json(OUT / "primary_minus_false_breakout_mbb.json", contrast_mbb)
        primary_metrics = cast(dict[str, int | float | None], primary["partial_observed_metrics"])
        primary_ci = cast(list[float], primary_mbb["ci95_percentile_linear"])
        contrast_ci = cast(list[float], contrast_mbb["ci95_percentile_linear"])
        gates = {
            "net_positive": bool(primary_metrics["net_pnl_jpy"] and primary_metrics["net_pnl_jpy"] > 0),
            "pf_gt_one": bool((primary_metrics["profit_factor"] or 0) > 1),
            "primary_mbb_ci95_lower_gt_zero": primary_ci[0] > 0,
            "primary_minus_false_breakout_mbb_ci95_lower_gt_zero": contrast_ci[0] > 0,
        }
        primary_pass = all(gates.values())
        year_net = cast(dict[str, int], primary["net_pnl_by_year_jpy_if_complete"])
        by_side: defaultdict[str, int] = defaultdict(int)
        for trade in trades_by_profile["primary"]:
            by_side[trade.side.value] += trade.net_pnl_jpy
        candidate_checks = {
            "primary_gate": primary_pass,
            "cost_2tick_net_positive": net_is_positive(reports["cost_2tick"]),
            "delayed_entry_net_positive": net_is_positive(reports["entry_delay_1m"]),
            "all_four_window_deadline_means_positive": all(
                (profile_mean(reports[name]) or 0) > 0
                for name in ("opening_20m", "opening_45m", "deadline_2230", "deadline_0030")
            ),
            "long_group_net_positive": by_side["long"] > 0,
            "short_group_net_positive": by_side["short"] > 0,
            "at_least_three_positive_2021_2024": sum(year_net[str(year)] > 0 for year in range(2021, 2025)) >= 3,
            "positive_2025_h1": year_net["2025"] > 0,
        }
        decision = {
            "status": "CANDIDATE"
            if all(candidate_checks.values())
            else ("INVESTIGATE" if primary_pass else "REJECT"),
            "s2_gate": s2_gate,
            "s3_gates": gates,
            "candidate_checks": candidate_checks,
            "primary_mbb": primary_mbb,
            "primary_minus_false_breakout_mbb": contrast_mbb,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
