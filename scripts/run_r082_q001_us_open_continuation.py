"""Execute frozen Development-only TASK-R082-Q001 exactly once."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np
import yaml

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r078_cash_first_hour_extreme_fade import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    aligned_daily_net,
    scheduled_axis,
)
from n225m_bt.research.r082_us_open_continuation import (
    MBB_SEED,
    _execution_event,
    assign_abs_u_quintiles,
    bootstrap,
    build_events,
    feasibility,
    nyse_open_jst,
    pre_open_sign_events,
)
from n225m_bt.strategies.r082_fixed_signal import R082FixedSignalStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r082-q001-20260915-us-open-continuation-02"
OUT = ROOT / "results" / "research" / RUN_ID
DOC = Path("docs/strategy/52_r082_q001_us_open_continuation.md")
NYSE_FILE = Path("config/calendars/nyse_regular_trading_days_2021_2025h1_v1.yaml")
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for path in files:
        shutil.copy2(ROOT / path, destination / path.name)


def nyse_days() -> tuple[set[date], dict[str, object]]:
    payload = cast(
        dict[str, object], yaml.safe_load((ROOT / NYSE_FILE).read_text(encoding="utf-8"))
    )
    start, end = (date.fromisoformat(value) for value in cast(list[str], payload["coverage"]))
    closed = {date.fromisoformat(value) for value in cast(list[str], payload["closed_dates"])}
    generated = {
        day
        for ordinal in range((end - start).days + 1)
        if (day := start.fromordinal(start.toordinal() + ordinal)).weekday() < 5
        and day not in closed
    }
    checks = {
        "calendar_id_exact": payload.get("calendar_id")
        == "nyse_regular_trading_days_2021_2025h1_v1",
        "timezone_exact": payload.get("timezone") == "America/New_York",
        "weekday_only": all(day.weekday() < 5 for day in generated),
        "closed_weekdays_excluded": not generated.intersection(closed),
        "dst_spring_2024": [
            nyse_open_jst(date(2024, 3, 8)).isoformat(),
            nyse_open_jst(date(2024, 3, 11)).isoformat(),
        ]
        == ["2024-03-08T23:30:00+09:00", "2024-03-11T22:30:00+09:00"],
        "dst_autumn_2024": [
            nyse_open_jst(date(2024, 11, 1)).isoformat(),
            nyse_open_jst(date(2024, 11, 4)).isoformat(),
        ]
        == ["2024-11-01T22:30:00+09:00", "2024-11-04T23:30:00+09:00"],
    }
    return generated, {
        "path": str(NYSE_FILE),
        "sha256": digest(ROOT / NYSE_FILE),
        "payload": payload,
        "trading_day_count": len(generated),
        "checks": checks,
        "passed": all(checks.values()),
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [row for key, rows in grouped.items() if key not in isolated for row in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in row.quality_flags for row in included
        ),
    }
    expected = {
        "parent_data_version": PARENT_HASH,
        "quarantined_sessions": 45,
        "quarantined_bars": 27345,
        "included_sessions": 2216,
        "included_bars": 1326086,
        "quarantined_session_list_hash": QUARANTINE_HASH,
        "included_tick_grid_violations": 0,
    }
    mismatches = {
        key: {"actual": audit[key], "expected": value}
        for key, value in expected.items()
        if audit[key] != value
    }
    audit.update(expected_match=not mismatches, mismatches=mismatches)
    if mismatches:
        raise ValueError(f"BLOCKED: fixed R004 quarantine mismatch: {mismatches}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def engine_for(
    instrument: object,
    baseline: Any,
    classifier: CalendarClassifier,
    *,
    ticks: int = 1,
    fee: int = 30,
) -> BacktestEngine:
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
    return BacktestEngine(cast(Any, instrument).instrument.to_spec(), config, classifier)


def execute_profile(
    axis: list[date],
    events: list[dict[str, object]],
    bars: dict[tuple[date, Session], list[Any]],
    engine: BacktestEngine,
    *,
    name: str,
    direction: str,
) -> tuple[tuple[Trade, ...], dict[str, int | None], set[date]]:
    by_date, trades, unknown = (
        {date.fromisoformat(cast(str, event["trade_date"])): event for event in events},
        [],
        set(),
    )
    for target in axis:
        event = by_date[target]
        if event.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN":
            unknown.add(target)
        elif event.get("status") == "EXECUTABLE":
            side = cast(str, event[f"{direction}_direction"])
            result = engine.run(
                bars[(target, Session.NIGHT)],
                R082FixedSignalStrategy(
                    f"r082_{name}_{target.isoformat()}",
                    datetime.fromisoformat(cast(str, event["entry_signal_jst"])),
                    datetime.fromisoformat(cast(str, event["exit_signal_jst"])),
                    side,
                ),
                parameter_hash=canonical_hash({"profile": name, "event": event, "direction": side}),
            )
            if result.canceled_orders or len(result.trades) != 1:
                raise ValueError(f"{name}/{target}: executable event did not fill exactly once")
            trade = result.trades[0]
            if (
                trade.entry_ts.isoformat() != event["entry_open_jst"]
                or trade.exit_ts.isoformat() != event["exit_open_jst"]
                or trade.side is not Side(side)
                or trade.exit_reason is not ExitReason.SIGNAL
                or trade.qty != 1
            ):
                raise ValueError(f"{name}/{target}: event/execution mismatch")
            trades.append(trade)
    ordered = tuple(sorted(trades, key=lambda item: item.trade_date))
    return ordered, aligned_daily_net(axis, ordered, unknown), unknown


def report(
    axis: list[date],
    trades: tuple[Trade, ...],
    daily: dict[str, int | None],
    events: list[dict[str, object]],
    *,
    primary: bool = False,
) -> dict[str, object]:
    by_date = {date.fromisoformat(cast(str, event["trade_date"])): event for event in events}
    result: dict[str, object] = {
        "metrics": ledger_metrics(trades),
        "scheduled_axis_observations": len(axis),
        "daily_net_pnl_jpy": daily,
        "unknown_outcome_trade_dates": [target for target, value in daily.items() if value is None],
        "by_year": {
            str(year): ledger_metrics(
                tuple(trade for trade in trades if trade.trade_date.year == year)
            )
            for year in range(2021, 2026)
        },
        "by_u_sign": {
            sign: ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if (cast(int, by_date[trade.trade_date]["u_points"]) > 0)
                    == (sign == "positive")
                )
            )
            for sign in ("positive", "negative")
        },
        "by_us_time": {
            state: ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if bool(by_date[trade.trade_date]["us_dst"]) == (state == "dst")
                )
            )
            for state in ("dst", "standard")
        },
        "profit_concentration": concentration(trades, axis),
    }
    if primary:
        result["by_abs_u_quintile"] = {
            str(q): ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if by_date[trade.trade_date].get("abs_u_quintile") == q
                )
            )
            for q in range(1, 6)
        }
        result["by_u_p_sign_relation"] = {
            relation: ledger_metrics(
                tuple(
                    trade
                    for trade in trades
                    if bool(by_date[trade.trade_date].get("p_valid"))
                    and (
                        (
                            cast(int, by_date[trade.trade_date]["u_points"])
                            * cast(int, by_date[trade.trade_date]["p_points"])
                            > 0
                        )
                        == (relation == "same")
                    )
                )
            )
            for relation in ("same", "opposite")
        }
    return result


def order_fill_ledger(trades: tuple[Trade, ...]) -> dict[str, list[dict[str, object]]]:
    orders, fills = [], []
    for trade in trades:
        orders.extend(
            [
                {
                    "trade_id": trade.trade_id,
                    "action": "ENTRY",
                    "signal_ts": trade.entry_signal_ts,
                    "planned_fill_ts": trade.entry_ts,
                    "side": trade.side.value,
                },
                {
                    "trade_id": trade.trade_id,
                    "action": "EXIT",
                    "signal_ts": trade.exit_signal_ts,
                    "planned_fill_ts": trade.exit_ts,
                    "side": "sell" if trade.side is Side.LONG else "buy",
                },
            ]
        )
        fills.extend(
            [
                {
                    "trade_id": trade.trade_id,
                    "action": "ENTRY",
                    "fill_ts": trade.entry_ts,
                    "reference_price": trade.entry_reference_price,
                    "fill_price": trade.entry_fill_price,
                },
                {
                    "trade_id": trade.trade_id,
                    "action": "EXIT",
                    "fill_ts": trade.exit_ts,
                    "reference_price": trade.exit_reference_price,
                    "fill_price": trade.exit_fill_price,
                },
            ]
        )
    return {"orders": orders, "fills": fills}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r082_q001_us_open_continuation.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_r082_q001_us_open_continuation.py"),
        Path("src/n225m_bt/research/r082_us_open_continuation.py"),
        Path("src/n225m_bt/strategies/r082_fixed_signal.py"),
        Path("tests/test_r082_q001.py"),
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
    ] + [NYSE_FILE]
    preregistration = {
        "task_id": "TASK-R082-Q001",
        "family_id": "us_cash_open_n225_night_price_discovery",
        "study_id": "R082-Q001",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_PRICE_PERFORMANCE",
        "preregistration_document": str(DOC),
        "preregistration_document_sha256": digest(ROOT / DOC),
        "prior_information_seen": True,
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "frozen_rule": "For the mapped NYSE regular-day 09:30 America/New_York open S inside each N225 night session, U=(S+29 close)-S open; every executable U!=0 enters in U direction at S+30 open and exits at S+180 open. No day-session, gap, pre-S direction, post-S+30 information, or abs(U) selection.",
        "s2_gate": "NYSE calendar SHA/mapping/DST/session-containment audit passes; unexplained exclusions=0; executable>=850; U positive/negative>=300 each; 2021-2024>=140 each; 2025H1>=60; otherwise INCONCLUSIVE before PnL.",
        "controls": "scheduled-axis no-trade JPY0; same-day/same-entry/same-exit U fade; same executable U!=0/P!=0 dates only, same-entry/same-exit pre-open P-sign control where P=(S-1 close)-(S-30 open).",
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-wrapping MBB; tail truncation; linear percentile",
        },
        "primary_gate": "continuation Net>0; PF>1; continuation daily MBB lower>0; continuation-fade paired daily lower>0; continuation-pre-open-sign-control paired daily lower>0 on common U/P axis.",
        "sensitivities": [
            "signal_15m",
            "signal_45m",
            "entry_delay_1m",
            "exit_s_plus_120m",
            "exit_s_plus_240m",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "decision_ceiling": "INVESTIGATE due to Development reuse; never CANDIDATE.",
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
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
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / DOC, OUT / "documentation_snapshot" / DOC.name)
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_r082_q001.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r082_us_open_continuation.py",
            "src/n225m_bt/strategies/r082_fixed_signal.py",
        ],
        "py_compile": [sys.executable, "-m", "py_compile", str(source_files[0])],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(
            command, cwd=ROOT, capture_output=True, text=True, check=False, env=environment
        )
        validation[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, object], item)["returncode"] == 0 for item in validation.values())
        else "FAIL"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(
            OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}
        )
        raise ValueError("pre-execution validation failed")
    nyse_trading_days, calendar_audit = nyse_days()
    write_json(OUT / "nyse_calendar_audit.json", calendar_audit)
    if not bool(calendar_audit["passed"]):
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "NYSE_CALENDAR_AUDIT_FAILED",
            "nyse_calendar_audit": calendar_audit,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier, axis = CalendarClassifier(sessions, calendar), scheduled_axis(calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars: dict[tuple[date, Session], list[Any]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in set(axis) and bar.session is Session.NIGHT:
            bars[(bar.trade_date, bar.session)].append(bar)
    events = assign_abs_u_quintiles(
        build_events(classifier, axis, bars, isolated, nyse_trading_days)
    )
    s2 = feasibility(events)
    mapping_rows = [
        {
            key: event.get(key)
            for key in (
                "trade_date",
                "nyse_trade_date",
                "s_et",
                "s_jst",
                "n225_night_open_jst",
                "n225_night_close_jst",
                "n225_night_contains_s",
                "us_dst",
                "reason",
            )
        }
        for event in events
    ]
    mapping_audit = {
        "nyse_n225_mapping": mapping_rows,
        "all_nyse_days_contained": all(
            bool(event.get("n225_night_contains_s"))
            for event in events
            if event.get("nyse_trade_date") in {day.isoformat() for day in nyse_trading_days}
        ),
        "mapped_nyse_days": sum(
            event.get("nyse_trade_date") in {day.isoformat() for day in nyse_trading_days}
            for event in events
        ),
    }
    mapping_audit["passed"] = bool(mapping_audit["all_nyse_days_contained"])
    write_json(OUT / "nyse_n225_mapping_audit.json", mapping_audit)
    write_json(OUT / "primary_events.json", events)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2 calendar/mapping and PnL-free R082 feasibility then conditional S3",
            "split": "development",
            "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
            "physical_partitions": development.quality["partitions"],
            "data_version": development.data_version,
            "quarantine": quarantine_audit,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    if not bool(mapping_audit["passed"]) or not bool(cast(dict[str, object], s2["gate"])["passed"]):
        reason = (
            "NYSE_N225_MAPPING_AUDIT_FAILED"
            if not bool(mapping_audit["passed"])
            else "R082_PNL_FREE_FEASIBILITY_GATE_FAILED"
        )
        decision = {
            "status": "INCONCLUSIVE",
            "reason": reason,
            "nyse_calendar_audit": calendar_audit,
            "nyse_n225_mapping_audit": mapping_audit,
            "s2_feasibility": s2,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    profiles: dict[str, tuple[list[dict[str, object]], int, int, str]] = {
        "continuation": (events, 1, 30, "continuation"),
        "fade": (events, 1, 30, "fade"),
        "pre_open_sign_control": (pre_open_sign_events(events), 1, 30, "pre_open"),
        "signal_15m": (
            build_events(classifier, axis, bars, isolated, nyse_trading_days, signal_minutes=15),
            1,
            30,
            "continuation",
        ),
        "signal_45m": (
            build_events(classifier, axis, bars, isolated, nyse_trading_days, signal_minutes=45),
            1,
            30,
            "continuation",
        ),
        "entry_delay_1m": (
            [_execution_event(event, bars, entry_delay_minutes=1) for event in events],
            1,
            30,
            "continuation",
        ),
        "exit_s_plus_120m": (
            [_execution_event(event, bars, exit_minutes=120) for event in events],
            1,
            30,
            "continuation",
        ),
        "exit_s_plus_240m": (
            [_execution_event(event, bars, exit_minutes=240) for event in events],
            1,
            30,
            "continuation",
        ),
        "cost_2tick": (events, 2, 30, "continuation"),
        "cost_3tick": (events, 3, 30, "continuation"),
        "fee_x2": (events, 1, 60, "continuation"),
    }
    reports: dict[str, dict[str, object]] = {}
    ledgers: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int | None]] = {}
    unknown_profiles: dict[str, list[str]] = {}
    for name, (profile_events, ticks, fee, direction) in profiles.items():
        trades, daily_axis, unknown = execute_profile(
            axis,
            profile_events,
            bars,
            engine_for(instrument, baseline, classifier, ticks=ticks, fee=fee),
            name=name,
            direction=direction,
        )
        ledgers[name], daily[name] = trades, daily_axis
        reports[name] = report(
            axis, trades, daily_axis, profile_events, primary=name == "continuation"
        )
        if unknown:
            unknown_profiles[name] = sorted(day.isoformat() for day in unknown)
        write_json(OUT / f"{name}_events.json", profile_events)
        write_json(OUT / f"{name}_orders_fills.json", order_fill_ledger(trades))
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily_axis)
    no_trade = {target.isoformat(): 0 for target in axis}
    write_json(OUT / "no_trade_control_daily_axis.json", no_trade)
    if unknown_profiles:
        decision = {
            "status": "INCONCLUSIVE",
            "reason": "UNKNOWN_FILLED_EXIT",
            "unknown_profiles": unknown_profiles,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})
        return
    common_axis = [
        event["trade_date"]
        for event in pre_open_sign_events(events)
        if event.get("status") == "EXECUTABLE"
    ]
    common_cont, common_pre = (
        [cast(int, daily["continuation"][target]) for target in common_axis],
        [cast(int, daily["pre_open_sign_control"][target]) for target in common_axis],
    )
    write_json(
        OUT / "pre_open_common_event_axis.json",
        {
            "trade_dates": common_axis,
            "count": len(common_axis),
            "continuation_daily_net_jpy": common_cont,
            "pre_open_sign_control_daily_net_jpy": common_pre,
        },
    )
    boot, primary_index, common_index = bootstrap(
        [cast(int, value) for value in daily["continuation"].values()],
        [cast(int, value) for value in daily["fade"].values()],
        common_cont,
        common_pre,
    )
    np.save(OUT / "bootstrap_primary_axis_indices.npy", primary_index)
    np.save(OUT / "bootstrap_pre_open_common_axis_indices.npy", common_index)
    metrics = cast(dict[str, int | float | None], reports["continuation"]["metrics"])
    primary_ci = cast(
        list[float],
        cast(dict[str, object], boot["continuation_scheduled_axis_mean_net_jpy_per_trade_date"])[
            "ci95_percentile_linear"
        ],
    )
    fade_ci = cast(
        list[float],
        cast(dict[str, object], boot["continuation_minus_fade_paired_daily_net_jpy"])[
            "ci95_percentile_linear"
        ],
    )
    pre_ci = cast(
        list[float],
        cast(
            dict[str, object],
            boot[
                "continuation_minus_pre_open_sign_control_paired_daily_net_jpy_on_common_u_p_axis"
            ],
        )["ci95_percentile_linear"],
    )
    gates = {
        "net_positive": cast(int, metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(metrics["profit_factor"] and cast(float, metrics["profit_factor"]) > 1),
        "continuation_mbb_ci95_lower_gt_zero": primary_ci[0] > 0,
        "continuation_minus_fade_ci95_lower_gt_zero": fade_ci[0] > 0,
        "continuation_minus_pre_open_sign_control_ci95_lower_gt_zero": pre_ci[0] > 0,
    }
    u_metrics, time_metrics, years, concentration_metrics = (
        cast(dict[str, dict[str, int | float | None]], reports["continuation"]["by_u_sign"]),
        cast(dict[str, dict[str, int | float | None]], reports["continuation"]["by_us_time"]),
        cast(dict[str, dict[str, int | float | None]], reports["continuation"]["by_year"]),
        cast(dict[str, int | float | None], reports["continuation"]["profit_concentration"]),
    )
    sensitivity_names = [
        "signal_15m",
        "signal_45m",
        "entry_delay_1m",
        "exit_s_plus_120m",
        "exit_s_plus_240m",
        "cost_2tick",
        "cost_3tick",
        "fee_x2",
    ]
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_fixed_sensitivity_net_positive": all(
            cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])
            > 0
            for name in sensitivity_names
        ),
        "both_u_sign_net_positive": all(
            cast(int, u_metrics[sign]["net_pnl_jpy"]) > 0 for sign in ("positive", "negative")
        ),
        "both_us_time_net_positive": all(
            cast(int, time_metrics[state]["net_pnl_jpy"]) > 0 for state in ("dst", "standard")
        ),
        "at_least_three_2021_2024_positive": sum(
            cast(int, years[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)
        )
        >= 3,
        "2025_h1_net_positive": cast(int, years["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(
            int, concentration_metrics["net_excluding_top10_jpy"]
        )
        > 0,
    }
    audit = {
        "one_trade_per_trade_date": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in ledgers.values()
        ),
        "primary_continuation_fade_same_event_entry_exit_opposite_side": {
            (trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value)
            for trade in ledgers["continuation"]
        }
        == {
            (
                trade.trade_date,
                trade.entry_ts,
                trade.exit_ts,
                "short" if trade.side is Side.LONG else "long",
            )
            for trade in ledgers["fade"]
        },
        "primary_selection_after_s_plus_29_close_then_s_plus_30_entry_and_s_plus_180_exit": all(
            trade.entry_signal_ts
            == datetime.fromisoformat(
                cast(
                    str,
                    next(
                        event
                        for event in events
                        if event["trade_date"] == trade.trade_date.isoformat()
                    )["signal_endpoint_jst"],
                )
            )
            and trade.entry_ts
            == datetime.fromisoformat(
                cast(
                    str,
                    next(
                        event
                        for event in events
                        if event["trade_date"] == trade.trade_date.isoformat()
                    )["entry_open_jst"],
                )
            )
            and trade.exit_ts
            == datetime.fromisoformat(
                cast(
                    str,
                    next(
                        event
                        for event in events
                        if event["trade_date"] == trade.trade_date.isoformat()
                    )["exit_open_jst"],
                )
            )
            for trade in ledgers["continuation"]
        ),
        "pre_open_control_is_same_u_p_common_dates_and_entry_exit": {
            (trade.trade_date, trade.entry_ts, trade.exit_ts)
            for trade in ledgers["pre_open_sign_control"]
        }
        == {
            (trade.trade_date, trade.entry_ts, trade.exit_ts)
            for trade in ledgers["continuation"]
            if trade.trade_date.isoformat() in set(common_axis)
        },
        "no_stop_or_target": all(
            trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET}
            for trades in ledgers.values()
            for trade in trades
        ),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in ledgers.values()
            for trade in trades
        ),
    }
    if not all(audit.values()):
        raise ValueError("R082 execution/accounting/causality audit failed")
    write_json(
        OUT / "profiles.json",
        reports | {"no_trade_control": {"daily_net_pnl_jpy": no_trade, "net_pnl_jpy": 0}},
    )
    write_json(
        OUT / "bootstrap.json",
        boot
        | {
            "primary_axis_index_file": "bootstrap_primary_axis_indices.npy",
            "pre_open_common_axis_index_file": "bootstrap_pre_open_common_axis_indices.npy",
        },
    )
    write_json(OUT / "execution_accounting_audit.json", audit)
    decision = {
        "status": "INVESTIGATE" if all(gates.values()) else "REJECT",
        "decision_ceiling": "INVESTIGATE",
        "nyse_calendar_audit": calendar_audit,
        "nyse_n225_mapping_audit": mapping_audit,
        "s2_feasibility": s2,
        "s3_gates": gates,
        "candidate_checks": candidate_checks,
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, **decision})


if __name__ == "__main__":
    main()
