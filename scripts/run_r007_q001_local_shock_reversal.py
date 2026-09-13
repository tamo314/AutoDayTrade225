"""Execute the preregistered Development-only R007-Q001 experiment."""

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
from n225m_bt.research.r007 import local_shock_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.local_shock_reversal import Condition, LocalShockReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r007-q001-20260913-local-shock-reversal-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
R004_OUT = ROOT / "results" / "research" / "r004-q001-20260913-failed-breakout-01"
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
EXPECTED_QUARANTINE = {
    "quarantined_sessions": 45,
    "quarantined_bars": 27345,
    "included_bars": 1326086,
    "included_sessions": 2216,
    "quarantined_sessions_by_type": {"day": 20, "night": 25},
}
BasicCondition = Literal["A_local_shock_reversal", "B_always_long", "C_always_short"]
CONDITIONS: tuple[BasicCondition, ...] = (
    "A_local_shock_reversal",
    "B_always_long",
    "C_always_short",
)


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, Any]]:
    """Reproduce the frozen R004 whole-session tick-grid isolation exactly."""
    grouped = session_groups(data.bars)
    bad = {
        key
        for key, bars in grouped.items()
        if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in bars)
    }
    included = [bar for key, bars in grouped.items() if key not in bad for bar in bars]
    sessions = [
        {"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(bad)
    ]
    audit: dict[str, Any] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(bad),
        "quarantined_sessions_by_type": dict(sorted(Counter(s.value for _, s in bad).items())),
        "included_bars": len(included),
        "included_sessions": len(grouped) - len(bad),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in bar.quality_flags for bar in included
        ),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": sessions,
        "quarantined_session_list_hash": canonical_hash(sessions),
        "legacy_r003_q001_session_list": "NOT_PERSISTED_IN_LEGACY_ARTIFACT",
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
    version = canonical_hash(
        {"parent": data.data_version, "rule": audit["rule"], "sessions": sessions}
    )
    return ResearchData(included, version, data.quality | {"quarantine": audit}), audit


def schedule_table(classifier: CalendarClassifier) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for day in (date(2021, 9, 17), date(2021, 9, 21), date(2024, 11, 5), date(2024, 11, 6)):
        for session in (Session.DAY, Session.NIGHT):
            start, close = (
                classifier.session_open(day, session),
                classifier.session_close(day, session),
            )
            rows.append(
                {
                    "trade_date_example": day.isoformat(),
                    "session": session.value,
                    "S_jst": start.isoformat(),
                    "candidate_start_jst": (start + timedelta(minutes=61)).isoformat(),
                    "candidate_end_jst": (start + timedelta(minutes=180)).isoformat(),
                    "new_entry_cutoff_jst": (close - timedelta(minutes=15)).isoformat(),
                    "F_force_flat_jst": (close - timedelta(minutes=5)).isoformat(),
                }
            )
    return rows


def preregistration(source: dict[str, object], classifier: CalendarClassifier) -> dict[str, Any]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R007-Q001",
        "status": "frozen_before_r007_price_statistics_events_or_pnl",
        "scope": "Development only: trade_date 2021-01-01..2025-06-30. This follows observed Development exploration and is not independent confirmation.",
        "novelty_correspondence": {
            "R001_R003": "session opening range/direction and its state filter; not a local 60-change median scale or first intraday one-minute shock",
            "R004_R005": "failed breakout/session-end direction; not local-price shock reversal or a 15-minute fixed hold",
            "R006": "session-to-session night/day gap with 60-minute hold; not same-session q_t against m_t",
            "conclusion": "No prior registered R001-R006 rule uses the same reference scale, event direction, and 15-minute holding window.",
        },
        "hypothesis": "Against the first one-minute change exceeding its immediately prior usual variation, A has positive post-cost results and higher daily Net than same-event always-long B and always-short C.",
        "mechanism_candidate": "Temporary liquidity scarcity may displace price and then recover; price-only evidence does not identify liquidity or order flow.",
        "alternative_explanations": [
            "information-driven continuation",
            "directional drift",
            "volatility expansion",
            "few-winner concentration",
            "reversal below costs",
        ],
        "fixed_event_and_execution": {
            "sessions": "day and night independently, with versioned scheduled S/F; never inferred from first or final observed bar",
            "candidates": "t=S+61 through S+180 inclusive; each requires all same-session expected timestamps t-61 through t (62 eligible bars)",
            "statistics": "q_t=close_t-close_(t-1); m_t=arithmetic mean of the central two values of 60 abs changes j=t-60..t-1; q_t itself is excluded",
            "threshold": "T_t=max(4*m_t,4*tick_size)=max(4*m_t,20 points); equality qualifies; m_t=0 uses 20-point floor",
            "event": "first qualifying evaluable candidate only; unevaluable candidates are recorded and later recovered windows remain searchable; after event no replacement follows cancel/nonfill",
            "planned_orders": "E=t+1; X=E+15; issue EXIT after X-1 close for earliest X open. E must be inside existing entry cutoff and X<=F. Delayed entry never extends X.",
            "conditions": {
                "A_local_shock_reversal": "q>0 short; q<0 long",
                "B_always_long": "long at same event",
                "C_always_short": "short at same event",
            },
            "prohibited": [
                "threshold/window optimization",
                "volume",
                "additional filter",
                "re-entry",
                "stop",
                "target",
                "combined portfolio",
            ],
            "schedule_versions": schedule_table(classifier),
        },
        "quality_and_inputs": {
            "physical_input": "Development-selected normalized center_continuous Parquet partitions only; no OOS or Final Holdout paths selected",
            "logical_price_access": "trade_date 2021-01-01..2025-06-30 only; load_split filters before collect",
            "r004_quarantine": {
                "parent_data_version": EXPECTED_PARENT_VERSION,
                "quarantine_list_hash": EXPECTED_QUARANTINE_HASH,
                "required": EXPECTED_QUARANTINE,
            },
            "quality_ceiling": "PASS_LIMITED: unresolved contract/roll/adjustment provenance, legacy R003 isolation-list limitation, and post-quality session conditioning remain.",
        },
        "costs": {
            "tick_size_points": 5,
            "point_value_jpy": 100,
            "quantity": 1,
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "stress": {"A_only_slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; slippage attribution is never deducted twice.",
        },
        "evaluation": {
            "aligned_daily": "sum included day/night session Nets by trade_date, fill absent sessions as 0; both isolated excluded, one isolated retains its other session",
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10000,
                "seed": 20260913,
                "same_indices_A_B_C": True,
                "sampling": "non-circular moving blocks with replacement, tail truncate",
                "ci": "95% linear percentile",
            },
            "continue_requires_all": [
                "A/B/C >=200 trades",
                "A long/short >=50",
                "A Net>0 and PF>1",
                "all A/A-B/A-C bootstrap lower bounds >0",
                "A 2-tick expectancy>0",
                "A positive months>=27/54",
                "A excluding top10 winners Net>0",
            ],
            "decision": "BLOCKED for premise/path/accounting failure; INCONCLUSIVE for count failure; REJECT after adequate counts on any failure; CONTINUE_DEV_ONLY only if all; never CANDIDATE.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "strategy_sha256": hashes["src/n225m_bt/strategies/local_shock_reversal.py"],
            "event_selector_sha256": hashes["src/n225m_bt/research/r007.py"],
            "config_sha256": {
                name: next(
                    value for key, value in hashes.items() if key.endswith(f"config/{name}.yaml")
                )
                for name in ("backtest", "sessions", "instrument", "research")
            },
            "r004_preflight_sha256": sha256((R004_OUT / "preflight.json").read_bytes()).hexdigest(),
        },
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def linear_percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    low, high = floor(position), ceil(position)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    )


def bootstrap(values_a: list[int], values_b: list[int], values_c: list[int]) -> dict[str, Any]:
    if not values_a or len(values_a) != len(values_b) or len(values_a) != len(values_c):
        raise ValueError("invalid aligned A/B/C daily inputs")
    count, block, rng = len(values_a), min(20, len(values_a)), Random(20260913)
    samples: dict[str, list[float]] = {"A": [], "A_minus_B": [], "A_minus_C": []}
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        indices = indices[:count]
        samples["A"].append(fmean(values_a[index] for index in indices))
        samples["A_minus_B"].append(fmean(values_a[index] - values_b[index] for index in indices))
        samples["A_minus_C"].append(fmean(values_a[index] - values_c[index] for index in indices))

    def estimate(key: str, value: float) -> dict[str, object]:
        return {
            "estimate": value,
            "ci95_percentile_linear": [
                linear_percentile(samples[key], 0.025),
                linear_percentile(samples[key], 0.975),
            ],
        }

    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": 20260913,
        "same_resampling_indices_A_B_C": True,
        "percentile_implementation": "linear interpolation at (n-1)*q",
        "A_daily_mean_net_jpy": estimate("A", fmean(values_a)),
        "A_minus_B_daily_mean_net_jpy": estimate(
            "A_minus_B", fmean(a - b for a, b in zip(values_a, values_b, strict=True))
        ),
        "A_minus_C_daily_mean_net_jpy": estimate(
            "A_minus_C", fmean(a - c for a, c in zip(values_a, values_c, strict=True))
        ),
    }


def all_months() -> list[str]:
    return [
        f"{year:04d}-{month:02d}"
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    ]


def run_condition(
    data: ResearchData, classifier: CalendarClassifier, engine: BacktestEngine, condition: Condition
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int], list[str]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    target_dates = sorted({day.isoformat() for day, _ in groups})
    for (trade_day, session), bars in sorted(groups.items()):
        audit["included_target_sessions"] += 1
        event = local_shock_event(classifier, trade_day, session, bars)
        event["condition"] = condition
        audit[f"event_{event['status']}"] += 1
        for key in (
            "candidate_total",
            "candidate_unevaluable",
            "candidate_scheduled_outside_execution_window",
            "candidate_evaluable_no_shock",
        ):
            audit[key] += int(event.get(key, 0))
        if event["status"] != "event":
            events.append(event)
            continue
        signal_time = datetime.fromisoformat(cast(str, event["t_signal_jst"]))
        strategy = LocalShockReversalStrategy(
            f"r007_q001_{condition}", signal_time, condition, cast(int, event["q_t_points"])
        )
        result = engine.run(
            bars,
            strategy,
            canonical_hash(
                {
                    "condition": condition,
                    "trade_date": trade_day.isoformat(),
                    "session": session.value,
                    "event": event,
                    "data_version": data.data_version,
                }
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R007 produced more than one trade per condition/session")
        event.update(strategy.finalize(bars))
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update(
                {
                    "status": "filled",
                    "side": trade.side.value,
                    "entry_ts_jst": trade.entry_ts.isoformat(),
                    "exit_ts_jst": trade.exit_ts.isoformat(),
                    "entry_delay_minutes": int(
                        (trade.entry_ts - signal_time - timedelta(minutes=1)).total_seconds() // 60
                    ),
                    "exit_delay_minutes": int(
                        (trade.exit_ts - signal_time - timedelta(minutes=16)).total_seconds() // 60
                    ),
                    "exit_reason": trade.exit_reason.value,
                    "gross_pnl_jpy": trade.gross_pnl_jpy,
                    "fees_jpy": trade.fees_jpy,
                    "slippage_cost_jpy": trade.slippage_cost_jpy,
                    "net_pnl_jpy": trade.net_pnl_jpy,
                }
            )
            trades.append(trade)
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
        else:
            event["status"] = "event_order_unfilled"
            audit["event_order_unfilled"] += 1
        events.append(event)
    stable = tuple(
        replace(trade, trade_id=f"trade-{index:06d}")
        for index, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
    )
    return stable, events, dict(sorted(audit.items())), target_dates


def daily_for_targets(trades: tuple[Trade, ...], target_dates: list[str]) -> dict[str, int]:
    daily = dict.fromkeys(target_dates, 0)
    for trade in trades:
        daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return daily


def write_condition(
    folder: Path,
    condition: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    metrics: dict[str, object],
    audit: dict[str, int],
    data: ResearchData,
    ticks: int,
    daily: dict[str, int],
) -> None:
    write_results(
        folder,
        trades,
        (),
        {
            "experiment_id": folder.name,
            "campaign_id": IDENTIFIER,
            "condition": condition,
            "status": "complete",
            "data_version": data.data_version,
            "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30},
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {"trade_date": sorted(daily), "net_pnl_jpy": [daily[key] for key in sorted(daily)]}
    ).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(
    events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int
) -> dict[str, object]:
    filled = [event for event in events if event.get("status") == "filled"]
    checks = {
        "one_trade_per_filled_event": len(filled) == len(trades),
        "entry_within_max_delay": all(
            0 <= cast(int, event["entry_delay_minutes"]) <= max_delay for event in filled
        ),
        "scheduled_signal_exit_within_max_delay": all(
            event["exit_reason"] == "signal"
            and 0 <= cast(int, event["exit_delay_minutes"]) <= max_delay
            for event in filled
        ),
        "no_force_flat": all(trade.exit_reason.value != "force_flat" for trade in trades),
        "no_end_of_data": all(trade.exit_reason.value != "end_of_data" for trade in trades),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades
        ),
    }
    return {
        "checks": checks,
        "all_pass": all(checks.values()),
        "filled_paths": [
            {
                key: event.get(key)
                for key in (
                    "trade_date",
                    "session",
                    "t_signal_jst",
                    "E_planned_entry_jst",
                    "entry_ts_jst",
                    "X_planned_exit_jst",
                    "exit_ts_jst",
                    "entry_delay_minutes",
                    "exit_delay_minutes",
                    "exit_reason",
                )
            }
            for event in filled
        ],
        "accounting": "slippage attribution informational only; never additionally subtracted",
    }


def main() -> None:
    if OUT.exists():
        raise ValueError(f"R007 output already exists and must never be overwritten: {OUT}")
    if not (R004_OUT / "preflight.json").exists():
        raise ValueError("R004 frozen preflight artifact is missing")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.max_fill_delay_minutes,
        baseline.execution.allow_cross_session_pending_order,
        baseline.risk.new_entry_cutoff_minutes_before_session_close,
        baseline.risk.force_flat_minutes_before_session_close,
    ) != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R007 specification")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    source, plan = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        None,
    )
    plan = preregistration(source, classifier)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "effective_config.json",
        {
            "instrument": instrument.model_dump(mode="json"),
            "backtest": baseline.model_dump(mode="json"),
            "schedule": schedule_table(classifier),
        },
    )
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "preregistered_before_r007_prices",
            "source": source,
            "plan_hash": canonical_hash(plan),
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    check = run(
        [executable, "-m", "pytest", "tests/test_r007_q001.py", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    validation = {
        "status": "PASS" if check.returncode == 0 else "BLOCKED",
        "returncode": check.returncode,
        "stdout": check.stdout,
        "stderr": check.stderr,
        "coverage": [
            "62 bars/60 changes",
            "even median/exclusion/floor/equality",
            "candidate boundaries/first event",
            "night trade_date/session boundary",
            "missing-window recovery",
            "next-open/fixed exit/delay",
            "controls/max one trade/accounting",
        ],
    }
    write_json(OUT / "pre_execution_validation.json", validation)
    if check.returncode:
        raise ValueError("R007 synthetic validation failed before Development data access")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit = quarantine(development)
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": quarantine_audit,
            "physical_io": "Development-selected Parquet partitions only; OOS/Final Holdout never selected",
            "logical_price_access": "trade_date 2021-01-01..2025-06-30 only",
            "quality_limit": plan["quality_and_inputs"]["quality_ceiling"],
        },
    )
    engine, results, trades_by, events_by, daily_by, target_dates = (
        BacktestEngine(instrument.instrument.to_spec(), baseline, classifier),
        {},
        {},
        {},
        {},
        None,
    )
    for condition in CONDITIONS:
        trades, events, audit, dates = run_condition(view, classifier, engine, condition)
        if target_dates is None:
            target_dates = dates
        elif target_dates != dates:
            raise ValueError("condition target-date universe differs")
        metrics, daily = research_metrics(trades, view.bars), daily_for_targets(trades, dates)
        write_condition(
            reserve_directory(OUT, condition),
            condition,
            trades,
            events,
            metrics,
            audit,
            view,
            1,
            daily,
        )
        results[condition] = {
            "trade_count": len(trades),
            "metrics": metrics,
            "execution_audit": audit,
        }
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, daily
    stress_config = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})}
    )
    stress_trades, stress_events, stress_audit, stress_dates = run_condition(
        view,
        classifier,
        BacktestEngine(instrument.instrument.to_spec(), stress_config, classifier),
        "A_local_shock_reversal",
    )
    if target_dates != stress_dates:
        raise ValueError("stress target-date universe differs")
    stress_metrics, stress_daily = (
        research_metrics(stress_trades, view.bars),
        daily_for_targets(stress_trades, stress_dates),
    )
    write_condition(
        reserve_directory(OUT, "A_local_shock_reversal_2tick"),
        "A_local_shock_reversal_2tick",
        stress_trades,
        stress_events,
        stress_metrics,
        stress_audit,
        view,
        2,
        stress_daily,
    )
    results["A_local_shock_reversal_2tick"] = {
        "trade_count": len(stress_trades),
        "metrics": stress_metrics,
        "execution_audit": stress_audit,
    }
    post_audits = {
        condition: path_audit(
            events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes
        )
        for condition in CONDITIONS
    } | {
        "A_local_shock_reversal_2tick": path_audit(
            stress_events, stress_trades, baseline.execution.max_fill_delay_minutes
        )
    }
    post = {
        "status": "PASS"
        if all(cast(bool, audit["all_pass"]) for audit in post_audits.values())
        else "BLOCKED",
        "conditions": post_audits,
        "policy": "Any path failure blocks the campaign; no trade removed.",
    }
    write_json(OUT / "post_execution_validation.json", post)
    assert target_dates is not None
    values = {
        condition: [daily_by[condition][day] for day in target_dates] for condition in CONDITIONS
    }
    boot = bootstrap(
        values["A_local_shock_reversal"], values["B_always_long"], values["C_always_short"]
    )
    a_metrics = cast(dict[str, Any], results["A_local_shock_reversal"]["metrics"])
    a_overall = cast(dict[str, Any], a_metrics["overall"])
    stress_overall = cast(dict[str, Any], stress_metrics["overall"])
    months = {month: 0 for month in all_months()}
    for day, value in daily_by["A_local_shock_reversal"].items():
        months[day[:7]] += value
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_local_shock_reversal"])
    short_count = len(trades_by["A_local_shock_reversal"]) - long_count
    checks = {
        "A_trade_count_at_least_200": len(trades_by["A_local_shock_reversal"]) >= 200,
        "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200,
        "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200,
        "A_long_at_least_50": long_count >= 50,
        "A_short_at_least_50": short_count >= 50,
        "A_net_positive": a_overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": a_overall["profit_factor"] is not None
        and a_overall["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": boot["A_daily_mean_net_jpy"]["ci95_percentile_linear"][0] > 0,
        "A_minus_B_bootstrap_lower_above_0": boot["A_minus_B_daily_mean_net_jpy"][
            "ci95_percentile_linear"
        ][0]
        > 0,
        "A_minus_C_bootstrap_lower_above_0": boot["A_minus_C_daily_mean_net_jpy"][
            "ci95_percentile_linear"
        ][0]
        > 0,
        "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None
        and stress_overall["expectancy_jpy"] > 0,
        "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])[
            "net_excluding_top10_jpy"
        ]
        > 0,
    }
    count_keys = [
        "A_trade_count_at_least_200",
        "B_trade_count_at_least_200",
        "C_trade_count_at_least_200",
        "A_long_at_least_50",
        "A_short_at_least_50",
    ]
    decision = (
        "BLOCKED"
        if post["status"] != "PASS"
        else "INCONCLUSIVE"
        if not all(checks[key] for key in count_keys)
        else "CONTINUE_DEV_ONLY"
        if all(checks.values())
        else "REJECT"
    )
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {
            "trade_dates": target_dates,
            **values,
            "no_trade": "0",
            "both_sessions_quarantined": "excluded",
            "one_session_quarantined": "remaining included session retained",
        },
    )
    write_json(OUT / "bootstrap.json", boot)
    write_json(
        OUT / "development_results.json",
        {
            "campaign_id": IDENTIFIER,
            "quality_status": "PASS_LIMITED",
            "decision": decision,
            "checks": checks,
            "A_direction_counts": {"long": long_count, "short": short_count},
            "positive_months_of_54": sum(value > 0 for value in months.values()),
            "monthly_A_net_jpy": months,
            "conditions": results,
            "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction",
            "scope": "Development only; 2025 Jan-Jun; WFA/OOS/Final Holdout not run",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "campaign_id": IDENTIFIER,
            "status": "development_complete",
            "decision": decision,
            "quality_status": "PASS_LIMITED",
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
