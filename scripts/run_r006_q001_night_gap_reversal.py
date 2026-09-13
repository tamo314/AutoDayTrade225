"""Execute preregistered Development-only R006-Q001 night/day gap reversal."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from random import Random
from statistics import fmean
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
from n225m_bt.research.r006 import (
    night_reference_time,
    reference_event,
    session_groups,
)
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.night_gap_reversal import Condition, NightGapReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r006-q001-20260913-night-gap-reversal-01"
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
BasicCondition = Literal["A_gap_reversal", "B_always_long", "C_always_short"]
CONDITIONS: tuple[BasicCondition, ...] = ("A_gap_reversal", "B_always_long", "C_always_short")


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, Any], set[tuple[date, Session]]]:
    """Reproduce R004's fixed whole-session tick-grid isolation exactly."""
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
        "quarantined_sessions_by_type": dict(
            sorted(Counter(session.value for _, session in bad).items())
        ),
        "included_bars": len(included),
        "included_sessions": len(grouped) - len(bad),
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
    audit["expected_match"] = not mismatches
    audit["mismatches"] = mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    version = canonical_hash(
        {"parent": data.data_version, "rule": audit["rule"], "sessions": sessions}
    )
    return ResearchData(included, version, data.quality | {"quarantine": audit}), audit, bad


def schedule_table(classifier: CalendarClassifier) -> list[dict[str, str]]:
    """Freeze actual versioned timestamps, including the auction distinction."""
    rows: list[dict[str, str]] = []
    for day in (
        date(2021, 9, 17),
        date(2021, 9, 21),
        date(2021, 9, 22),
        date(2024, 11, 5),
        date(2024, 11, 6),
    ):
        C_start, C_known, version, basis = night_reference_time(classifier, day)
        S = classifier.session_open(day, Session.DAY)
        rows.append(
            {
                "trade_date_example": day.isoformat(),
                "night_schedule_version": version,
                "C_normal_bar_start_jst": C_start.isoformat(),
                "C_close_known_jst": C_known.isoformat(),
                "C_selection_basis": basis,
                "S_day_open_bar_start_jst": S.isoformat(),
                "E_entry_jst": (S + timedelta(minutes=1)).isoformat(),
                "EXIT_signal_bar_start_jst": (S + timedelta(minutes=60)).isoformat(),
                "EXIT_fill_jst": (S + timedelta(minutes=61)).isoformat(),
            }
        )
    return rows


def preregistration(source: dict[str, object], classifier: CalendarClassifier) -> dict[str, Any]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R006-Q001",
        "status": "frozen_before_r006_price_statistics_events_or_pnl",
        "scope": "Development only: trade_date 2021-01-01..2025-06-30. Existing Development results from R001-R005 were already observed; this is additional exploratory Development work, not unused validation.",
        "hypothesis": "Holding the opposite direction of G=O-C for the fixed first 60 minutes after planned day entry has positive post-cost expectancy and higher daily Net than both always-long and always-short controls on the same pre-decidable G!=0 events.",
        "alternative_explanations": [
            "persistent directional drift",
            "information-driven gap continuation",
            "concentration in a few abrupt moves",
            "reversal smaller than costs",
        ],
        "mechanism_limit": "Price-only evidence does not establish an order-flow or liquidity mechanism.",
        "conditions": {
            "A_gap_reversal": "G>0 short, G<0 long, G=0 skip",
            "B_always_long": "long whenever the same reference is available and G!=0",
            "C_always_short": "short whenever the same reference is available and G!=0",
            "A_2tick_stress": "A order path replayed by the unmodified engine at 2 ticks/side; fee unchanged",
            "prohibited": [
                "gap threshold",
                "first-bar direction confirmation",
                "volume",
                "weekday",
                "year",
                "volatility",
                "side filter",
                "stop",
                "target",
                "reentry",
                "combined portfolio",
            ],
        },
        "reference_and_execution": {
            "S": "scheduled day session_open, not an observed first row; O is its one-minute bar open and is usable after S bar close",
            "night_link": "the scheduled night belonging to the same OSE trade_date, established by versioned exchange calendar mapping; neither calendar_date matching nor a nearest observed session is used",
            "C": "close of schedule-defined final normal one-minute night bar. When regular_end_next_day exists, C bar starts regular_end-1 minute and the separate closing auction is excluded; otherwise C bar starts session_close-1 minute because no finer auction metadata is frozen.",
            "signal": "evaluate G=O-C after S closes; first possible entry fill is E=S+1 minute open",
            "exit": "after the S+60 bar close issue EXIT; first possible fill is scheduled E+60=S+61 minute open. Entry delay never extends the planned exit.",
            "one_trade": "one contract, maximum one position and one attempted entry per condition/day session",
            "missing": "existing engine max fill delay/cancel/end-of-data/force-flat contracts remain unchanged; a reference-night quarantine or missing/ineligible required reference/signal produces a recorded common no-trade and never substitutes an earlier/nearest price.",
            "schedule_versions": schedule_table(classifier),
        },
        "quality_and_inputs": {
            "physical_input": "only selected Development normalized center_continuous Parquet partitions; trade_date filtered before collect. OOS and Final Holdout paths are not selected or read.",
            "range_rule": "the associated night is limited to the same in-range trade_date. A first-in-range day requiring out-of-range reference would be skipped rather than substituted.",
            "r004_quarantine": {
                "parent_data_version": EXPECTED_PARENT_VERSION,
                "quarantine_list_hash": EXPECTED_QUARANTINE_HASH,
                "required": EXPECTED_QUARANTINE,
            },
            "quality_ceiling": "PASS_LIMITED only: continuous-series whole-session isolation is post-quality; constituent contract, roll, and adjustment provenance are unresolved. Inter-session gaps are particularly sensitive to this limitation and no result proves executable individual-contract profit.",
        },
        "costs": {
            "tick_size_points": 5,
            "point_value_jpy": 100,
            "quantity": 1,
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "stress": {"A_only_slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "accounting": "Gross is fill-to-fill and already incorporates fill slippage; Net=Gross-fees; slippage attribution is not deducted twice.",
        },
        "evaluation": {
            "aligned_daily": "all included target day-session trade_dates; common no-trades are 0 and day-session quarantines are excluded. Comparison never restricts to common actual fills.",
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10000,
                "seed": 20260913,
                "same_indices_A_B_C": True,
                "sampling": "non-circular moving blocks with replacement and tail truncation to original length",
                "ci": "95% percentile using linear interpolation",
            },
            "continue_requires_all": [
                "each A/B/C baseline >=200 trades",
                "A long and short >=50 each",
                "A Net>0 and PF>1",
                "lower 95% bootstrap bounds for A daily mean, A-B, and A-C all >0",
                "A 2-tick expectancy>0",
                "positive months >=27 of all 54 months",
                "A Net excluding top 10 positive trades>0",
            ],
            "decision": "BLOCKED for schedule/input/implementation/accounting/path failure; INCONCLUSIVE for count failure; REJECT after count sufficiency if any criterion fails; CONTINUE_DEV_ONLY only if every criterion passes; never CANDIDATE.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "strategy_sha256": hashes["src/n225m_bt/strategies/night_gap_reversal.py"],
            "reference_sha256": hashes["src/n225m_bt/research/r006.py"],
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
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = floor(position), ceil(position)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    )


def bootstrap(values_a: list[int], values_b: list[int], values_c: list[int]) -> dict[str, Any]:
    if not values_a or len(values_a) != len(values_b) or len(values_a) != len(values_c):
        raise ValueError("invalid aligned A/B/C daily inputs")
    count, block = len(values_a), min(20, len(values_a))
    starts, random = list(range(count - block + 1)), Random(20260913)
    samples: dict[str, list[float]] = {"A": [], "A_minus_B": [], "A_minus_C": []}
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = starts[random.randrange(len(starts))]
            indices.extend(range(start, start + block))
        indices = indices[:count]
        samples["A"].append(fmean(values_a[index] for index in indices))
        samples["A_minus_B"].append(fmean(values_a[index] - values_b[index] for index in indices))
        samples["A_minus_C"].append(fmean(values_a[index] - values_c[index] for index in indices))

    def result(key: str, estimate: float) -> dict[str, object]:
        return {
            "estimate": estimate,
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
        "A_daily_mean_net_jpy": result("A", fmean(values_a)),
        "A_minus_B_daily_mean_net_jpy": result(
            "A_minus_B", fmean(a - b for a, b in zip(values_a, values_b, strict=True))
        ),
        "A_minus_C_daily_mean_net_jpy": result(
            "A_minus_C", fmean(a - c for a, c in zip(values_a, values_c, strict=True))
        ),
    }


def all_months() -> list[str]:
    result: list[str] = []
    year, month = 2021, 1
    while (year, month) <= (2025, 6):
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def execution_path_audit(
    events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int
) -> dict[str, object]:
    filled = [row for row in events if row.get("status") == "filled"]
    paths = [
        {
            "trade_date": row["trade_date"],
            "scheduled_entry_jst": row["E_planned_entry_jst"],
            "actual_entry_jst": row["entry_ts_jst"],
            "scheduled_exit_jst": row["EXIT_planned_fill_jst"],
            "actual_exit_jst": row["exit_ts_jst"],
            "entry_delay_minutes": row["entry_delay_minutes"],
            "exit_delay_minutes": row["exit_delay_minutes"],
            "exit_reason": row["exit_reason"],
        }
        for row in filled
    ]
    checks = {
        "one_trade_per_filled_event": len(filled) == len(trades),
        "entry_within_max_delay": all(
            0 <= cast(int, row["entry_delay_minutes"]) <= max_delay for row in paths
        ),
        "exit_signal_within_max_delay": all(
            row["exit_reason"] == "signal"
            and 0 <= cast(int, row["exit_delay_minutes"]) <= max_delay
            for row in paths
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
        "filled_paths": paths,
        "accounting": "slippage attribution is informational only and not additionally subtracted",
    }


def run_condition(
    data: ResearchData,
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    condition: Condition,
    quarantined: set[tuple[date, Session]],
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int], list[str]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    target_dates: list[str] = []
    for (trade_day, session), day_bars in sorted(groups.items()):
        if session is not Session.DAY:
            continue
        target_dates.append(trade_day.isoformat())
        event = reference_event(
            classifier,
            trade_day,
            day_bars,
            groups.get((trade_day, Session.NIGHT)),
            reference_night_quarantined=(trade_day, Session.NIGHT) in quarantined,
        )
        event["condition"] = condition
        audit["target_day_sessions"] += 1
        audit[f"event_{event.get('reason', 'unknown')}"] += 1
        if event["status"] != "eligible":
            events.append(event)
            continue
        gap = cast(int, event["G_points"])
        strategy = NightGapReversalStrategy(
            f"r006_q001_{condition}",
            classifier.session_open(trade_day, Session.DAY),
            condition,
            gap,
        )
        result = engine.run(
            day_bars,
            strategy,
            canonical_hash(
                {
                    "condition": condition,
                    "trade_date": trade_day.isoformat(),
                    "data_version": data.data_version,
                }
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R006 produced more than one trade in a day session")
        event.update(strategy.finalize(day_bars))
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
                        (
                            trade.entry_ts
                            - (
                                classifier.session_open(trade_day, Session.DAY)
                                + timedelta(minutes=1)
                            )
                        ).total_seconds()
                        // 60
                    ),
                    "exit_delay_minutes": int(
                        (
                            trade.exit_ts
                            - (
                                classifier.session_open(trade_day, Session.DAY)
                                + timedelta(minutes=61)
                            )
                        ).total_seconds()
                        // 60
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
            audit["eligible_without_trade"] += 1
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
    target_daily: dict[str, int],
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
        {
            "trade_date": sorted(target_daily),
            "net_pnl_jpy": [target_daily[key] for key in sorted(target_daily)],
        }
    ).write_parquet(folder / "daily_net_pnl.parquet")


def main() -> None:
    if OUT.exists():
        raise ValueError(f"R006 output already exists and must never be overwritten: {OUT}")
    if not (R004_OUT / "preflight.json").exists():
        raise ValueError("R004 frozen preflight artifact is missing")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.max_fill_delay_minutes,
        baseline.execution.allow_cross_session_pending_order,
    ) != (1, 30, 10, False):
        raise ValueError("active execution/cost contract differs from frozen R006 specification")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
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
            "status": "preregistered_before_r006_prices",
            "source": source,
            "plan_hash": canonical_hash(plan),
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    # Only after immutable registration and source/config/input identifiers exist do prices load.
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, bad = quarantine(development)
    preflight = {
        "status": "PASS_LIMITED",
        "development_input": development.quality,
        "quarantine": quarantine_audit,
        "physical_io": "Development-selected Parquet partitions only; OOS and Final Holdout partitions never selected",
        "logical_price_access": "trade_date 2021-01-01..2025-06-30 only",
        "reference_contract": "all C timestamps derive from schedule/calendar before looking up a bar; no observed-last-bar substitution",
        "quality_limit": plan["quality_and_inputs"]["quality_ceiling"],
    }
    write_json(OUT / "preflight.json", preflight)
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, dict[str, object]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    target_dates: list[str] | None = None
    daily_by: dict[str, dict[str, int]] = {}
    for condition in CONDITIONS:
        folder = reserve_directory(OUT, condition)
        trades, events, audit, dates = run_condition(view, classifier, engine, condition, bad)
        if target_dates is None:
            target_dates = dates
        elif target_dates != dates:
            raise ValueError("target day universe differs across fixed conditions")
        metrics, daily = research_metrics(trades, view.bars), daily_for_targets(trades, dates)
        write_condition(folder, condition, trades, events, metrics, audit, view, 1, daily)
        results[condition] = {
            "trade_count": len(trades),
            "metrics": metrics,
            "execution_audit": audit,
        }
        trades_by[condition] = trades
        events_by[condition] = events
        daily_by[condition] = daily
    stress_config = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})}
    )
    stress_trades, stress_events, stress_audit, stress_dates = run_condition(
        view,
        classifier,
        BacktestEngine(instrument.instrument.to_spec(), stress_config, classifier),
        "A_gap_reversal",
        bad,
    )
    if target_dates != stress_dates:
        raise ValueError("stress target day universe differs")
    stress_metrics, stress_daily = (
        research_metrics(stress_trades, view.bars),
        daily_for_targets(stress_trades, stress_dates),
    )
    write_condition(
        reserve_directory(OUT, "A_gap_reversal_2tick"),
        "A_gap_reversal_2tick",
        stress_trades,
        stress_events,
        stress_metrics,
        stress_audit,
        view,
        2,
        stress_daily,
    )
    results["A_gap_reversal_2tick"] = {
        "trade_count": len(stress_trades),
        "metrics": stress_metrics,
        "execution_audit": stress_audit,
    }
    path_audits = {
        condition: execution_path_audit(
            events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes
        )
        for condition in CONDITIONS
    } | {
        "A_gap_reversal_2tick": execution_path_audit(
            stress_events, stress_trades, baseline.execution.max_fill_delay_minutes
        )
    }
    post = {
        "status": "PASS"
        if all(cast(bool, audit["all_pass"]) for audit in path_audits.values())
        else "BLOCKED",
        "conditions": path_audits,
        "policy": "Any execution-path failure blocks the campaign; no trade is removed.",
    }
    write_json(OUT / "post_execution_validation.json", post)
    assert target_dates is not None
    values = {key: [daily_by[key][day] for day in target_dates] for key in CONDITIONS}
    boot = bootstrap(values["A_gap_reversal"], values["B_always_long"], values["C_always_short"])
    A = cast(dict[str, Any], results["A_gap_reversal"]["metrics"])
    A_overall = cast(dict[str, Any], A["overall"])
    stress_overall = cast(dict[str, Any], stress_metrics["overall"])
    months = {month: 0 for month in all_months()}
    for day, value in daily_by["A_gap_reversal"].items():
        months[day[:7]] += value
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_gap_reversal"])
    short_count = len(trades_by["A_gap_reversal"]) - long_count
    checks = {
        "A_trade_count_at_least_200": len(trades_by["A_gap_reversal"]) >= 200,
        "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200,
        "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200,
        "A_long_at_least_50": long_count >= 50,
        "A_short_at_least_50": short_count >= 50,
        "A_net_positive": A_overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": A_overall["profit_factor"] is not None
        and A_overall["profit_factor"] > 1,
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
        "A_net_excluding_top10_positive": cast(dict[str, Any], A["concentration"])[
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
        else (
            "INCONCLUSIVE"
            if not all(checks[key] for key in count_keys)
            else ("CONTINUE_DEV_ONLY" if all(checks.values()) else "REJECT")
        )
    )
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {
            "trade_dates": target_dates,
            "A_gap_reversal": values["A_gap_reversal"],
            "B_always_long": values["B_always_long"],
            "C_always_short": values["C_always_short"],
            "no_trade": "0",
            "day_session_quarantine": "excluded",
            "reference_night_quarantine": "included target day as common zero no-trade",
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
            "accounting": "Gross is fill-to-fill slippage-inclusive PnL; Net=Gross-fees; slippage attribution is not deducted again",
            "scope": "Development only; 2025 is Jan-Jun; WFA/OOS/Final Holdout not run",
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
