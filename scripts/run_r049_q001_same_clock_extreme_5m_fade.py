"""Execute the preregistered Development-only R049-Q001 experiment."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, floor, log
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, cast

import numpy as np
import polars as pl

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r049 import TSE_NORMAL_SCHEDULE, r049_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r049-q001-20260915-same-clock-extreme-5m-fade-05"
OUT = ROOT / "results" / "research" / IDENTIFIER
PREDECESSOR = ROOT / "results" / "research" / "r049-q001-20260915-same-clock-extreme-5m-fade-04"
AXIS = (
    ROOT
    / "results"
    / "research"
    / "r012-q001-20260914-night-direction-followthrough-01"
    / "daily_net_pnl_aligned.json"
)
R045 = ROOT / "results" / "research" / "r045-q001-20260914-tse-lunch-placebo-reversal-03"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20260929
BASE = (
    "A_extreme_fade",
    "B_broad_fade",
    "C_moderate_fade",
    "D_buy",
    "E_sell",
    "F_extreme_continue",
)
BASE_THRESHOLDS = {"B_broad_fade": 75}
VARIANTS = {"A2_2tick": (2, 0), "A3_3tick": (3, 0), "A_delay": (1, 1)}
SENSITIVITIES = {"A85": (85, 15), "A95": (95, 15), "A_h10": (90, 10), "A_h20": (90, 20)}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fixed_axis() -> list[str]:
    axis = json.loads(AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(axis, list) or len(axis) != 1111 or len(set(axis)) != 1111:
        raise ValueError("fixed 1,111 trade_date axis unavailable")
    return cast(list[str], axis)


def axis_bars(data: ResearchData, axis: list[str]) -> list[Any]:
    """Keep all R049 statistics on the fixed eligible day axis, not the residual night bars."""
    eligible_dates = {date.fromisoformat(day) for day in axis}
    bars = [bar for bar in data.bars if bar.trade_date in eligible_dates]
    observed = {bar.trade_date.isoformat() for bar in bars}
    if observed != set(axis):
        raise ValueError("R049 metrics bars do not reproduce the fixed 1,111-date axis")
    return bars


def input_manifest(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_r049_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R045 / "institutional_evidence" / "cabinet_office_public_holidays.csv"
    if not source.exists():
        raise ValueError("frozen TSE calendar evidence unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copied = destination / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    if calendar.source_start > date(2021, 1, 1) or calendar.source_end < date(2025, 6, 30):
        raise ValueError("frozen TSE calendar does not cover Development")
    evidence = {
        "holiday_csv_sha256": digest(copied),
        "r045_completed_sha256": digest(R045 / "COMPLETED.json"),
        "schedule_id": "R049-TSE-NORMAL-1",
        "schedule": {
            "effective_start": TSE_NORMAL_SCHEDULE.effective_start.isoformat(),
            "effective_end": TSE_NORMAL_SCHEDULE.effective_end.isoformat(),
            "morning_open_jst": TSE_NORMAL_SCHEDULE.morning_open.isoformat(),
            "afternoon_open_jst": TSE_NORMAL_SCHEDULE.afternoon_open.isoformat(),
            "source": TSE_NORMAL_SCHEDULE.source,
        },
        "rule": "TSE business days come from frozen Cabinet Office holidays/weekend rules; cash boundaries come only from versioned R049-TSE-NORMAL-1.",
    }
    write_json(destination / "evidence_manifest.json", evidence)
    return calendar, evidence


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
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
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in bar.quality_flags for bar in included
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
    mismatch = {
        key: {"actual": audit[key], "expected": value}
        for key, value in expected.items()
        if audit[key] != value
    }
    audit.update(
        expected_match=not mismatch,
        mismatches=mismatch,
        rule="exclude whole (trade_date, session) for TICK_GRID_VIOLATION",
    )
    if mismatch:
        raise ValueError(f"R004 isolation reproduction mismatch: {mismatch}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def event_for_day(
    day: date,
    grouped: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    tse_days: list[date],
    cash: TSECashMarketCalendar,
) -> dict[str, object]:
    if day not in tse_days:
        return {
            "trade_date": day.isoformat(),
            "status": "skipped",
            "reason": "TSE_CASH_MARKET_CLOSED_OR_UNSCHEDULED",
        }
    index = tse_days.index(day)
    history = [
        (prior, grouped.get((prior, Session.DAY)), (prior, Session.DAY) in isolated)
        for prior in tse_days[max(0, index - 120) : index]
    ]
    return r049_event(
        day,
        grouped.get((day, Session.DAY)),
        history,
        cash,
        target_quarantined=(day, Session.DAY) in isolated,
    )


def select(
    condition: str, event: dict[str, object], threshold: int = 90
) -> dict[str, object] | None:
    if event.get("status") != "E":
        return None
    rows = cast(list[dict[str, object]], event["observations"])
    if condition == "C_moderate_fade":
        matches = [row for row in rows if bool(row["moderate_q75_q90"])]
    else:
        matches = [row for row in rows if float(row["x"]) >= float(row[f"q{threshold}"])]
    return matches[0] if matches else None


def direction(condition: str, row: dict[str, object]) -> str:
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    sign = int(row["r_sign"])
    if condition == "F_extreme_continue":
        return "long" if sign > 0 else "short"
    return "short" if sign > 0 else "long"


def add_zero_tick(event: dict[str, object], rows: list[Any]) -> None:
    if event.get("status") != "E":
        return
    by_time = {bar.ts_jst: bar for bar in rows}
    for row in cast(list[dict[str, object]], event["observations"]):
        entry = datetime.fromisoformat(str(row["entry_jst"]))
        exit_ = datetime.fromisoformat(str(row["exit_h15_jst"]))
        if entry not in by_time or exit_ not in by_time:
            raise ValueError("R049 common E lacks OLS endpoints")
        row["future_gross_0tick_prefee_jpy"] = (
            (by_time[exit_].open - by_time[entry].open) * (-int(row["r_sign"])) * 100
        )


def run_condition(
    data: ResearchData,
    engine: BacktestEngine,
    condition: str,
    grouped: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    axis: list[str],
    tse_days: list[date],
    cash: TSECashMarketCalendar,
    *,
    threshold: int = 90,
    holding: int = 15,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    select_condition = (
        "A_extreme_fade" if condition in {"D_buy", "E_sell", "F_extreme_continue"} else condition
    )
    for text_day in axis:
        day = date.fromisoformat(text_day)
        event = event_for_day(day, grouped, isolated, tse_days, cash)
        event.update(
            condition=condition,
            base_event_status=event.get("status"),
            threshold=threshold,
            holding_minutes=holding,
            variant_delay_minutes=delay,
        )
        rows = grouped.get((day, Session.DAY))
        if rows is not None:
            add_zero_tick(event, rows)
        chosen = select(select_condition, event, threshold)
        if chosen is None:
            event.update(status="skipped", reason_for_condition="CONDITION_NOT_MET")
            audit["skipped"] += 1
            events.append(event)
            continue
        entry = datetime.fromisoformat(str(chosen["entry_jst"]))
        exit_ = entry + __import__("datetime").timedelta(minutes=holding)
        side = direction(condition, chosen)
        event.update(
            selected_anchor=chosen["anchor"],
            selection_reason=(
                "FIRST_Q75_TO_Q90"
                if condition == "C_moderate_fade"
                else f"FIRST_X_GTE_Q{threshold}"
            ),
            selected_r_points=chosen["r_points"],
            planned_entry_jst=entry.isoformat(),
            planned_exit_jst=exit_.isoformat(),
            planned_direction=side,
        )
        if rows is None:
            raise AssertionError("R049 selected condition lacks day rows")
        result = engine.run(
            rows,
            R049FixedTimeStrategy(f"r049_{condition}", entry, exit_, side, delay),
            canonical_hash(
                {
                    "condition": condition,
                    "event": event,
                    "data": data.data_version,
                    "delay": delay,
                    "holding": holding,
                }
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R049 violates one daily position")
        audit["canceled_orders"] += result.canceled_orders
        if not result.trades:
            event.update(
                status="eligible_order_unfilled",
                reason_for_condition="ENGINE_NO_FILL_OR_FIXED_EXIT",
            )
            audit["eligible_order_unfilled"] += 1
            events.append(event)
            continue
        trade = result.trades[0]
        event.update(
            status="filled",
            side=trade.side.value,
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            entry_signal_ts_jst=trade.entry_signal_ts.isoformat(),
            exit_signal_ts_jst=trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None,
            entry_delay_minutes=int((trade.entry_ts - entry).total_seconds() // 60),
            exit_delay_minutes=int((trade.exit_ts - exit_).total_seconds() // 60),
            exit_reason=trade.exit_reason.value,
            gross_pnl_jpy=trade.gross_pnl_jpy,
            slippage_cost_jpy=trade.slippage_cost_jpy,
            fees_jpy=trade.fees_jpy,
            net_pnl_jpy=trade.net_pnl_jpy,
        )
        trades.append(trade)
        audit["trades"] += 1
        events.append(event)
    return (
        tuple(
            replace(trade, trade_id=f"trade-{number:06d}")
            for number, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
        ),
        events,
        dict(sorted(audit.items())),
    )


def write_condition(
    folder: Path,
    name: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    audit: dict[str, int],
    bars: list[Any],
    ticks: int,
    daily: dict[str, int],
) -> dict[str, object]:
    folder.mkdir(parents=True)
    write_results(
        folder,
        trades,
        (),
        {
            "campaign_id": IDENTIFIER,
            "condition": name,
            "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30},
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    metrics = research_metrics(trades, bars)
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    write_json(folder / "events.json", events)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": list(daily), "net_pnl_jpy": list(daily.values())}).write_parquet(
        folder / "daily_net_pnl.parquet"
    )
    return metrics


def percentile(values: list[float], q: float) -> float:
    ordered, location = sorted(values), (len(values) - 1) * q
    lo, hi = floor(location), ceil(location)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (location - lo)


def ols(
    events: list[dict[str, object]], sampled_dates: list[str] | None = None
) -> tuple[float, dict[str, object]]:
    raw: list[dict[str, float | str]] = []
    for event in events:
        if event.get("base_event_status") != "E":
            continue
        for row in cast(list[dict[str, object]], event["observations"]):
            raw.append(
                {
                    "date": str(event["trade_date"]),
                    "anchor": str(row["anchor"]),
                    "y": float(row["future_gross_0tick_prefee_jpy"]),
                    "Q": float(float(row["x"]) >= float(row["q90"])),
                    "z": log(float(row["x"]) / float(row["q90"])),
                    "U": float(int(row["r_sign"]) > 0),
                }
            )
    if not raw:
        raise ValueError("R049 OLS has no common-E anchor observations")
    counts = (
        Counter(sampled_dates)
        if sampled_dates is not None
        else Counter({str(row["date"]): 1 for row in raw})
    )
    anchors = ["mS+30", "mS+60", "mS+90", "aS+15", "aS+45", "aS+75"]
    years = sorted({str(row["date"])[:4] for row in raw if counts[str(row["date"])] > 0})
    matrix = np.array(
        [
            [
                1.0,
                row["Q"],
                row["z"],
                float(row["z"]) ** 2,
                row["U"],
                *(float(row["anchor"] == anchor) for anchor in anchors[1:]),
                *(float(str(row["date"])[:4] == year) for year in years[1:]),
            ]
            for row in raw
        ],
        dtype=float,
    )
    weights = np.array([counts[str(row["date"])] for row in raw], dtype=float)
    cross = matrix.T @ (matrix * weights[:, None])
    rank = int(np.linalg.matrix_rank(cross, tol=1e-8)) if sampled_dates is None else matrix.shape[1]
    audit: dict[str, object] = {
        "formula": "y=alpha+delta*Q+gamma1*z+gamma2*z^2+eta*U+anchor fixed effects+calendar-year fixed effects+epsilon",
        "rows": len(raw),
        "columns": matrix.shape[1],
        "rank": rank,
        "full_rank": rank == matrix.shape[1],
        "anchor_levels": anchors,
        "year_levels": years,
        "delta_column": 1,
        "bootstrap_weighting": "exact multiplicity weights by sampled trade_date",
    }
    if not audit["full_rank"]:
        raise ValueError("R049 fixed OLS design is not full rank")
    y = np.array([row["y"] for row in raw], dtype=float)
    return float(np.linalg.solve(cross, matrix.T @ (y * weights))[1]), audit


def bootstrap(
    daily: dict[str, dict[str, int]], events: dict[str, list[dict[str, object]]], axis: list[str]
) -> dict[str, object]:
    filled = {
        name: {str(row["trade_date"]): row.get("status") == "filled" for row in rows}
        for name, rows in events.items()
    }
    labels = (
        "A_daily_mean_net_jpy",
        "A_minus_B_conditional_expectancy_jpy",
        "A_minus_C_conditional_expectancy_jpy",
        "A_minus_D_conditional_expectancy_jpy",
        "A_minus_E_conditional_expectancy_jpy",
        "A_minus_F_conditional_expectancy_jpy",
        "delta_q90_increment_jpy",
    )
    samples: dict[str, list[float]] = {label: [] for label in labels}
    estimate, audit = ols(events["A_extreme_fade"])
    comparisons = (
        ("B_broad_fade", "B"),
        ("C_moderate_fade", "C"),
        ("D_buy", "D"),
        ("E_sell", "E"),
        ("F_extreme_continue", "F"),
    )
    rng, block = Random(SEED), 20
    sampled_lengths: set[int] = set()
    sampled_min_index, sampled_max_index = len(axis), -1
    for _ in range(10_000):
        indexes: list[int] = []
        while len(indexes) < len(axis):
            begin = rng.randrange(len(axis) - block + 1)
            indexes.extend(range(begin, begin + block))
        indexes = indexes[: len(axis)]
        sampled_lengths.add(len(indexes))
        sampled_min_index = min(sampled_min_index, min(indexes))
        sampled_max_index = max(sampled_max_index, max(indexes))
        days = [axis[index] for index in indexes]
        samples["A_daily_mean_net_jpy"].append(fmean(daily["A_extreme_fade"][day] for day in days))
        for other, label in comparisons:
            samples[f"A_minus_{label}_conditional_expectancy_jpy"].append(
                fmean(daily["A_extreme_fade"][day] for day in days if filled["A_extreme_fade"][day])
                - fmean(daily[other][day] for day in days if filled[other][day])
            )
        samples["delta_q90_increment_jpy"].append(ols(events["A_extreme_fade"], days)[0])
    estimates = {
        "A_daily_mean_net_jpy": fmean(daily["A_extreme_fade"].values()),
        **{
            f"A_minus_{label}_conditional_expectancy_jpy": fmean(
                daily["A_extreme_fade"][day] for day in axis if filled["A_extreme_fade"][day]
            )
            - fmean(daily[other][day] for day in axis if filled[other][day])
            for other, label in comparisons
        },
        "delta_q90_increment_jpy": estimate,
    }
    return {
        "method": "20 trade_date noncircular moving-block bootstrap, 10,000 repetitions, seed 20260929, common index, tail truncate, linear percentile. Rolling thresholds and events are retained; conditional sum/count and OLS are recomputed.",
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": SEED,
        "input_index_audit": {
            "input_trade_date_count": len(axis),
            "input_trade_date_sha256": canonical_hash(axis),
            "sampled_trade_date_count_values": sorted(sampled_lengths),
            "sampled_index_min": sampled_min_index,
            "sampled_index_max": sampled_max_index,
            "all_sampled_indexes_in_bounds": sampled_min_index == 0
            and sampled_max_index == len(axis) - 1,
            "common_index_for_all_series": True,
        },
        "ols": audit,
        **{
            name: {
                "estimate": estimates[name],
                "ci95_percentile_linear": [percentile(sample, 0.025), percentile(sample, 0.975)],
            }
            for name, sample in samples.items()
        },
    }


def condition_and_population_audit(
    ledger: dict[str, dict[str, dict[str, object]]], axis: list[str]
) -> dict[str, object]:
    """Audit the preregistered A/B/C wiring and the fixed common-E population."""
    conditions = {
        "A_extreme_fade": 90,
        "B_broad_fade": 75,
        "C_moderate_fade": 90,
    }
    e_days = {
        day for day in axis if ledger["A_extreme_fade"][day].get("base_event_status") == "E"
    }
    selected = {
        name: {day for day in axis if "selected_anchor" in ledger[name][day]}
        for name in conditions
    }
    selection_failures: dict[str, list[dict[str, object]]] = {name: [] for name in conditions}
    predicate_failures: dict[str, list[dict[str, object]]] = {name: [] for name in conditions}
    for name, threshold in conditions.items():
        for day in axis:
            row = ledger[name][day]
            # run_condition replaces event.status with filled/skipped after
            # selection.  Reconstruct the causal base-event state for a
            # ledger-only selection audit.
            selection_event = dict(row)
            selection_event["status"] = row.get("base_event_status")
            expected = select(name, selection_event, threshold)
            actual_anchor = row.get("selected_anchor")
            if (expected is None) != (actual_anchor is None) or (
                expected is not None and actual_anchor != expected["anchor"]
            ):
                selection_failures[name].append(
                    {
                        "trade_date": day,
                        "expected_anchor": None if expected is None else expected["anchor"],
                        "actual_anchor": actual_anchor,
                    }
                )
                continue
            if expected is None:
                continue
            chosen = next(
                item
                for item in cast(list[dict[str, object]], row["observations"])
                if item["anchor"] == actual_anchor
            )
            predicate = (
                bool(chosen["moderate_q75_q90"])
                if name == "C_moderate_fade"
                else float(chosen["x"]) >= float(chosen[f"q{threshold}"])
            )
            if not predicate:
                predicate_failures[name].append(
                    {"trade_date": day, "anchor": actual_anchor}
                )

    base_rows = ledger["A_extreme_fade"]
    waterfall: Counter[str] = Counter()
    yearly_waterfall: dict[str, Counter[str]] = defaultdict(Counter)
    anchor_reason: Counter[tuple[str, str, str]] = Counter()
    for day in axis:
        row = base_rows[day]
        year = day[:4]
        if row.get("base_event_status") == "E":
            reason = "COMMON_E"
        else:
            reason = str(row.get("reason", "UNCLASSIFIED"))
            if reason == "HISTORY_NOT_EXACTLY_120_TSE_DAYS":
                reason = "WARM_UP"
            elif reason == "INSUFFICIENT_VALID_REFERENCES":
                reason = "HISTORY_LT100_VALID"
            elif reason == "TSE_CASH_MARKET_CLOSED_OR_UNSCHEDULED":
                reason = "TSE_CLOSED_OR_UNSCHEDULED"
        waterfall[reason] += 1
        yearly_waterfall[year][reason] += 1
        if reason == "HISTORY_LT100_VALID":
            for anchor, count in cast(dict[str, int], row["reference_counts"]).items():
                if count < 100:
                    anchor_reason[(year, anchor, reason)] += 1
        elif row.get("reason") == "TARGET_ANCHOR_INVALID":
            for observation in cast(list[dict[str, object]], row["observations"]):
                if observation.get("status") != "valid":
                    anchor_reason[(year, str(observation["anchor"]), str(observation.get("reason")))] += 1
        else:
            anchor_reason[(year, "ALL", reason)] += 1

    invariants = {
        "A_subset_B": selected["A_extreme_fade"] <= selected["B_broad_fade"],
        "C_subset_B": selected["C_moderate_fade"] <= selected["B_broad_fade"],
        "max_A_C_lte_B_lte_E": max(
            len(selected["A_extreme_fade"]), len(selected["C_moderate_fade"])
        )
        <= len(selected["B_broad_fade"])
        <= len(e_days),
        "common_E_fixed_all_conditions": all(
            ledger[name][day].get("base_event_status") == ("E" if day in e_days else "skipped")
            for name in ledger
            for day in axis
        ),
        "independent_first_anchor": all(not values for values in selection_failures.values()),
        "selected_predicates": all(not values for values in predicate_failures.values()),
    }
    return {
        "status": "PASS" if all(invariants.values()) else "BLOCKED",
        "invariants": invariants,
        "counts": {"E": len(e_days), **{name: len(days) for name, days in selected.items()}},
        "set_differences": {
            "A_minus_B": sorted(selected["A_extreme_fade"] - selected["B_broad_fade"]),
            "C_minus_B": sorted(selected["C_moderate_fade"] - selected["B_broad_fade"]),
            "B_minus_E": sorted(selected["B_broad_fade"] - e_days),
        },
        "selection_failures": selection_failures,
        "predicate_failures": predicate_failures,
        "population_waterfall": dict(sorted(waterfall.items())),
        "population_waterfall_by_year": {
            year: dict(sorted(counts.items())) for year, counts in sorted(yearly_waterfall.items())
        },
        "anchor_year_reason_exclusions": [
            {"year": year, "anchor": anchor, "reason": reason, "count": count}
            for (year, anchor, reason), count in sorted(anchor_reason.items())
        ],
        "scope": "Fixed 1,111-date Development axis only; common E is evaluated before A/B/C selection.",
    }


def aggregation_axis_audit(
    events_by: dict[str, list[dict[str, object]]],
    trades_by: dict[str, tuple[Trade, ...]],
    daily: dict[str, dict[str, int]],
    results: dict[str, dict[str, object]],
    axis: list[str],
    isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Prove that -05 changes only the erroneous 1,120-date metrics aggregation."""
    if not PREDECESSOR.exists():
        raise ValueError("R049 -04 is required as the frozen aggregation-audit predecessor")
    previous_daily = cast(
        dict[str, object], json.loads((PREDECESSOR / "daily_net_pnl_aligned.json").read_text())
    )
    previous_series = cast(dict[str, dict[str, int]], previous_daily["series"])
    previous_axis = cast(list[str], previous_daily["trade_dates"])
    if previous_axis != axis:
        raise ValueError("R049 predecessor does not use the fixed 1,111-date aligned axis")

    condition_audit: dict[str, object] = {}
    all_pass = True
    metric_extra_dates: set[str] | None = None
    for name, current in sorted(results.items()):
        prior_events = json.loads((PREDECESSOR / name / "events.json").read_text())
        prior_metrics = cast(
            dict[str, object],
            json.loads((PREDECESSOR / name / "metrics_research.json").read_text()),
        )
        prior_overall = cast(dict[str, object], prior_metrics["overall"])
        current_metrics = cast(dict[str, object], current["metrics"])
        current_overall = cast(dict[str, object], current_metrics["overall"])
        prior_daily_metric = cast(dict[str, int], prior_metrics["daily_net_pnl_jpy"])
        extra = set(prior_daily_metric) - set(axis)
        if metric_extra_dates is None:
            metric_extra_dates = extra
        elif metric_extra_dates != extra:
            raise ValueError("R049 predecessor metrics axes differ by condition")
        changed_overall = {
            key: {"before": prior_overall.get(key), "after": current_overall.get(key)}
            for key in current_overall
            if prior_overall.get(key) != current_overall.get(key)
        }
        event_equal = prior_events == events_by[name]
        trade_count_equal = int(prior_overall["trade_count"]) == len(trades_by[name])
        net_equal = int(prior_overall["net_pnl_jpy"]) == sum(
            trade.net_pnl_jpy for trade in trades_by[name]
        )
        aligned_daily_equal = previous_series[name] == daily[name]
        checks = {
            "event_ledger_equal": event_equal,
            "trade_count_equal": trade_count_equal,
            "net_pnl_equal": net_equal,
            "aligned_daily_net_pnl_equal": aligned_daily_equal,
        }
        all_pass = all_pass and all(checks.values())
        condition_audit[name] = {
            "checks": checks,
            "event_count_before_after": [len(prior_events), len(events_by[name])],
            "trade_count_before_after": [int(prior_overall["trade_count"]), len(trades_by[name])],
            "net_pnl_jpy_before_after": [
                int(prior_overall["net_pnl_jpy"]),
                sum(trade.net_pnl_jpy for trade in trades_by[name]),
            ],
            "overall_metrics_changed": changed_overall,
        }
    if metric_extra_dates is None or len(metric_extra_dates) != 9:
        raise ValueError("R049 expected exactly nine extra metrics dates in -04")
    day_isolation = {day.isoformat() for day, session in isolated if session is Session.DAY}
    if not metric_extra_dates <= day_isolation:
        raise ValueError("R049 extra metrics dates are not all R004 day-session isolates")
    date_details = []
    for day in sorted(metric_extra_dates):
        per_condition = {
            name: {
                "metrics_daily_net_pnl_jpy": cast(
                    dict[str, int],
                    json.loads((PREDECESSOR / name / "metrics_research.json").read_text())["daily_net_pnl_jpy"],
                )[day],
                "event_rows": sum(row["trade_date"] == day for row in events_by[name]),
                "trade_count": sum(
                    trade.trade_date.isoformat() == day for trade in trades_by[name]
                ),
            }
            for name in sorted(results)
        }
        date_details.append(
            {
                "trade_date": day,
                "on_fixed_1111_axis": False,
                "r004_day_session_quarantined": True,
                "per_condition": per_condition,
            }
        )
    if not all(
        values["metrics_daily_net_pnl_jpy"] == 0
        and values["event_rows"] == 0
        and values["trade_count"] == 0
        for detail in date_details
        for values in cast(dict[str, dict[str, int]], detail["per_condition"]).values()
    ):
        raise ValueError("R049 extra metrics date carries an event, trade, or PnL")
    return {
        "status": "PASS" if all_pass else "BLOCKED",
        "predecessor": PREDECESSOR.name,
        "correction": "Metrics statistics now receive only bars whose trade_date belongs to the fixed aligned 1,111-date axis. Events, orders, fills, trades, costs, seed, bootstrap and decision criteria are unchanged.",
        "fixed_axis": {"count": len(axis), "sha256": canonical_hash(axis)},
        "predecessor_metrics_axis": {"count": len(axis) + len(metric_extra_dates)},
        "extra_dates": date_details,
        "conditions": condition_audit,
        "ci_status": "UNCHANGED: -04 bootstrap already consumed daily_net_pnl_aligned on the fixed 1,111-date axis; -05 recomputes and verifies the same seed/index contract.",
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"append-only output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    runtime = baseline.model_copy(
        update={
            "risk": baseline.risk.model_copy(
                update={"new_entry_cutoff_minutes_before_session_close": 0}
            )
        }
    )
    if runtime.execution.slippage_ticks != 1 or runtime.fees.jpy_per_side_per_contract != 30:
        raise ValueError("R049 requires baseline one tick and JPY30 per side")
    exchange = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier, axis = CalendarClassifier(sessions, exchange), fixed_axis()
    source, manifest = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        input_manifest(data_config.gold_root),
    )
    cash, evidence = cash_calendar()
    files = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r049.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r049_q001.py"),
    ]
    implementation = {str(path): digest(ROOT / path) for path in files}
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R049-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development exploration, not independent confirmation.",
        "duplicate_review": {
            "R001_R048": "R035 is a same-clock 5-minute shock study but lacks this fixed six TSE-relative-anchor, 120 scheduled-day q75/q85/q90/q95, common-E, q75--q90 control, specified comparisons and OLS design.",
            "conclusion": "No equivalent registration; execute this one fixed experiment only.",
        },
        "hypothesis": "At six fixed anchors away from TSE cash boundaries, the first causal same-clock q90-or-higher absolute five-minute futures move has positive post-cost 15-minute fade expectancy and exceeds broad/moderate fade, fixed directions and continuation. It does not identify liquidity shocks, news, order flow, participants or cash prices.",
        "state": "Anchors are mS+30/+60/+90 and aS+15/+45/+75 from R049-TSE-NORMAL-1. For each anchor p0=open(anchor-5), p5=close(anchor-1), r=p5-p0, x=abs(r)/p0. The target is excluded; exactly the preceding 120 scheduled TSE days provide same-anchor valid x, with >=100 required and nearest-rank q75/q85/q90/q95. No backfill. All six target observations, thresholds and entry-to-20m paths define E.",
        "conditions": {
            "A_extreme_fade": "first x>=q90, -sign(r), 15m",
            "B_broad_fade": "independent first x>=q75, -sign(r), 15m",
            "C_moderate_fade": "independent first q75<=x<q90, -sign(r), 15m",
            "D_buy/E_sell/F_extreme_continue": "A event/time fixed long/fixed short/sign(r)",
            "A2/A3/A_delay": "A at 2/3 ticks or one-minute entry delay without exit extension",
            "A85/A95": "independent first x>=q85/q95",
            "A_h10/A_h20": "A event/time/side at fixed 10/20m exits",
        },
        "execution": "One contract, one trade/date/position, anchor open entry following anchor-1 close, fixed exit open; baseline one tick plus JPY30 per side; no Stop/Target/reentry/update/early exit; Gross is slippage-inclusive and Net=Gross-fees.",
        "ols": "All eligible anchors: y is -sign(r) adjusted zero-tick/pre-fee 15m gross JPY; Q=1[x>=q90], z=ln(x/q90), U=1[r>0], plus anchor and calendar-year FE. Original design full rank required.",
        "evaluation": "Fixed 1,111 date axis including zero for every no-trade state. 20-date noncircular moving-block bootstrap, 10,000, common dates, tail truncate, linear percentile, seed 20260929; do not reestimate events/thresholds.",
        "inputs": {
            "physical": manifest,
            "axis": {"count": 1111, "sha256": digest(AXIS)},
            "tse_calendar": evidence,
            "r004_quarantine": "45 sessions/27345 bars excluded; 2216 sessions/1326086 bars retained; fixed hash required.",
            "quality_ceiling": "PASS_LIMITED",
        },
        "identifiers_before_run": {
            "seed": SEED,
            "source": source,
            "implementation": implementation,
            "implementation_hash": canonical_hash(implementation),
            "input_manifest_hash": canonical_hash(manifest),
        },
        "information_gate": "E>=750,A>=250,B>=450,C>=250,A long/short>=80 each, every anchor A>=25,A95>=120.",
        "decision": "BLOCKED for input/schedule/synthetic/execution/accounting/OLS failure; INCONCLUSIVE for information shortage; otherwise REJECT unless every preregistered economic/CI/robustness criterion passes, then INVESTIGATE only.",
        "prohibited": [
            "raw",
            "volume",
            "cash/external prices",
            "OOS",
            "Final Holdout",
            "additional anchors/quantiles/windows/holdings/directions/regressions",
            "Stop/Target",
            "WFA",
            "rescue changes",
        ],
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", manifest)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "effective_config.json",
        {
            "instrument": instrument.model_dump(mode="json"),
            "backtest": runtime.model_dump(mode="json"),
        },
    )
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "status": "preregistered",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "plan_hash": canonical_hash(plan),
            "seed": SEED,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    commands = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r049_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r049.py",
            "src/n225m_bt/strategies/r049_fixed_time.py",
            "tests/test_r049_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r049.py",
            "src/n225m_bt/strategies/r049_fixed_time.py",
        ],
    }
    validation: dict[str, Any] = {
        name: {"returncode": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
        for name, command in commands.items()
        for process in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation.update(
        coverage="schedule/cash holidays/calendar_date-trade_date, six anchors, 5m p0-p5-r-x, exact 120/no-target/no-backfill/ranks/ties, common E/zero/isolation/prefix, event first/no substitute, fixed entry/exit/delay/cost/accounting/OOS-holdout lock",
        status="PASS"
        if all(cast(dict[str, object], value)["returncode"] == 0 for value in validation.values())
        else "BLOCKED",
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R049 validation failed before Development price access")
    development = load_split(data_config.gold_root, "development")
    view, qaudit, isolated = quarantine(development)
    observed = {
        bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY
    } - {day.isoformat() for day, session in isolated if session is Session.DAY}
    if set(axis) != observed:
        raise ValueError("fixed 1,111 date axis mismatch")
    metrics_bars = axis_bars(view, axis)
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": qaudit,
            "axis": axis,
            "physical_io": "Development normalized Parquet only; OOS/Final Holdout never selected",
        },
    )
    grouped = session_groups(view.bars)
    tse_days = [
        row.trade_date
        for row in exchange.trading_days()
        if date(2021, 1, 1) <= row.trade_date <= date(2025, 6, 30) and cash.is_open(row.trade_date)
    ]
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily: dict[str, dict[str, int]] = {}
    results: dict[str, dict[str, object]] = {}

    def execute(
        name: str,
        condition: str,
        *,
        ticks: int = 1,
        threshold: int = 90,
        holding: int = 15,
        delay: int = 0,
    ) -> None:
        config = runtime.model_copy(
            update={"execution": runtime.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, events, audit = run_condition(
            view,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            condition,
            grouped,
            isolated,
            axis,
            tse_days,
            cash,
            threshold=threshold,
            holding=holding,
            delay=delay,
        )
        values = dict.fromkeys(axis, 0)
        for trade in trades:
            values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(OUT / name, name, trades, events, audit, metrics_bars, ticks, values)
        trades_by[name], events_by[name], daily[name], results[name] = (
            trades,
            events,
            values,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )

    for name in BASE:
        # B is an independent q75 condition.  The prior -02 invocation used
        # execute()'s q90 default here, which invalidated its B ledger.
        execute(name, name, threshold=BASE_THRESHOLDS.get(name, 90))
    for name, (ticks, delay) in VARIANTS.items():
        execute(name, "A_extreme_fade", ticks=ticks, delay=delay)
    for name, (threshold, holding) in SENSITIVITIES.items():
        execute(name, "A_extreme_fade", threshold=threshold, holding=holding)
    ledger = {
        name: {str(row["trade_date"]): row for row in rows} for name, rows in events_by.items()
    }
    condition_audit = condition_and_population_audit(ledger, axis)
    write_json(OUT / "eligibility_condition_audit.json", condition_audit)
    e_days = [day for day in axis if ledger["A_extreme_fade"][day].get("base_event_status") == "E"]
    a_days = [day for day in axis if ledger["A_extreme_fade"][day].get("status") == "filled"]
    checks = {
        "condition_wiring_and_common_E": condition_audit["status"] == "PASS",
        "common_E_all_conditions": all(
            ledger[name][day].get("base_event_status") == "E"
            for name in (*BASE, *VARIANTS, *SENSITIVITIES)
            for day in e_days
        ),
        "A_D_E_F_same_event_entry_exit": all(
            all(
                ledger["A_extreme_fade"][day].get(field) == ledger[name][day].get(field)
                for field in ("selected_anchor", "planned_entry_jst", "planned_exit_jst")
            )
            for name in ("D_buy", "E_sell", "F_extreme_continue")
            for day in a_days
        ),
        "A_F_opposite_side": all(
            ledger["A_extreme_fade"][day].get("side")
            != ledger["F_extreme_continue"][day].get("side")
            for day in a_days
        ),
        "variants_event_side_equal": all(
            ledger[name][day].get(field) == ledger["A_extreme_fade"][day].get(field)
            for name in (*VARIANTS, "A_h10", "A_h20")
            for day in axis
            for field in ("selected_anchor", "planned_direction", "side")
        ),
        "fixed_exit_and_delay": all(
            row.get("exit_reason") == ExitReason.SIGNAL.value
            and row.get("exit_delay_minutes") == 0
            and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0)
            for name, rows in events_by.items()
            for row in rows
            if row.get("status") == "filled"
        ),
        "one_position": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in trades_by.values()
        ),
        "no_cancellations_or_unfilled": not any(
            row.get("status") == "eligible_order_unfilled"
            for rows in events_by.values()
            for row in rows
        ),
        "accounting": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "status": "PASS" if all(checks.values()) else "BLOCKED",
            "checks": checks,
            "accounting": "Gross fill-to-fill is slippage-inclusive; Net=Gross-fees; slippage is not separately deducted.",
        },
    )
    if not all(checks.values()):
        raise ValueError("R049 execution/accounting gate failed")
    boot = bootstrap(daily, events_by, axis)
    aggregation_audit = aggregation_axis_audit(
        events_by, trades_by, daily, results, axis, isolated
    )
    write_json(OUT / "aggregation_axis_audit.json", aggregation_audit)
    if aggregation_audit["status"] != "PASS":
        raise ValueError("R049 aggregation-axis correction changed an event, trade count, or Net PnL")
    a_metrics = cast(dict[str, Any], results["A_extreme_fade"]["metrics"])
    anchors = {
        anchor: sum(
            row.get("status") == "filled" and row.get("selected_anchor") == anchor
            for row in events_by["A_extreme_fade"]
        )
        for anchor in ("mS+30", "mS+60", "mS+90", "aS+15", "aS+45", "aS+75")
    }
    directions = {
        side: sum(
            row.get("status") == "filled" and row.get("side") == side
            for row in events_by["A_extreme_fade"]
        )
        for side in ("long", "short")
    }
    years = {
        str(year): sum(daily["A_extreme_fade"][day] for day in axis if day.startswith(str(year)))
        for year in range(2021, 2026)
    }
    months = {
        f"{year}-{month:02d}": sum(
            daily["A_extreme_fade"][day] for day in axis if day.startswith(f"{year}-{month:02d}")
        )
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }
    by_anchor = {
        anchor: sum(
            int(row.get("net_pnl_jpy", 0))
            for row in events_by["A_extreme_fade"]
            if row.get("status") == "filled" and row.get("selected_anchor") == anchor
        )
        for anchor in anchors
    }
    by_direction = {
        side: sum(
            int(row.get("net_pnl_jpy", 0))
            for row in events_by["A_extreme_fade"]
            if row.get("status") == "filled" and row.get("side") == side
        )
        for side in directions
    }
    write_json(
        OUT / "A_breakdowns.json",
        {
            "anchor_net_jpy": by_anchor,
            "direction_net_jpy": by_direction,
            "year_net_jpy": years,
            "month_net_jpy": months,
            "positive_months": sum(value > 0 for value in months.values()),
            "concentration": a_metrics["concentration"],
        },
    )
    info = {
        "E>=750": len(e_days) >= 750,
        "A>=250": len(trades_by["A_extreme_fade"]) >= 250,
        "B>=450": len(trades_by["B_broad_fade"]) >= 450,
        "C>=250": len(trades_by["C_moderate_fade"]) >= 250,
        "A_long_short>=80": all(value >= 80 for value in directions.values()),
        "each_anchor_A>=25": all(value >= 25 for value in anchors.values()),
        "A95>=120": len(trades_by["A95"]) >= 120,
    }

    def ci_positive(name: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
        )

    gate = {
        "A_net_positive": a_metrics["overall"]["net_pnl_jpy"] > 0,
        "A_pf_gt_1": a_metrics["overall"]["profit_factor"] is not None
        and a_metrics["overall"]["profit_factor"] > 1,
        "all_requested_ci_positive": all(
            ci_positive(name) for name in boot if name.endswith("jpy")
        ),
        "A2_A3_A_delay_expectancy_positive": all(
            cast(dict[str, Any], results[name]["metrics"])["overall"]["expectancy_jpy"] > 0
            for name in VARIANTS
        ),
        "A85_A95_and_h10_h20_positive_pf": all(
            cast(dict[str, Any], results[name]["metrics"])["overall"]["net_pnl_jpy"] > 0
            and cast(dict[str, Any], results[name]["metrics"])["overall"]["profit_factor"]
            is not None
            and cast(dict[str, Any], results[name]["metrics"])["overall"]["profit_factor"] > 1
            for name in SENSITIVITIES
        ),
        "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3,
        "positive_months>=27": sum(value > 0 for value in months.values()) >= 27,
        "top10_removed_positive": a_metrics["concentration"]["net_excluding_top10_jpy"] > 0,
    }
    decision = (
        "INCONCLUSIVE"
        if not all(info.values())
        else "INVESTIGATE"
        if all(gate.values())
        else "REJECT"
    )
    write_json(OUT / "bootstrap.json", boot)
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {"trade_dates": axis, "no_trade_value_jpy": 0, "series": daily},
    )
    write_json(
        OUT / "development_results.json",
        {
            "experiment_id": IDENTIFIER,
            "decision": decision,
            "quality": "PASS_LIMITED",
            "information_gate": info,
            "fixed_gates": gate,
            "E": len(e_days),
            "A_anchor_events": anchors,
            "A_direction_events": directions,
            "A_year_net_jpy": years,
            "A_month_net_jpy": months,
            "positive_months": sum(value > 0 for value in months.values()),
            "conditions": results,
            "OLS": boot["ols"],
            "scope": "Development only; OOS and Final Holdout not evaluated/accessed",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "development_complete",
            "decision": decision,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
