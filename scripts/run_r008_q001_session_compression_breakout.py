"""Execute the preregistered Development-only R008-Q001 experiment."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from sys import executable
from typing import Any, Literal, cast

import polars as pl

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r008 import compression_breakout_event, session_groups
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.session_compression_breakout import (
    Condition,
    SessionCompressionBreakoutStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r008-q001-20260913-session-compression-breakout-07"
OUT = ROOT / "results" / "research" / IDENTIFIER
R004_OUT = ROOT / "results" / "research" / "r004-q001-20260913-failed-breakout-01"
BasicCondition = Literal["A_compression_breakout", "B_always_long", "C_always_short"]
CONDITIONS: tuple[BasicCondition, ...] = (
    "A_compression_breakout",
    "B_always_long",
    "C_always_short",
)
ALL_CONDITIONS: tuple[Condition, ...] = (*CONDITIONS, "D_unfiltered_breakout")


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, Any]]:
    """Reproduce the frozen R004 whole-session tick-grid isolation exactly."""
    grouped = session_groups(data.bars)
    bad = {
        key
        for key, session_bars in grouped.items()
        if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in session_bars)
    }
    included = [
        bar for key, session_bars in grouped.items() if key not in bad for bar in session_bars
    ]
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
        "legacy_r003_q001_session_list": "NOT_PERSISTED_IN_LEGACY_ARTIFACT",
    }
    expected = {
        "parent_data_version": "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0",
        "quarantined_sessions": 45,
        "quarantined_bars": 27345,
        "included_bars": 1326086,
        "included_sessions": 2216,
        "quarantined_sessions_by_type": {"day": 20, "night": 25},
    }
    mismatches = {
        key: {"actual": audit.get(key), "expected": value}
        for key, value in expected.items()
        if audit.get(key) != value
    }
    if (
        audit["quarantined_session_list_hash"]
        != "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
    ):
        mismatches["quarantined_session_list_hash"] = {
            "actual": audit["quarantined_session_list_hash"],
            "expected": "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
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
    from datetime import date

    from n225m_bt.domain import Session

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
                    "candidate_start_jst": (start + timedelta(minutes=90)).isoformat(),
                    "candidate_end_jst": (start + timedelta(minutes=180)).isoformat(),
                    "new_entry_cutoff_jst": (close - timedelta(minutes=15)).isoformat(),
                    "F_force_flat_jst": (close - timedelta(minutes=5)).isoformat(),
                }
            )
    return rows


def all_months() -> list[str]:
    return [
        f"{year:04d}-{month:02d}"
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    ]


def linear_percentile(values: list[float], q: float) -> float:
    from math import ceil, floor

    ordered, position = sorted(values), (len(values) - 1) * q
    low, high = floor(position), ceil(position)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    )


def bootstrap(values_a: list[int], values_b: list[int], values_c: list[int]) -> dict[str, Any]:
    from random import Random
    from statistics import fmean

    if not values_a or len(values_a) != len(values_b) or len(values_a) != len(values_c):
        raise ValueError("invalid aligned daily bootstrap inputs")
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

    def estimate(key: str, value: float) -> dict[str, Any]:
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


def preregistration(source: dict[str, object], classifier: CalendarClassifier) -> dict[str, Any]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R008-Q001",
        "status": "frozen_before_r008_price_statistics_events_or_pnl",
        "scope": "Development only: trade_date 2021-01-01..2025-06-30. Known-Development exploratory evidence, not independent confirmation.",
        "novelty_correspondence": {
            "R001_R003": "fixed opening range/direction and cross-session opening-range history; not three same-session rolling 30-minute range windows after S+90.",
            "R004_R005": "failed breakout and session-end direction; not continuation after local range compression.",
            "R006": "night-to-day gap reversal; not a same-session local range event.",
            "R007": "one-minute local-shock reversal against a 60-change scale and 15-minute hold; R008 follows direction after 3x30-minute range compression for 30 minutes.",
            "conclusion": "No R001-R007 registered/evaluated rule shares the reference windows, compression inequality, continuation direction, and holding window; R003 is an unexecuted, distinct opening-range design.",
        },
        "hypothesis": "A follows the first close breakout after local range compression for 30 minutes and has positive post-cost results, exceeding same-event always-long B, always-short C, and unfiltered-breakout D.",
        "mechanism_candidate": "New price formation after locally compressed range may persist; this price-only mechanism is unverified.",
        "alternative_explanations": [
            "false breakout / mean reversion",
            "persistent directional drift",
            "time-of-day or volatility composition",
            "few-winner concentration",
            "trade-count and cost differences",
        ],
        "fixed_event_and_execution": {
            "sessions": "day/night independently; versioned planned S/F and existing cutoff/max delay, never inferred from observations.",
            "candidates": "t=S+90 through S+180 inclusive. E=t+1 and X=E+30; E<=existing new-entry cutoff (inclusive), X<=F. Each candidate first requires exactly 91 expected same-session eligible bars [t-90,t].",
            "windows": "W0=[t-30,t), W1=[t-60,t-30), W2=[t-90,t-60); t is excluded from all range windows. Rk=max(high)-min(low), U=max(high W0), L=min(low W0).",
            "compression": "R0>0, R1+R2>0, and 4*R0<=R1+R2; equality qualifies. No price rounding; tick_size=5; breakout is close_t>=U+5 long or close_t<=L-5 short.",
            "event": "Each rule takes its first qualifying evaluable candidate; missing windows may recover later. Once an event occurs it is never replaced after cancel/nonfill.",
            "conditions": {
                "A_compression_breakout": "compressed first breakout in its direction",
                "B_always_long": "long at A's preselected event",
                "C_always_short": "short at A's preselected event",
                "D_unfiltered_breakout": "first breakout under the same quality/time/range-positive rules but without compression; independent event universe.",
            },
            "orders": "signal after t close; next eligible E open; issue EXIT after X-1 close for earliest X open. Entry delay never extends X. No stop/target, one contract, one position, one trade/session/rule.",
            "prohibited": [
                "parameter/time/holding exploration",
                "filters",
                "re-entry",
                "R007 inverse trade",
                "existing-strategy reruns",
                "portfolio combination",
            ],
            "schedule_versions": schedule_table(classifier),
        },
        "quality_and_inputs": {
            "physical_input": "Development-selected normalized center_continuous Parquet partitions only; OOS and Final Holdout paths never selected.",
            "logical_price_access": "trade_date 2021-01-01..2025-06-30 only; split filter before collect.",
            "r004_quarantine_required": {
                "artifact": "r004-q001-20260913-failed-breakout-01/preflight.json",
                "included_bars": 1326086,
                "included_sessions": 2216,
                "quarantined_bars": 27345,
                "quarantined_sessions": 45,
            },
            "quality_ceiling": "PASS_LIMITED: unresolved contract/roll/adjustment provenance, legacy R003 list absence, and post-quality whole-session conditioning.",
        },
        "costs": {
            "tick_size_points": 5,
            "point_value_jpy": 100,
            "quantity": 1,
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "stress": {"A_only_slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; slippage attribution is not deducted twice.",
        },
        "evaluation": {
            "aligned_daily": "sum included day/night Net by trade_date; no-trade=0; both isolated excluded; one isolated keeps the other session.",
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10000,
                "seed": 20260913,
                "common_indices_A_B_C_D": True,
                "sampling": "non-circular moving blocks with replacement and tail truncate",
                "ci": "95% linear percentile",
            },
            "continue_requires_all": [
                "A/B/C/D >=200 trades",
                "A long/short >=50",
                "A Net>0 and PF>1",
                "A, A-B, A-C, A-D bootstrap lower bounds >0",
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
            "strategy_sha256": hashes["src/n225m_bt/strategies/session_compression_breakout.py"],
            "event_selector_sha256": hashes["src/n225m_bt/research/r008.py"],
            "test_sha256": sha256((ROOT / "tests" / "test_r008_q001.py").read_bytes()).hexdigest(),
            "r004_preflight_sha256": sha256((R004_OUT / "preflight.json").read_bytes()).hexdigest(),
        },
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def run_condition(
    data: ResearchData, classifier: CalendarClassifier, engine: BacktestEngine, condition: Condition
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int], list[str]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    require_compression = condition != "D_unfiltered_breakout"
    groups = session_groups(data.bars)
    for (trade_day, session), bars in sorted(groups.items()):
        audit["included_target_sessions"] += 1
        event = compression_breakout_event(
            classifier, trade_day, session, bars, require_compression=require_compression
        )
        event["condition"] = condition
        audit[f"event_{event['status']}"] += 1
        for key in (
            "candidate_total",
            "candidate_unevaluable",
            "candidate_scheduled_outside_execution_window",
            "candidate_zero_range",
            "candidate_not_compressed",
            "candidate_no_breakout",
        ):
            audit[key] += cast(int, event.get(key, 0))
        if event["status"] != "event":
            events.append(event)
            continue
        signal_time = datetime.fromisoformat(cast(str, event["t_signal_jst"]))
        strategy = SessionCompressionBreakoutStrategy(
            f"r008_q001_{condition}", signal_time, condition, cast(str, event["direction"])
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
            raise AssertionError("R008 produced more than one trade per condition/session")
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
                        (trade.exit_ts - signal_time - timedelta(minutes=31)).total_seconds() // 60
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
    return (
        stable,
        events,
        dict(sorted(audit.items())),
        sorted({day.isoformat() for day, _ in groups}),
    )


def daily_for_targets(trades: tuple[Trade, ...], targets: list[str]) -> dict[str, int]:
    daily = dict.fromkeys(targets, 0)
    for trade in trades:
        daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return daily


def bootstrap_lower_above_zero(bootstrap_output: dict[str, Any], name: str) -> bool:
    return cast(list[float], bootstrap_output[name]["ci95_percentile_linear"])[0] > 0


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


def finalize_from_artifacts() -> None:
    """Aggregate already-written one-condition runs without re-reading prices."""
    required = [*ALL_CONDITIONS, "A_compression_breakout_2tick"]
    missing = [name for name in required if not (OUT / name / "metrics_research.json").exists()]
    if missing:
        raise ValueError(f"cannot finalize R008; missing completed condition artifacts: {missing}")
    metrics: dict[str, dict[str, Any]] = {}
    audits: dict[str, dict[str, Any]] = {}
    events: dict[str, list[dict[str, Any]]] = {}
    daily: dict[str, dict[str, int]] = {}
    for condition in required:
        folder = OUT / condition
        metrics[condition] = json.loads(
            (folder / "metrics_research.json").read_text(encoding="utf-8")
        )
        audits[condition] = json.loads(
            (folder / "execution_audit.json").read_text(encoding="utf-8")
        )
        events[condition] = pl.read_parquet(folder / "events.parquet").to_dicts()
        daily[condition] = dict(
            zip(
                pl.read_parquet(folder / "daily_net_pnl.parquet")["trade_date"].to_list(),
                pl.read_parquet(folder / "daily_net_pnl.parquet")["net_pnl_jpy"].to_list(),
                strict=True,
            )
        )
    targets = sorted(daily["A_compression_breakout"])
    values = {name: [daily[name][day] for day in targets] for name in ALL_CONDITIONS}
    boot_abc = bootstrap(
        values["A_compression_breakout"], values["B_always_long"], values["C_always_short"]
    )
    boot_ad = bootstrap(
        values["A_compression_breakout"],
        values["D_unfiltered_breakout"],
        values["D_unfiltered_breakout"],
    )
    boot = boot_abc | {
        "A_minus_D_daily_mean_net_jpy": boot_ad["A_minus_B_daily_mean_net_jpy"],
        "common_indices_all_conditions": True,
    }
    shared = all(
        (a.get("status"), a.get("t_signal_jst"))
        == (b.get("status"), b.get("t_signal_jst"))
        == (c.get("status"), c.get("t_signal_jst"))
        for a, b, c in zip(
            events["A_compression_breakout"],
            events["B_always_long"],
            events["C_always_short"],
            strict=True,
        )
    )

    def route_ok(rows: list[dict[str, Any]]) -> bool:
        filled = [row for row in rows if row.get("status") == "filled"]
        return all(
            row.get("exit_reason") == "signal"
            and 0 <= int(row.get("entry_delay_minutes", -1)) <= 10
            and 0 <= int(row.get("exit_delay_minutes", -1)) <= 10
            and int(row.get("net_pnl_jpy", 0))
            == int(row.get("gross_pnl_jpy", 0)) - int(row.get("fees_jpy", 0))
            for row in filled
        )

    post = {
        "status": "PASS"
        if shared and all(route_ok(events[name]) for name in required)
        else "BLOCKED",
        "A_B_C_shared_pre_event": shared,
        "policy": "Artifact-only finalization; any path/accounting failure blocks campaign.",
    }
    a_rows, d_rows = events["A_compression_breakout"], events["D_unfiltered_breakout"]
    long_count = sum(row.get("side") == "long" for row in a_rows if row.get("status") == "filled")
    short_count = sum(row.get("side") == "short" for row in a_rows if row.get("status") == "filled")
    months = {month: 0 for month in all_months()}
    for day, value in daily["A_compression_breakout"].items():
        months[day[:7]] += value
    a_overall, stress_overall = (
        metrics["A_compression_breakout"]["overall"],
        metrics["A_compression_breakout_2tick"]["overall"],
    )
    checks = {
        "A_trade_count_at_least_200": metrics["A_compression_breakout"]["overall"]["trade_count"]
        >= 200,
        "B_trade_count_at_least_200": metrics["B_always_long"]["overall"]["trade_count"] >= 200,
        "C_trade_count_at_least_200": metrics["C_always_short"]["overall"]["trade_count"] >= 200,
        "D_trade_count_at_least_200": metrics["D_unfiltered_breakout"]["overall"]["trade_count"]
        >= 200,
        "A_long_at_least_50": long_count >= 50,
        "A_short_at_least_50": short_count >= 50,
        "A_net_positive": a_overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": a_overall["profit_factor"] is not None
        and a_overall["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": bootstrap_lower_above_zero(boot, "A_daily_mean_net_jpy"),
        "A_minus_B_bootstrap_lower_above_0": bootstrap_lower_above_zero(
            boot, "A_minus_B_daily_mean_net_jpy"
        ),
        "A_minus_C_bootstrap_lower_above_0": bootstrap_lower_above_zero(
            boot, "A_minus_C_daily_mean_net_jpy"
        ),
        "A_minus_D_bootstrap_lower_above_0": bootstrap_lower_above_zero(
            boot, "A_minus_D_daily_mean_net_jpy"
        ),
        "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None
        and stress_overall["expectancy_jpy"] > 0,
        "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "A_net_excluding_top10_positive": metrics["A_compression_breakout"]["concentration"][
            "net_excluding_top10_jpy"
        ]
        > 0,
    }
    count_keys = [
        key
        for key in checks
        if "trade_count" in key or key in {"A_long_at_least_50", "A_short_at_least_50"}
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
    write_json(OUT / "post_execution_validation.json", post)
    aligned: dict[str, object] = {
        "trade_dates": targets,
        "no_trade": "0",
        "both_sessions_quarantined": "excluded",
        "one_session_quarantined": "remaining included session retained",
    }
    aligned.update({str(key): value for key, value in values.items()})
    write_json(OUT / "daily_net_pnl_aligned.json", aligned)
    write_json(OUT / "bootstrap.json", boot)
    def is_event(row: dict[str, Any]) -> bool:
        return row.get("status") in {"event", "filled"}

    comparison = {
        "both": sum(
            is_event(a) and is_event(d)
            for a, d in zip(a_rows, d_rows, strict=True)
        ),
        "A_only": sum(
            is_event(a) and not is_event(d)
            for a, d in zip(a_rows, d_rows, strict=True)
        ),
        "D_only": sum(
            not is_event(a) and is_event(d)
            for a, d in zip(a_rows, d_rows, strict=True)
        ),
        "interpretation": "A-D includes event time, direction, trade count, and costs; it is not a causal compression-only effect.",
    }
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
            "conditions": {
                name: {
                    "trade_count": metrics[name]["overall"]["trade_count"],
                    "metrics": metrics[name],
                    "execution_audit": audits[name],
                }
                for name in required
            },
            "A_D_event_comparison": comparison,
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


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r008_q001_session_compression_breakout.py')

    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", choices=[*ALL_CONDITIONS, "A_compression_breakout_2tick"])
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if args.finalize:
        finalize_from_artifacts()
        return
    fresh = not OUT.exists()
    if not fresh and args.condition is None:
        raise ValueError(
            "existing R008 output requires --condition or --finalize; it is never overwritten"
        )
    if not (R004_OUT / "preflight.json").exists():
        raise ValueError("R004 frozen preflight artifact is missing")
    if fresh:
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
        raise ValueError("active execution/cost contract differs from frozen R008 specification")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    if fresh:
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
                "status": "preregistered_before_r008_prices",
                "source": source,
                "plan_hash": canonical_hash(plan),
                "oos": "NOT_EVALUATED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
        check = run(
            [executable, "-m", "pytest", "tests/test_r008_q001.py", "-q"],
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
                "W0/W1/W2/t separation",
                "91 required bars",
                "compression equality/non-compression/zero range",
                "breakout equality",
                "boundaries/recovery/session isolation",
                "A-D distinction and B/C event sharing",
                "next open/fixed exit/delay/max one/accounting",
            ],
        }
        write_json(OUT / "pre_execution_validation.json", validation)
        if check.returncode:
            raise ValueError("R008 synthetic validation failed before Development data access")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit = quarantine(development)
    if fresh:
        write_json(
            OUT / "preflight.json",
            {
                "status": "PASS_LIMITED",
                "development_input": development.quality,
                "quarantine": quarantine_audit,
                "physical_io": "Development-selected Parquet partitions only; OOS/Final Holdout never selected",
                "logical_price_access": "trade_date 2021-01-01..2025-06-30 only",
                "quality_limit": "PASS_LIMITED",
            },
        )
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, object] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    target_dates: list[str] | None = None
    if args.condition == "A_compression_breakout_2tick":
        selected: tuple[Condition, ...] = ()
    else:
        selected = ALL_CONDITIONS if args.condition is None else (cast(Condition, args.condition),)
    for condition in selected:
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
    if args.condition is not None and args.condition != "A_compression_breakout_2tick":
        return
    stress = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})}
    )
    stress_trades, stress_events, stress_audit, stress_dates = run_condition(
        view,
        classifier,
        BacktestEngine(instrument.instrument.to_spec(), stress, classifier),
        "A_compression_breakout",
    )
    if target_dates is None:
        target_dates = stress_dates
    elif target_dates != stress_dates:
        raise ValueError("stress target-date universe differs")
    stress_metrics, stress_daily = (
        research_metrics(stress_trades, view.bars),
        daily_for_targets(stress_trades, stress_dates),
    )
    write_condition(
        reserve_directory(OUT, "A_compression_breakout_2tick"),
        "A_compression_breakout_2tick",
        stress_trades,
        stress_events,
        stress_metrics,
        stress_audit,
        view,
        2,
        stress_daily,
    )
    results["A_compression_breakout_2tick"] = {
        "trade_count": len(stress_trades),
        "metrics": stress_metrics,
        "execution_audit": stress_audit,
    }
    if args.condition == "A_compression_breakout_2tick":
        return
    post_audits = {
        condition: path_audit(
            events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes
        )
        for condition in ALL_CONDITIONS
    } | {
        "A_compression_breakout_2tick": path_audit(
            stress_events, stress_trades, baseline.execution.max_fill_delay_minutes
        )
    }
    shared = all(
        (left.get("status"), left.get("t_signal_jst"))
        == (right.get("status"), right.get("t_signal_jst"))
        for left, right in zip(
            events_by["A_compression_breakout"], events_by["B_always_long"], strict=True
        )
    ) and all(
        (left.get("status"), left.get("t_signal_jst"))
        == (right.get("status"), right.get("t_signal_jst"))
        for left, right in zip(
            events_by["A_compression_breakout"], events_by["C_always_short"], strict=True
        )
    )
    post = {
        "status": "PASS"
        if shared and all(cast(bool, audit["all_pass"]) for audit in post_audits.values())
        else "BLOCKED",
        "conditions": post_audits,
        "A_B_C_shared_pre_event": shared,
        "policy": "Any path/accounting failure blocks campaign; no individual trade is removed.",
    }
    write_json(OUT / "post_execution_validation.json", post)
    assert target_dates is not None
    values = {
        condition: [daily_by[condition][day] for day in target_dates]
        for condition in ALL_CONDITIONS
    }
    boot_abc = bootstrap(
        values["A_compression_breakout"], values["B_always_long"], values["C_always_short"]
    )
    boot_ad = bootstrap(
        values["A_compression_breakout"],
        values["D_unfiltered_breakout"],
        values["D_unfiltered_breakout"],
    )
    boot = boot_abc | {
        "A_minus_D_daily_mean_net_jpy": boot_ad["A_minus_B_daily_mean_net_jpy"],
        "common_indices_all_conditions": True,
    }
    a_metrics = cast(
        dict[str, Any], cast(dict[str, Any], results["A_compression_breakout"])["metrics"]
    )
    a_overall = cast(dict[str, Any], a_metrics["overall"])
    stress_overall = cast(dict[str, Any], stress_metrics["overall"])
    months = {month: 0 for month in all_months()}
    for day, value in daily_by["A_compression_breakout"].items():
        months[day[:7]] += value
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_compression_breakout"])
    short_count = len(trades_by["A_compression_breakout"]) - long_count
    checks = {
        "A_trade_count_at_least_200": len(trades_by["A_compression_breakout"]) >= 200,
        "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200,
        "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200,
        "D_trade_count_at_least_200": len(trades_by["D_unfiltered_breakout"]) >= 200,
        "A_long_at_least_50": long_count >= 50,
        "A_short_at_least_50": short_count >= 50,
        "A_net_positive": a_overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": a_overall["profit_factor"] is not None
        and a_overall["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": bootstrap_lower_above_zero(boot, "A_daily_mean_net_jpy"),
        "A_minus_B_bootstrap_lower_above_0": bootstrap_lower_above_zero(
            boot, "A_minus_B_daily_mean_net_jpy"
        ),
        "A_minus_C_bootstrap_lower_above_0": bootstrap_lower_above_zero(
            boot, "A_minus_C_daily_mean_net_jpy"
        ),
        "A_minus_D_bootstrap_lower_above_0": bootstrap_lower_above_zero(
            boot, "A_minus_D_daily_mean_net_jpy"
        ),
        "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None
        and stress_overall["expectancy_jpy"] > 0,
        "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])[
            "net_excluding_top10_jpy"
        ]
        > 0,
    }
    count_keys = [
        key
        for key in checks
        if "trade_count" in key or key in {"A_long_at_least_50", "A_short_at_least_50"}
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
    aligned_daily: dict[str, object] = {
        "trade_dates": target_dates,
        "no_trade": "0",
        "both_sessions_quarantined": "excluded",
        "one_session_quarantined": "remaining included session retained",
    }
    aligned_daily.update({str(key): value for key, value in values.items()})
    write_json(OUT / "daily_net_pnl_aligned.json", aligned_daily)
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
            "A_D_event_comparison": {
                "both": sum(
                    a.get("status") == "event" and d.get("status") == "event"
                    for a, d in zip(
                        events_by["A_compression_breakout"],
                        events_by["D_unfiltered_breakout"],
                        strict=True,
                    )
                ),
                "A_only": sum(
                    a.get("status") == "event" and d.get("status") != "event"
                    for a, d in zip(
                        events_by["A_compression_breakout"],
                        events_by["D_unfiltered_breakout"],
                        strict=True,
                    )
                ),
                "D_only": sum(
                    a.get("status") != "event" and d.get("status") == "event"
                    for a, d in zip(
                        events_by["A_compression_breakout"],
                        events_by["D_unfiltered_breakout"],
                        strict=True,
                    )
                ),
                "interpretation": "A-D includes event time, direction, trade count, and costs; it is not a causal compression-only effect.",
            },
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
