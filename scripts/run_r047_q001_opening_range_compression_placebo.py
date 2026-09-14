"""Execute preregistered Development-only R047-Q001 without holdout access."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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
from n225m_bt.research.r047 import r047_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r047_fixed_time import R047FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r047-q001-20260915-opening-range-compression-placebo-04"
OUT = ROOT / "results" / "research" / IDENTIFIER
R012_AXIS = (
    ROOT
    / "results"
    / "research"
    / "r012-q001-20260914-night-direction-followthrough-01"
    / "daily_net_pnl_aligned.json"
)
R025 = ROOT / "results" / "research" / "r025-q001-20260914-prior-tse-day-range-acceptance-03"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20260927
CONDITIONS = (
    "A_open_compressed",
    "B_open_all",
    "C_open_noncompressed",
    "D_buy",
    "E_sell",
    "F_reverse",
    "G_placebo_compressed",
)
VARIANTS = {"A2_2tick": (2, 0), "A3_3tick": (3, 0), "A_delay": (1, 1)}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def fixed_axis() -> list[str]:
    rows = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(rows, list) or len(rows) != 1111 or len(set(rows)) != 1111:
        raise ValueError("fixed 1,111 trade_date axis unavailable")
    return cast(list[str], rows)


def input_manifest(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_r047_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R025 / "institutional_evidence" / "r020_cabinet_office_public_holidays.csv"
    if not source.exists():
        raise ValueError("frozen TSE calendar evidence unavailable")
    target = OUT / "institutional_evidence"
    target.mkdir()
    copied = target / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    if calendar.source_start > date(2021, 1, 1) or calendar.source_end < date(2025, 6, 30):
        raise ValueError("frozen TSE calendar does not cover Development")
    evidence = {
        "holiday_csv_sha256": digest(copied),
        "r025_completed_sha256": digest(R025 / "COMPLETED.json"),
        "rule": "TSE business days are frozen Cabinet Office holidays plus weekend/Jan1-3/Dec31 rules, never inferred from futures observations.",
    }
    write_json(target / "evidence_manifest.json", evidence)
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


def select(condition: str, event: dict[str, object]) -> bool:
    if event["status"] != "E":
        return False
    prefix = "placebo" if condition == "G_placebo_compressed" else "open"
    if f"{prefix}_direction" not in event:
        return False
    if condition == "B_open_all":
        return True
    if condition == "C_open_noncompressed":
        return not cast(bool, event["open_compressed_q25"])
    if condition == "G_placebo_compressed":
        return cast(bool, event["placebo_compressed_q25"])
    return cast(bool, event["open_compressed_q25"])


def direction(condition: str, event: dict[str, object]) -> str:
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    base = cast(
        str, event["placebo_direction" if condition == "G_placebo_compressed" else "open_direction"]
    )
    if condition == "F_reverse":
        return "short" if base == "long" else "long"
    return base


def event_for_day(
    classifier: CalendarClassifier,
    target: date,
    grouped: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    tse_days: list[date],
) -> dict[str, object]:
    if target not in tse_days:
        return {
            "trade_date": target.isoformat(),
            "status": "skipped",
            "reason": "TSE_CASH_MARKET_CLOSED",
        }
    index = tse_days.index(target)
    history = [
        (day, grouped.get((day, Session.DAY)), (day, Session.DAY) in isolated)
        for day in tse_days[max(0, index - 60) : index][::-1]
    ]
    return r047_event(
        classifier,
        target,
        grouped.get((target, Session.DAY)),
        history,
        target_quarantined=(target, Session.DAY) in isolated,
    )


def zero_tick(event: dict[str, object], rows: list[Any]) -> None:
    if event.get("status") != "E":
        return
    by_time = {bar.ts_jst: bar for bar in rows}
    for prefix, entry_key, exit_key, direction_key in (
        ("open", "A_entry_jst", "A_exit_jst", "open_direction"),
        ("placebo", "G_entry_jst", "G_exit_jst", "placebo_direction"),
    ):
        if direction_key not in event:
            continue
        entry, exit_ = (
            datetime.fromisoformat(cast(str, event[key])) for key in (entry_key, exit_key)
        )
        if by_time.get(entry) is None or by_time.get(exit_) is None:
            raise ValueError("R047 common E lacks OLS price endpoint")
        sign = 1 if event[direction_key] == "long" else -1
        event[f"{prefix}_future_gross_0tick_prefee_jpy"] = (
            (by_time[exit_].open - by_time[entry].open) * sign * 100
        )


def run_condition(
    data: ResearchData,
    engine: BacktestEngine,
    condition: str,
    grouped: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    axis: list[str],
    tse_days: list[date],
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text_day in axis:
        day = date.fromisoformat(text_day)
        event = event_for_day(engine.classifier, day, grouped, isolated, tse_days)
        event.update(
            condition=condition, base_event_status=event.get("status"), variant_delay_minutes=delay
        )
        rows = grouped.get((day, Session.DAY))
        if rows is not None:
            zero_tick(event, rows)
        if not select(condition, event):
            prefix = "placebo" if condition == "G_placebo_compressed" else "open"
            event.update(
                status="skipped",
                reason_for_condition=event.get(
                    f"{prefix}_breakout_status", event.get("reason", "CONDITION_NOT_MET")
                ),
            )
            audit[f"skipped_{event['reason_for_condition']}"] += 1
            events.append(event)
            continue
        if rows is None:
            raise AssertionError("R047 eligible E has no day bars")
        prefix = "G" if condition == "G_placebo_compressed" else "A"
        signal = datetime.fromisoformat(cast(str, event[f"{prefix}_signal_jst"]))
        result = engine.run(
            rows,
            R047FixedTimeStrategy(
                f"r047_{condition}",
                signal,
                datetime.fromisoformat(cast(str, event[f"{prefix}_exit_jst"])),
                direction(condition, event),
                delay,
            ),
            canonical_hash(
                {"condition": condition, "event": event, "data": data.data_version, "delay": delay}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R047 violates one daily position")
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
        planned_entry, planned_exit = (
            datetime.fromisoformat(cast(str, event[f"{prefix}_{key}_jst"]))
            for key in ("entry", "exit")
        )
        event.update(
            status="filled",
            side=trade.side.value,
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            entry_signal_ts_jst=trade.entry_signal_ts.isoformat(),
            exit_signal_ts_jst=trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None,
            entry_delay_minutes=int((trade.entry_ts - planned_entry).total_seconds() // 60),
            exit_delay_minutes=int((trade.exit_ts - planned_exit).total_seconds() // 60),
            exit_reason=trade.exit_reason.value,
            gross_pnl_jpy=trade.gross_pnl_jpy,
            slippage_cost_jpy=trade.slippage_cost_jpy,
            fees_jpy=trade.fees_jpy,
            net_pnl_jpy=trade.net_pnl_jpy,
        )
        trades.append(trade)
        audit["trades"] += 1
        events.append(event)
    numbered = tuple(
        replace(trade, trade_id=f"trade-{number:06d}")
        for number, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
    )
    return numbered, events, dict(sorted(audit.items()))


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


_OLS_CACHE: dict[int, list[dict[str, float | str]]] = {}


def ols(
    events: list[dict[str, object]],
    sampled_dates: list[str] | None = None,
    fixed_means: tuple[float, float, float] | None = None,
) -> tuple[float, dict[str, object]]:
    """Fixed pooled OLS; bootstrap uses exact date-frequency weights, not resampled events."""
    raw = _OLS_CACHE.get(id(events))
    if raw is None:
        raw = []
        for event in events:
            if event.get("base_event_status") != "E":
                continue
            for prefix, opening in (("open", 1.0), ("placebo", 0.0)):
                if f"{prefix}_direction" not in event:
                    continue
                high, low, p0 = (
                    float(cast(int, event[f"{prefix}_{field}"])) for field in ("high", "low", "p0")
                )
                if high <= low:
                    raise ValueError("R047 OLS zR undefined for zero observation range")
                raw.append(
                    {
                        "date": cast(str, event["trade_date"]),
                        "y": float(cast(int, event[f"{prefix}_future_gross_0tick_prefee_jpy"])),
                        "O": opening,
                        "Q": float(cast(bool, event[f"{prefix}_compressed_q25"])),
                        "zR": log((high - low) / p0),
                        "zB": log(
                            (float(cast(int, event[f"{prefix}_breakout_excess_points"])) + 5.0) / p0
                        ),
                        "tau": float(cast(int, event[f"{prefix}_signal_minute"])),
                        "U": float(event[f"{prefix}_direction"] == "long"),
                    }
                )
        _OLS_CACHE[id(events)] = raw
    if not raw:
        raise ValueError("R047 OLS has no breakout observations")
    mean_r, mean_b, mean_tau = fixed_means or (
        fmean(cast(float, row["zR"]) for row in raw),
        fmean(cast(float, row["zB"]) for row in raw),
        fmean(cast(float, row["tau"]) for row in raw),
    )
    counts = (
        Counter(sampled_dates)
        if sampled_dates is not None
        else Counter({cast(str, row["date"]): 1 for row in raw})
    )
    # A resample can omit a calendar year.  Its fixed-effect column is then a
    # zero column, so omit only that absent level for that resample; the stated
    # calendar-year-FE model and delta column are otherwise unchanged.
    years = sorted(
        {cast(str, row["date"])[:4] for row in raw if counts[cast(str, row["date"])] > 0}
    )
    matrix = np.array(
        [
            [
                1.0,
                row["O"],
                row["Q"],
                cast(float, row["O"]) * cast(float, row["Q"]),
                cast(float, row["zR"]) - mean_r,
                (cast(float, row["zR"]) - mean_r) ** 2,
                cast(float, row["zB"]) - mean_b,
                cast(float, row["tau"]) - mean_tau,
                row["U"],
                *(float(cast(str, row["date"])[:4] == year) for year in years[1:]),
            ]
            for row in raw
        ],
        dtype=float,
    )
    y = np.array([row["y"] for row in raw], dtype=float)
    weights = np.array([counts[cast(str, row["date"])] for row in raw], dtype=float)
    cross = matrix.T @ (matrix * weights[:, None])
    # Identification is a preregistered gate on the original fixed design.
    # Bootstrap repetitions retain that design and only change date weights.
    rank = int(np.linalg.matrix_rank(cross, tol=1e-8)) if sampled_dates is None else matrix.shape[1]
    audit: dict[str, object] = {
        "formula": "y=alpha+theta*O+lambda*Q+delta*(O*Q)+gamma1*(zR-mean(zR))+gamma2*(zR-mean(zR))^2+gamma3*(zB-mean(zB))+gamma4*(tau-mean(tau))+eta*U+calendar-year fixed effects+epsilon",
        "rows": len(raw),
        "columns": matrix.shape[1],
        "rank": rank,
        "full_rank": rank == matrix.shape[1],
        "mean_zR": mean_r,
        "mean_zB": mean_b,
        "mean_tau": mean_tau,
        "year_levels": years,
        "delta_column": 3,
        "bootstrap_weighting": "exact multiplicity weights by sampled trade_date",
    }
    if not audit["full_rank"]:
        raise ValueError("R047 fixed pooled OLS design is not full rank")
    coef = np.linalg.solve(cross, matrix.T @ (y * weights))
    return float(coef[3]), audit


def bootstrap(
    daily: dict[str, dict[str, int]], events: dict[str, list[dict[str, object]]], axis: list[str]
) -> dict[str, object]:
    eligible = {
        cast(str, row["trade_date"]): row.get("base_event_status") == "E"
        for row in events["B_open_all"]
    }
    filled = {
        name: {cast(str, row["trade_date"]): row.get("status") == "filled" for row in rows}
        for name, rows in events.items()
    }
    labels = (
        "A_daily_mean_net_jpy",
        "A_minus_B_conditional_expectancy_jpy",
        "A_minus_C_conditional_expectancy_jpy",
        "A_minus_D_conditional_expectancy_jpy",
        "A_minus_E_conditional_expectancy_jpy",
        "A_minus_F_conditional_expectancy_jpy",
        "A_minus_G_conditional_expectancy_jpy",
        "delta_open_compressed_increment_jpy",
    )
    samples: dict[str, list[float]] = {label: [] for label in labels}
    rng, block, count = Random(SEED), 20, len(axis)
    estimate_delta, audit = ols(events["B_open_all"])
    centers = (
        cast(float, audit["mean_zR"]),
        cast(float, audit["mean_zB"]),
        cast(float, audit["mean_tau"]),
    )
    comparisons = (
        ("B_open_all", "B"),
        ("C_open_noncompressed", "C"),
        ("D_buy", "D"),
        ("E_sell", "E"),
        ("F_reverse", "F"),
        ("G_placebo_compressed", "G"),
    )
    for _ in range(10_000):
        picked: list[int] = []
        while len(picked) < count:
            start = rng.randrange(count - block + 1)
            picked.extend(range(start, start + block))
        days = [axis[index] for index in picked[:count]]
        e_days = [day for day in days if eligible[day]]
        samples["A_daily_mean_net_jpy"].append(
            fmean(daily["A_open_compressed"][day] for day in days)
        )
        for other, label in comparisons:
            samples[f"A_minus_{label}_conditional_expectancy_jpy"].append(
                fmean(
                    daily["A_open_compressed"][day]
                    for day in days
                    if filled["A_open_compressed"][day]
                )
                - fmean(daily[other][day] for day in days if filled[other][day])
            )
        samples["delta_open_compressed_increment_jpy"].append(
            ols(events["B_open_all"], e_days, centers)[0]
        )
    estimates = {
        "A_daily_mean_net_jpy": fmean(daily["A_open_compressed"].values()),
        **{
            f"A_minus_{label}_conditional_expectancy_jpy": fmean(
                daily["A_open_compressed"][day] for day in axis if filled["A_open_compressed"][day]
            )
            - fmean(daily[other][day] for day in axis if filled[other][day])
            for other, label in comparisons
        },
        "delta_open_compressed_increment_jpy": estimate_delta,
    }
    return {
        "method": "20 trade_date noncircular moving-block bootstrap, 10,000 repetitions, seed 20260927, common index, tail truncate, linear percentile. Rolling classifications remain fixed ex ante; conditional sum/count and OLS are recomputed each replicate.",
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": SEED,
        "ols": audit,
        **{
            name: {
                "estimate": estimates[name],
                "ci95_percentile_linear": [percentile(values, 0.025), percentile(values, 0.975)],
            }
            for name, values in samples.items()
        },
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
    exchange_calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, exchange_calendar)
    source, manifest, axis = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        input_manifest(data_config.gold_root),
        fixed_axis(),
    )
    cash, evidence = cash_calendar()
    files = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r047.py"),
        Path("src/n225m_bt/strategies/r047_fixed_time.py"),
        Path("tests/test_r047_q001.py"),
    ]
    implementation = {str(path): digest(ROOT / path) for path in files}
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R047-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation.",
        "duplicate_review": {
            "R001_R046": "R033 used a different 20-day raw-range threshold, 31--90 minute search and 60-minute holding period. R046 used path efficiency, not range/breakout. No prior registration combines causal q25 normalized 08:45--09:14 range, 09:15--09:29 first strict-close breakout, 10:15 fixed exit, and normal-morning common-E placebo.",
            "conclusion": "No duplicate; execute this one fixed experiment only.",
        },
        "hypothesis": "When the 08:45--09:14 normalized range is at or below its causal q25, the first 09:15--09:29 strict close breakout continues in its breakout direction after costs and exceeds all/noncompressed/fixed/reverse and analogous normal-morning placebo. It is a price-revision proxy only and does not identify order flow, news, participant type or cash prices.",
        "state": "W0=08:45--09:14 and WP=09:45--10:14 use p0=first open, H=max(high), L=min(low), x=(H-L)/p0. Each window uses exactly the immediately prior 60 scheduled TSE business days; at least 50 valid values and nearest-rank q20/q25/q30, no target inclusion or backfill. x<=q is compressed. S0=09:15--09:29 and SP=10:15--10:29 use only the first strict close>H or close<L; entry is next scheduled eligible open, main exit 10:15 and placebo exit 11:15. E requires both windows, both thresholds, all search windows and all execution paths.",
        "conditions": {
            "A_open_compressed": "W0 q25 compressed first S0 breakout direction, next open to 10:15",
            "B_open_all": "all E S0 breakouts in their breakout direction",
            "C_open_noncompressed": "W0 q25 noncompressed S0 breakouts in their breakout direction",
            "D_buy/E_sell": "A event/time always long/short",
            "F_reverse": "A event opposite breakout direction",
            "G_placebo_compressed": "WP q25 compressed first SP breakout direction, next open to 11:15",
            "A_delay": "A entry one additional scheduled minute later, without extending 10:15 exit",
            "A2/A3": "A re-priced at 2/3 ticks per side",
        },
        "execution": "Each condition one contract, at most one trade/date and position; baseline one tick plus JPY30 per side; no Stop/Target/reentry/update/early exit; Gross already includes slippage and Net=Gross-fees.",
        "evaluation": {
            "bootstrap": "20 trade_date moving blocks noncircular/tail truncate/linear percentile, 10,000 reps, seed 20260927; causal classifications/events remain fixed, conditional sum/count and pooled OLS are recomputed. A bootstrap sample omitting a calendar year drops only its zero year-dummy level.",
            "ols": "Each main or placebo observation with a breakout supplies one row. The specified pooled OLS uses the one-time means over those rows; the original design must be full rank.",
            "information_gate": "E>=800,B>=450,A>=120,C>=300,A long/short each>=40,W0 compressed/noncompressed x direction each>=40,G>=120 and long/short each>=40,A20>=90.",
            "decision": "BLOCKED gate/OLS failure; INCONCLUSIVE information shortfall; otherwise REJECT unless every stated positive/economic/robustness criterion passes, then INVESTIGATE only.",
        },
        "inputs": {
            "physical": manifest,
            "fixed_axis": {"count": 1111, "sha256": digest(R012_AXIS)},
            "r004_quarantine": "45 sessions/27345 bars removed; 2216 sessions/1326086 bars retained; fixed hash required.",
            "tse_calendar": evidence,
            "quality_ceiling": "PASS_LIMITED",
        },
        "identifiers_before_run": {
            "seed": SEED,
            "source": source,
            "implementation": implementation,
            "implementation_hash": canonical_hash(implementation),
            "input_manifest_hash": canonical_hash(manifest),
        },
        "prohibited": [
            "raw",
            "volume",
            "cash/external prices",
            "OOS",
            "Final Holdout",
            "additional thresholds",
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
            "tests/test_r047_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r047.py",
            "src/n225m_bt/strategies/r047_fixed_time.py",
            "tests/test_r047_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r047.py",
            "src/n225m_bt/strategies/r047_fixed_time.py",
        ],
    }
    validation: dict[str, Any] = {
        name: {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        for name, command in commands.items()
        for proc in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation["coverage"] = (
        "holiday/calendar_date/trade_date, exact 30 bars p0-p30/r/L/zero, 60 TSE business days/no target/no backfill/ranks/ties, common E, prefix, paths, next-open/fixed-exit/delay, cost/accounting, OOS/holdout lock"
    )
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, object], validation[name])["returncode"] == 0 for name in commands)
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R047 validation failed before Development price access")
    development = load_split(data_config.gold_root, "development")
    view, qaudit, isolated = quarantine(development)
    observed = {
        bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY
    } - {day.isoformat() for day, session in isolated if session is Session.DAY}
    if set(axis) != observed:
        raise ValueError("fixed 1,111 date axis mismatch")
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
        for row in exchange_calendar.trading_days()
        if date(2021, 1, 1) <= row.trade_date <= date(2025, 6, 30) and cash.is_open(row.trade_date)
    ]
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily: dict[str, dict[str, int]] = {}
    results: dict[str, dict[str, object]] = {}
    for name in CONDITIONS:
        trades, events, audit = run_condition(
            view,
            BacktestEngine(instrument.instrument.to_spec(), runtime, classifier),
            name,
            grouped,
            isolated,
            axis,
            tse_days,
        )
        values = dict.fromkeys(axis, 0)
        for trade in trades:
            values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(OUT / name, name, trades, events, audit, view.bars, 1, values)
        trades_by[name], events_by[name], daily[name], results[name] = (
            trades,
            events,
            values,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )
    for name, (ticks, delay) in VARIANTS.items():
        config = runtime.model_copy(
            update={"execution": runtime.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, events, audit = run_condition(
            view,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            "A_open_compressed",
            grouped,
            isolated,
            axis,
            tse_days,
            delay,
        )
        values = dict.fromkeys(axis, 0)
        for trade in trades:
            values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(OUT / name, name, trades, events, audit, view.bars, ticks, values)
        trades_by[name], events_by[name], results[name] = (
            trades,
            events,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )
    ledger = {
        name: {cast(str, row["trade_date"]): row for row in rows}
        for name, rows in events_by.items()
    }
    e_days = [day for day in axis if ledger["B_open_all"][day].get("base_event_status") == "E"]
    high_days = [
        day for day in e_days if ledger["A_open_compressed"][day].get("status") == "filled"
    ]
    check = {
        "common_E_all_conditions": all(
            ledger[name][day].get("base_event_status") == "E"
            for name in CONDITIONS
            for day in e_days
        ),
        "high_A_B_match": all(
            all(
                ledger["A_open_compressed"][day].get(field) == ledger["B_open_all"][day].get(field)
                for field in ("side", "entry_ts_jst", "exit_ts_jst", "net_pnl_jpy")
            )
            for day in high_days
        ),
        "noncompressed_B_C_match": all(
            all(
                ledger["B_open_all"][day].get(field)
                == ledger["C_open_noncompressed"][day].get(field)
                for field in ("side", "entry_ts_jst", "exit_ts_jst", "net_pnl_jpy")
            )
            for day in e_days
            if not ledger["B_open_all"][day]["open_compressed_q25"]
        ),
        "A_F_opposite": all(
            ledger["A_open_compressed"][day]["side"] != ledger["F_reverse"][day]["side"]
            for day in high_days
        ),
        "A_fixed_controls_same_event": all(
            ledger["A_open_compressed"][day]["A_signal_jst"] == ledger[name][day]["A_signal_jst"]
            for name in ("D_buy", "E_sell")
            for day in high_days
        ),
        "A_G_common_E": all(
            ledger["A_open_compressed"][day].get("base_event_status")
            == ledger["G_placebo_compressed"][day].get("base_event_status")
            for day in e_days
        ),
        "variants_event_side_equal": all(
            ledger[name][day].get(field) == ledger["A_open_compressed"][day].get(field)
            for name in VARIANTS
            for day in axis
            for field in ("open_x", "open_direction", "side")
        ),
        "fixed_exit_delay_not_extended": all(
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
            "status": "PASS" if all(check.values()) else "BLOCKED",
            "checks": check,
            "accounting": "Gross fill-to-fill is slippage-inclusive; Net=Gross-fees.",
        },
    )
    if not all(check.values()):
        raise ValueError("R047 execution/accounting gate failed")
    boot = bootstrap(daily, events_by, axis)
    open_groups = {
        f"open_{compressed}_{side}": sum(
            row.get("base_event_status") == "E"
            and row.get("status") == "filled"
            and row.get("open_compressed_q25") is compressed
            and row.get("open_direction") == side
            for row in events_by["B_open_all"]
        )
        for compressed in (True, False)
        for side in ("long", "short")
    }
    placebo_groups = {
        f"placebo_{compressed}_{side}": sum(
            row.get("base_event_status") == "E"
            and row.get("placebo_direction") == side
            and row.get("placebo_compressed_q25") is compressed
            for row in events_by["B_open_all"]
        )
        for compressed in (True, False)
        for side in ("long", "short")
    }
    q20_count = sum(
        row.get("status") == "filled" and row.get("open_compressed_q20")
        for row in events_by["B_open_all"]
    )
    g_side = {
        side: sum(
            row.get("status") == "filled" and row.get("placebo_direction") == side
            for row in events_by["G_placebo_compressed"]
        )
        for side in ("long", "short")
    }
    a_metrics = results["A_open_compressed"]["metrics"]
    years = {
        str(year): sum(daily["A_open_compressed"][day] for day in axis if day.startswith(str(year)))
        for year in range(2021, 2026)
    }
    months = {
        f"{year}-{month:02d}": sum(
            daily["A_open_compressed"][day] for day in axis if day.startswith(f"{year}-{month:02d}")
        )
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }

    def positive_ci(name: str) -> bool:
        return cast(list[float], boot[name]["ci95_percentile_linear"])[0] > 0

    info = {
        "E>=800": len(e_days) >= 800,
        "B>=450": len(trades_by["B_open_all"]) >= 450,
        "A>=120": len(trades_by["A_open_compressed"]) >= 120,
        "C>=300": len(trades_by["C_open_noncompressed"]) >= 300,
        "A_long_short>=40": all(
            sum(
                row.get("status") == "filled" and row.get("side") == side
                for row in events_by["A_open_compressed"]
            )
            >= 40
            for side in ("long", "short")
        ),
        "open_compressed_noncompressed_direction_groups>=40": all(
            count >= 40 for count in open_groups.values()
        ),
        "G>=120": len(trades_by["G_placebo_compressed"]) >= 120,
        "G_long_short>=40": all(g_side[side] >= 40 for side in g_side),
        "A20>=90": q20_count >= 90,
    }
    gate = {
        "A_net_positive": a_metrics["overall"]["net_pnl_jpy"] > 0,
        "A_pf_gt_1": a_metrics["overall"]["profit_factor"] is not None
        and a_metrics["overall"]["profit_factor"] > 1,
        "all_requested_ci_positive": all(
            positive_ci(name) for name in boot if name.endswith("jpy")
        ),
        "A2_A3_A_delay_expectancy_positive": all(
            results[name]["metrics"]["overall"]["expectancy_jpy"] > 0 for name in VARIANTS
        ),
        "A20_A30_positive_pf": True,
        "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3,
        "positive_months>=27": sum(value > 0 for value in months.values()) >= 27,
        "top10_removed_positive": a_metrics["concentration"]["net_excluding_top10_jpy"] > 0,
    }
    # Sensitivities are the same causal E ledger and fills with q20/q30 filters; record them without new price reads.
    sensitivities: dict[str, object] = {}
    for percentile in (20, 30):
        rows = [
            row
            for row in events_by["B_open_all"]
            if row.get("status") == "filled" and row.get(f"open_compressed_q{percentile}")
        ]
        nets = [cast(int, row["net_pnl_jpy"]) for row in rows]
        wins, losses = (
            [value for value in nets if value > 0],
            [value for value in nets if value < 0],
        )
        sensitivities[f"A{percentile}"] = {
            "trade_count": len(rows),
            "net_pnl_jpy": sum(nets),
            "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            "expectancy_jpy": fmean(nets) if nets else None,
        }
    gate["A20_A30_positive_pf"] = all(
        cast(dict[str, object], sensitivities[name])["net_pnl_jpy"] > 0
        and cast(float | None, cast(dict[str, object], sensitivities[name])["profit_factor"])
        is not None
        and cast(float, cast(dict[str, object], sensitivities[name])["profit_factor"]) > 1
        for name in sensitivities
    )
    # Fixed diagnostic split uses un-slipped open prices and is not a selection statistic.
    split = {"A_gross_09_15_to_09_30_jpy": 0, "A_gross_09_30_to_10_15_jpy": 0}
    for trade in trades_by["A_open_compressed"]:
        mid = {bar.ts_jst: bar for bar in grouped[(trade.trade_date, Session.DAY)]}[
            trade.entry_ts + timedelta(minutes=15)
        ]
        split["A_gross_09_15_to_09_30_jpy"] += (
            (mid.open - trade.entry_fill_price) * trade.side.sign * 100
        )
        split["A_gross_09_30_to_10_15_jpy"] += (
            (trade.exit_fill_price - mid.open) * trade.side.sign * 100
        )
    decision = (
        "INCONCLUSIVE"
        if not all(info.values())
        else "INVESTIGATE"
        if all(gate.values())
        else "REJECT"
    )
    write_json(
        OUT / "event_groups.json",
        {
            "open_compressed_noncompressed_by_direction": open_groups,
            "placebo_compressed_noncompressed_by_direction": placebo_groups,
            "G_placebo_compressed_by_direction": g_side,
        },
    )
    write_json(OUT / "sensitivity_q20_q30.json", sensitivities)
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "gross_time_decomposition.json", split)
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
            "sensitivities": sensitivities,
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
