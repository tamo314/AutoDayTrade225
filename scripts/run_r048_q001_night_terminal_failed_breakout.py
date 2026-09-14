"""Execute preregistered Development-only R048-Q001; never select OOS/holdout."""

from __future__ import annotations

import json
from collections import Counter
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
from n225m_bt.research.r048 import r048_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r048_fixed_time import R048FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r048-q001-20260915-night-terminal-failed-breakout-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
AXIS_PATH = (
    ROOT
    / "results"
    / "research"
    / "r012-q001-20260914-night-direction-followthrough-01"
    / "daily_net_pnl_aligned.json"
)
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20260928
CONDITIONS = (
    "A_open_failed_fade",
    "B_open_first_break_fade",
    "C_buy",
    "D_sell",
    "F_failed_continue",
    "G_placebo_failed_fade",
)
VARIANTS = {"A2_2tick": (2, 0), "A3_3tick": (3, 0), "A_delay": (1, 1)}
SENSITIVITIES = {"A_h3": 3, "A_h7": 7}


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def fixed_axis() -> list[str]:
    axis = json.loads(AXIS_PATH.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(axis, list) or len(axis) != 1111 or len(set(axis)) != 1111:
        raise ValueError("fixed 1,111 trade_date axis unavailable")
    return cast(list[str], axis)


def input_manifest(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_r048_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
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
    classifier: CalendarClassifier,
    target: date,
    grouped: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    horizon: int = 5,
) -> dict[str, object]:
    return r048_event(
        classifier,
        target,
        grouped.get((target, Session.DAY)),
        grouped.get((target, Session.NIGHT)),
        day_quarantined=(target, Session.DAY) in isolated,
        night_quarantined=(target, Session.NIGHT) in isolated,
        horizon=horizon,
    )


def selected(condition: str, event: dict[str, object]) -> bool:
    if event.get("status") != "E":
        return False
    if condition == "B_open_first_break_fade":
        return event.get("main_status") != "NO_BREAKOUT"
    if condition == "G_placebo_failed_fade":
        return event.get("placebo_status") == "FAILED_BREAKOUT"
    return event.get("main_status") == "FAILED_BREAKOUT"


def planned(condition: str, event: dict[str, object]) -> tuple[datetime, datetime, str]:
    prefix = "placebo" if condition == "G_placebo_failed_fade" else "main"
    if condition == "B_open_first_break_fade":
        signal = datetime.fromisoformat(cast(str, event["main_breakout_jst"]))
        entry = signal + __import__("datetime").timedelta(minutes=1)
        direction = "short" if event["main_breakout_direction"] == "up" else "long"
    else:
        signal = datetime.fromisoformat(cast(str, event[f"{prefix}_return_jst"]))
        entry = datetime.fromisoformat(cast(str, event[f"{prefix}_entry_jst"]))
        direction = cast(str, event[f"{prefix}_fade_direction"])
    if condition == "C_buy":
        direction = "long"
    elif condition == "D_sell":
        direction = "short"
    elif condition == "F_failed_continue":
        direction = cast(str, event["main_continue_direction"])
    return signal, entry, direction


def add_zero_tick(event: dict[str, object], rows: list[Any]) -> None:
    if event.get("status") != "E":
        return
    by_time = {bar.ts_jst: bar for bar in rows}
    for prefix in ("main", "placebo"):
        if event.get(f"{prefix}_status") != "FAILED_BREAKOUT":
            continue
        entry, exit_ = (
            datetime.fromisoformat(cast(str, event[f"{prefix}_{key}_jst"]))
            for key in ("entry", "exit")
        )
        event[f"{prefix}_future_gross_0tick_prefee_jpy"] = (
            (by_time[exit_].open - by_time[entry].open)
            * (1 if event[f"{prefix}_fade_direction"] == "long" else -1)
            * 100
        )


def run_condition(
    data: ResearchData,
    engine: BacktestEngine,
    name: str,
    grouped: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    axis: list[str],
    *,
    horizon: int = 5,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text_day in axis:
        day = date.fromisoformat(text_day)
        event = event_for_day(engine.classifier, day, grouped, isolated, horizon)
        event.update(
            condition=name, base_event_status=event.get("status"), variant_delay_minutes=delay
        )
        rows = grouped.get((day, Session.DAY))
        if rows is not None:
            add_zero_tick(event, rows)
        if not selected(name, event):
            reason = event.get(
                "main_status" if name != "G_placebo_failed_fade" else "placebo_status",
                event.get("reason", "CONDITION_NOT_MET"),
            )
            event.update(status="skipped", reason_for_condition=reason)
            audit[f"skipped_{reason}"] += 1
            events.append(event)
            continue
        assert rows is not None
        signal, planned_entry, direction = planned(name, event)
        exit_time = planned_entry + __import__("datetime").timedelta(minutes=30)
        result = engine.run(
            rows,
            R048FixedTimeStrategy(f"r048_{name}", signal, exit_time, direction, delay),
            canonical_hash(
                {"condition": name, "event": event, "data": data.data_version, "delay": delay}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R048 violates one daily position")
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
            planned_entry_jst=planned_entry.isoformat(),
            planned_exit_jst=exit_time.isoformat(),
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            entry_signal_ts_jst=trade.entry_signal_ts.isoformat(),
            exit_signal_ts_jst=trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None,
            entry_delay_minutes=int((trade.entry_ts - planned_entry).total_seconds() // 60),
            exit_delay_minutes=int((trade.exit_ts - exit_time).total_seconds() // 60),
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
            replace(trade, trade_id=f"trade-{index:06d}")
            for index, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
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


def percentile(values: list[float], quantile: float) -> float:
    ordered, location = sorted(values), (len(values) - 1) * quantile
    lo, hi = floor(location), ceil(location)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (location - lo)


_OLS_CACHE: dict[int, list[dict[str, float | str]]] = {}


def ols(
    events: list[dict[str, object]],
    sampled_dates: list[str] | None = None,
    fixed_means: tuple[float, float, float] | None = None,
) -> tuple[float, dict[str, object]]:
    raw = _OLS_CACHE.get(id(events))
    if raw is None:
        raw = []
        for event in events:
            if event.get("base_event_status") != "E":
                continue
            for prefix, opening in (("main", 1.0), ("placebo", 0.0)):
                if event.get(f"{prefix}_status") != "FAILED_BREAKOUT":
                    continue
                p0, high, low = (
                    float(cast(int, event[f"{prefix}_{key}_points"])) for key in ("P", "H", "L")
                )
                raw.append(
                    {
                        "date": cast(str, event["trade_date"]),
                        "y": float(cast(int, event[f"{prefix}_future_gross_0tick_prefee_jpy"])),
                        "O": opening,
                        "zR": log((high - low) / p0),
                        "zB": log(
                            (float(cast(int, event[f"{prefix}_breakout_excess_points"])) + 5.0) / p0
                        ),
                        "k": float(cast(int, event[f"{prefix}_return_minutes"])),
                        "U": float(event[f"{prefix}_breakout_direction"] == "up"),
                    }
                )
        _OLS_CACHE[id(events)] = raw
    if not raw:
        raise ValueError("R048 OLS has no failed-breakout observations")
    means = fixed_means or tuple(
        fmean(cast(float, row[name]) for row in raw) for name in ("zR", "zB", "k")
    )
    counts = (
        Counter(sampled_dates)
        if sampled_dates is not None
        else Counter({cast(str, row["date"]): 1 for row in raw})
    )
    years = sorted(
        {cast(str, row["date"])[:4] for row in raw if counts[cast(str, row["date"])] > 0}
    )
    matrix = np.array(
        [
            [
                1.0,
                row["O"],
                cast(float, row["zR"]) - means[0],
                (cast(float, row["zR"]) - means[0]) ** 2,
                cast(float, row["zB"]) - means[1],
                cast(float, row["k"]) - means[2],
                row["U"],
                *(float(cast(str, row["date"])[:4] == year) for year in years[1:]),
            ]
            for row in raw
        ],
        dtype=float,
    )
    weights = np.array([counts[cast(str, row["date"])] for row in raw], dtype=float)
    cross = matrix.T @ (matrix * weights[:, None])
    rank = int(np.linalg.matrix_rank(cross, tol=1e-8)) if sampled_dates is None else matrix.shape[1]
    audit: dict[str, object] = {
        "formula": "y=alpha+delta*O+gamma1*(zR-mean(zR))+gamma2*(zR-mean(zR))^2+gamma3*(zB-mean(zB))+gamma4*(k-mean(k))+eta*U+calendar-year fixed effects+epsilon",
        "rows": len(raw),
        "columns": matrix.shape[1],
        "rank": rank,
        "full_rank": rank == matrix.shape[1],
        "mean_zR": means[0],
        "mean_zB": means[1],
        "mean_k": means[2],
        "year_levels": years,
        "delta_column": 1,
        "bootstrap_weighting": "exact multiplicity weights by sampled trade_date",
    }
    if not audit["full_rank"]:
        raise ValueError("R048 fixed pooled OLS design is not full rank")
    y = np.array([row["y"] for row in raw], dtype=float)
    return float(np.linalg.solve(cross, matrix.T @ (y * weights))[1]), audit


def bootstrap(
    daily: dict[str, dict[str, int]], events: dict[str, list[dict[str, object]]], axis: list[str]
) -> dict[str, object]:
    filled = {
        name: {cast(str, row["trade_date"]): row.get("status") == "filled" for row in rows}
        for name, rows in events.items()
    }
    labels = (
        "A_daily_mean_net_jpy",
        "A_minus_B_conditional_expectancy_jpy",
        "A_minus_C_conditional_expectancy_jpy",
        "A_minus_D_conditional_expectancy_jpy",
        "A_minus_F_conditional_expectancy_jpy",
        "A_minus_G_conditional_expectancy_jpy",
        "delta_open_boundary_increment_jpy",
    )
    values: dict[str, list[float]] = {label: [] for label in labels}
    estimate, audit = ols(events["B_open_first_break_fade"])
    means = cast(tuple[float, float, float], (audit["mean_zR"], audit["mean_zB"], audit["mean_k"]))
    comparisons = (
        ("B_open_first_break_fade", "B"),
        ("C_buy", "C"),
        ("D_sell", "D"),
        ("F_failed_continue", "F"),
        ("G_placebo_failed_fade", "G"),
    )
    rng, block = Random(SEED), 20
    for _ in range(10_000):
        indexes: list[int] = []
        while len(indexes) < len(axis):
            begin = rng.randrange(len(axis) - block + 1)
            indexes.extend(range(begin, begin + block))
        days = [axis[index] for index in indexes[: len(axis)]]
        values["A_daily_mean_net_jpy"].append(
            fmean(daily["A_open_failed_fade"][day] for day in days)
        )
        for other, label in comparisons:
            values[f"A_minus_{label}_conditional_expectancy_jpy"].append(
                fmean(
                    daily["A_open_failed_fade"][day]
                    for day in days
                    if filled["A_open_failed_fade"][day]
                )
                - fmean(daily[other][day] for day in days if filled[other][day])
            )
        values["delta_open_boundary_increment_jpy"].append(
            ols(events["B_open_first_break_fade"], days, means)[0]
        )
    estimates = {
        "A_daily_mean_net_jpy": fmean(daily["A_open_failed_fade"].values()),
        **{
            f"A_minus_{label}_conditional_expectancy_jpy": fmean(
                daily["A_open_failed_fade"][day]
                for day in axis
                if filled["A_open_failed_fade"][day]
            )
            - fmean(daily[other][day] for day in axis if filled[other][day])
            for other, label in comparisons
        },
        "delta_open_boundary_increment_jpy": estimate,
    }
    return {
        "method": "20 trade_date noncircular moving-block bootstrap, 10,000 repetitions, seed 20260928, common index, tail truncate, linear percentile. Events are not reestimated; fixed causal classifications retain, conditional sum/count and OLS are recomputed.",
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": SEED,
        "ols": audit,
        **{
            name: {
                "estimate": estimates[name],
                "ci95_percentile_linear": [percentile(sample, 0.025), percentile(sample, 0.975)],
            }
            for name, sample in values.items()
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
    if runtime.execution.slippage_ticks != 1 or runtime.fees.jpy_per_side_per_contract != 30:
        raise ValueError("R048 requires baseline one tick and JPY30 per side")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    source, manifest, axis = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        input_manifest(data_config.gold_root),
        fixed_axis(),
    )
    files = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r048.py"),
        Path("src/n225m_bt/strategies/r048_fixed_time.py"),
        Path("tests/test_r048_q001.py"),
    ]
    implementation = {str(path): digest(ROOT / path) for path in files}
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R048-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development exploration, not independent confirmation.",
        "duplicate_review": {
            "R001_R047": "R042 has night terminal-range follow-through but no first day close breakout, causal return-within-5 confirmation, 30m failed-breakout fade, unconditional initial-break fade, or 09:45--10:44 same-rule placebo. R036 is a prior-day range event. No equivalent registration found.",
            "conclusion": "No duplicate; execute this one fixed experiment only.",
        },
        "hypothesis": "A same-trade-date OSE night final-30 scheduled-minute range that is first strictly close-broken within the first 30 scheduled day minutes and then close-returns inside it within five scheduled minutes has positive post-cost 30-minute opposite-breakout expectancy, exceeding unconditional first-break fade, fixed buy/sell, failed-break continuation, and same-rule ordinary-morning placebo. This is a price/inventory-liquidity hypothesis only, not identification of order flow, participants, news or cash prices.",
        "fixed_rule": "W0 is the same-trade-date scheduled normal OSE night final 30 minutes; H0=max(high), L0=min(low), P0=first open. S0 is day open..+29. First strict close>H0/<L0 is up/down; equals, wick-only and later breakouts do not qualify. Search next 5 scheduled bars only: first L0<=close<=H0 confirms; strict opposite-boundary close before return is ambiguous/no trade. WP=day+60..+89 (09:45--10:14 under current schedule), SP=day+90..+119 (10:15--10:44), same rule. E requires all W0/WP/S0/SP/maximum-confirmation/latest-entry/30m-exit bars, with no observed substitutes.",
        "conditions": {
            "A_open_failed_fade": "main h5 failed only; up->short/down->long, return-confirmation next open, 30m hold",
            "B_open_first_break_fade": "all E main first breakouts, opposite side, breakout next open, 30m hold; return never filters",
            "C_buy/D_sell": "A event/entry/exit constant long/short",
            "F_failed_continue": "A event/entry/exit breakout direction",
            "G_placebo_failed_fade": "WP/SP h5 same-rule fade",
            "A_delay": "A entry one scheduled minute later; original exit unchanged",
            "A2/A3": "A same event/side/times, 2/3 ticks per side plus JPY30",
            "A_h3/A_h7": "only separately preregistered 3/7 minute return horizons; initial breakout independently reclassified",
        },
        "execution": "One contract, max one trade/date/position, no stop/target/re-entry/update/early exit; baseline one tick plus JPY30 per side; Gross is slippage-inclusive and Net=Gross-fees.",
        "ols": "Each h5 main/placebo failed breakout is one observation. y is fade-direction adjusted zero-tick/pre-fee 30m Gross JPY; O=1 main; zR=ln((H-L)/P), zB=ln((breakout excess+one tick)/P), k=return scheduled minutes, U=1[up], calendar-year FE. Pooled full rank required; means fixed once over all rows.",
        "evaluation": "Fixed 1,111 date axis, zero JPY for every ineligible/no-event/cancel/no-trade; 20-date noncircular moving-block bootstrap 10,000 common-index repetitions/tail truncation/linear percentile, seed 20260928. Do not reestimate events.",
        "inputs": {
            "physical": manifest,
            "axis": {"count": 1111, "sha256": digest(AXIS_PATH)},
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
        "prohibited": [
            "raw",
            "volume",
            "cash/external prices",
            "OOS",
            "Final Holdout",
            "other reference/search/return/holding/direction specifications",
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
            "tests/test_r048_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r048.py",
            "src/n225m_bt/strategies/r048_fixed_time.py",
            "tests/test_r048_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r048.py",
            "src/n225m_bt/strategies/r048_fixed_time.py",
        ],
    }
    validation: dict[str, Any] = {
        name: {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        for name, command in commands.items()
        for proc in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation.update(
        coverage="schedule regime/calendar_date/trade_date/same-trade-date night, terminal 30 and morning placebo, strict close/equality/wick, 3/5/7 return/opposite ambiguity/no signal, E/missing/isolation/holdout/prefix, next-open/fixed exit/delay/cost/accounting",
        status="PASS"
        if all(cast(dict[str, object], value)["returncode"] == 0 for value in validation.values())
        else "BLOCKED",
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R048 validation failed before Development price access")
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
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily: dict[str, dict[str, int]] = {}
    results: dict[str, dict[str, object]] = {}

    def execute(
        name: str, condition: str, *, ticks: int = 1, delay: int = 0, horizon: int = 5
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
            horizon=horizon,
            delay=delay,
        )
        values = dict.fromkeys(axis, 0)
        for trade in trades:
            values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(OUT / name, name, trades, events, audit, view.bars, ticks, values)
        trades_by[name], events_by[name], daily[name], results[name] = (
            trades,
            events,
            values,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )

    for name in CONDITIONS:
        execute(name, name)
    for name, (ticks, delay) in VARIANTS.items():
        execute(name, "A_open_failed_fade", ticks=ticks, delay=delay)
    for name, horizon in SENSITIVITIES.items():
        execute(name, "A_open_failed_fade", horizon=horizon)
    ledger = {
        name: {cast(str, row["trade_date"]): row for row in rows}
        for name, rows in events_by.items()
    }
    e_days = [
        day
        for day in axis
        if ledger["B_open_first_break_fade"][day].get("base_event_status") == "E"
    ]
    a_days = [day for day in axis if ledger["A_open_failed_fade"][day].get("status") == "filled"]
    checks = {
        "common_E_all_conditions": all(
            ledger[name][day].get("base_event_status") == "E"
            for name in CONDITIONS
            for day in e_days
        ),
        "A_C_D_F_same_event_entry_exit": all(
            all(
                ledger["A_open_failed_fade"][day].get(field) == ledger[name][day].get(field)
                for field in ("main_return_jst", "planned_entry_jst", "planned_exit_jst")
            )
            for name in ("C_buy", "D_sell", "F_failed_continue")
            for day in a_days
        ),
        "A_F_opposite_side": all(
            ledger["A_open_failed_fade"][day]["side"] != ledger["F_failed_continue"][day]["side"]
            for day in a_days
        ),
        "variants_event_side_equal": all(
            ledger[name][day].get(field) == ledger["A_open_failed_fade"][day].get(field)
            for name in VARIANTS
            for day in axis
            for field in ("main_return_jst", "main_fade_direction", "side")
        ),
        "B_uses_first_break_without_return_filter": all(
            row.get("status") == "filled"
            for row in events_by["B_open_first_break_fade"]
            if row.get("base_event_status") == "E" and row.get("main_status") != "NO_BREAKOUT"
        ),
        "G_same_relative_timing": all(
            datetime.fromisoformat(cast(str, row["planned_entry_jst"]))
            - datetime.fromisoformat(cast(str, row["placebo_return_jst"]))
            == __import__("datetime").timedelta(minutes=1)
            and datetime.fromisoformat(cast(str, row["planned_exit_jst"]))
            - datetime.fromisoformat(cast(str, row["planned_entry_jst"]))
            == __import__("datetime").timedelta(minutes=30)
            for row in events_by["G_placebo_failed_fade"]
            if row.get("status") == "filled"
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
            "status": "PASS" if all(checks.values()) else "BLOCKED",
            "checks": checks,
            "accounting": "Gross fill-to-fill is slippage-inclusive; Net=Gross-fees; slippage is not separately deducted.",
        },
    )
    if not all(checks.values()):
        raise ValueError("R048 execution/accounting gate failed")
    boot = bootstrap(daily, events_by, axis)
    main_direction = {
        side: sum(
            row.get("status") == "filled" and row.get("main_breakout_direction") == side
            for row in events_by["A_open_failed_fade"]
        )
        for side in ("up", "down")
    }
    placebo_direction = {
        side: sum(
            row.get("status") == "filled" and row.get("placebo_breakout_direction") == side
            for row in events_by["G_placebo_failed_fade"]
        )
        for side in ("up", "down")
    }
    status_direction = {
        f"main_{status}_{side}": sum(
            row.get("base_event_status") == "E"
            and row.get("main_status") == status
            and row.get("main_breakout_direction") == side
            for row in events_by["B_open_first_break_fade"]
        )
        for status in ("FAILED_BREAKOUT", "BREAKOUT_NO_RETURN", "AMBIGUOUS_OPPOSITE_BOUNDARY")
        for side in ("up", "down")
    }
    status_direction |= {
        f"placebo_{status}_{side}": sum(
            row.get("base_event_status") == "E"
            and row.get("placebo_status") == status
            and row.get("placebo_breakout_direction") == side
            for row in events_by["B_open_first_break_fade"]
        )
        for status in ("FAILED_BREAKOUT", "BREAKOUT_NO_RETURN", "AMBIGUOUS_OPPOSITE_BOUNDARY")
        for side in ("up", "down")
    }
    a_metrics = cast(dict[str, Any], results["A_open_failed_fade"]["metrics"])
    variant_metrics = {
        name: cast(dict[str, Any], results[name]["metrics"]) for name in (*VARIANTS, *SENSITIVITIES)
    }
    years = {
        str(year): sum(
            daily["A_open_failed_fade"][day] for day in axis if day.startswith(str(year))
        )
        for year in range(2021, 2026)
    }
    months = {
        f"{year}-{month:02d}": sum(
            daily["A_open_failed_fade"][day]
            for day in axis
            if day.startswith(f"{year}-{month:02d}")
        )
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }
    info = {
        "E>=800": len(e_days) >= 800,
        "B>=400": len(trades_by["B_open_first_break_fade"]) >= 400,
        "A>=120": len(trades_by["A_open_failed_fade"]) >= 120,
        "G>=120": len(trades_by["G_placebo_failed_fade"]) >= 120,
        "A_up_down>=40": all(value >= 40 for value in main_direction.values()),
        "G_up_down>=40": all(value >= 40 for value in placebo_direction.values()),
        "A_h3>=80": len(trades_by["A_h3"]) >= 80,
    }

    def positive_ci(name: str) -> bool:
        row = cast(dict[str, object], boot[name])
        return cast(list[float], row["ci95_percentile_linear"])[0] > 0

    gate = {
        "A_net_positive": a_metrics["overall"]["net_pnl_jpy"] > 0,
        "A_pf_gt_1": a_metrics["overall"]["profit_factor"] is not None
        and a_metrics["overall"]["profit_factor"] > 1,
        "all_requested_ci_positive": all(
            positive_ci(name) for name in boot if name.endswith("jpy")
        ),
        "A2_A3_A_delay_expectancy_positive": all(
            variant_metrics[name]["overall"]["expectancy_jpy"] > 0 for name in VARIANTS
        ),
        "A_h3_h7_positive_pf": all(
            variant_metrics[name]["overall"]["net_pnl_jpy"] > 0
            and variant_metrics[name]["overall"]["profit_factor"] is not None
            and variant_metrics[name]["overall"]["profit_factor"] > 1
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
    write_json(OUT / "event_status_direction_counts.json", status_direction)
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
            "A_year_net_jpy": years,
            "A_month_net_jpy": months,
            "positive_months": sum(value > 0 for value in months.values()),
            "main_failed_breakout_direction": main_direction,
            "placebo_failed_breakout_direction": placebo_direction,
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
