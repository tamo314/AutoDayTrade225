"""Execute the frozen Development-only TASK-R072-Q001 experiment."""

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
from n225m_bt.domain import Bar, ExitReason, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import ledger_metrics
from n225m_bt.research.r072_night_direction_day_reversal import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    feasibility,
    mbb_mean_ci,
    night_direction_day_reversal_event,
    scheduled_axis,
)
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r072-q001-20260915-night-direction-day-reversal-01"
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
    night_end: time = time(5, 30),
    entry: time = time(9, 0),
    exit_: time = time(14, 30),
    reverse_direction: bool = True,
) -> tuple[tuple[Trade, ...], dict[str, int | None], dict[str, object], dict[str, dict[str, object]]]:
    """Replay a 05:30-fixed side while retaining unknown filled exits as null."""
    trades: list[Trade] = []
    unknown: set[date] = set()
    audit: dict[str, str] = {}
    events: dict[str, dict[str, object]] = {}
    for target in axis:
        event = night_direction_day_reversal_event(
            target,
            bars_by_day.get(target, []),
            classifier,
            night_end=night_end,
            entry=entry,
            exit_=exit_,
            reverse_direction=reverse_direction,
        )
        events[target.isoformat()] = event
        status = str(event["status"])
        if status in {"NO_SCHEDULED_CROSS_SESSION_WINDOW", "SKIPPED", "ENTRY_CANCELLED"}:
            audit[target.isoformat()] = status
            continue
        if status == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            audit[target.isoformat()] = status
            continue
        if status != "EXECUTABLE":
            raise ValueError(f"{name}/{target}: unexpected event status {status}")
        entry_open = datetime.fromisoformat(str(event["scheduled_entry_open_jst"]))
        exit_open = datetime.fromisoformat(str(event["scheduled_exit_open_jst"]))
        result = engine.run(
            bars_by_day.get(target, []),
            R049FixedTimeStrategy(
                f"r072_{name}_{target.isoformat()}",
                entry_open,
                exit_open,
                str(event["execution_direction"]),
            ),
            parameter_hash=canonical_hash({"profile": name, "event": event}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: expected exactly one completed day trade")
        trade = result.trades[0]
        if (
            trade.entry_ts != entry_open
            or trade.exit_ts != exit_open
            or trade.side is not Side(str(event["execution_direction"]))
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{name}/{target}: event/engine execution mismatch")
        trades.append(trade)
        audit[target.isoformat()] = "TRADED"
    ordered = tuple(sorted(trades, key=lambda item: item.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), {"per_trade_date": audit}, events


def summary(axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None]) -> dict[str, object]:
    observed = [value for value in daily.values() if value is not None]
    unknown = [key for key, value in daily.items() if value is None]
    return {
        "partial_observed_metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "observed_axis_observations": len(observed),
        "unknown_outcome_trade_dates": unknown,
        "partial_observed_net_pnl_jpy": sum(observed),
        "partial_observed_daily_mean_net_jpy": sum(observed) / len(observed) if observed else None,
        "net_pnl_by_year_jpy_if_complete": (
            {
                str(year): sum(value or 0 for key, value in daily.items() if key.startswith(str(year)))
                for year in range(2021, 2026)
            }
            if not unknown
            else None
        ),
        "daily_net_pnl_jpy": daily,
    }


def net_is_positive(report: dict[str, object]) -> bool:
    metrics = cast(dict[str, int | float | None], report["partial_observed_metrics"])
    return metrics["net_pnl_jpy"] is not None and metrics["net_pnl_jpy"] > 0


def mean_is_positive(report: dict[str, object]) -> bool:
    if cast(list[str], report["unknown_outcome_trade_dates"]):
        return False
    daily = cast(dict[str, int | None], report["daily_net_pnl_jpy"])
    return sum(cast(int, value) for value in daily.values()) / len(daily) > 0


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r072_q001_night_direction_day_reversal.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r072_q001_night_direction_day_reversal.py"),
        Path("src/n225m_bt/research/r072_night_direction_day_reversal.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r072_q001.py"),
    ]
    preregistration_document = Path("docs/strategy/24_r072_q001_night_direction_day_reversal.md")
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
        "task_id": "TASK-R072-Q001",
        "family_id": "night_directional_inventory_reversal",
        "study_id": "R072-Q001",
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
        "non_overlap_review": "R070 is unconditional within-night long; R071 is a night opening-range continuation; R062 uses a threshold plus a post-day-open 15-minute rejection condition. R072 alone uses the unthresholded 16:30-to-05:30 night-open direction, fixed at 05:30, to reverse only during 09:00-to-14:30 of the next day session.",
        "hypothesis": "A night-session direction contains temporary inventory/sentiment pressure that reverses in the following day session.",
        "primary_rule": "For a versioned night session containing 16:30 and 05:30, compare those eligible bar opens. Positive means sell and negative means buy at 09:00 eligible day open; equal means no trade. Fix the side at 05:30 and exit at 14:30 eligible day open.",
        "prohibited": "No stop, target, post-day-open price input, threshold, re-entry, or holiday-spanning position.",
        "controls": "Primary control is no trade (JPY0 on the scheduled axis). Co-primary mechanism control is the same selected date and entry/exit with the night direction instead of its reversal.",
        "costs": "One adverse tick plus JPY30 fee per side; Net=gross_fill-fees with slippage included once in gross_fill.",
        "s2_gate": ">=900 executable trades, >=180 in each 2021-2024 year, and >=300 each buy/sell; failure stops as INCONCLUSIVE before PnL.",
        "primary_gate": "Reversal Net>0; PF>1; reversal scheduled-axis daily-mean MBB CI95 lower>0; paired reversal-minus-momentum daily-difference MBB CI95 lower>0.",
        "sensitivity_profiles": [
            "night_end_0500",
            "entry_0901",
            "exit_1415",
            "exit_1445",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "candidate_gate": "Primary gate plus cost_2tick Net>0; entry_0901 Net>0; positive daily mean for night_end_0500, entry_0901, exit_1415, and exit_1445; buy and sell Net>0; >=3 positive 2021-2024 years; positive 2025H1.",
        "scheduled_axis": "All version-controlled Development trade dates 2021-01-01..2025-06-30. Known nonexecution is JPY0; a filled entry with unavailable later exit is null, not zero.",
        "execution_contract": "05:30 fixed direction; no day price is used to select side; 08:59 is only a timer activation for the existing bar-close/next-open engine; one contract/max one day-only position; fixed exit; no exit-driven entry cancellation.",
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
        "pytest": [executable, "-m", "pytest", "tests/test_r072_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r072_night_direction_day_reversal.py",
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
    axis_set = set(axis)
    bars_by_day: dict[date, list[Bar]] = defaultdict(list)
    for bar in development.bars:
        if bar.trade_date in axis_set and bar.session.value in {"night", "day"}:
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
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return

    def engine_for(ticks: int = 1, fee: int = 30) -> BacktestEngine:
        config = baseline.model_copy(
            update={
                "mode": "day_only",
                "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
                "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
                "risk": baseline.risk.model_copy(
                    update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
                ),
            }
        )
        return BacktestEngine(instrument.instrument.to_spec(), config, classifier)

    profiles = {
        "reversal_primary": (time(5, 30), time(9, 0), time(14, 30), True, 1, 30),
        "night_direction_momentum": (time(5, 30), time(9, 0), time(14, 30), False, 1, 30),
        "night_end_0500": (time(5, 0), time(9, 0), time(14, 30), True, 1, 30),
        "entry_0901": (time(5, 30), time(9, 1), time(14, 30), True, 1, 30),
        "exit_1415": (time(5, 30), time(9, 0), time(14, 15), True, 1, 30),
        "exit_1445": (time(5, 30), time(9, 0), time(14, 45), True, 1, 30),
        "cost_2tick": (time(5, 30), time(9, 0), time(14, 30), True, 2, 30),
        "cost_3tick": (time(5, 30), time(9, 0), time(14, 30), True, 3, 30),
        "fee_x2": (time(5, 30), time(9, 0), time(14, 30), True, 1, 60),
    }
    reports: dict[str, dict[str, object]] = {}
    all_events: dict[str, dict[str, dict[str, object]]] = {}
    trades_by_profile: dict[str, tuple[Trade, ...]] = {}
    for name, (night_end, entry, exit_, reverse, ticks, fee) in profiles.items():
        trades, daily, audit, events = profile(
            axis,
            bars_by_day,
            classifier,
            engine_for(ticks, fee),
            name=name,
            night_end=night_end,
            entry=entry,
            exit_=exit_,
            reverse_direction=reverse,
        )
        reports[name] = summary(axis, trades, daily) | {"execution_audit": audit}
        all_events[name] = events
        trades_by_profile[name] = trades
        write_json(OUT / f"{name}_trades.json", serialise_trades(trades))
        write_json(OUT / f"{name}_daily_axis.json", daily)
    write_json(OUT / "events.json", all_events)
    write_json(OUT / "profiles.json", reports)

    primary, momentum = reports["reversal_primary"], reports["night_direction_momentum"]
    audit = {
        "one_trade_per_trade_date": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in trades_by_profile.values()
        ),
        "primary_momentum_same_date_entry_exit_opposite_side": {
            trade.trade_date: (trade.entry_ts, trade.exit_ts, trade.side.value)
            for trade in trades_by_profile["reversal_primary"]
        }
        == {
            trade.trade_date: (
                trade.entry_ts,
                trade.exit_ts,
                "short" if trade.side is Side.LONG else "long",
            )
            for trade in trades_by_profile["night_direction_momentum"]
        },
        "day_only_no_holiday_spanning_position": all(
            trade.entry_ts.date() == trade.exit_ts.date() == trade.trade_date
            for trades in trades_by_profile.values()
            for trade in trades
        ),
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
        raise ValueError("R072 execution/accounting audit failed")
    write_json(OUT / "execution_accounting_audit.json", audit)
    primary_unknown = cast(list[str], primary["unknown_outcome_trade_dates"])
    momentum_unknown = cast(list[str], momentum["unknown_outcome_trade_dates"])
    if primary_unknown or momentum_unknown:
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "UNKNOWN_FILLED_EXIT_OUTCOMES",
            "primary_unknown_trade_dates": primary_unknown,
            "momentum_unknown_trade_dates": momentum_unknown,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
    else:
        primary_daily = cast(dict[str, int | None], primary["daily_net_pnl_jpy"])
        momentum_daily = cast(dict[str, int | None], momentum["daily_net_pnl_jpy"])
        primary_values = [cast(int, value) for value in primary_daily.values()]
        contrast_values = [
            cast(int, primary_daily[key]) - cast(int, momentum_daily[key]) for key in primary_daily
        ]
        primary_mbb, contrast_mbb = mbb_mean_ci(primary_values), mbb_mean_ci(contrast_values)
        write_json(OUT / "primary_mbb.json", primary_mbb)
        write_json(OUT / "primary_minus_momentum_mbb.json", contrast_mbb)
        primary_metrics = cast(dict[str, int | float | None], primary["partial_observed_metrics"])
        primary_ci = cast(list[float], primary_mbb["ci95_percentile_linear"])
        contrast_ci = cast(list[float], contrast_mbb["ci95_percentile_linear"])
        gates = {
            "net_positive": bool(primary_metrics["net_pnl_jpy"] and primary_metrics["net_pnl_jpy"] > 0),
            "pf_gt_one": bool((primary_metrics["profit_factor"] or 0) > 1),
            "primary_mbb_ci95_lower_gt_zero": primary_ci[0] > 0,
            "primary_minus_momentum_mbb_ci95_lower_gt_zero": contrast_ci[0] > 0,
        }
        primary_pass = all(gates.values())
        year_net = cast(dict[str, int], primary["net_pnl_by_year_jpy_if_complete"])
        by_side: defaultdict[str, int] = defaultdict(int)
        for trade in trades_by_profile["reversal_primary"]:
            by_side[trade.side.value] += trade.net_pnl_jpy
        candidate_checks = {
            "primary_gate": primary_pass,
            "cost_2tick_net_positive": net_is_positive(reports["cost_2tick"]),
            "entry_0901_net_positive": net_is_positive(reports["entry_0901"]),
            "night_endpoint_axis_mean_positive": mean_is_positive(reports["night_end_0500"]),
            "entry_axis_mean_positive": mean_is_positive(reports["entry_0901"]),
            "exit_axis_both_registered_means_positive": all(
                mean_is_positive(reports[name]) for name in ("exit_1415", "exit_1445")
            ),
            "buy_group_net_positive": by_side["long"] > 0,
            "sell_group_net_positive": by_side["short"] > 0,
            "at_least_three_positive_2021_2024": sum(
                year_net[str(year)] > 0 for year in range(2021, 2025)
            ) >= 3,
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
            "primary_minus_momentum_mbb": contrast_mbb,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
