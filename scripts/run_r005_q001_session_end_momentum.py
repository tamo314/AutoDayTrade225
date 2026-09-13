"""Execute the preregistered Development-only R005-Q001 session-end study."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
from math import floor
from pathlib import Path
from random import Random
from statistics import fmean
from typing import Any, cast

import polars as pl

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.features import HistorySnapshot, OpeningSummary
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.compression import CompressionBreakoutStrategy
from n225m_bt.strategies.failed_breakout import FailedBreakoutReversalStrategy
from n225m_bt.strategies.opening import OpeningStrategy
from n225m_bt.strategies.session_end_momentum import Condition, SessionEndMomentumStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r005-q001-20260913-corrected-exit-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
R004_OUT = ROOT / "results" / "research" / "r004-q001-20260913-failed-breakout-01"
EXIT_DIAGNOSTIC_ID = "r005-q001-20260913-exit-path-diagnostic-01"
INVALID_R005_ID = "r005-q001-20260913-session-end-momentum-03"
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
EXPECTED_QUARANTINE = {
    "quarantined_sessions": 45,
    "quarantined_bars": 27345,
    "included_bars": 1326086,
    "included_sessions": 2216,
    "quarantined_sessions_by_type": {"day": 20, "night": 25},
}


def session_groups(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    grouped: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.trade_date, bar.session)].append(bar)
    return {key: sorted(value, key=lambda item: item.ts_jst) for key, value in grouped.items()}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, Any]]:
    """Reproduce the frozen R004 view; no new exclusion criterion is permitted."""
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
        "legacy_r003_q001_session_list": "NOT_PERSISTED_IN_LEGACY_ARTIFACT; reproduced directly from identical parent view and frozen rule",
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
    version = canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": sessions})
    return ResearchData(included, version, data.quality | {"quarantine": audit}), audit


def percentile(values: list[float], q: float) -> float:
    """Frozen nearest-rank-lower empirical quantile: sorted[floor((n-1)q)]."""
    ordered = sorted(values)
    return ordered[floor((len(ordered) - 1) * q)]


def paired_block_bootstrap(values_a: list[int], values_b: list[int]) -> dict[str, Any]:
    count = len(values_a)
    if count != len(values_b) or count == 0:
        raise ValueError("invalid paired daily series")
    block = min(20, count)
    starts = list(range(count - block + 1))
    random = Random(20260913)
    means_a: list[float] = []
    means_diff: list[float] = []
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = starts[random.randrange(len(starts))]
            indices.extend(range(start, start + block))
        indices = indices[:count]
        sample_a = [values_a[index] for index in indices]
        sample_b = [values_b[index] for index in indices]
        means_a.append(fmean(sample_a))
        means_diff.append(fmean(a - b for a, b in zip(sample_a, sample_b, strict=True)))
    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10_000,
        "seed": 20260913,
        "same_resampling_indices_for_A_B": True,
        "percentile_implementation": "sorted_samples[floor((n-1)*q)]",
        "A_daily_mean_net_jpy": {
            "estimate": fmean(values_a),
            "ci95_percentile": [percentile(means_a, 0.025), percentile(means_a, 0.975)],
        },
        "A_minus_B_daily_mean_net_jpy": {
            "estimate": fmean(a - b for a, b in zip(values_a, values_b, strict=True)),
            "ci95_percentile": [
                percentile(means_diff, 0.025),
                percentile(means_diff, 0.975),
            ],
        },
    }


def schedule_table(classifier: CalendarClassifier) -> list[dict[str, str]]:
    """Record all versioned times from known schedule, never observed session ends."""
    representatives = (date(2021, 9, 17), date(2021, 9, 21), date(2024, 11, 5))
    rows: list[dict[str, str]] = []
    for day in representatives:
        for session in (Session.DAY, Session.NIGHT):
            close = classifier.session_close(day, session)
            force = close - timedelta(minutes=5)
            rows.append(
                {
                    "trade_date_example": day.isoformat(),
                    "session": session.value,
                    "scheduled_open_jst": classifier.session_open(day, session).isoformat(),
                    "scheduled_close_jst": close.isoformat(),
                    "F_force_flat_scheduled_jst": force.isoformat(),
                    "A_E_jst": (force - timedelta(minutes=55)).isoformat(),
                    "B_E_jst": (force - timedelta(minutes=175)).isoformat(),
                    "new_entry_cutoff_jst": (close - timedelta(minutes=15)).isoformat(),
                    "max_fill_delay_minutes": "10",
                }
            )
    return rows


def preregistration(source: dict[str, object], classifier: CalendarClassifier) -> dict[str, Any]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R005-Q001",
        "execution_revision": "corrected EXIT callback semantics rerun of the same fixed hypothesis; not an independent hypothesis or unused Development test",
        "status": "frozen_before_r005_event_price_statistics_or_pnl",
        "scope": "Development only: trade_date 2021-01-01 through 2025-06-30; no WFA, OOS, or Final Holdout",
        "hypothesis": "Toward scheduled session end, the immediately preceding 60-clock-minute price direction continues. A fixed direction-following rule near F has positive post-cost performance and higher daily Net than the same rule at an earlier fixed time B.",
        "mechanism_limit": "Order-processing is an untested explanatory mechanism. A/B compares complete time-of-day rules and is not a causal estimate of scheduled-close timing.",
        "known_prior_execution_access": {
            "invalid_execution_id": INVALID_R005_ID,
            "invalid_execution_status": "BLOCKED / NOT_RUN_VALID; retained and never overwritten or retrospectively validated",
            "exit_diagnostic_id": EXIT_DIAGNOSTIC_ID,
            "A_correction": "A scheduled EXIT callback at F-1 was incorrectly suppressed by the new-entry cutoff; the correction permits a held position to issue EXIT while retaining the new-entry cutoff for flat strategies.",
            "B_record_correction": "The invalid B ledger has 2,122 populated signal exits at F-120 and 94 no-position rows. This diagnostic count is not a trade count or performance input for this corrected execution.",
            "old_real_data_performance_use": "No invalid R005 Gross, fee, Net, bootstrap, or decision was used to choose this fixed specification.",
        },
        "alternative_explanations": [
            "general short-horizon momentum",
            "time-of-day differences in volatility or directional composition",
            "concentration in a small number of large moves",
            "prediction too weak to exceed costs",
        ],
        "conditions": {
            "A_session_end": "E=F-55 minutes; 60 bars [E-60,E); hold to scheduled E+55=F",
            "B_time_control": "E=F-175 minutes; same 60 bars and fixed 55-minute scheduled exit",
            "A_2tick_stress": "same A order path, actual existing-engine replay with slippage_ticks=2 and 30 JPY/side",
            "prohibited": ["threshold", "volume", "compression", "weekday", "year", "side filter", "reentry", "portfolio A+B", "stop", "target"],
        },
        "time_execution_contract": {
            "bar_timestamp": "JST bar start; close known after its minute ends",
            "F": "classifier scheduled session_close minus existing force_flat_minutes_before_session_close=5, fixed before session data is examined",
            "D": "close(E-1 minute bar) - open(E-60 minute bar), only if exactly 60 consecutive eligible one-minute bars exist",
            "entry": "issue after E-1 close; existing engine first eligible fill is E open; D>0 long, D<0 short, D=0 skip",
            "exit": "issue after F-1 close; existing engine first eligible fill is F open. It is fixed to F even if entry fill is delayed.",
            "force_flat_conflict": "scheduled exit pending is applied at F open before existing force-flat evaluation of F; exactly one exit/round-trip fee. Missing/delay follows unmodified engine and is reported.",
            "one_trade": "one contract; at most one position and one attempted entry per session; no carry/scale/re-entry",
            "schedule_versions": schedule_table(classifier),
        },
        "quality_and_data": {
            "input": "only development-selected normalized center_continuous Parquet partitions, filtered by trade_date before collection",
            "oos": "not_read",
            "final_holdout": "not_read",
            "r004_frozen_view": {
                "parent_data_version": EXPECTED_PARENT_VERSION,
                "quarantine_list_hash": EXPECTED_QUARANTINE_HASH,
                "required": EXPECTED_QUARANTINE,
                "legacy_limit": "R003-Q001 did not persist the list; R004 reconstructed it from its frozen rule and parent view.",
            },
            "quality_ceiling": "PASS_LIMITED: post-quality whole-session quarantine continuous-series research; constituent contract/roll/adjustment provenance unresolved",
        },
        "costs": {
            "tick_size_points": 5,
            "point_value_jpy": 100,
            "quantity": 1,
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "stress": {"A_only_slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "accounting": "Gross uses slippage-inclusive fill prices; Net=Gross-fees. Reported slippage is attribution and is never deducted twice.",
        },
        "evaluation": {
            "aligned_daily": "same included trade_dates, day+night summed; no-trade=0; quarantined sessions excluded rather than zero; no mutual sample conditioning",
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10_000,
                "seed": 20260913,
                "same_indices_A_B": True,
                "tail": "truncate resampled concatenated blocks to original day count",
                "ci": "95% percentile, sorted[floor((n-1)*q)]",
            },
            "continue_requires_all": [
                "A and B each >=200 trades",
                "A baseline Net>0 and PF>1",
                "both A daily-mean and A-B daily-mean bootstrap lower bounds >0",
                "A 2-tick expectancy>0",
                "positive months >=27 of the 54 calendar months",
                "A Net excluding top 10 winning trades>0",
            ],
            "decision": "BLOCKED for prerequisite/measurement/accounting mismatch; INCONCLUSIVE for either count<200; REJECT after counts if any necessary condition fails; CONTINUE_DEV_ONLY only if all pass. Never CANDIDATE.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "strategy_sha256": hashes["src/n225m_bt/strategies/session_end_momentum.py"],
            "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "config_sha256": {
                name: next(
                    value
                    for key, value in hashes.items()
                    if key.endswith(f"config/{name}.yaml")
                )
                for name in ("backtest", "sessions", "instrument", "research")
            },
            "r004_preflight_sha256": sha256((R004_OUT / "preflight.json").read_bytes()).hexdigest(),
            "exit_diagnostic_preregistration_sha256": sha256((ROOT / "results" / "research" / EXIT_DIAGNOSTIC_ID / "preregistration.json").read_bytes()).hexdigest(),
            "exit_diagnostic_post_fix_sha256": sha256((ROOT / "results" / "research" / EXIT_DIAGNOSTIC_ID / "post_fix_results.json").read_bytes()).hexdigest(),
            "invalid_r005_blocked_sha256": sha256((ROOT / "results" / "research" / INVALID_R005_ID / "BLOCKED.json").read_bytes()).hexdigest(),
        },
    }


def synthetic_bar(stamp: datetime, *, open_: int = 100, high: int = 110, low: int = 95, close: int = 105) -> Bar:
    return Bar(stamp, stamp.date(), stamp.date(), Session.DAY, "r005-exit-regression", open_, high, low, close)


def synthetic_regression_factories() -> dict[str, tuple[list[Bar], object]]:
    """The four saved EXIT-diagnostic cases, reconstructed without market data."""
    opening_start = datetime(2024, 11, 5, 15, 0, tzinfo=timezone(timedelta(hours=9)))
    opening_bars = [synthetic_bar(opening_start + timedelta(minutes=index)) for index in range(41)]
    opening_bars[0] = replace(opening_bars[0], is_session_open=True)
    r002_bars = list(opening_bars)
    r002_bars[2] = replace(r002_bars[2], close=115, high=115)
    compression_start = datetime(2024, 11, 5, 14, 0, tzinfo=timezone(timedelta(hours=9)))
    compression_bars = [synthetic_bar(compression_start + timedelta(minutes=index)) for index in range(101)]
    compression_bars[30] = replace(compression_bars[30], close=115, high=115)
    summaries = tuple(
        OpeningSummary("2024-10-01", Session.DAY, compression_start - timedelta(days=index + 1), compression_start - timedelta(days=index + 1), 120, 100, 20, 30, 30, True, ())
        for index in range(20)
    )
    history = HistorySnapshot(summaries, "ready", Fraction(20))
    r004_bars = [replace(synthetic_bar(compression_start + timedelta(minutes=index)), high=104, low=96, close=100) for index in range(101)]
    r004_bars[:30] = [replace(bar, high=100, low=90, close=95) for bar in r004_bars[:30]]
    r004_bars[30] = replace(r004_bars[30], high=110, low=96, close=105)
    return {
        "R001": (opening_bars, OpeningStrategy("opening_momentum", 2, 29, opening_start)),
        "R002": (r002_bars, OpeningStrategy("opening_breakout", 2, 28, opening_start)),
        "R003": (
            compression_bars,
            CompressionBreakoutStrategy(
                strategy_id="r003",
                session_open=compression_start,
                history=history,
                threshold=Fraction(1),
                holding_minutes=60,
            ),
        ),
        "R004": (
            r004_bars,
            FailedBreakoutReversalStrategy(
                strategy_id="r004",
                session_open=compression_start,
                entry_cutoff=datetime(2024, 11, 5, 15, 30, tzinfo=timezone(timedelta(hours=9))),
                condition="B_immediate_control",
                holding_minutes=60,
            ),
        ),
    }


def trade_snapshot(trade: Trade) -> dict[str, object]:
    return {
        "entry_order": {"signal_jst": trade.entry_signal_ts.isoformat(), "fill_jst": trade.entry_ts.isoformat(), "status": "filled"},
        "entry_price": {"reference": trade.entry_reference_price, "fill": trade.entry_fill_price},
        "exit_order": {"signal_jst": trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None, "fill_jst": trade.exit_ts.isoformat(), "reason": trade.exit_reason.value, "status": "filled"},
        "exit_price": {"reference": trade.exit_reference_price, "fill": trade.exit_fill_price},
        "fees_jpy": trade.fees_jpy,
        "slippage_attribution_jpy": trade.slippage_cost_jpy,
        "gross_pnl_jpy": trade.gross_pnl_jpy,
        "net_pnl_jpy": trade.net_pnl_jpy,
        "net_equals_gross_minus_fees": trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy,
    }


def regression_impact_table(
    instrument: Any, baseline: Any, classifier: CalendarClassifier
) -> dict[str, object]:
    """Compare legacy/fixed semantics in the exact synthetic R001--R004 cases."""
    rows: list[dict[str, object]] = []
    for name, (bars, strategy) in synthetic_regression_factories().items():
        legacy = BacktestEngine(instrument, baseline, classifier)
        legacy._strategy_callback_allowed = (
            lambda has_position, bar, legacy_engine=legacy: not legacy_engine._entry_cutoff(bar)
        )  # type: ignore[method-assign]
        legacy_trade = legacy.run(bars, strategy).trades[0]
        _, fixed_strategy = synthetic_regression_factories()[name]
        fixed_trade = BacktestEngine(instrument, baseline, classifier).run(bars, fixed_strategy).trades[0]
        before, after = trade_snapshot(legacy_trade), trade_snapshot(fixed_trade)
        changed = [key for key in before if before[key] != after[key]]
        rows.append({
            "study": name,
            "legacy": before,
            "corrected": after,
            "changed_fields": changed,
            "economic_difference": any(before[key] != after[key] for key in ("exit_price", "gross_pnl_jpy", "net_pnl_jpy")),
            "interpretation": "Previously suppressed timed EXIT becomes the specified normal signal EXIT. Any price/Gross/Net change is the consequence of the normative timed-exit correction, not a strategy change.",
        })
    return {
        "scope": "Synthetic-only EXIT regression; no historical real-data R001--R004 ledger was rerun, aggregated, or re-decided.",
        "legacy_rule": "All strategy callbacks rejected at/after new-entry cutoff.",
        "corrected_rule": "Only flat strategies are rejected at/after cutoff; held-position EXIT callback remains eligible.",
        "rows": rows,
        "impact_on_historical_decisions": {
            "R003": "BLOCKED / Development PnL NOT_RUN",
            "R003_Q001": "historical REJECT retained; not confirmed under corrected EXIT code",
            "R004_Q001": "historical REJECT retained; not confirmed under corrected EXIT code",
            "required_if_relied_on": "Separately preregistered corrected-code rerun of any real-data experiment whose scheduled EXIT can fall after cutoff.",
        },
    }


def execution_path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay_minutes: int) -> dict[str, object]:
    filled = [event for event in events if event.get("status") == "filled"]
    paths: list[dict[str, object]] = []
    for event in filled:
        scheduled_entry = datetime.fromisoformat(cast(str, event["entry_time_jst"]))
        scheduled_exit = datetime.fromisoformat(cast(str, event["scheduled_exit_time_jst"]))
        actual_entry = datetime.fromisoformat(cast(str, event["entry_ts_jst"]))
        actual_exit = datetime.fromisoformat(cast(str, event["exit_ts_jst"]))
        paths.append({
            "trade_date": event["trade_date"], "session": event["session"], "scheduled_entry_jst": scheduled_entry.isoformat(), "actual_entry_jst": actual_entry.isoformat(), "entry_delay_minutes": int((actual_entry - scheduled_entry).total_seconds() // 60), "scheduled_exit_jst": scheduled_exit.isoformat(), "actual_exit_jst": actual_exit.isoformat(), "exit_delay_minutes": int((actual_exit - scheduled_exit).total_seconds() // 60), "exit_reason": event["exit_reason"],
        })
    route_checks = {
        "one_trade_per_session": len(filled) == len(trades),
        "all_entries_next_eligible_within_max_delay": all(0 <= row["entry_delay_minutes"] <= max_delay_minutes for row in paths),
        "all_exits_normal_signal_within_max_delay": all(row["exit_reason"] == "signal" and 0 <= row["exit_delay_minutes"] <= max_delay_minutes for row in paths),
        "no_force_flat_trade": all(trade.exit_reason.value != "force_flat" for trade in trades),
        "no_end_of_data_trade": all(trade.exit_reason.value != "end_of_data" for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades),
        "no_double_slippage_deduction": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades),
    }
    return {"checks": route_checks, "all_pass": all(route_checks.values()), "filled_paths": paths, "force_flat_conflict_rule": "pending scheduled market EXIT fills before force-flat; exactly one round trip/fee"}


def run_condition(
    data: ResearchData,
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    condition: Condition,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    offset = 55 if condition == "A_session_end" else 175
    for (trade_day, session), bars in sorted(session_groups(data.bars).items()):
        close = classifier.session_close(trade_day, session)
        force = close - timedelta(minutes=engine.config.risk.force_flat_minutes_before_session_close)
        entry = force - timedelta(minutes=offset)
        strategy = SessionEndMomentumStrategy(
            f"r005_q001_{condition}", entry, entry + timedelta(minutes=55)
        )
        result = engine.run(
            bars, strategy, canonical_hash({"condition": condition, "data_version": data.data_version})
        )
        if len(result.trades) > 1:
            raise AssertionError("R005 session produced more than one trade")
        event = strategy.finalize(bars)
        event.update(
            {
                "trade_date": trade_day.isoformat(),
                "session": session.value,
                "scheduled_close_jst": close.isoformat(),
                "F_jst": force.isoformat(),
                "condition": condition,
            }
        )
        audit["target_sessions"] += 1
        audit[f"event_{event.get('reason', event.get('status', 'unknown'))}"] += 1
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update(
                {
                    "status": "filled",
                    "entry_ts_jst": trade.entry_ts.isoformat(),
                    "exit_ts_jst": trade.exit_ts.isoformat(),
                    "entry_delay_minutes": int((trade.entry_ts - entry).total_seconds() // 60),
                    "exit_delay_minutes_from_F": int((trade.exit_ts - force).total_seconds() // 60),
                    "exit_reason": trade.exit_reason.value,
                    "gross_pnl_jpy": trade.gross_pnl_jpy,
                    "fees_jpy": trade.fees_jpy,
                    "slippage_cost_jpy": trade.slippage_cost_jpy,
                    "net_pnl_jpy": trade.net_pnl_jpy,
                }
            )
            audit["orders"] += 2
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
            trades.append(trade)
        events.append(event)
    stable = tuple(replace(trade, trade_id=f"trade-{index:06d}") for index, trade in enumerate(sorted(trades, key=lambda value: value.entry_ts), 1))
    return stable, events, dict(sorted(audit.items()))


def write_condition(
    folder: Path,
    condition: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    metrics: dict[str, object],
    audit: dict[str, int],
    data: ResearchData,
    ticks: int,
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
    daily = cast(dict[str, int], metrics["daily_net_pnl_jpy"])
    dates = sorted(daily)
    pl.DataFrame({"trade_date": dates, "net_pnl_jpy": [daily[day] for day in dates]}).write_parquet(
        folder / "daily_net_pnl.parquet"
    )


def main() -> None:
    if OUT.exists():
        raise ValueError(f"R005 output already exists and must never be overwritten: {OUT}")
    if not (R004_OUT / "preflight.json").exists():
        raise ValueError("R004 frozen preflight artifact is missing")
    diagnostic = ROOT / "results" / "research" / EXIT_DIAGNOSTIC_ID
    if not (diagnostic / "post_fix_results.json").exists():
        raise ValueError("EXIT diagnostic post-fix artifact is missing")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks != 1
        or baseline.fees.jpy_per_side_per_contract != 30
        or not baseline.risk.force_flat
        or baseline.risk.force_flat_minutes_before_session_close != 5
        or baseline.risk.new_entry_cutoff_minutes_before_session_close != 15
        or baseline.execution.max_fill_delay_minutes != 10
    ):
        raise ValueError("existing execution/cost contract differs from frozen R005 specification")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    plan = preregistration(source, classifier)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"backtest": baseline.model_dump(mode="json"), "schedule": schedule_table(classifier)})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r005_price_statistics_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(
        OUT / "r001_r004_exit_regression_impact.json",
        regression_impact_table(instrument.instrument.to_spec(), baseline, classifier),
    )

    # Only after immutable R005 registration has been saved do we access Development prices.
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit = quarantine(development)
    preflight = {
        "status": "PASS_LIMITED",
        "development_input": development.quality,
        "quarantine": quarantine_audit,
        "physical_io": "Polars scan_parquet accesses metadata/row groups only in Development-selected partition paths; it does not select OOS or Final Holdout paths.",
        "logical_price_access": "trade_date-filtered Development rows only, 2021-01-01 through 2025-06-30; no all-period preload",
        "quality_limit": "post-quality whole-session isolation conditions this continuous-series research; unresolved constituent contract/roll/adjustment provenance remains",
    }
    write_json(OUT / "preflight.json", preflight)
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, dict[str, object]] = {}
    trades_by_condition: dict[str, tuple[Trade, ...]] = {}
    events_by_condition: dict[str, list[dict[str, object]]] = {}
    for condition in ("A_session_end", "B_time_control"):
        folder = reserve_directory(OUT, condition)
        trades, events, audit = run_condition(view, classifier, engine, condition)
        metrics = research_metrics(trades, view.bars)
        write_condition(folder, condition, trades, events, metrics, audit, view, 1)
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
        trades_by_condition[condition] = trades
        events_by_condition[condition] = events
    stress_config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    stress_engine = BacktestEngine(instrument.instrument.to_spec(), stress_config, classifier)
    stress_folder = reserve_directory(OUT, "A_session_end_2tick")
    stress_trades, stress_events, stress_audit = run_condition(view, classifier, stress_engine, "A_session_end")
    stress_metrics = research_metrics(stress_trades, view.bars)
    write_condition(stress_folder, "A_session_end_2tick", stress_trades, stress_events, stress_metrics, stress_audit, view, 2)
    results["A_session_end_2tick"] = {"trade_count": len(stress_trades), "metrics": stress_metrics, "execution_audit": stress_audit}

    path_audits = {
        "A_session_end": execution_path_audit(
            events_by_condition["A_session_end"],
            trades_by_condition["A_session_end"],
            baseline.execution.max_fill_delay_minutes,
        ),
        "B_time_control": execution_path_audit(
            events_by_condition["B_time_control"],
            trades_by_condition["B_time_control"],
            baseline.execution.max_fill_delay_minutes,
        ),
        "A_session_end_2tick": execution_path_audit(
            stress_events, stress_trades, baseline.execution.max_fill_delay_minutes
        ),
    }
    post_execution_validation = {
        "status": "PASS" if all(audit["all_pass"] for audit in path_audits.values()) else "BLOCKED",
        "exit_diagnostic_id": EXIT_DIAGNOSTIC_ID,
        "all_conditions": path_audits,
        "unexpected_path_policy": "Any failed path audit blocks the complete corrected execution; no individual trade is removed.",
    }
    write_json(OUT / "post_execution_validation.json", post_execution_validation)

    metrics_a = cast(dict[str, object], results["A_session_end"]["metrics"])
    metrics_b = cast(dict[str, object], results["B_time_control"]["metrics"])
    daily_a = cast(dict[str, int], metrics_a["daily_net_pnl_jpy"])
    daily_b = cast(dict[str, int], metrics_b["daily_net_pnl_jpy"])
    if set(daily_a) != set(daily_b):
        raise ValueError("A/B daily trade-date universes differ")
    dates = sorted(daily_a)
    values_a, values_b = [daily_a[key] for key in dates], [daily_b[key] for key in dates]
    bootstrap = paired_block_bootstrap(values_a, values_b)
    overall_a = cast(dict[str, Any], metrics_a["overall"])
    overall_b = cast(dict[str, Any], metrics_b["overall"])
    stress_overall = cast(dict[str, Any], cast(dict[str, object], results["A_session_end_2tick"]["metrics"])["overall"])
    concentration_a = cast(dict[str, Any], metrics_a["concentration"])
    positive_months = sum(value["net_pnl_jpy"] > 0 for value in cast(dict[str, Any], metrics_a["segments"])["month"].values())
    checks = {
        "A_trade_count_at_least_200": cast(int, results["A_session_end"]["trade_count"]) >= 200,
        "B_trade_count_at_least_200": cast(int, results["B_time_control"]["trade_count"]) >= 200,
        "A_net_positive": overall_a["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": overall_a["profit_factor"] is not None and overall_a["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": bootstrap["A_daily_mean_net_jpy"]["ci95_percentile"][0] > 0,
        "A_minus_B_bootstrap_lower_above_0": bootstrap["A_minus_B_daily_mean_net_jpy"]["ci95_percentile"][0] > 0,
        "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None and stress_overall["expectancy_jpy"] > 0,
        "A_positive_months_at_least_27_of_54": positive_months >= 27,
        "A_net_excluding_top10_positive": concentration_a["net_excluding_top10_jpy"] > 0,
        "A_no_end_of_data_exit": cast(dict[str, int], results["A_session_end"]["execution_audit"]).get("exit_end_of_data", 0) == 0,
        "B_no_end_of_data_exit": cast(dict[str, int], results["B_time_control"]["execution_audit"]).get("exit_end_of_data", 0) == 0,
    }
    if post_execution_validation["status"] != "PASS":
        decision = "BLOCKED"
    elif not checks["A_trade_count_at_least_200"] or not checks["B_trade_count_at_least_200"]:
        decision = "INCONCLUSIVE"
    elif all(checks.values()):
        decision = "CONTINUE_DEV_ONLY"
    else:
        decision = "REJECT"
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": dates, "A_session_end": values_a, "B_time_control": values_b, "no_trade": "0", "quarantined_sessions": "excluded"})
    write_json(OUT / "bootstrap.json", bootstrap)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "checks": checks, "execution_path_status": post_execution_validation["status"], "positive_months_of_54": positive_months, "conditions": results, "A_B_overall": {"A": overall_a, "B": overall_b}, "accounting": "gross is fill-to-fill slippage-inclusive PnL; Net=gross-fees; slippage attribution not deducted again", "scope": "Development only; 2025 consists of Jan-Jun; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
