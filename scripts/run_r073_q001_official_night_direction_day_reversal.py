"""Execute the frozen Development-only TASK-R073-Q001 experiment."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from sys import argv, executable
from typing import Any, cast

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r073_official_night_direction_day_reversal import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    aligned_daily_net,
    feasibility,
    mbb_mean_ci,
    official_night_direction_event,
    q002_feasibility,
    scheduled_axis,
)
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r073-q001-20260915-official-night-direction-day-reversal-01"
OUT = ROOT / "results" / "research" / RUN_ID


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def profile(
    axis: list[date],
    bars_by_day: dict[
        date,
        list[Bar],
    ],
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    *,
    name: str,
    night_end: time = time(5, 30),
    entry: time = time(9),
    exit_: time = time(14, 30),
    reverse: bool = True,
) -> tuple[tuple[Trade, ...], dict[str, int | None], dict[str, dict[str, object]]]:
    trades: list[Trade] = []
    unknown: set[date] = set()
    events: dict[str, dict[str, object]] = {}
    for target in axis:
        event = official_night_direction_event(
            target,
            bars_by_day.get(target, []),
            classifier,
            night_end=night_end,
            entry=entry,
            exit_=exit_,
            reverse_direction=reverse,
        )
        events[target.isoformat()] = event
        status = str(event["status"])
        if status in {"NO_SCHEDULED_CROSS_SESSION_WINDOW", "SKIPPED", "ENTRY_CANCELLED"}:
            continue
        if status == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
            continue
        if status != "EXECUTABLE":
            raise ValueError(f"{name}/{target}: unexpected event state {status}")
        entry_open = datetime.fromisoformat(str(event["scheduled_entry_open_jst"]))
        exit_open = datetime.fromisoformat(str(event["scheduled_exit_open_jst"]))
        result = engine.run(
            bars_by_day.get(target, []),
            R049FixedTimeStrategy(
                f"r073_{name}_{target.isoformat()}",
                entry_open,
                exit_open,
                str(event["execution_direction"]),
            ),
            parameter_hash=canonical_hash({"profile": name, "event": event}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: expected one completed day trade")
        trade = result.trades[0]
        if (
            trade.entry_ts != entry_open
            or trade.exit_ts != exit_open
            or trade.side is not Side(str(event["execution_direction"]))
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{name}/{target}: event/engine mismatch")
        trades.append(trade)
    ordered = tuple(sorted(trades, key=lambda item: item.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), events


def report(
    axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None]
) -> dict[str, object]:
    unknown = [key for key, value in daily.items() if value is None]
    observed = [value for value in daily.values() if value is not None]
    return {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "unknown_outcome_trade_dates": unknown,
        "partial_observed_net_pnl_jpy": sum(observed),
        "daily_net_pnl_jpy": daily,
        "net_pnl_by_year_jpy_if_complete": {
            str(year): sum(value or 0 for key, value in daily.items() if key.startswith(str(year)))
            for year in range(2021, 2026)
        }
        if not unknown
        else None,
    }


def net_positive(report_value: dict[str, object]) -> bool:
    value = cast(dict[str, int | float | None], report_value["metrics"])["net_pnl_jpy"]
    return value is not None and value > 0


def yearly_direction_diagnostics(axis: list[date], trades: tuple[Trade, ...]) -> dict[str, object]:
    """Save the pre-registered descriptive slices without changing any gate."""
    by_year = {
        str(year): tuple(trade for trade in trades if trade.trade_date.year == year)
        for year in range(2021, 2026)
    }
    by_side = {
        side.value: tuple(trade for trade in trades if trade.side is side)
        for side in (Side.LONG, Side.SHORT)
    }
    return {
        "by_year": {year: ledger_metrics(group) for year, group in by_year.items()},
        "by_direction": {side: ledger_metrics(group) for side, group in by_side.items()},
        "profit_concentration": concentration(trades, axis),
    }


def main(*, variant: str = "q001") -> None:
    global RUN_ID, OUT
    variants = {
        "q001": {
            "task_id": "TASK-R073-Q001",
            "study_id": "R073-Q001",
            "spec_version": "v1",
            "run_id": "r073-q001-20260915-official-night-direction-day-reversal-01",
            "document": Path("docs/strategy/27_r073_q001_official_night_direction_day_reversal.md"),
            "s2_gate": ">=900 executable; >=180 in each 2021-2024; >=300 each buy/sell; failure returns INCONCLUSIVE before PnL.",
            "feasibility": feasibility,
        },
        "q002": {
            "task_id": "TASK-R073-Q002",
            "study_id": "R073-Q002",
            "spec_version": "v1-s2-886-calendar-gate",
            "run_id": "r073-q002-20260915-official-night-direction-day-reversal-01",
            "document": Path("docs/strategy/29_r073_q002_official_night_direction_day_reversal.md"),
            "s2_gate": "TASK-R073-D001's price-independent 886 calendar-eligible dates are required; unexplained data/trade_date/implementation exclusions=0; executable non-ties >=ceil(95%*886)=842; retain >=180 in each 2021-2024 and >=300 each buy/sell; failure returns INCONCLUSIVE before PnL.",
            "feasibility": q002_feasibility,
        },
    }
    if variant not in variants:
        raise ValueError(f"unknown R073 variant: {variant}")
    specification = variants[variant]
    RUN_ID = str(specification["run_id"])
    OUT = ROOT / "results" / "research" / RUN_ID
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r073_q001_official_night_direction_day_reversal.py"),
        Path("src/n225m_bt/research/r073_official_night_direction_day_reversal.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r073_q001.py"),
    ]
    prereg_document = cast(Path, specification["document"])
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
        "task_id": specification["task_id"],
        "family_id": "night_directional_inventory_reversal",
        "study_id": specification["study_id"],
        "spec_version": specification["spec_version"],
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "preregistration_document": str(prereg_document),
        "preregistration_document_sha256": digest(ROOT / prereg_document),
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-wrapping MBB; tail truncation; linear percentile",
        },
        "prior_information_seen": True,
        "primary_rule": "Compare the actual versioned official night-session opening eligible bar open (16:30 before the 2024-11-05 change and 17:00 from it) to 05:30. Positive selects 09:00 sell, negative buy, equal no trade; side is fixed at 05:30 and exit is 14:30.",
        "scheduled_axis": "Every version-controlled Development trade_date 2021-01-01..2025-06-30; known nonexecution=JPY0 and filled missing exit=null.",
        "controls": "No-trade JPY0 primary control and same-date/same-entry/same-exit night-direction momentum co-primary control.",
        "costs": "One adverse tick plus JPY30 per side; slippage is included once in gross_fill.",
        "s2_gate": specification["s2_gate"],
        "primary_gate": "Net>0; PF>1; primary and paired reversal-minus-momentum scheduled-axis daily mean MBB CI95 lower >0.",
        "sensitivity_profiles": [
            "night_end_0500",
            "entry_0901",
            "exit_1415",
            "exit_1445",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "candidate_gate": "Primary plus 2tick and 09:01 Net>0; all registered time-axis means>0; buy/sell Net>0; >=3 positive 2021-2024 years; positive 2025H1; both official 16:30 and 17:00 regimes Net>0.",
        "oos": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
        "input_partitions": [
            str(path.resolve().relative_to(ROOT))
            for path in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
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
    for directory, files in (
        (OUT / "source_snapshot", source_files),
        (OUT / "config_snapshot", config_files),
    ):
        directory.mkdir()
        for path in files:
            shutil.copy2(ROOT / path, directory / path.name)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / prereg_document, OUT / "documentation_snapshot" / prereg_document.name)
    commands = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r073_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r073_official_night_direction_day_reversal.py",
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
    validation["status"] = (
        "PASS" if all(value["returncode"] == 0 for value in validation.values()) else "FAIL"
    )
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
        if bar.trade_date in set(axis) and bar.session.value in {"night", "day"}:
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
    feasibility_function = cast(Any, specification["feasibility"])
    s2 = feasibility_function(axis, bars_by_day, classifier)
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
        "reversal_primary": (time(5, 30), time(9), time(14, 30), True, 1, 30),
        "night_direction_momentum": (time(5, 30), time(9), time(14, 30), False, 1, 30),
        "night_end_0500": (time(5), time(9), time(14, 30), True, 1, 30),
        "entry_0901": (time(5, 30), time(9, 1), time(14, 30), True, 1, 30),
        "exit_1415": (time(5, 30), time(9), time(14, 15), True, 1, 30),
        "exit_1445": (time(5, 30), time(9), time(14, 45), True, 1, 30),
        "cost_2tick": (time(5, 30), time(9), time(14, 30), True, 2, 30),
        "cost_3tick": (time(5, 30), time(9), time(14, 30), True, 3, 30),
        "fee_x2": (time(5, 30), time(9), time(14, 30), True, 1, 60),
    }
    reports: dict[str, dict[str, object]] = {}
    events_by_profile: dict[str, dict[str, dict[str, object]]] = {}
    trades_by_profile: dict[str, tuple[Trade, ...]] = {}
    for name, (night_end, entry, exit_, reverse, ticks, fee) in profiles.items():
        trades, daily, events = profile(
            axis,
            bars_by_day,
            classifier,
            engine_for(ticks, fee),
            name=name,
            night_end=night_end,
            entry=entry,
            exit_=exit_,
            reverse=reverse,
        )
        reports[name] = report(axis, trades, daily)
        events_by_profile[name] = events
        trades_by_profile[name] = trades
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    write_json(OUT / "events.json", events_by_profile)
    write_json(OUT / "profiles.json", reports)
    write_json(
        OUT / "yearly_direction_diagnostics.json",
        {
            name: yearly_direction_diagnostics(axis, trades_by_profile[name])
            for name in ("reversal_primary", "night_direction_momentum")
        },
    )
    primary, momentum = reports["reversal_primary"], reports["night_direction_momentum"]
    audit = {
        "one_trade_per_trade_date": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in trades_by_profile.values()
        ),
        "primary_momentum_same_date_entry_exit_opposite_side": {
            (trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value)
            for trade in trades_by_profile["reversal_primary"]
        }
        == {
            (
                trade.trade_date,
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
        raise ValueError("R073 execution/accounting audit failed")
    write_json(OUT / "execution_accounting_audit.json", audit)
    if cast(list[str], primary["unknown_outcome_trade_dates"]) or cast(
        list[str], momentum["unknown_outcome_trade_dates"]
    ):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "UNKNOWN_FILLED_EXIT_OUTCOMES",
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
    else:
        primary_daily = cast(dict[str, int | None], primary["daily_net_pnl_jpy"])
        momentum_daily = cast(dict[str, int | None], momentum["daily_net_pnl_jpy"])
        primary_mbb = mbb_mean_ci([cast(int, value) for value in primary_daily.values()])
        contrast_mbb = mbb_mean_ci(
            [
                cast(int, primary_daily[key]) - cast(int, momentum_daily[key])
                for key in primary_daily
            ]
        )
        write_json(OUT / "primary_mbb.json", primary_mbb)
        write_json(OUT / "primary_minus_momentum_mbb.json", contrast_mbb)
        metrics = cast(dict[str, int | float | None], primary["metrics"])
        gates = {
            "net_positive": bool(metrics["net_pnl_jpy"] and metrics["net_pnl_jpy"] > 0),
            "pf_gt_one": bool((metrics["profit_factor"] or 0) > 1),
            "primary_mbb_ci95_lower_gt_zero": cast(
                list[float], primary_mbb["ci95_percentile_linear"]
            )[0]
            > 0,
            "primary_minus_momentum_mbb_ci95_lower_gt_zero": cast(
                list[float], contrast_mbb["ci95_percentile_linear"]
            )[0]
            > 0,
        }
        primary_pass = all(gates.values())
        year_net = cast(dict[str, int], primary["net_pnl_by_year_jpy_if_complete"])
        side_net: defaultdict[str, int] = defaultdict(int)
        regime_net: defaultdict[str, int] = defaultdict(int)
        for trade in trades_by_profile["reversal_primary"]:
            side_net[trade.side.value] += trade.net_pnl_jpy
            regime_net[
                "old_1630"
                if str(
                    events_by_profile["reversal_primary"][trade.trade_date.isoformat()][
                        "feature_start_open_jst"
                    ]
                ).endswith("16:30:00+09:00")
                else "new_1700"
            ] += trade.net_pnl_jpy
        candidate_checks = {
            "primary_gate": primary_pass,
            "cost_2tick_net_positive": net_positive(reports["cost_2tick"]),
            "entry_0901_net_positive": net_positive(reports["entry_0901"]),
            "all_time_sensitivity_means_positive": all(
                sum(
                    cast(int, value)
                    for value in cast(
                        dict[str, int | None], reports[name]["daily_net_pnl_jpy"]
                    ).values()
                )
                / len(axis)
                > 0
                for name in ("night_end_0500", "entry_0901", "exit_1415", "exit_1445")
            ),
            "buy_group_net_positive": side_net["long"] > 0,
            "sell_group_net_positive": side_net["short"] > 0,
            "at_least_three_positive_2021_2024": sum(
                year_net[str(year)] > 0 for year in range(2021, 2025)
            )
            >= 3,
            "positive_2025_h1": year_net["2025"] > 0,
            "old_1630_regime_net_positive": regime_net["old_1630"] > 0,
            "new_1700_regime_net_positive": regime_net["new_1700"] > 0,
        }
        decision = {
            "status": "CANDIDATE"
            if all(candidate_checks.values())
            else ("INVESTIGATE" if primary_pass else "REJECT"),
            "s2_gate": s2_gate,
            "s3_gates": gates,
            "candidate_checks": candidate_checks,
            "regime_net_jpy": dict(regime_net),
            "primary_mbb": primary_mbb,
            "primary_minus_momentum_mbb": contrast_mbb,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main(variant="q002" if argv[1:] == ["--q002"] else "q001")
