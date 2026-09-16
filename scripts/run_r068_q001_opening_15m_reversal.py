"""Execute frozen Development-only R068-Q001; no OOS or Holdout read path exists."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
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
from n225m_bt.research.r068_opening_reversal import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    PRIMARY_ENTRY,
    PRIMARY_EXIT,
    aligned_daily_net,
    feasibility,
    mbb_mean_ci,
    opening_event,
    scheduled_axis,
)
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r068-q001-20260915-opening-15m-reversal-01"
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


def run_profile(
    axis: list[date],
    bars_by_day: dict[date, list[Bar]],
    engine: BacktestEngine,
    *,
    name: str,
    direction_key: str,
    window: int = 15,
    exit_: time = PRIMARY_EXIT,
    delay: int = 0,
) -> tuple[
    tuple[Trade, ...], dict[str, int | None], dict[str, object], dict[str, dict[str, object]]
]:
    trades: list[Trade] = []
    unknown: set[date] = set()
    audits: dict[str, str] = {}
    events: dict[str, dict[str, object]] = {}
    for target in axis:
        event = opening_event(
            target,
            bars_by_day.get(target, []),
            window_minutes=window,
            exit_time=exit_,
            entry_delay_minutes=delay,
        )
        events[target.isoformat()] = event
        if event["status"] != "EXECUTABLE":
            audits[target.isoformat()] = str(event.get("reason", "SKIPPED"))
            continue
        entry_clock = time(9, window + delay)
        bars = bars_by_day[target]
        tz = bars[0].ts_jst.tzinfo
        if tz is None:
            raise ValueError("timezone-aware bars required")
        direction = str(event[direction_key])
        strategy = R049FixedTimeStrategy(
            f"r068_{name}_{target.isoformat()}",
            datetime.combine(target, entry_clock, tz),
            datetime.combine(target, exit_, tz),
            direction,
        )
        result = engine.run(
            bars,
            strategy,
            parameter_hash=canonical_hash({"name": name, "event": event, "direction": direction}),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(f"{name}/{target}: expected one completed trade")
        trade = result.trades[0]
        if (
            trade.entry_ts.time() != entry_clock
            or trade.exit_ts.time() != exit_
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{name}/{target}: execution contract mismatch")
        trades.append(trade)
        audits[target.isoformat()] = "TRADED"
    ordered = tuple(sorted(trades, key=lambda trade: trade.trade_date))
    return (
        ordered,
        aligned_daily_net(axis, ordered, unknown),
        {"condition": name, "per_trade_date": audits},
        events,
    )


def profile_summary(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int | None],
    audit: dict[str, object],
) -> dict[str, object]:
    if any(value is None for value in daily.values()):
        raise ValueError("unknown outcome prevents R068 S3 evaluation")
    years = {
        str(year): sum(
            value or 0 for target, value in daily.items() if target.startswith(str(year))
        )
        for year in range(2021, 2026)
    }
    sides: defaultdict[str, int] = defaultdict(int)
    for trade in trades:
        sides[trade.side.value] += trade.net_pnl_jpy
    return {
        "metrics": ledger_metrics(trades),
        "daily_net_pnl_jpy": daily,
        "net_pnl_by_year_jpy": years,
        "net_pnl_by_side_jpy": dict(sides),
        "scheduled_axis_observations": len(axis),
        "execution_audit": audit,
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r068_q001_opening_15m_reversal.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r068_q001_opening_15m_reversal.py"),
        Path("src/n225m_bt/research/r068_opening_reversal.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r068_q001.py"),
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
    preregistration = {
        "task_id": "TASK-R068-Q001",
        "study_id": "R068-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "seed": MBB_SEED,
        "duplicate_review": "R001-R067 reviewed before price performance. R067-Q001 is night-to-day information continuation; no prior R study uses 09:00-open to 09:14-close direction, 09:15 entry, 14:30 exit, and its same-event inverse momentum control.",
        "hypothesis": "The direction during the first 15 minutes after the daytime opening is a short-term overreaction; taking its inverse to before the close has positive net expectancy.",
        "primary_rule": "For each trade_date, if close_09:14-open_09:00 is positive enter one short, if negative enter one long, and if zero skip. Determine only after 09:14 close; next eligible open (scheduled 09:15) enters and scheduled 14:30 open exits.",
        "prohibited": "No Stop, Target, re-entry, prior-session information, threshold optimization, WFA, OOS, or Final Holdout.",
        "controls": "Primary no-trade JPY0; mechanism control is same-date/same-clock 15-minute opening momentum, the exact opposite side.",
        "costs": "JPY30 fee plus 1 tick adverse slippage per side. Fixed stresses: 2/3 ticks per side and fee x2; slippage is included once in fill-to-fill gross.",
        "s2_gate": "Direction-decidable and entry/exit-executable dates >=900; each 2021-2024 >=180; reversal long and short each >=300. Availability/direction only; fail is INCONCLUSIVE with no S3.",
        "primary_gate": "Reversal Net>0, PF>1, 20-trade_date non-wrapping MBB 10,000-replicate daily-Net CI lower>0, and same-bootstrap reversal-minus-momentum daily-Net CI lower>0.",
        "sensitivities": "Independent 10/20-minute direction windows; exits 14:15/14:45; one-minute delayed primary entry with exit unextended; 2/3 ticks; fee x2.",
        "candidate_gate": "2-tick and delayed-entry Net>0; all four window/exit sensitivities mean daily Net>0; both reversal signal sides Net>0; 2025H1 and at least three of 2021-2024 Net>0.",
        "scheduled_axis": "Version-controlled Development trade_date axis 2021-01-01..2025-06-30; known no-trade is JPY0.",
        "execution_contract": "bar-close signal, next eligible-bar open, one contract/max one position, fixed exit not extended by delay, existing missing-bar and cost contracts inherited.",
        "oos": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partitions
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
    commands = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r068_q001.py",
            "tests/test_r049_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r068_opening_reversal.py",
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
        "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
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
    axis_set = set(axis)
    bars_by_day: dict[date, list[Bar]] = defaultdict(list)
    for bar in development.bars:
        if bar.session.value == "day" and bar.trade_date in axis_set:
            bars_by_day[bar.trade_date].append(bar)
    s2 = feasibility(axis, bars_by_day)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2",
            "split": "development",
            "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
            "physical_partitions": development.quality["partitions"],
            "outputs": "availability and direction only; no returns/orders/fills/trades/PnL",
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(OUT / "s2_feasibility.json", s2)
    s2_gate = cast(dict[str, object], s2["gate"])
    if not bool(s2_gate["passed"]):
        s2_decision = {
            "status": "INCONCLUSIVE",
            "reason": "S2_GATE_FAILED",
            "s2_gate": s2_gate,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", s2_decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **s2_decision})
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
        "reversal": ("reversal_direction", 15, PRIMARY_EXIT, 0, 1, 30),
        "momentum_control": ("momentum_direction", 15, PRIMARY_EXIT, 0, 1, 30),
        "window_10": ("reversal_direction", 10, PRIMARY_EXIT, 0, 1, 30),
        "window_20": ("reversal_direction", 20, PRIMARY_EXIT, 0, 1, 30),
        "exit_1415": ("reversal_direction", 15, time(14, 15), 0, 1, 30),
        "exit_1445": ("reversal_direction", 15, time(14, 45), 0, 1, 30),
        "entry_delay_1m": ("reversal_direction", 15, PRIMARY_EXIT, 1, 1, 30),
        "cost_2tick": ("reversal_direction", 15, PRIMARY_EXIT, 0, 2, 30),
        "cost_3tick": ("reversal_direction", 15, PRIMARY_EXIT, 0, 3, 30),
        "fee_2x": ("reversal_direction", 15, PRIMARY_EXIT, 0, 1, 60),
    }
    output: dict[str, Any] = {}
    trades_by_profile: dict[str, tuple[Trade, ...]] = {}
    for name, (direction_key, window, exit_, delay, ticks, fee) in profiles.items():
        trades, daily, audit, events = run_profile(
            axis,
            bars_by_day,
            BacktestEngine(instrument.instrument.to_spec(), config(ticks, fee), classifier),
            name=name,
            direction_key=direction_key,
            window=window,
            exit_=exit_,
            delay=delay,
        )
        output[name] = profile_summary(axis, trades, daily, audit)
        trades_by_profile[name] = trades
        write_json(OUT / f"{name}_events.json", events)
        write_json(
            OUT / f"{name}_trades.json",
            [
                {
                    "trade_id": t.trade_id,
                    "trade_date": t.trade_date,
                    "side": t.side.value,
                    "entry_ts": t.entry_ts,
                    "exit_ts": t.exit_ts,
                    "gross_pnl_jpy": t.gross_pnl_jpy,
                    "fees_jpy": t.fees_jpy,
                    "slippage_cost_jpy": t.slippage_cost_jpy,
                    "net_pnl_jpy": t.net_pnl_jpy,
                    "exit_reason": t.exit_reason.value,
                }
                for t in trades
            ],
        )
    reversal_daily = list(output["reversal"]["daily_net_pnl_jpy"].values())
    momentum_daily = list(output["momentum_control"]["daily_net_pnl_jpy"].values())
    assert all(value is not None for value in reversal_daily + momentum_daily)
    primary_bootstrap = mbb_mean_ci([int(value) for value in reversal_daily])
    paired_bootstrap = mbb_mean_ci(
        [int(reversal_daily[i]) - int(momentum_daily[i]) for i in range(len(axis))]
    )
    write_json(OUT / "bootstrap_primary_daily_mean.json", primary_bootstrap)
    write_json(OUT / "bootstrap_reversal_minus_momentum_daily_mean.json", paired_bootstrap)
    metrics = output["reversal"]["metrics"]
    years = output["reversal"]["net_pnl_by_year_jpy"]
    side_net = output["reversal"]["net_pnl_by_side_jpy"]
    primary_ci = cast(list[float], primary_bootstrap["ci95_percentile_linear"])
    paired_ci = cast(list[float], paired_bootstrap["ci95_percentile_linear"])
    gates: dict[str, bool] = {
        "reversal_net_positive": metrics["net_pnl_jpy"] > 0,
        "reversal_pf_gt_one": (metrics["profit_factor"] or 0) > 1,
        "reversal_mbb_ci95_lower_gt_zero": primary_ci[0] > 0,
        "reversal_minus_momentum_mbb_ci95_lower_gt_zero": paired_ci[0] > 0,
        "two_tick_net_positive": output["cost_2tick"]["metrics"]["net_pnl_jpy"] > 0,
        "delayed_entry_net_positive": output["entry_delay_1m"]["metrics"]["net_pnl_jpy"] > 0,
        "all_four_window_exit_sensitivities_mean_daily_positive": all(
            sum(value or 0 for value in output[name]["daily_net_pnl_jpy"].values()) / len(axis) > 0
            for name in ("window_10", "window_20", "exit_1415", "exit_1445")
        ),
        "both_signal_sides_net_positive": side_net.get("long", 0) > 0
        and side_net.get("short", 0) > 0,
        "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3,
        "positive_2025_h1": years["2025"] > 0,
    }
    primary_names = tuple(
        name for name in gates if name.startswith("reversal_") or name == "reversal_pf_gt_one"
    )
    primary_pass = all(gates[name] for name in primary_names)
    candidate_pass = primary_pass and all(gates.values())
    decision = "CANDIDATE" if candidate_pass else "INVESTIGATE" if primary_pass else "REJECT"
    execution_audit = {
        "one_trade_per_date": all(
            len(trades) <= len(axis) for trades in trades_by_profile.values()
        ),
        "primary_fixed_clocks": all(
            t.entry_ts.time() == PRIMARY_ENTRY
            and t.exit_ts.time() == PRIMARY_EXIT
            and t.exit_reason is ExitReason.SIGNAL
            for t in trades_by_profile["reversal"]
        ),
        "reversal_momentum_same_dates_opposite_sides": {
            t.trade_date: t.side.value for t in trades_by_profile["reversal"]
        }
        == {
            t.trade_date: ("short" if t.side.value == "long" else "long")
            for t in trades_by_profile["momentum_control"]
        },
        "no_stop_or_target": all(
            t.exit_reason not in {ExitReason.STOP, ExitReason.TARGET}
            for trades in trades_by_profile.values()
            for t in trades
        ),
        "net_equals_gross_minus_fees": all(
            t.net_pnl_jpy == t.gross_pnl_jpy - t.fees_jpy
            for trades in trades_by_profile.values()
            for t in trades
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
            "decision": decision,
            "data_version": development.data_version,
            "data_quality": development.quality,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "decision.json",
        {
            "status": decision,
            "s2_gate": s2["gate"],
            "s3_gates": gates,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "status": "COMPLETE",
            "decision": decision,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
