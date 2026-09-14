"""Execute the preregistered Development-only R028-Q001 experiment."""

from __future__ import annotations

import json
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
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r028 import cash_open_discontinuity_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.cash_open_discontinuity import CashOpenDiscontinuityStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r028-q001-20260914-cash-open-discontinuity-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
R021 = ROOT / "results" / "research" / "r021-q001-20260914-opening-cash-close-followthrough-05"
R012_AXIS = (
    ROOT
    / "results"
    / "research"
    / "r012-q001-20260914-night-direction-followthrough-01"
    / "daily_net_pnl_aligned.json"
)
EXPECTED_AXIS_COUNT = 1_111
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal["A_reversal", "B_buy", "C_sell", "D_follow", "F_preopen"]
CONDITIONS: tuple[Condition, ...] = ("A_reversal", "B_buy", "C_sell", "D_follow", "F_preopen")


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_tse_evidence() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R021 / "institutional_evidence"
    files = (
        source / "evidence_manifest.json",
        source / "r020_cabinet_office_public_holidays.csv",
        R020 / "institutional_evidence" / "evidence_manifest.json",
    )
    if not all(path.exists() for path in files):
        raise ValueError("R020/R021 frozen official TSE evidence is unavailable")
    folder = reserve_directory(OUT, "institutional_evidence")
    holiday = folder / "r020_cabinet_office_public_holidays.csv"
    holiday.write_bytes(files[1].read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(holiday.read_text(encoding="cp932"))
    if not (calendar.source_start <= date(2021, 1, 1) and calendar.source_end >= date(2025, 6, 30)):
        raise ValueError("frozen TSE evidence does not cover Development")
    evidence = {
        "r020_completed_sha256": sha256_file(R020 / "COMPLETED.json"),
        "r020_evidence_manifest_sha256": sha256_file(files[2]),
        "r021_completed_sha256": sha256_file(R021 / "COMPLETED.json"),
        "r021_evidence_manifest_sha256": sha256_file(files[0]),
        "holiday_csv_sha256": sha256_file(holiday),
        "calendar_rule": "TSE open only on weekdays not in official Cabinet Office holidays and excluding Dec31/Jan1-3; never inferred from OSE futures observations.",
    }
    write_json(folder / "evidence_manifest.json", evidence)
    return calendar, evidence | {
        "evidence_manifest_sha256": sha256_file(folder / "evidence_manifest.json")
    }


def input_manifest(root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)}
        for path in partition_paths(root, "development")
    ]
    return {
        "status": "frozen_before_r028_price_statistics_events_or_pnl",
        "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS, Final Holdout prohibited.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {
        key
        for key, rows in groups.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [row for key, rows in groups.items() if key not in isolated for row in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(isolated),
        "quarantined_sessions_by_type": dict(
            sorted(Counter(session.value for _, session in isolated).items())
        ),
        "included_bars": len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in row.quality_flags for row in included
        ),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
    }
    expected = {
        "parent_data_version": EXPECTED_PARENT_VERSION,
        "quarantined_sessions": 45,
        "quarantined_bars": 27345,
        "included_bars": 1326086,
        "included_sessions": 2216,
        "quarantined_sessions_by_type": {"day": 20, "night": 25},
        "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH,
    }
    mismatch = {
        key: {"actual": audit.get(key), "expected": value}
        for key, value in expected.items()
        if audit.get(key) != value
    }
    if audit["included_tick_grid_violations"]:
        mismatch["included_tick_grid_violations"] = {
            "actual": audit["included_tick_grid_violations"],
            "expected": 0,
        }
    audit["expected_match"], audit["mismatches"] = not mismatch, mismatch
    if mismatch:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatch}")
    return (
        ResearchData(
            included,
            canonical_hash(
                {"parent": data.data_version, "rule": audit["rule"], "sessions": listed}
            ),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def axis() -> list[str]:
    if not R012_AXIS.exists():
        raise ValueError("fixed R012 1,111-day axis missing")
    values = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if (
        not isinstance(values, list)
        or len(values) != EXPECTED_AXIS_COUNT
        or len(set(values)) != len(values)
    ):
        raise ValueError("fixed R012 axis is not unique 1,111 trade_dates")
    return values


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    low, high = floor(position), ceil(position)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    )


def bootstrap(values: dict[str, list[int]]) -> dict[str, object]:
    count, block = len(values["A_reversal"]), 20
    if count != EXPECTED_AXIS_COUNT or any(len(series) != count for series in values.values()):
        raise ValueError("R028 daily series are not the fixed 1,111-day axis")
    a, b, c, d, f = (values[name] for name in CONDITIONS)
    series = {
        "A": a,
        "A_minus_B": [x - y for x, y in zip(a, b, strict=True)],
        "A_minus_C": [x - y for x, y in zip(a, c, strict=True)],
        "A_minus_D": [x - y for x, y in zip(a, d, strict=True)],
        "A_minus_F": [x - y for x, y in zip(a, f, strict=True)],
    }
    samples: dict[str, list[float]] = {key: [] for key in series}
    rng = Random(20260913)
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        selected = indices[:count]
        for name, values_ in series.items():
            samples[name].append(fmean(values_[index] for index in selected))
    labels = {
        "A": "A_daily_mean_net_jpy",
        "A_minus_B": "A_minus_B_buy_daily_mean_net_jpy",
        "A_minus_C": "A_minus_C_sell_daily_mean_net_jpy",
        "A_minus_D": "A_minus_D_follow_daily_mean_net_jpy",
        "A_minus_F": "A_minus_F_preopen_daily_mean_net_jpy",
    }
    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10_000,
        "seed": 20260913,
        "common_indices_all_conditions": True,
        "percentile_implementation": "linear interpolation at (n-1)*q",
        **{
            labels[key]: {
                "estimate": fmean(value),
                "ci95_percentile_linear": [
                    percentile(samples[key], 0.025),
                    percentile(samples[key], 0.975),
                ],
            }
            for key, value in series.items()
        },
    }


def direction(condition: Condition, event: dict[str, object]) -> str:
    if condition == "B_buy":
        return "long"
    if condition == "C_sell":
        return "short"
    return cast(
        str,
        event[
            {
                "A_reversal": "A_direction",
                "D_follow": "D_follow_direction",
                "F_preopen": "F_preopen_direction",
            }[condition]
        ],
    )


def run_condition(
    data: ResearchData,
    cash: TSECashMarketCalendar,
    engine: BacktestEngine,
    condition: Condition,
    isolated: set[tuple[date, Session]],
    targets: list[str],
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    for text_day in targets:
        trade_day, key = date.fromisoformat(text_day), (date.fromisoformat(text_day), Session.DAY)
        event = cash_open_discontinuity_event(
            trade_day, groups.get(key), cash, day_quarantined=key in isolated
        )
        event.update({"condition": condition, "pre_event_status": event["status"]})
        audit[f"event_{event['status']}"] += 1
        if event["status"] != "eligible":
            audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(
            groups[key],
            CashOpenDiscontinuityStrategy(
                f"r028_q001_{condition}", signal, direction(condition, event)
            ),
            canonical_hash(
                {"condition": condition, "event": event, "data_version": data.data_version}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R028 produced more than one daily trade")
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
                        (trade.exit_ts - signal - timedelta(minutes=16)).total_seconds() // 60
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
        replace(item, trade_id=f"trade-{number:06d}")
        for number, item in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
    )
    return stable, events, dict(sorted(audit.items()))


def write_condition(
    folder: Path,
    condition: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    audit: dict[str, int],
    data: ResearchData,
    ticks: int,
    daily: dict[str, int],
) -> dict[str, object]:
    metrics = research_metrics(trades, data.bars)
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
        {"trade_date": sorted(daily), "net_pnl_jpy": [daily[day] for day in sorted(daily)]}
    ).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def path_audit(
    events: list[dict[str, object]], trades: tuple[Trade, ...], maximum_delay: int
) -> dict[str, object]:
    eligible, filled = (
        [row for row in events if row.get("pre_event_status") == "eligible"],
        [row for row in events if row.get("status") == "filled"],
    )
    checks = {
        "all_eligible_filled": len(eligible) == len(filled),
        "one_trade_per_filled_event": len(filled) == len(trades),
        "entry_strictly_after_0900": all(
            row["entry_ts_jst"] > row["t_signal_bar_start_jst"] for row in filled
        ),
        "entry_at_or_after_0901": all(
            row["entry_ts_jst"] >= row["E_planned_entry_jst"] for row in filled
        ),
        "entry_within_max_delay": all(
            0 <= cast(int, row["entry_delay_minutes"]) <= maximum_delay for row in filled
        ),
        "fixed_0916_signal_exit": all(
            row["exit_reason"] == "signal"
            and 0 <= cast(int, row["exit_delay_minutes"]) <= maximum_delay
            for row in filled
        ),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades
        ),
        "no_force_flat_or_end_of_data": all(
            trade.exit_reason.value not in {"force_flat", "end_of_data"} for trade in trades
        ),
        "one_position_per_day": len({trade.trade_date for trade in trades}) == len(trades),
    }
    return {
        "checks": checks,
        "all_pass": all(checks.values()),
        "accounting": "Gross is fill-to-fill and already slippage-inclusive; attribution is not deducted again.",
    }


def group_diagnostics(events: list[dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for p_sign in (-1, 1):
        for j_sign in (-1, 1):
            selected = [
                row
                for row in events
                if row.get("pre_event_status") == "eligible"
                and row.get("P_sign") == p_sign
                and row.get("J_sign") == j_sign
            ]
            filled = [row for row in selected if row.get("status") == "filled"]
            result[
                f"P_{'positive' if p_sign > 0 else 'negative'}_J_{'positive' if j_sign > 0 else 'negative'}"
            ] = {
                "pre_event_count": len(selected),
                "trade_count": len(filled),
                "gross_pnl_jpy": sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in filled),
                "fees_jpy": sum(cast(int, row.get("fees_jpy", 0)) for row in filled),
                "net_pnl_jpy": sum(cast(int, row.get("net_pnl_jpy", 0)) for row in filled),
                "expectancy_jpy": fmean(cast(int, row["net_pnl_jpy"]) for row in filled)
                if filled
                else None,
                "entry_delay_minutes_total": sum(
                    cast(int, row.get("entry_delay_minutes", 0)) for row in filled
                ),
                "exit_delay_minutes_total": sum(
                    cast(int, row.get("exit_delay_minutes", 0)) for row in filled
                ),
            }
    return result


def preregistration(
    source: dict[str, object],
    inputs: dict[str, object],
    evidence: dict[str, object],
    implementation: dict[str, str],
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R028-Q001",
        "status": "frozen_before_r028_price_statistics_zero_counts_events_orders_fills_trades_pnl_or_bootstrap",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_snapshot": source,
        "implementation_hashes": implementation,
        "scope": "Development trade_date 2021-01-01..2025-06-30 only. Known-Development additional exploration, not independent confirmation or multiplicity-corrected validation.",
        "novelty": {
            "R001": "15-45 minute session-start rules",
            "R007": "61-minute-plus local shocks",
            "R023": "08:45-08:59 change and prior-night gap, signal 08:59",
            "R024": "09:00-09:04 whole-window confirmation",
            "conclusion": "No R001-R027 registered the C_08:59 to K_open_09:00 discontinuity and fixed 09:01-09:16 reversal with these controls.",
        },
        "hypothesis": "A nonzero futures price discontinuity J from 08:59 close to 09:00 open reverses over the following fixed fifteen minutes after costs, against fixed same-event controls. It neither calls the 09:00 futures open a cash price nor identifies order flow, basis, arbitrage, liquidity, or participant behavior.",
        "institution_and_calendar": evidence,
        "inputs": {
            "physical": inputs,
            "r004_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa must reproduce.",
            "daily_axis": {
                "source": str(R012_AXIS.relative_to(ROOT)),
                "sha256": sha256_file(R012_AXIS),
                "count": 1111,
                "day_isolated": "excluded",
                "TSE closed/missing/zero/cancel/no-trade": "zero",
            },
            "quality_ceiling": "PASS_LIMITED inherited continuous-series/roll/adjustment and post-hoc whole-session isolation constraints.",
        },
        "fixed_rule": {
            "event": "Official TSE business day and 16 contiguous eligible day bars 08:45..09:00. O=open_08:45, C=close_08:59, K=open_09:00, P=C-O, J=K-C. P=0 or J=0, missing, ineligible, isolation, or TSE closure skips all conditions.",
            "conditions": {
                "A": "-sign(J)",
                "B_buy": "long",
                "C_sell": "short",
                "D_follow": "sign(J)",
                "F_preopen": "-sign(P)",
                "A2": "A, unchanged engine at 2 tick/side",
            },
            "execution": "Observe only 09:00 open; issue entry after 09:00, earliest 09:01 open. After 09:15 close issue EXIT, earliest 09:16 open. Delay never extends exit; no entry from 09:16 onward; one contract/day/position, no stop/target/re-entry/early exit.",
            "prohibited": [
                "thresholds",
                "standardization",
                "range/gap rate",
                "weekday/year/night/volume/high-low-close filters",
                "cash/external prices",
                "extra costs/delay",
                "WFA",
                "OOS",
                "Final Holdout",
            ],
        },
        "costs": {
            "baseline": "1 tick/side + JPY30/side",
            "A2": "2 ticks/side + JPY30/side",
            "diagnostic": "A 0-tick Gross only",
            "accounting": "Net=Gross-fees; do not deduct slippage attribution twice.",
        },
        "evaluation": {
            "bootstrap": "20 trade-date non-circular moving blocks, 10,000 repetitions, seed 20260913, common indices, tail truncation, linear percentile",
            "information_gate": "each 1-tick A/B/C/D/F >=300; A long/short >=75; P!=J signs >=100; all four P x J groups >=50 pre-events",
            "pass_requires": "A Net>0, PF>1, all A/control CI lower bounds>0, A2 expectancy>0, positive months>=27/54, top10-removed Net>0",
            "decision": "BLOCKED gate failure; INCONCLUSIVE information shortfall; otherwise REJECT on any failed primary condition; all pass only INVESTIGATE.",
        },
    }


def main() -> None:
    reserve_directory(OUT.parent, IDENTIFIER)
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    implementation = {
        str(path): sha256_file(ROOT / path)
        for path in (
            Path(__file__).relative_to(ROOT),
            Path("src/n225m_bt/research/r028.py"),
            Path("src/n225m_bt/strategies/cash_open_discontinuity.py"),
            Path("tests/test_r028_q001.py"),
        )
    }
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if baseline.execution.slippage_ticks != 1 or baseline.fees.jpy_per_side_per_contract != 30:
        raise ValueError("R028 requires unchanged baseline one tick and JPY30 per side")
    cash, evidence = frozen_tse_evidence()
    inputs = input_manifest(data_config.gold_root)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(
        OUT / "preregistration.json", preregistration(source, inputs, evidence, implementation)
    )
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "status": "preregistered_before_development_price_access",
            "seed": 20260913,
            "source": source,
            "implementation_hashes": implementation,
            "input_manifest_sha256": sha256_file(OUT / "input_manifest.json"),
            "preregistration_sha256": sha256_file(OUT / "preregistration.json"),
            "oos": "PROHIBITED",
            "final_holdout": "LOCKED",
        },
    )
    calendar = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    development = load_split(data_config.gold_root, "development")
    view, q_audit, isolated = quarantine(development)
    targets = axis()
    actual = sorted(
        day.isoformat()
        for day, session in session_groups(development.bars)
        if session is Session.DAY and (day, Session.DAY) not in isolated
    )
    if targets != actual:
        raise ValueError("fixed R012 axis does not reproduce R004 retained day-session universe")
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": q_audit,
            "fixed_target_trade_dates": targets,
            "target_count": len(targets),
            "physical_io": "Development normalized Parquet only; OOS/Final Holdout never selected",
            "institutional_gate": evidence,
        },
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, calendar)
    results: dict[str, object] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, cash, engine, condition, isolated, targets)
        daily = dict.fromkeys(targets, 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(
            reserve_directory(OUT, condition), condition, trades, events, audit, view, 1, daily
        )
        results[condition] = {
            "trade_count": len(trades),
            "metrics": metrics,
            "execution_audit": audit,
        }
        events_by[condition], trades_by[condition], daily_by[condition] = events, trades, daily
    a2_config = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})}
    )
    a2_trades, a2_events, a2_audit = run_condition(
        view,
        cash,
        BacktestEngine(instrument.instrument.to_spec(), a2_config, calendar),
        "A_reversal",
        isolated,
        targets,
    )
    a2_daily = dict.fromkeys(targets, 0)
    for trade in a2_trades:
        a2_daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    a2_metrics = write_condition(
        reserve_directory(OUT, "A_reversal_2tick"),
        "A_reversal_2tick",
        a2_trades,
        a2_events,
        a2_audit,
        view,
        2,
        a2_daily,
    )
    a0_config = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 0})}
    )
    a0_trades, a0_events, a0_audit = run_condition(
        view,
        cash,
        BacktestEngine(instrument.instrument.to_spec(), a0_config, calendar),
        "A_reversal",
        isolated,
        targets,
    )
    a0_daily = dict.fromkeys(targets, 0)
    for trade in a0_trades:
        a0_daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    a0_metrics = write_condition(
        reserve_directory(OUT, "diagnostics") / "A_reversal_0tick",
        "A_reversal_0tick_diagnostic",
        a0_trades,
        a0_events,
        a0_audit,
        view,
        0,
        a0_daily,
    )
    audits = {
        name: path_audit(
            events_by[name], trades_by[name], baseline.execution.max_fill_delay_minutes
        )
        for name in CONDITIONS
    } | {
        "A_reversal_2tick": path_audit(
            a2_events, a2_trades, baseline.execution.max_fill_delay_minutes
        ),
        "A_reversal_0tick": path_audit(
            a0_events, a0_trades, baseline.execution.max_fill_delay_minutes
        ),
    }
    shared_fields = (
        "trade_date",
        "pre_event_status",
        "reason",
        "P_points",
        "J_points",
        "E_planned_entry_jst",
        "X_planned_exit_jst",
    )
    shared = all(
        all(
            tuple(row.get(field) for field in shared_fields)
            == tuple(events_by["A_reversal"][index].get(field) for field in shared_fields)
            for index, row in enumerate(events_by[name])
        )
        for name in CONDITIONS[1:]
    )
    a, d, f = events_by["A_reversal"], events_by["D_follow"], events_by["F_preopen"]
    eligible_pairs = [
        (a[index], d[index], f[index])
        for index in range(len(a))
        if a[index].get("pre_event_status") == "eligible"
    ]
    relation = {
        "A_D_all_opposite_side_same_schedule_and_fillability": all(
            x.get("side") != y.get("side")
            and x.get("E_planned_entry_jst") == y.get("E_planned_entry_jst")
            and x.get("X_planned_exit_jst") == y.get("X_planned_exit_jst")
            and x.get("status") == y.get("status")
            for x, y, _ in eligible_pairs
        ),
        "A_F_same_sign_identical": all(
            x.get("side") == z.get("side") and x.get("net_pnl_jpy") == z.get("net_pnl_jpy")
            for x, _, z in eligible_pairs
            if x.get("P_sign") == x.get("J_sign")
        ),
        "A_F_opposite_sign_opposite_side": all(
            x.get("side") != z.get("side")
            for x, _, z in eligible_pairs
            if x.get("P_sign") != x.get("J_sign")
        ),
        "same_sign_count": sum(x.get("P_sign") == x.get("J_sign") for x, _, _ in eligible_pairs),
        "opposite_sign_count": sum(
            x.get("P_sign") != x.get("J_sign") for x, _, _ in eligible_pairs
        ),
    }
    a_a2_fields = (
        "trade_date",
        "pre_event_status",
        "P_points",
        "J_points",
        "A_direction",
        "E_planned_entry_jst",
        "X_planned_exit_jst",
    )
    a_a2 = len(a) == len(a2_events) and all(
        tuple(row.get(field) for field in a_a2_fields)
        == tuple(a[index].get(field) for field in a_a2_fields)
        for index, row in enumerate(a2_events)
    )
    post = {
        "status": "PASS"
        if shared
        and a_a2
        and all(cast(bool, audit["all_pass"]) for audit in audits.values())
        and all(
            cast(bool, value)
            for key, value in relation.items()
            if key.endswith("side_same_schedule_and_fillability")
            or key.endswith("identical")
            or key.endswith("opposite_side")
        )
        else "BLOCKED",
        "conditions": audits,
        "all_conditions_same_pre_event": shared,
        "A_A2_identical_pre_event_and_direction": a_a2,
        "directional_identity": relation,
        "policy": "No successful-fill intersection is selected; every retained daily axis no-trade remains zero.",
    }
    write_json(OUT / "post_execution_validation.json", post)
    diagnostics = group_diagnostics(a)
    write_json(OUT / "p_j_sign_diagnostics.json", diagnostics)
    write_json(
        OUT / "a_0tick_diagnostic.json",
        {
            "purpose": "diagnostic only: pre-cost A gross prediction",
            "trade_count": len(a0_trades),
            "gross_pnl_jpy": sum(trade.gross_pnl_jpy for trade in a0_trades),
            "gross_expectancy_jpy": fmean(trade.gross_pnl_jpy for trade in a0_trades)
            if a0_trades
            else None,
            "metrics": a0_metrics["overall"],
        },
    )
    values = {name: [daily_by[name][day] for day in targets] for name in CONDITIONS}
    boot = bootstrap(values)
    months = {
        f"{year:04d}-{month:02d}": 0
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }
    for day, value in daily_by["A_reversal"].items():
        months[day[:7]] += value
    a_metrics = cast(dict[str, Any], results["A_reversal"])["metrics"]
    a_overall = a_metrics["overall"]
    a2_overall = a2_metrics["overall"]

    def lower(name: str) -> bool:
        return cast(list[float], boot[name]["ci95_percentile_linear"])[0] > 0

    long_count = sum(trade.side.value == "long" for trade in trades_by["A_reversal"])
    gate: dict[str, bool] = {
        "A_trade_count_at_least_300": len(trades_by["A_reversal"]) >= 300,
        "B_buy_trade_count_at_least_300": len(trades_by["B_buy"]) >= 300,
        "C_sell_trade_count_at_least_300": len(trades_by["C_sell"]) >= 300,
        "D_follow_trade_count_at_least_300": len(trades_by["D_follow"]) >= 300,
        "F_preopen_trade_count_at_least_300": len(trades_by["F_preopen"]) >= 300,
        "A_long_at_least_75": long_count >= 75,
        "A_short_at_least_75": len(trades_by["A_reversal"]) - long_count >= 75,
        "P_sign_not_equal_J_sign_at_least_100": cast(int, relation["opposite_sign_count"]) >= 100,
        "each_P_J_group_at_least_50": all(
            cast(int, value["pre_event_count"]) >= 50 for value in diagnostics.values()
        ),
        "A_net_positive": a_overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": a_overall["profit_factor"] is not None
        and a_overall["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"),
        "A_minus_B_lower_above_0": lower("A_minus_B_buy_daily_mean_net_jpy"),
        "A_minus_C_lower_above_0": lower("A_minus_C_sell_daily_mean_net_jpy"),
        "A_minus_D_lower_above_0": lower("A_minus_D_follow_daily_mean_net_jpy"),
        "A_minus_F_lower_above_0": lower("A_minus_F_preopen_daily_mean_net_jpy"),
        "A2_expectancy_positive": a2_overall["expectancy_jpy"] is not None
        and a2_overall["expectancy_jpy"] > 0,
        "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "A_net_excluding_top10_positive": a_metrics["concentration"]["net_excluding_top10_jpy"] > 0,
    }
    information = [
        key
        for key in gate
        if "trade_count" in key
        or key.startswith("A_long")
        or key.startswith("A_short")
        or key.startswith("P_sign")
        or key.startswith("each_P")
    ]
    decision = (
        "BLOCKED"
        if post["status"] != "PASS"
        else "INCONCLUSIVE"
        if not all(gate[key] for key in information)
        else "INVESTIGATE"
        if all(gate.values())
        else "REJECT"
    )
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {
            "trade_dates": targets,
            "no_trade": "0",
            "day_session_quarantined": "excluded",
            "TSE_closed_missing_zero_cancel_unfilled": "0",
            **values,
        },
    )
    write_json(OUT / "bootstrap.json", boot)
    development_results = {
        "campaign_id": IDENTIFIER,
        "quality_status": "PASS_LIMITED",
        "decision": decision,
        "gates": gate,
        "A_direction_counts": {
            "long": long_count,
            "short": len(trades_by["A_reversal"]) - long_count,
        },
        "positive_months_of_54": sum(value > 0 for value in months.values()),
        "monthly_A_net_jpy": months,
        "A_year_month_long_short_metrics": {
            "year": a_metrics["segments"]["year"],
            "month": a_metrics["segments"]["month"],
            "side": a_metrics["segments"]["side"],
        },
        "conditions": results
        | {
            "A_reversal_2tick": {
                "trade_count": len(a2_trades),
                "metrics": a2_metrics,
                "execution_audit": a2_audit,
            }
        },
        "P_J_sign_diagnostics": diagnostics,
        "A_0tick_diagnostic": {"metrics": a0_metrics, "execution_audit": a0_audit},
        "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction",
        "scope": "Development only; 2025 is Jan-Jun partial; no WFA/OOS/Final Holdout",
    }
    write_json(OUT / "development_results.json", development_results)
    (OUT / "summary.md").write_text(
        f"# R028-Q001\n\nDevelopment-only fixed cash-open discontinuity experiment. Decision: **{decision}**.\n\nSee `preregistration.json`, `development_results.json`, `bootstrap.json`, and `post_execution_validation.json`. OOS and Final Holdout were not accessed.\n",
        encoding="utf-8",
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
