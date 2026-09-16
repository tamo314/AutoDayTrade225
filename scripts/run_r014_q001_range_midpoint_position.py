"""Execute the fixed Development-only R014-Q001 experiment."""

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
from n225m_bt.research.r014 import range_midpoint_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.range_midpoint import RangeMidpointStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r014-q001-20260914-range-midpoint-position-01"
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
EXPECTED_TARGET_DATES = 1120
Condition = Literal["A_range_midpoint", "B_always_long", "C_always_short", "D_delta_follow"]
CONDITIONS: tuple[Condition, ...] = (
    "A_range_midpoint",
    "B_always_long",
    "C_always_short",
    "D_delta_follow",
)


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, Any], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    bad = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in bad for bar in rows]
    listed = [
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
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
    }
    expected = EXPECTED_QUARANTINE | {
        "parent_data_version": EXPECTED_PARENT_VERSION,
        "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH,
    }
    mismatches = {
        key: {"actual": audit.get(key), "expected": value}
        for key, value in expected.items()
        if audit.get(key) != value
    }
    if audit["included_tick_grid_violations"]:
        mismatches["included_tick_grid_violations"] = audit["included_tick_grid_violations"]
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    version = canonical_hash(
        {"parent": data.data_version, "rule": audit["rule"], "sessions": listed}
    )
    return ResearchData(included, version, data.quality | {"quarantine": audit}), audit, bad


def month_keys() -> list[str]:
    return [
        f"{year:04d}-{month:02d}"
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    ]


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    low, high = floor(position), ceil(position)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    )


def bootstrap(values: dict[str, list[int]]) -> dict[str, Any]:
    count = len(values["A_range_midpoint"])
    if count != EXPECTED_TARGET_DATES or any(len(series) != count for series in values.values()):
        raise ValueError("invalid fixed aligned daily input universe")
    rng, block = Random(20260913), 20
    a, b, c, d = (values[key] for key in CONDITIONS)
    samples: dict[str, list[float]] = {"A": [], "A_minus_B": [], "A_minus_C": [], "A_minus_D": []}
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

    def estimate(key: str, observed: float) -> dict[str, object]:
        return {
            "estimate": observed,
            "ci95_percentile_linear": [
                percentile(samples[key], 0.025),
                percentile(samples[key], 0.975),
            ],
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
        "A_minus_B_daily_mean_net_jpy": estimate(
            "A_minus_B", fmean(x - y for x, y in zip(a, b, strict=True))
        ),
        "A_minus_C_daily_mean_net_jpy": estimate(
            "A_minus_C", fmean(x - y for x, y in zip(a, c, strict=True))
        ),
        "A_minus_D_daily_mean_net_jpy": estimate(
            "A_minus_D", fmean(x - y for x, y in zip(a, d, strict=True))
        ),
    }


def preregistration(source: dict[str, object]) -> dict[str, Any]:
    hashes = cast(dict[str, str], source["file_hashes"])
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R014-Q001",
        "status": "frozen_before_r014_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation, unused sample, or multiplicity-corrected validation.",
        "novelty": {
            "R002_R003_R008": "range-breakout rules require a breakout or compression, unlike this fixed current range-midpoint sign",
            "R004": "failed breakout with midpoint exit, unlike no breakout condition and fixed time exit",
            "R009": "close-path consistency, unlike current high/low range position",
            "R011": "prior close-mean deviation, unlike current high/low midpoint",
            "conclusion": "No R001-R013 evaluation uses K=2*close_t-H-L over t-59..t at fixed S+119 with Delta=close_t-close_t-60 control through X=S+180.",
        },
        "hypothesis": "At a fixed session time, sign(K), the close's position relative to the current 60-bar high/low midpoint, has positive post-cost expectancy and exceeds fixed-direction and same-window close-change-following controls. It is a price-pattern hypothesis only and does not claim observed order flow or participants.",
        "fixed_rule": {
            "schedule": "Versioned day/night S; t=S+119, E=S+120, X=S+180. Scheduled E<=cutoff and X<=F are checked before prices; row endpoints never define schedule.",
            "window": "Require 61 contiguous eligible same-session bars [t-60,t]. H=max(high[t-59:t]), L=min(low[t-59:t]), K=2*close_t-H-L, Delta=close_t-close_t-60 using integer prices. H>L, K!=0 and Delta!=0 are common pre-events. No midpoint rounding, magnitude filter, normalization, breakout, weekday or year filter.",
            "conditions": {
                "A": "sign(K)",
                "B": "always long",
                "C": "always short",
                "D": "sign(Delta)",
                "A2": "same A, engine rerun at 2 ticks/side",
            },
            "orders": "Signal after t close; earliest E open entry. Issue EXIT after X-1 close; earliest X open. Entry delay never extends X; no entry from X onward.",
            "prohibited": [
                "Stop",
                "Target",
                "midpoint early exit",
                "re-entry",
                "direction reversal",
                "window/time/threshold rescue",
                "WFA",
                "additional cost/delay",
                "OOS",
                "Final Holdout",
            ],
        },
        "inputs": {
            "physical": "Development-selected normalized center_continuous Parquet only, lazy trade_date filter before collect; raw/external/volume/OOS/Final Holdout prohibited.",
            "r004_quarantine": EXPECTED_QUARANTINE
            | {
                "parent_data_version": EXPECTED_PARENT_VERSION,
                "quarantine_hash": EXPECTED_QUARANTINE_HASH,
            },
            "target_trade_dates": EXPECTED_TARGET_DATES,
            "quality_ceiling": "PASS_LIMITED due to inherited continuous-series, roll/adjustment and post-quality whole-session-isolation limits.",
        },
        "costs": {
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; attribution is never deducted twice.",
        },
        "evaluation": {
            "daily": "day/night sum per fixed 1,120 trade_date; skips are zero; exclude only both-session isolation dates and retain one nonisolated session.",
            "bootstrap": {
                "block": 20,
                "repetitions": 10_000,
                "seed": 20260913,
                "common_indices": True,
                "sampling": "non-circular with replacement, tail truncate",
                "percentile": "linear",
            },
            "information_gate": [
                "A/B/C/D >=200",
                "A long/short >=50",
                "A/D direction disagreement >=50",
            ],
            "pass_all": [
                "A Net>0",
                "A PF>1",
                "A/A-B/A-C/A-D CI lower bounds>0",
                "A2 expectancy>0",
                "positive months>=27/54",
                "A top10-excluded Net>0",
            ],
            "decision": "BLOCKED on input/synthetic/execution/accounting failure; INCONCLUSIVE for information failure; REJECT if any primary condition fails; all passing is Development primary only/PASS_LIMITED and never automatic CANDIDATE.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "event_selector_sha256": hashes["src/n225m_bt/research/r014.py"],
            "strategy_sha256": hashes["src/n225m_bt/strategies/range_midpoint.py"],
            "test_sha256": sha256((ROOT / "tests" / "test_r014_q001.py").read_bytes()).hexdigest(),
        },
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def direction(condition: Condition, event: dict[str, object]) -> str:
    if condition == "B_always_long":
        return "long"
    if condition == "C_always_short":
        return "short"
    return cast(
        str, event["A_direction"] if condition == "A_range_midpoint" else event["D_direction"]
    )


def run_condition(
    data: ResearchData,
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    condition: Condition,
    bad: set[tuple[date, Session]],
    targets: list[str],
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    for text_day in targets:
        current_day = date.fromisoformat(text_day)
        for session in (Session.DAY, Session.NIGHT):
            if (current_day, session) in bad:
                continue
            event = range_midpoint_event(
                classifier, current_day, session, groups.get((current_day, session), [])
            )
            event.update({"condition": condition, "pre_event_status": event["status"]})
            if event["status"] == "eligible":
                event["A_D_direction_relation"] = (
                    "same" if event["A_direction"] == event["D_direction"] else "different"
                )
            audit[f"event_{event['status']}"] += 1
            if event["status"] != "eligible":
                audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
                events.append(event)
                continue
            signal = datetime.fromisoformat(cast(str, event["t_signal_jst"]))
            result = engine.run(
                groups[(current_day, session)],
                RangeMidpointStrategy(
                    f"r014_q001_{condition}", signal, direction(condition, event)
                ),
                canonical_hash(
                    {"condition": condition, "event": event, "data_version": data.data_version}
                ),
            )
            if len(result.trades) > 1:
                raise AssertionError("R014 produced more than one trade/session")
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
                            (trade.entry_ts - signal - timedelta(minutes=1)).total_seconds() // 60
                        ),
                        "exit_delay_minutes": int(
                            (trade.exit_ts - signal - timedelta(minutes=61)).total_seconds() // 60
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
                event["status"] = "eligible_order_unfilled"
                audit["eligible_order_unfilled"] += 1
            events.append(event)
    stable = tuple(
        replace(item, trade_id=f"trade-{index:06d}")
        for index, item in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
    )
    return stable, events, dict(sorted(audit.items()))


def daily(trades: tuple[Trade, ...], targets: list[str]) -> dict[str, int]:
    output = dict.fromkeys(targets, 0)
    for trade in trades:
        output[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return output


def write_condition(
    folder: Path,
    condition: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    metrics: dict[str, object],
    audit: dict[str, int],
    data: ResearchData,
    ticks: int,
    net_daily: dict[str, int],
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
            "trade_date": sorted(net_daily),
            "net_pnl_jpy": [net_daily[key] for key in sorted(net_daily)],
        }
    ).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(
    events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int
) -> dict[str, object]:
    eligible = [row for row in events if row.get("pre_event_status") == "eligible"]
    filled = [row for row in events if row.get("status") == "filled"]
    checks = {
        "every_eligible_event_filled": len(eligible) == len(filled),
        "one_trade_per_filled_event": len(filled) == len(trades),
        "no_canceled_or_unfilled_eligible_order": not any(
            row.get("status") == "eligible_order_unfilled" for row in events
        ),
        "entry_within_max_delay": all(
            0 <= cast(int, row["entry_delay_minutes"]) <= max_delay for row in filled
        ),
        "fixed_signal_exit_within_max_delay": all(
            row["exit_reason"] == "signal"
            and 0 <= cast(int, row["exit_delay_minutes"]) <= max_delay
            for row in filled
        ),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades
        ),
        "no_force_flat_or_end_of_data": all(
            trade.exit_reason.value not in {"force_flat", "end_of_data"} for trade in trades
        ),
    }
    return {
        "checks": checks,
        "all_pass": all(checks.values()),
        "accounting": "slippage attribution is informational and never additionally deducted",
    }


def direction_diagnostic(
    a_events: list[dict[str, object]], d_events: list[dict[str, object]]
) -> dict[str, object]:
    output: dict[str, object] = {}
    for relation in ("same", "different"):
        pairs = [
            (a, d)
            for a, d in zip(a_events, d_events, strict=True)
            if a.get("A_D_direction_relation") == relation
        ]
        output[relation] = {
            "pre_event_count": len(pairs),
            "A_filled": sum(a.get("status") == "filled" for a, _ in pairs),
            "D_filled": sum(d.get("status") == "filled" for _, d in pairs),
            "filled_side_match": all(
                a.get("side") == d.get("side")
                for a, d in pairs
                if a.get("status") == d.get("status") == "filled"
            ),
            "A_minus_D_net_jpy": sum(
                cast(int, a.get("net_pnl_jpy", 0)) - cast(int, d.get("net_pnl_jpy", 0))
                for a, d in pairs
            ),
        }
    return output


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r014_q001_range_midpoint_position.py')

    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.max_fill_delay_minutes,
        baseline.execution.allow_cross_session_pending_order,
        baseline.risk.new_entry_cutoff_minutes_before_session_close,
        baseline.risk.force_flat_minutes_before_session_close,
    )
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R014 specification")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    plan = preregistration(source)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "effective_config.json",
        {
            "instrument": instrument.model_dump(mode="json"),
            "backtest": baseline.model_dump(mode="json"),
        },
    )
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "preregistered_before_r014_prices",
            "source": source,
            "plan_hash": canonical_hash(plan),
            "seed": 20260913,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r014_q001.py", "-q"],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r014.py",
            "src/n225m_bt/strategies/range_midpoint.py",
            "tests/test_r014_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r014.py",
            "src/n225m_bt/strategies/range_midpoint.py",
            "tests/test_r014_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
    }
    validation: dict[str, Any] = {
        "coverage": [
            "61 input bars versus 60 current-including H/L bars, integer K/Delta, all signs and zero/H=L",
            "same closes with OHLC-consistent high-only K reversal, half-tick midpoint",
            "missing, ineligible/session boundary and prefix invariance",
            "versioned schedule, next eligible entry, fixed exit, delay non-extension, one position and accounting",
            "Final Holdout input rejection",
        ]
    }
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    validation["status"] = (
        "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R014 validation failed before Development price access")
    development = load_split(data_config.gold_root, "development")
    parent_groups = session_groups(development.bars)
    view, quarantine_audit, bad = quarantine(development)
    targets = sorted(
        {
            day.isoformat()
            for day, _ in parent_groups
            if not ((day, Session.DAY) in bad and (day, Session.NIGHT) in bad)
        }
    )
    if len(targets) != EXPECTED_TARGET_DATES:
        raise ValueError(f"R014 target date count {len(targets)} != {EXPECTED_TARGET_DATES}")
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": quarantine_audit,
            "fixed_target_trade_dates": targets,
            "physical_io": "Development selected normalized Parquet only; OOS/Final Holdout never selected",
            "logical_price_access": "trade_date 2021-01-01..2025-06-30 only",
        },
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, dict[str, object]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, classifier, engine, condition, bad, targets)
        metrics, net_daily = research_metrics(trades, view.bars), daily(trades, targets)
        write_condition(
            reserve_directory(OUT, condition),
            condition,
            trades,
            events,
            metrics,
            audit,
            view,
            1,
            net_daily,
        )
        results[condition] = {
            "trade_count": len(trades),
            "metrics": metrics,
            "execution_audit": audit,
        }
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, net_daily
    stress = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})}
    )
    stress_trades, stress_events, stress_audit = run_condition(
        view,
        classifier,
        BacktestEngine(instrument.instrument.to_spec(), stress, classifier),
        "A_range_midpoint",
        bad,
        targets,
    )
    stress_metrics, stress_daily = (
        research_metrics(stress_trades, view.bars),
        daily(stress_trades, targets),
    )
    write_condition(
        reserve_directory(OUT, "A_range_midpoint_2tick"),
        "A_range_midpoint_2tick",
        stress_trades,
        stress_events,
        stress_metrics,
        stress_audit,
        view,
        2,
        stress_daily,
    )
    results["A_range_midpoint_2tick"] = {
        "trade_count": len(stress_trades),
        "metrics": stress_metrics,
        "execution_audit": stress_audit,
    }
    fields = (
        "trade_date",
        "session",
        "pre_event_status",
        "reason",
        "H_points",
        "L_points",
        "K_points_times_1",
        "Delta_points",
    )
    shared = all(
        len(events_by[key]) == len(events_by["A_range_midpoint"])
        and all(
            tuple(row.get(field) for field in fields)
            == tuple(events_by[key][index].get(field) for field in fields)
            for index, row in enumerate(events_by["A_range_midpoint"])
        )
        for key in CONDITIONS[1:]
    )
    diagnostics = direction_diagnostic(events_by["A_range_midpoint"], events_by["D_delta_follow"])
    audits = {
        key: path_audit(events_by[key], trades_by[key], baseline.execution.max_fill_delay_minutes)
        for key in CONDITIONS
    } | {
        "A_range_midpoint_2tick": path_audit(
            stress_events, stress_trades, baseline.execution.max_fill_delay_minutes
        )
    }
    post = {
        "status": "PASS"
        if shared
        and all(cast(bool, item["all_pass"]) for item in audits.values())
        and cast(bool, cast(dict[str, object], diagnostics["same"])["filled_side_match"])
        else "BLOCKED",
        "conditions": audits,
        "all_conditions_identical_pre_event": shared,
        "A_D_direction_diagnostic": diagnostics,
        "policy": "Path/accounting mismatch blocks economic decision; successful-fill intersection is prohibited.",
    }
    write_json(OUT / "post_execution_validation.json", post)
    write_json(OUT / "a_d_direction_diagnostic.json", diagnostics)
    values: dict[str, list[int]] = {
        key: [daily_by[key][day] for day in targets] for key in CONDITIONS
    }
    boot = bootstrap(values)
    monthly = {month: 0 for month in month_keys()}
    for day, amount in daily_by["A_range_midpoint"].items():
        monthly[day[:7]] += amount
    a_metrics = cast(dict[str, Any], cast(dict[str, Any], results["A_range_midpoint"])["metrics"])
    a_overall, stress_overall = (
        cast(dict[str, Any], a_metrics["overall"]),
        cast(dict[str, Any], stress_metrics["overall"]),
    )
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_range_midpoint"])
    short_count = len(trades_by["A_range_midpoint"]) - long_count

    def lower(key: str) -> bool:
        return cast(list[float], boot[key]["ci95_percentile_linear"])[0] > 0

    gates = {
        "A_trade_count_at_least_200": len(trades_by["A_range_midpoint"]) >= 200,
        "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200,
        "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200,
        "D_trade_count_at_least_200": len(trades_by["D_delta_follow"]) >= 200,
        "A_long_at_least_50": long_count >= 50,
        "A_short_at_least_50": short_count >= 50,
        "A_D_different_direction_events_at_least_50": cast(
            int, cast(dict[str, object], diagnostics["different"])["pre_event_count"]
        )
        >= 50,
        "A_net_positive": a_overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": a_overall["profit_factor"] is not None
        and a_overall["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"),
        "A_minus_B_bootstrap_lower_above_0": lower("A_minus_B_daily_mean_net_jpy"),
        "A_minus_C_bootstrap_lower_above_0": lower("A_minus_C_daily_mean_net_jpy"),
        "A_minus_D_bootstrap_lower_above_0": lower("A_minus_D_daily_mean_net_jpy"),
        "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None
        and stress_overall["expectancy_jpy"] > 0,
        "A_positive_months_at_least_27_of_54": sum(amount > 0 for amount in monthly.values()) >= 27,
        "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])[
            "net_excluding_top10_jpy"
        ]
        > 0,
    }
    information = [
        key
        for key in gates
        if "trade_count" in key
        or key
        in {
            "A_long_at_least_50",
            "A_short_at_least_50",
            "A_D_different_direction_events_at_least_50",
        }
    ]
    decision = (
        "BLOCKED"
        if post["status"] != "PASS"
        else "INCONCLUSIVE"
        if not all(gates[key] for key in information)
        else "DEVELOPMENT_PRIMARY_CONDITIONS_PASSED_PASS_LIMITED"
        if all(gates.values())
        else "REJECT"
    )
    aligned: dict[str, object] = {
        "trade_dates": targets,
        "no_trade": "0",
        "both_sessions_quarantined": "excluded",
        "one_session_quarantined": "remaining included session retained",
    }
    aligned.update(values)
    write_json(OUT / "daily_net_pnl_aligned.json", aligned)
    write_json(OUT / "bootstrap.json", boot)
    final = {
        "campaign_id": IDENTIFIER,
        "quality_status": "PASS_LIMITED",
        "decision": decision,
        "gates": gates,
        "A_direction_counts": {"long": long_count, "short": short_count},
        "positive_months_of_54": sum(amount > 0 for amount in monthly.values()),
        "monthly_A_net_jpy": monthly,
        "conditions": results,
        "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction",
        "scope": "Development only; 2025 Jan-Jun; WFA/OOS/Final Holdout not run",
    }
    write_json(OUT / "development_results.json", final)
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
