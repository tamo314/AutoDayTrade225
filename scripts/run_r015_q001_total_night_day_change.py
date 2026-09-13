"""Execute the preregistered Development-only R015-Q001 experiment."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, Literal, cast

import polars as pl

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r015 import total_change_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.total_night_day_change import TotalNightDayChangeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r015-q001-20260914-night-close-to-day-opening-total-change-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
EXPECTED_QUARANTINE = {
    "quarantined_sessions": 45,
    "quarantined_bars": 27345,
    "included_bars": 1326086,
    "included_sessions": 2216,
    "quarantined_sessions_by_type": {"day": 20, "night": 25},
}
EXPECTED_TARGET_DAYS = 1111
Condition = Literal[
    "A_total_follow", "B_always_long", "C_always_short", "D_day_initial_follow", "F_gap_follow"
]
CONDITIONS: tuple[Condition, ...] = (
    "A_total_follow",
    "B_always_long",
    "C_always_short",
    "D_day_initial_follow",
    "F_gap_follow",
)


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, Any], set[tuple[date, Session]]]:
    """Reproduce R004's fixed whole-session isolation exactly."""
    groups = session_groups(data.bars)
    bad = {
        key
        for key, rows in groups.items()
        if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in rows)
    }
    included = [bar for key, rows in groups.items() if key not in bad for bar in rows]
    sessions = [
        {"trade_date": trade_day.isoformat(), "session": session.value}
        for trade_day, session in sorted(bad)
    ]
    audit: dict[str, Any] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(bad),
        "quarantined_sessions_by_type": dict(sorted(Counter(x.value for _, x in bad).items())),
        "included_bars": len(included),
        "included_sessions": len(groups) - len(bad),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in bar.quality_flags for bar in included
        ),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": sessions,
        "quarantined_session_list_hash": canonical_hash(sessions),
    }
    expected = EXPECTED_QUARANTINE | {"parent_data_version": EXPECTED_PARENT_VERSION}
    mismatches = {
        key: {"actual": audit.get(key), "expected": value}
        for key, value in expected.items()
        if audit.get(key) != value
    }
    if audit["quarantined_session_list_hash"] != EXPECTED_QUARANTINE_HASH:
        mismatches["quarantined_session_list_hash"] = {
            "actual": audit["quarantined_session_list_hash"],
            "expected": EXPECTED_QUARANTINE_HASH,
        }
    if audit["included_tick_grid_violations"]:
        mismatches["included_tick_grid_violations"] = audit["included_tick_grid_violations"]
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    version = canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": sessions})
    return ResearchData(included, version, data.quality | {"quarantine": audit}), audit, bad


def month_keys() -> list[str]:
    return [
        f"{year:04d}-{month:02d}"
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    ]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = floor(position), ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap(values: dict[str, list[int]]) -> dict[str, Any]:
    count = len(values["A_total_follow"])
    if count != EXPECTED_TARGET_DAYS or any(len(series) != count for series in values.values()):
        raise ValueError("invalid fixed aligned daily bootstrap inputs")
    rng, block = Random(20260913), 20
    a, b, c, d, f = (values[key] for key in CONDITIONS)
    samples: dict[str, list[float]] = {
        "A": [],
        "A_minus_B": [],
        "A_minus_C": [],
        "A_minus_D": [],
        "A_minus_F": [],
    }
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        indices = indices[:count]
        samples["A"].append(fmean(a[index] for index in indices))
        samples["A_minus_B"].append(fmean(a[index] - b[index] for index in indices))
        samples["A_minus_C"].append(fmean(a[index] - c[index] for index in indices))
        samples["A_minus_D"].append(fmean(a[index] - d[index] for index in indices))
        samples["A_minus_F"].append(fmean(a[index] - f[index] for index in indices))

    def estimate(key: str, observed: float) -> dict[str, object]:
        return {
            "estimate": observed,
            "ci95_percentile_linear": [percentile(samples[key], 0.025), percentile(samples[key], 0.975)],
        }

    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10_000,
        "seed": 20260913,
        "common_indices_all_conditions": True,
        "percentile_implementation": "linear interpolation at (n-1)*q",
        "A_daily_mean_net_jpy": estimate("A", fmean(a)),
        "A_minus_B_daily_mean_net_jpy": estimate("A_minus_B", fmean(x - y for x, y in zip(a, b, strict=True))),
        "A_minus_C_daily_mean_net_jpy": estimate("A_minus_C", fmean(x - y for x, y in zip(a, c, strict=True))),
        "A_minus_D_daily_mean_net_jpy": estimate("A_minus_D", fmean(x - y for x, y in zip(a, d, strict=True))),
        "A_minus_F_daily_mean_net_jpy": estimate("A_minus_F", fmean(x - y for x, y in zip(a, f, strict=True))),
    }


def preregistration(source: dict[str, object]) -> dict[str, Any]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R015-Q001",
        "status": "frozen_before_r015_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation, unused sample, or multiplicity-corrected validation.",
        "novelty_correspondence": {
            "R001": "same-session day opening movement only, not C_N to C_t total change",
            "R006": "C_N-to-O_D gap reversal, not gap following plus S-to-t movement",
            "R012": "linked-night open-to-close direction, not C_N-to-C_t total change",
            "R014": "same-session high/low midpoint, not linked-night close reference",
            "conclusion": "No R001-R014 artifact evaluates sign(Q=C_t-C_N) at t=S+29 through X=S+90 against sign(G) and sign(M) on the same G/M/Q-nonzero day event.",
        },
        "hypothesis": "At day t=S+29, following sign(Q=C_t-C_N) for 60 minutes has positive post-cost expectancy and exceeds sign(G=O_D-C_N) and sign(M=C_t-O_D), along with always-long and always-short controls. This is a price-pattern hypothesis only; it does not assert observed participants, order flow, or a causal market mechanism.",
        "fixed_rule": {
            "schedule": "Versioned day S; t=S+29, E=S+30, X=S+90. Scheduled E<=new-entry cutoff and X<=F are checked without observing prices.",
            "reference": "C_N is R006/R012's schedule-defined final normal linked-night bar close, excluding auction and never substituted by observed final row, force-flat time, or an earlier night.",
            "window": "Require 30 contiguous eligible day bars [S,t]. O_D=open_S, C_t=close_t; G=O_D-C_N, M=C_t-O_D, Q=C_t-C_N=G+M are integer prices. Require G,M,Q nonzero with no threshold, normalization, volume, breakout, weekday, year, or direction filter.",
            "conditions": {"A": "sign(Q)", "B": "always long", "C": "always short", "D": "sign(M)", "F": "sign(G)", "A2": "A re-executed by unchanged engine at 2 ticks/side"},
            "orders": "Signal after t close, earliest E open entry; issue EXIT after X-1 close, earliest X open. Entry delay never extends X and no new entry starts at or after X.",
            "prohibited": ["Stop", "Target", "night-close early exit", "re-entry", "direction reversal", "window/time/reference/threshold rescue", "WFA", "additional cost/delay", "OOS", "Final Holdout"],
        },
        "inputs": {
            "physical": "Development-selected normalized center_continuous Parquet only, trade_date filtered before collect; raw/external/volume/OOS/Final Holdout prohibited.",
            "r004_quarantine": EXPECTED_QUARANTINE | {"parent_data_version": EXPECTED_PARENT_VERSION, "quarantine_hash": EXPECTED_QUARANTINE_HASH},
            "target_day_trade_dates": EXPECTED_TARGET_DAYS,
            "quality_ceiling": "PASS_LIMITED due to inherited continuous-series, actual-contract/roll/adjustment and post-quality whole-session-isolation limits.",
        },
        "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "accounting": "Gross is fill-to-fill slippage-inclusive; Net=Gross-fees; slippage attribution is never deducted twice."},
        "evaluation": {
            "daily": "All 1,111 non-quarantined day trade_dates are retained; missing reference/window or zero event is common zero no-trade, never a successful-fill intersection.",
            "bootstrap": {"block_length_trade_dates": 20, "repetitions": 10_000, "seed": 20260913, "common_indices_all_conditions": True, "sampling": "non-circular blocks with replacement and tail truncation", "ci": "linear percentile"},
            "diagnostics": "Record G/M same-vs-opposite signs and A/D, A/F agreement-vs-disagreement as diagnostics only; same-direction event paths and PnL must match.",
            "information_gate": ["A/B/C/D/F >=200", "A long/short >=50", "A/D and A/F disagreement eligible events >=50 each"],
            "pass_requires_all": ["A Net>0", "A PF>1", "A/A-B/A-C/A-D/A-F CI lower bounds>0", "A2 expectancy>0", "A positive months>=27/54", "A top10-winner-excluded Net>0"],
            "decision": "BLOCKED on input/synthetic/execution/accounting failure; INCONCLUSIVE for information failure; REJECT if any primary condition fails after information sufficiency; all passing is Development primary/PASS_LIMITED only and never automatic CANDIDATE.",
        },
        "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "event_selector_sha256": hashes["src/n225m_bt/research/r015.py"], "strategy_sha256": hashes["src/n225m_bt/strategies/total_night_day_change.py"], "test_sha256": sha256((ROOT / "tests" / "test_r015_q001.py").read_bytes()).hexdigest()},
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def direction_for(condition: Condition, event: dict[str, object]) -> str:
    if condition == "B_always_long":
        return "long"
    if condition == "C_always_short":
        return "short"
    key = {"A_total_follow": "A_direction", "D_day_initial_follow": "D_direction", "F_gap_follow": "F_direction"}[condition]
    return cast(str, event[key])


def run_condition(
    data: ResearchData,
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    condition: Condition,
    bad: set[tuple[date, Session]],
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int], list[str]]:
    groups = session_groups(data.bars)
    target_dates = sorted(day.isoformat() for day, session in groups if session is Session.DAY)
    if len(target_dates) != EXPECTED_TARGET_DAYS:
        raise ValueError(f"R015 target day count {len(target_dates)} != {EXPECTED_TARGET_DAYS}")
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for day_text in target_dates:
        trade_day = date.fromisoformat(day_text)
        event = total_change_event(
            classifier,
            trade_day,
            groups[(trade_day, Session.DAY)],
            groups.get((trade_day, Session.NIGHT)),
            reference_night_quarantined=(trade_day, Session.NIGHT) in bad,
        )
        event.update({"condition": condition, "pre_event_status": event["status"]})
        if event["status"] == "eligible":
            event.update(
                {
                    "G_M_relation": "same" if event["D_direction"] == event["F_direction"] else "opposite",
                    "A_D_direction_relation": "same" if event["A_direction"] == event["D_direction"] else "different",
                    "A_F_direction_relation": "same" if event["A_direction"] == event["F_direction"] else "different",
                }
            )
        audit[f"event_{event['status']}"] += 1
        if event["status"] != "eligible":
            audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(
            groups[(trade_day, Session.DAY)],
            TotalNightDayChangeStrategy(f"r015_q001_{condition}", signal, direction_for(condition, event)),
            canonical_hash({"condition": condition, "event": event, "data_version": data.data_version}),
        )
        if len(result.trades) > 1:
            raise AssertionError("R015 produced more than one trade/day")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update({"status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_delay_minutes": int((trade.entry_ts - signal - timedelta(minutes=1)).total_seconds() // 60), "exit_delay_minutes": int((trade.exit_ts - signal - timedelta(minutes=61)).total_seconds() // 60), "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy})
            trades.append(trade)
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
        else:
            event["status"] = "eligible_order_unfilled"
            audit["eligible_order_unfilled"] += 1
        events.append(event)
    stable = tuple(replace(item, trade_id=f"trade-{index:06d}") for index, item in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1))
    return stable, events, dict(sorted(audit.items())), target_dates


def daily_for_targets(trades: tuple[Trade, ...], target_dates: list[str]) -> dict[str, int]:
    daily = dict.fromkeys(target_dates, 0)
    for trade in trades:
        daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return daily


def write_condition(folder: Path, condition: str, trades: tuple[Trade, ...], events: list[dict[str, object]], metrics: dict[str, object], audit: dict[str, int], data: ResearchData, ticks: int, daily: dict[str, int]) -> None:
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "status": "complete", "data_version": data.data_version, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(daily), "net_pnl_jpy": [daily[day] for day in sorted(daily)]}).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int) -> dict[str, object]:
    eligible = [item for item in events if item.get("pre_event_status") == "eligible"]
    filled = [item for item in events if item.get("status") == "filled"]
    checks = {
        "every_eligible_event_filled": len(eligible) == len(filled),
        "one_trade_per_filled_event": len(filled) == len(trades),
        "no_canceled_or_unfilled_eligible_order": not any(item.get("status") == "eligible_order_unfilled" for item in events),
        "entry_within_max_delay": all(0 <= cast(int, item["entry_delay_minutes"]) <= max_delay for item in filled),
        "fixed_signal_exit_within_max_delay": all(item["exit_reason"] == "signal" and 0 <= cast(int, item["exit_delay_minutes"]) <= max_delay for item in filled),
        "net_equals_gross_minus_fees": all(item.net_pnl_jpy == item.gross_pnl_jpy - item.fees_jpy for item in trades),
        "no_force_flat_or_end_of_data": all(item.exit_reason.value not in {"force_flat", "end_of_data"} for item in trades),
    }
    return {"checks": checks, "all_pass": all(checks.values()), "accounting": "slippage attribution is informational and never additionally subtracted"}


def pre_event_match(events_by: dict[str, list[dict[str, object]]]) -> bool:
    fields = ("trade_date", "pre_event_status", "reason", "C_night_normal_close", "O_day_open", "C_t", "G_points", "M_points", "Q_points")
    base = events_by["A_total_follow"]
    return all(len(events_by[key]) == len(base) and all(tuple(row.get(field) for field in fields) == tuple(events_by[key][index].get(field) for field in fields) for index, row in enumerate(base)) for key in CONDITIONS[1:])


def direction_diagnostics(events_a: list[dict[str, object]], events_d: list[dict[str, object]], events_f: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    comparisons = {"A_D": (events_d, "A_D_direction_relation"), "A_F": (events_f, "A_F_direction_relation")}
    for name, (other, field) in comparisons.items():
        for relation in ("same", "different"):
            pairs = [(a, b) for a, b in zip(events_a, other, strict=True) if a.get(field) == relation]
            output[f"{name}_{relation}"] = {"pre_event_count": len(pairs), "A_filled": sum(a.get("status") == "filled" for a, _ in pairs), "control_filled": sum(b.get("status") == "filled" for _, b in pairs), "execution_side_match": all(a.get("side") == b.get("side") for a, b in pairs if a.get("status") == b.get("status") == "filled"), "A_minus_control_net_jpy": sum(cast(int, a.get("net_pnl_jpy", 0)) - cast(int, b.get("net_pnl_jpy", 0)) for a, b in pairs)}
    for relation in ("same", "opposite"):
        rows = [row for row in events_a if row.get("G_M_relation") == relation]
        output[f"G_M_{relation}"] = {"pre_event_count": len(rows), "A_filled": sum(row.get("status") == "filled" for row in rows), "A_net_jpy": sum(cast(int, row.get("net_pnl_jpy", 0)) for row in rows)}
    return output


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close)
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R015 specification")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    plan = preregistration(source)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r015_prices", "source": source, "plan_hash": canonical_hash(plan), "seed": 20260913, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r015_q001.py", "tests/test_r006_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r015.py", "src/n225m_bt/strategies/total_night_day_change.py", "tests/test_r015_q001.py", str(Path(__file__).relative_to(ROOT))],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r015.py", "src/n225m_bt/strategies/total_night_day_change.py"],
    }
    validation: dict[str, Any] = {"coverage": ["linked-night scheduled final normal close, auction exclusion, weekend and schedule version", "contiguous 30-bar S..t window, missing/ineligible/reference quarantine rejection", "integer G/M/Q signs, zeros, Q=G+M, same component with other component reversing Q", "G/M agreement and opposite-sign larger-component direction", "next-bar entry, fixed exit, delayed non-extension, prefix invariance, one position, accounting and Final Holdout input rejection"]}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R015 synthetic/static validation failed before Development data access")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, bad = quarantine(development)
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "physical_io": "Development selected normalized Parquet only; OOS and Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only", "expected_target_day_count": EXPECTED_TARGET_DAYS})
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, dict[str, object]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    target_dates: list[str] | None = None
    for condition in CONDITIONS:
        trades, events, audit, dates = run_condition(view, classifier, engine, condition, bad)
        if target_dates is None:
            target_dates = dates
        elif target_dates != dates:
            raise ValueError("condition target-date universe differs")
        metrics, daily = research_metrics(trades, view.bars), daily_for_targets(trades, dates)
        write_condition(reserve_directory(OUT, condition), condition, trades, events, metrics, audit, view, 1, daily)
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, daily
    stress = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    stress_trades, stress_events, stress_audit, stress_dates = run_condition(view, classifier, BacktestEngine(instrument.instrument.to_spec(), stress, classifier), "A_total_follow", bad)
    if target_dates != stress_dates:
        raise ValueError("A2 target-date universe differs")
    stress_metrics, stress_daily = research_metrics(stress_trades, view.bars), daily_for_targets(stress_trades, stress_dates)
    write_condition(reserve_directory(OUT, "A_total_follow_2tick"), "A_total_follow_2tick", stress_trades, stress_events, stress_metrics, stress_audit, view, 2, stress_daily)
    results["A_total_follow_2tick"] = {"trade_count": len(stress_trades), "metrics": stress_metrics, "execution_audit": stress_audit}
    assert target_dates is not None
    values = {condition: [daily_by[condition][day] for day in target_dates] for condition in CONDITIONS}
    boot = bootstrap(values)
    audits = {condition: path_audit(events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes) for condition in CONDITIONS} | {"A_total_follow_2tick": path_audit(stress_events, stress_trades, baseline.execution.max_fill_delay_minutes)}
    diagnostics = direction_diagnostics(events_by["A_total_follow"], events_by["D_day_initial_follow"], events_by["F_gap_follow"])
    shared = pre_event_match(events_by)
    same_checks = [cast(bool, cast(dict[str, object], diagnostics[key])["execution_side_match"]) and cast(int, cast(dict[str, object], diagnostics[key])["A_minus_control_net_jpy"]) == 0 for key in ("A_D_same", "A_F_same")]
    post = {"status": "PASS" if shared and all(cast(bool, audit["all_pass"]) for audit in audits.values()) and all(same_checks) else "BLOCKED", "conditions": audits, "all_conditions_identical_pre_event": shared, "direction_diagnostics": diagnostics, "same_direction_path_and_pnl_match": all(same_checks), "policy": "Any execution/accounting mismatch blocks economic decision; no successful-fill intersection is used."}
    write_json(OUT / "post_execution_validation.json", post)
    write_json(OUT / "direction_diagnostics.json", diagnostics)
    monthly = {month: 0 for month in month_keys()}
    for day, amount in daily_by["A_total_follow"].items():
        monthly[day[:7]] += amount
    a_metrics = cast(dict[str, Any], results["A_total_follow"]["metrics"])
    a_overall, stress_overall = cast(dict[str, Any], a_metrics["overall"]), cast(dict[str, Any], stress_metrics["overall"])
    long_count = sum(item.side.value == "long" for item in trades_by["A_total_follow"])
    short_count = len(trades_by["A_total_follow"]) - long_count
    def lower(key: str) -> bool:
        return cast(list[float], boot[key]["ci95_percentile_linear"])[0] > 0
    gates = {"A_trade_count_at_least_200": len(trades_by["A_total_follow"]) >= 200, "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200, "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200, "D_trade_count_at_least_200": len(trades_by["D_day_initial_follow"]) >= 200, "F_trade_count_at_least_200": len(trades_by["F_gap_follow"]) >= 200, "A_long_at_least_50": long_count >= 50, "A_short_at_least_50": short_count >= 50, "A_D_different_direction_events_at_least_50": cast(int, cast(dict[str, object], diagnostics["A_D_different"])["pre_event_count"]) >= 50, "A_F_different_direction_events_at_least_50": cast(int, cast(dict[str, object], diagnostics["A_F_different"])["pre_event_count"]) >= 50, "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"), "A_minus_B_bootstrap_lower_above_0": lower("A_minus_B_daily_mean_net_jpy"), "A_minus_C_bootstrap_lower_above_0": lower("A_minus_C_daily_mean_net_jpy"), "A_minus_D_bootstrap_lower_above_0": lower("A_minus_D_daily_mean_net_jpy"), "A_minus_F_bootstrap_lower_above_0": lower("A_minus_F_daily_mean_net_jpy"), "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None and stress_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(amount > 0 for amount in monthly.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])["net_excluding_top10_jpy"] > 0}
    information = [key for key in gates if "trade_count" in key or key in {"A_long_at_least_50", "A_short_at_least_50", "A_D_different_direction_events_at_least_50", "A_F_different_direction_events_at_least_50"}]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(gates[key] for key in information) else "DEVELOPMENT_PRIMARY_CONDITIONS_PASSED_PASS_LIMITED" if all(gates.values()) else "REJECT"
    aligned: dict[str, object] = {"trade_dates": target_dates, "no_trade": "0", "day_session_quarantine": "excluded", "reference_night_quarantine_or_missing_or_zero": "included target day as common zero no-trade"}
    aligned.update(values)
    write_json(OUT / "daily_net_pnl_aligned.json", aligned)
    write_json(OUT / "bootstrap.json", boot)
    final = {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "A_direction_counts": {"long": long_count, "short": short_count}, "positive_months_of_54": sum(amount > 0 for amount in monthly.values()), "monthly_A_net_jpy": monthly, "conditions": results, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; day session only; 2025 Jan-Jun; no WFA/OOS/Final Holdout"}
    write_json(OUT / "development_results.json", final)
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
