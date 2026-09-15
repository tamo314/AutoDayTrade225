"""Execute frozen TASK-R069-Q001 on Development data only."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from typing import Any, cast

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.metrics import ledger_metrics
from n225m_bt.research.r069_opening_range_breakout import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    PRIMARY_EXIT,
    aligned_daily_net,
    feasibility,
    mbb_mean_ci,
    opening_range_breakout_event,
    scheduled_axis,
)
from n225m_bt.strategies.r069_opening_range_breakout import R069OpeningRangeBreakoutStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r069-q001-20260915-opening-range-breakout-01"
OUT = ROOT / "results" / "research" / RUN_ID


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
    bars_by_day: dict[date, list[Any]],
    engine: BacktestEngine,
    *,
    name: str,
    opening_minutes: int = 30,
    search_end_hour: int = 11,
    search_end_minute: int = 0,
    entry_delay_minutes: int = 0,
    reverse_direction: bool = False,
) -> tuple[tuple[Trade, ...], dict[str, int | None], dict[str, object], dict[str, dict[str, object]]]:
    from datetime import time

    trades: list[Trade] = []
    unknown: set[date] = set()
    events: dict[str, dict[str, object]] = {}
    audit: dict[str, str] = {}
    search_end = time(search_end_hour, search_end_minute)
    for target in axis:
        bars = bars_by_day.get(target, [])
        event = opening_range_breakout_event(
            target,
            bars,
            opening_minutes=opening_minutes,
            search_end=search_end,
            entry_delay_minutes=entry_delay_minutes,
        )
        events[target.isoformat()] = event
        if event["status"] != "SIGNALLED":
            audit[target.isoformat()] = str(event.get("reason", "SKIPPED"))
            continue
        strategy = R069OpeningRangeBreakoutStrategy(
            f"r069_{name}_{target.isoformat()}",
            opening_minutes=opening_minutes,
            search_end=search_end,
            entry_delay_minutes=entry_delay_minutes,
            reverse_direction=reverse_direction,
        )
        result = engine.run(
            bars,
            strategy,
            parameter_hash=canonical_hash(
                {
                    "name": name,
                    "event": event,
                    "reverse_direction": reverse_direction,
                }
            ),
        )
        if bool(event.get("outcome_observable")):
            if result.canceled_orders or len(result.trades) != 1:
                raise ValueError(f"{name}/{target}: observable signal did not produce exactly one trade")
            trade = result.trades[0]
            if (
                trade.entry_ts.isoformat() != event["entry_open_jst"]
                or trade.exit_ts.time() != PRIMARY_EXIT
                or trade.exit_reason is not ExitReason.SIGNAL
                or trade.qty != 1
            ):
                raise ValueError(f"{name}/{target}: execution contract mismatch")
            trades.append(trade)
            audit[target.isoformat()] = "TRADED"
        elif bool(event.get("entry_observable")):
            # A later unavailable exit cannot retroactively turn a live entry into a no-trade.
            unknown.add(target)
            audit[target.isoformat()] = "UNKNOWN_OUTCOME_AFTER_SIGNALLED_ENTRY"
        else:
            if result.trades:
                raise ValueError(f"{name}/{target}: unobservable entry produced a completed trade")
            audit[target.isoformat()] = "UNFILLED_ENTRY_NO_TRADE"
    ordered = tuple(sorted(trades, key=lambda trade: trade.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), {"condition": name, "per_trade_date": audit}, events


def profile_summary(
    axis: list[date], trades: tuple[Trade, ...], daily: dict[str, int | None], audit: dict[str, object]
) -> dict[str, object]:
    sides: defaultdict[str, int] = defaultdict(int)
    for trade in trades:
        sides[trade.side.value] += trade.net_pnl_jpy
    years = {
        str(year): sum(value or 0 for target, value in daily.items() if target.startswith(str(year)))
        for year in range(2021, 2026)
    }
    return {
        "metrics": ledger_metrics(trades),
        "daily_net_pnl_jpy": daily,
        "net_pnl_by_year_jpy": years,
        "net_pnl_by_side_jpy": dict(sides),
        "scheduled_axis_observations": len(axis),
        "unknown_outcome_dates": [target for target, value in daily.items() if value is None],
        "execution_audit": audit,
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r069_q001_opening_range_breakout.py"),
        Path("src/n225m_bt/research/r069_opening_range_breakout.py"),
        Path("src/n225m_bt/strategies/r069_opening_range_breakout.py"),
        Path("tests/test_r069_q001.py"),
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
    partitions = partition_paths(data_config.gold_root, "development")
    preregistration: dict[str, object] = {
        "task_id": "TASK-R069-Q001",
        "family_id": "opening-range-first-breakout-followthrough",
        "study_id": "R069-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "artifact_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "bootstrap_seed": MBB_SEED,
        "duplicate_review": "R001-R068 reviewed before performance. R033 is opening-range compression-conditioned breakout with a 60-minute hold; R034 is an opening-range rejection classifier at b+15; R008 is local compression. No prior registered study uses every valid day, the unfiltered first strict close breakout of a frozen 09:00-09:29 high/low range through 11:00, next-eligible-open entry, fixed 14:30 exit, and the same-event inverse-direction control.",
        "hypothesis": "A clear first breakout of the 30-minute daytime opening range reveals same-direction price discovery through the close, producing positive post-cost expectancy.",
        "primary_rule": "Fix opening_high=max(high) and opening_low=min(low) from the 30 confirmed eligible bars 09:00-09:29. From 09:30 through 11:00 inclusive, the first close strictly > opening_high enters one long and the first close strictly < opening_low enters one short. Equality, wick-only contact, and no breakout are no trade. The bar-close signal enters at the next eligible one-minute open under the inherited maximum fill-delay contract and exits from the 14:29 close signal at the eligible 14:30 open. One contract, one trade/day, and one position maximum.",
        "execution_and_missingness": "The fixed scheduled trade_date axis remains intact. A missing/ineligible prefix required to construct the range or search causes a known no-trade. A later unavailable exit never cancels an already signalled/filled entry; its day Net is null and prevents complete primary evaluation. Known no-trade is JPY0. Existing next-eligible-open, 10-minute maximum fill delay, cost, and conservative intrabar contracts are inherited.",
        "prohibited": "No Stop, Target, prior-session input, breakout-distance threshold, re-entry, early exit, WFA, OOS, or Final Holdout.",
        "controls": "Primary control: scheduled-axis no-trade JPY0. Mechanism co-primary: same signalled trade_date, entry and exit, and costs, with only direction reversed (false breakout).",
        "costs": "Baseline one adverse tick plus JPY30 fee per side. Prespecified stresses are 2/3 ticks per side and fee x2. Fill-to-fill gross includes slippage once; Net equals gross minus fees.",
        "s2_gate": "Observable executable trades >=400; each of 2021-2024 >=60; long and short breakouts each >=120. Availability/direction only, no PnL; failure is INCONCLUSIVE and stops S3.",
        "primary_gate": "Net>0; PF>1; 20-trade_date non-wrapping MBB with 10,000 repetitions, linear-percentile 95% CI lower bound of scheduled-axis daily mean Net >0; and paired same-bootstrap scheduled-axis daily Net (primary minus false-breakout) CI lower bound >0.",
        "sensitivities": "Opening range 20 and 45 minutes (with search beginning immediately after each range); search deadline 10:30 and 11:30; one-minute delayed entry without exit extension; 2/3 ticks per side; fee x2. No other variations are authorized.",
        "candidate_gate": "Primary passes; 2-tick Net>0; delayed-entry Net>0; all four opening-window/deadline sensitivities have positive scheduled-axis mean daily Net; both signal-side groups Net>0; at least three of 2021-2024 and 2025H1 Net>0.",
        "scheduled_axis": "Version-controlled Development trade_date axis 2021-01-01 through 2025-06-30; OOS and Final Holdout are excluded from physical selection and logical evaluation.",
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partitions
        ],
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
    command_env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r069_q001.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r069_opening_range_breakout.py",
            "src/n225m_bt/strategies/r069_opening_range_breakout.py",
            str(source_files[0]),
        ],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=command_env)
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
    bars_by_day: dict[date, list[Any]] = defaultdict(list)
    for bar in development.bars:
        if bar.session.value == "day" and bar.trade_date in axis_set:
            bars_by_day[bar.trade_date].append(bar)
    s2 = feasibility(axis, bars_by_day)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 then S3 only if S2 passes",
            "split": "development",
            "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
            "physical_partitions": development.quality["partitions"],
            "s2_output": "availability and breakout direction only; no returns/orders/fills/trades/PnL",
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(OUT / "s2_feasibility.json", s2)
    s2_gate = cast(dict[str, object], s2["gate"])
    if not bool(s2_gate["passed"]):
        decision = {"status": "INCONCLUSIVE", "reason": "S2_GATE_FAILED", "s2_gate": s2_gate}
        write_json(OUT / "decision.json", decision | {"oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return

    def config(ticks: int = 1, fee: int = 30) -> Any:
        return baseline.model_copy(
            update={
                "mode": "day_only",
                "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
                "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
                "risk": baseline.risk.model_copy(
                    update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
                ),
            }
        )

    profiles = {
        "primary": (30, 11, 0, 0, False, 1, 30),
        "false_breakout_control": (30, 11, 0, 0, True, 1, 30),
        "opening_window_20": (20, 11, 0, 0, False, 1, 30),
        "opening_window_45": (45, 11, 0, 0, False, 1, 30),
        "search_deadline_1030": (30, 10, 30, 0, False, 1, 30),
        "search_deadline_1130": (30, 11, 30, 0, False, 1, 30),
        "entry_delay_1m": (30, 11, 0, 1, False, 1, 30),
        "cost_2tick": (30, 11, 0, 0, False, 2, 30),
        "cost_3tick": (30, 11, 0, 0, False, 3, 30),
        "fee_2x": (30, 11, 0, 0, False, 1, 60),
    }
    output: dict[str, dict[str, object]] = {}
    trades_by_profile: dict[str, tuple[Trade, ...]] = {}
    for name, (window, end_hour, end_minute, delay, reverse, ticks, fee) in profiles.items():
        trades, daily, audit, events = run_profile(
            axis,
            bars_by_day,
            BacktestEngine(instrument.instrument.to_spec(), config(ticks, fee), classifier),
            name=name,
            opening_minutes=window,
            search_end_hour=end_hour,
            search_end_minute=end_minute,
            entry_delay_minutes=delay,
            reverse_direction=reverse,
        )
        output[name] = profile_summary(axis, trades, daily, audit)
        trades_by_profile[name] = trades
        write_json(OUT / f"{name}_events.json", events)
        write_json(
            OUT / f"{name}_trades.json",
            [
                {
                    "trade_id": trade.trade_id,
                    "trade_date": trade.trade_date,
                    "side": trade.side.value,
                    "entry_signal_ts": trade.entry_signal_ts,
                    "entry_ts": trade.entry_ts,
                    "exit_signal_ts": trade.exit_signal_ts,
                    "exit_ts": trade.exit_ts,
                    "gross_pnl_jpy": trade.gross_pnl_jpy,
                    "fees_jpy": trade.fees_jpy,
                    "slippage_cost_jpy": trade.slippage_cost_jpy,
                    "net_pnl_jpy": trade.net_pnl_jpy,
                    "exit_reason": trade.exit_reason.value,
                }
                for trade in trades
            ],
        )
    unknown_profiles = {
        name: value["unknown_outcome_dates"]
        for name, value in output.items()
        if value["unknown_outcome_dates"]
    }
    if unknown_profiles:
        decision = {
            "status": "BLOCKED",
            "reason": "UNKNOWN_OUTCOMES_PREVENT_COMPLETE_DAILY_NET_EVALUATION",
            "unknown_profiles": unknown_profiles,
            "s2_gate": s2_gate,
        }
        write_json(OUT / "s3_results.json", {"profiles": output, **decision})
        write_json(OUT / "decision.json", decision | {"oos": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return

    primary_daily = list(cast(dict[str, int], output["primary"]["daily_net_pnl_jpy"]).values())
    false_daily = list(cast(dict[str, int], output["false_breakout_control"]["daily_net_pnl_jpy"]).values())
    primary_bootstrap = mbb_mean_ci(primary_daily)
    paired_bootstrap = mbb_mean_ci([primary_daily[i] - false_daily[i] for i in range(len(axis))])
    write_json(OUT / "bootstrap_primary_daily_mean.json", primary_bootstrap)
    write_json(OUT / "bootstrap_primary_minus_false_breakout_daily_mean.json", paired_bootstrap)
    metrics = cast(dict[str, object], output["primary"]["metrics"])
    years = cast(dict[str, int], output["primary"]["net_pnl_by_year_jpy"])
    sides = cast(dict[str, int], output["primary"]["net_pnl_by_side_jpy"])
    primary_ci = cast(list[float], primary_bootstrap["ci95_percentile_linear"])
    paired_ci = cast(list[float], paired_bootstrap["ci95_percentile_linear"])
    gates = {
        "net_positive": cast(int, metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": cast(float | None, metrics["profit_factor"]) is not None
        and cast(float, metrics["profit_factor"]) > 1,
        "primary_mbb_ci95_lower_gt_zero": primary_ci[0] > 0,
        "primary_minus_false_breakout_mbb_ci95_lower_gt_zero": paired_ci[0] > 0,
        "two_tick_net_positive": cast(
            int, cast(dict[str, object], output["cost_2tick"]["metrics"])["net_pnl_jpy"]
        )
        > 0,
        "delayed_entry_net_positive": cast(
            int, cast(dict[str, object], output["entry_delay_1m"]["metrics"])["net_pnl_jpy"]
        )
        > 0,
        "all_four_window_deadline_sensitivities_mean_daily_positive": all(
            sum(cast(dict[str, int], output[name]["daily_net_pnl_jpy"]).values()) / len(axis) > 0
            for name in (
                "opening_window_20",
                "opening_window_45",
                "search_deadline_1030",
                "search_deadline_1130",
            )
        ),
        "both_signal_sides_net_positive": sides.get("long", 0) > 0 and sides.get("short", 0) > 0,
        "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3,
        "positive_2025_h1": years["2025"] > 0,
    }
    primary_pass = all(
        gates[name]
        for name in (
            "net_positive",
            "pf_gt_one",
            "primary_mbb_ci95_lower_gt_zero",
            "primary_minus_false_breakout_mbb_ci95_lower_gt_zero",
        )
    )
    candidate_pass = primary_pass and all(gates.values())
    decision_status = "CANDIDATE" if candidate_pass else "INVESTIGATE" if primary_pass else "REJECT"
    primary = trades_by_profile["primary"]
    control = trades_by_profile["false_breakout_control"]
    execution_audit = {
        "one_trade_per_trade_date": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in trades_by_profile.values()
        ),
        "fixed_1430_signal_exit": all(
            trade.exit_ts.time() == PRIMARY_EXIT and trade.exit_reason is ExitReason.SIGNAL
            for trade in primary
        ),
        "primary_control_same_dates_entry_exit_opposite_side": {
            trade.trade_date: (trade.entry_ts, trade.exit_ts, trade.side.value) for trade in primary
        }
        == {
            trade.trade_date: (
                trade.entry_ts,
                trade.exit_ts,
                "short" if trade.side.value == "long" else "long",
            )
            for trade in control
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
    if not all(execution_audit.values()):
        raise ValueError("execution/accounting audit failed")
    write_json(OUT / "execution_accounting_audit.json", execution_audit)
    write_json(
        OUT / "s3_results.json",
        {
            "profiles": output,
            "no_trade_control": {"net_pnl_jpy": 0, "daily_net_pnl_jpy": 0},
            "gates": gates,
            "decision": decision_status,
            "data_version": development.data_version,
            "data_quality": development.quality,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "decision.json",
        {
            "status": decision_status,
            "s2_gate": s2_gate,
            "s3_gates": gates,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "status": "COMPLETE",
            "decision": decision_status,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
