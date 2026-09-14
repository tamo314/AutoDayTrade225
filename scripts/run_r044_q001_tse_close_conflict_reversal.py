"""Execute the preregistered Development-only R044-Q001 experiment."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor, log
from pathlib import Path
from random import Random
from shutil import copyfile
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, Literal, cast

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
from n225m_bt.research.r044 import tse_close_conflict_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.tse_close_conflict_reversal import TSECloseConflictReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r044-q001-20260914-tse-close-conflict-reversal-01"
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
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED, AXIS_COUNT = 20260924, 1_111
Condition = Literal[
    "A_close_conflict",
    "B_close_all",
    "C_close_agreement",
    "D_buy",
    "E_sell",
    "F_close_continue",
    "G_placebo_conflict",
]
CONDITIONS: tuple[Condition, ...] = (
    "A_close_conflict",
    "B_close_all",
    "C_close_agreement",
    "D_buy",
    "E_sell",
    "F_close_continue",
    "G_placebo_conflict",
)
VARIANTS = {"A2_2tick": (2, 0), "A3_3tick": (3, 0), "A_delay": (1, 1)}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def fixed_axis() -> list[str]:
    values = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(values, list) or len(values) != AXIS_COUNT or len(set(values)) != AXIS_COUNT:
        raise ValueError("fixed R012 Development axis unavailable")
    return cast(list[str], values)


def inputs(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_r044_price_statistics_events_or_pnl",
        "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def frozen_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R020 / "institutional_evidence" / "cabinet_office_public_holidays.csv"
    jpx = R021 / "institutional_evidence" / "jpx_tse_trading_hours.pdf"
    if not source.exists() or not jpx.exists():
        raise ValueError("frozen R020/R021 TSE evidence unavailable")
    target = OUT / "institutional_evidence"
    target.mkdir()
    copyfile(source, target / source.name)
    copyfile(jpx, target / jpx.name)
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(
        (target / source.name).read_text(encoding="cp932")
    )
    if calendar.source_start > date(2021, 1, 1) or calendar.source_end < date(2025, 6, 30):
        raise ValueError("frozen holiday evidence does not cover Development")
    evidence = {
        "holiday_csv_sha256": digest(target / source.name),
        "jpx_schedule_sha256": digest(target / jpx.name),
        "r020_completed_sha256": digest(R020 / "COMPLETED.json"),
        "r021_completed_sha256": digest(R021 / "COMPLETED.json"),
        "schedule": "Versioned official TSE close is 15:00 through 2024-11-01 and 15:30 from 2024-11-05, supplied by r021.tse_cash_close; never inferred from futures observations.",
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
        "rule": "exclude whole (trade_date, session) for any TICK_GRID_VIOLATION",
    }
    expected = {
        "parent_data_version": PARENT_HASH,
        "quarantined_sessions": 45,
        "quarantined_bars": 27_345,
        "included_sessions": 2_216,
        "included_bars": 1_326_086,
        "quarantined_session_list_hash": QUARANTINE_HASH,
        "included_tick_grid_violations": 0,
    }
    mismatches = {
        key: {"actual": audit[key], "expected": value}
        for key, value in expected.items()
        if audit[key] != value
    }
    audit.update(expected_match=not mismatches, mismatches=mismatches)
    if mismatches:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatches}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def selected(condition: Condition, event: dict[str, object]) -> bool:
    main, placebo = event.get("status"), event.get("placebo_status")
    if condition == "G_placebo_conflict":
        return placebo == "conflict"
    if condition == "B_close_all":
        return main in {"conflict", "agreement"}
    if condition == "C_close_agreement":
        return main == "agreement"
    return main == "conflict"


def direction(condition: Condition, event: dict[str, object]) -> str:
    last = cast(
        str, event["rLB_direction" if condition == "G_placebo_conflict" else "rL_direction"]
    )
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    if condition == "F_close_continue":
        return last
    return "short" if last == "long" else "long"


def raw_return(event: dict[str, object], rows: list[Any]) -> None:
    if event.get("status") not in {"conflict", "agreement"}:
        return
    entry, exit_ = (
        datetime.fromisoformat(cast(str, event[key]))
        for key in ("E_planned_entry_jst", "X_planned_exit_jst")
    )
    by_time = {bar.ts_jst: bar for bar in rows}
    first, last = by_time.get(entry), by_time.get(exit_)
    if first is None or last is None or not first.is_eligible or not last.is_eligible:
        event["raw_return_reason"] = "POST_CLOSE_10M_PATH_MISSING_OR_INELIGIBLE"
        return
    if cast(int, event["open_P_points"]) <= 0 or cast(int, event["open_L_points"]) <= 0:
        event["raw_return_reason"] = "NONPOSITIVE_LOG_DENOMINATOR"
        return
    sign = cast(int, event["rL_sign"])
    event.update(
        open_T_points=first.open,
        open_T_plus_10_points=last.open,
        raw_sign_adjusted_T_Tplus10_jpy=-sign * (last.open - first.open) * 100,
        raw_return_reason="OK",
    )


def run_condition(
    data: ResearchData,
    calendar: TSECashMarketCalendar,
    engine: BacktestEngine,
    condition: Condition,
    grouped: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    axis: list[str],
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text_day in axis:
        day = date.fromisoformat(text_day)
        rows = grouped.get((day, Session.DAY))
        event = tse_close_conflict_event(
            day, rows, calendar, day_quarantined=(day, Session.DAY) in isolated
        )
        event.update(
            condition=condition,
            base_event_status=event.get("status"),
            base_placebo_status=event.get("placebo_status"),
            variant_delay_minutes=delay,
        )
        if rows is not None:
            raw_return(event, rows)
        if not selected(condition, event):
            event.update(
                status="skipped",
                reason_for_condition="CONDITION_FILTER"
                if event.get("base_event_status") in {"conflict", "agreement"}
                or event.get("base_placebo_status") == "conflict"
                else event.get("placebo_reason")
                if condition == "G_placebo_conflict"
                else event.get("reason"),
            )
            audit[f"skipped_{event['reason_for_condition']}"] += 1
            records.append(event)
            continue
        if rows is None:
            raise AssertionError("selected event has no day rows")
        signal_key, exit_key = (
            ("G_signal_bar_start_jst", "G_planned_exit_jst")
            if condition == "G_placebo_conflict"
            else ("t_signal_bar_start_jst", "X_planned_exit_jst")
        )
        signal, exit_ = (
            datetime.fromisoformat(cast(str, event[signal_key])),
            datetime.fromisoformat(cast(str, event[exit_key])),
        )
        result = engine.run(
            rows,
            TSECloseConflictReversalStrategy(
                f"r044_{condition}", signal, exit_, direction(condition, event), delay
            ),
            canonical_hash(
                {"condition": condition, "event": event, "data": data.data_version, "delay": delay}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R044 violated one position/day")
        audit["canceled_orders"] += result.canceled_orders
        if not result.trades:
            event.update(
                status="eligible_order_unfilled",
                reason_for_condition="ENGINE_NO_FILL_OR_FIXED_EXIT",
            )
            audit["eligible_order_unfilled"] += 1
            records.append(event)
            continue
        trade = result.trades[0]
        planned_entry = datetime.fromisoformat(
            cast(
                str,
                event[
                    "G_planned_entry_jst"
                    if condition == "G_placebo_conflict"
                    else "E_planned_entry_jst"
                ],
            )
        )
        planned_exit = datetime.fromisoformat(
            cast(
                str,
                event[
                    "G_planned_exit_jst"
                    if condition == "G_placebo_conflict"
                    else "X_planned_exit_jst"
                ],
            )
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
        audit[f"exit_{trade.exit_reason.value}"] += 1
        records.append(event)
    return (
        tuple(
            replace(trade, trade_id=f"trade-{number:06d}")
            for number, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
        ),
        records,
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
    daily_values: dict[str, int],
) -> dict[str, object]:
    folder.mkdir(parents=True)
    metrics = research_metrics(trades, bars)
    write_results(
        folder,
        trades,
        (),
        {
            "campaign_id": IDENTIFIER,
            "condition": name,
            "cost": f"{ticks} tick/side + 30JPY/side",
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(folder / "events.json", events)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {"trade_date": list(daily_values), "net_pnl_jpy": list(daily_values.values())}
    ).write_parquet(folder / "daily_net_pnl.parquet")
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    return metrics


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    lo, hi = floor(point), ceil(point)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (point - lo)


def design(
    rows: list[dict[str, object]], means: tuple[float, float] | None = None
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], dict[str, object]]:
    usable = [
        row
        for row in rows
        if row.get("base_event_status") in {"conflict", "agreement"}
        and row.get("raw_return_reason") == "OK"
    ]
    if not usable:
        raise ValueError("OLS has no complete eligible Development observations")
    z_p = [
        log(abs(float(cast(int, row["rP_points"]))) / float(cast(int, row["open_P_points"])))
        for row in usable
    ]
    z_l = [
        log(abs(float(cast(int, row["rL_points"]))) / float(cast(int, row["open_L_points"])))
        for row in usable
    ]
    mean_p, mean_l = means or (fmean(z_p), fmean(z_l))
    years = sorted({cast(str, row["trade_date"])[:4] for row in usable})
    matrix = np.array(
        [
            [
                1.0,
                float(row["base_event_status"] == "conflict"),
                zp - mean_p,
                zl - mean_l,
                (zl - mean_l) ** 2,
                float(cast(int, row["rP_sign"]) > 0),
                *(float(cast(str, row["trade_date"])[:4] == year) for year in years[1:]),
            ]
            for row, zp, zl in zip(usable, z_p, z_l, strict=True)
        ],
        dtype=float,
    )
    y = np.array([float(cast(int, row["raw_sign_adjusted_T_Tplus10_jpy"])) for row in usable])
    rank = int(np.linalg.matrix_rank(matrix))
    audit: dict[str, object] = {
        "formula": "y=alpha+beta*T+gamma1*(zP-mean(zP))+gamma2*(zL-mean(zL))+gamma3*(zL-mean(zL))^2+eta*U+calendar-year fixed effects+epsilon",
        "y": "-sign(rL)*(open_(tC+10)-open_tC), zero-tick pre-fee JPY per contract",
        "means": {"zP": mean_p, "zL": mean_l},
        "year_levels": years,
        "observation_count": len(usable),
        "design_columns": matrix.shape[1],
        "rank": rank,
        "full_rank": rank == matrix.shape[1],
    }
    if not cast(bool, audit["full_rank"]):
        raise ValueError("OLS design matrix is not full rank")
    return matrix, y, audit


def bootstrap(
    daily_by: dict[str, dict[str, int]],
    records_by: dict[str, list[dict[str, object]]],
    axis: list[str],
    matrix: np.ndarray[Any, Any],
    y: np.ndarray[Any, Any],
    audit: dict[str, object],
) -> dict[str, object]:
    arrays = {
        name: np.array([daily_by[name][day] for day in axis], dtype=float)
        for name in CONDITIONS
        if name != "G_placebo_conflict"
    }
    filled = {
        name: np.array(
            [
                float(
                    next(
                        (
                            row["net_pnl_jpy"]
                            for row in records_by[name]
                            if row.get("status") == "filled" and row["trade_date"] == day
                        ),
                        np.nan,
                    )
                )
                for day in axis
            ]
        )
        for name in ("A_close_conflict", "B_close_all", "C_close_agreement", "G_placebo_conflict")
    }
    raw_mask = np.array(
        [
            next(
                (
                    row.get("raw_return_reason") == "OK"
                    for row in records_by["B_close_all"]
                    if row["trade_date"] == day
                ),
                False,
            )
            for day in axis
        ]
    )
    if int(raw_mask.sum()) != matrix.shape[0]:
        raise ValueError("OLS rows do not align to fixed axis")
    samples: dict[str, list[float]] = {
        name: []
        for name in (
            "A_daily_mean_net_jpy",
            "A_minus_B_conditional_expectancy_jpy",
            "A_minus_C_conditional_expectancy_jpy",
            "A_minus_D_daily_mean_net_jpy",
            "A_minus_E_daily_mean_net_jpy",
            "A_minus_F_daily_mean_net_jpy",
            "A_minus_G_conditional_expectancy_jpy",
            "beta_conflict_jpy",
        )
    }
    rng, count, block = Random(SEED), len(axis), 20
    for _ in range(10_000):
        index: list[int] = []
        while len(index) < count:
            start = rng.randrange(count - block + 1)
            index.extend(range(start, start + block))
        picked = np.array(index[:count])
        samples["A_daily_mean_net_jpy"].append(float(arrays["A_close_conflict"][picked].mean()))
        for name, label in (("D_buy", "D"), ("E_sell", "E"), ("F_close_continue", "F")):
            samples[f"A_minus_{label}_daily_mean_net_jpy"].append(
                float((arrays["A_close_conflict"][picked] - arrays[name][picked]).mean())
            )
        for name, label in (
            ("B_close_all", "B"),
            ("C_close_agreement", "C"),
            ("G_placebo_conflict", "G"),
        ):
            a, b = filled["A_close_conflict"][picked], filled[name][picked]
            samples[f"A_minus_{label}_conditional_expectancy_jpy"].append(
                float(np.nanmean(a) - np.nanmean(b))
            )
        observed = picked[raw_mask[picked]]
        compressed = np.searchsorted(np.flatnonzero(raw_mask), observed)
        coef, *_ = np.linalg.lstsq(matrix[compressed], y[compressed], rcond=None)
        samples["beta_conflict_jpy"].append(float(coef[1]))
    estimates = {
        "A_daily_mean_net_jpy": float(arrays["A_close_conflict"].mean()),
        "A_minus_B_conditional_expectancy_jpy": float(
            np.nanmean(filled["A_close_conflict"]) - np.nanmean(filled["B_close_all"])
        ),
        "A_minus_C_conditional_expectancy_jpy": float(
            np.nanmean(filled["A_close_conflict"]) - np.nanmean(filled["C_close_agreement"])
        ),
        "A_minus_D_daily_mean_net_jpy": float(
            (arrays["A_close_conflict"] - arrays["D_buy"]).mean()
        ),
        "A_minus_E_daily_mean_net_jpy": float(
            (arrays["A_close_conflict"] - arrays["E_sell"]).mean()
        ),
        "A_minus_F_daily_mean_net_jpy": float(
            (arrays["A_close_conflict"] - arrays["F_close_continue"]).mean()
        ),
        "A_minus_G_conditional_expectancy_jpy": float(
            np.nanmean(filled["A_close_conflict"]) - np.nanmean(filled["G_placebo_conflict"])
        ),
    }
    coef, *_ = np.linalg.lstsq(matrix, y, rcond=None)
    estimates["beta_conflict_jpy"] = float(coef[1])
    audit["beta_conflict_jpy"] = float(coef[1])
    return {
        "method": "noncircular moving-block bootstrap with replacement; tail truncate; conditional comparisons recompute sum/count per replicate",
        "block_length_trade_dates": block,
        "repetitions": 10_000,
        "seed": SEED,
        "common_indices_all_conditions": True,
        "percentile": "linear",
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
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    files = {
        str(path): digest(ROOT / path)
        for path in (
            Path(__file__).relative_to(ROOT),
            Path("src/n225m_bt/research/r044.py"),
            Path("src/n225m_bt/strategies/tse_close_conflict_reversal.py"),
            Path("tests/test_r044_q001.py"),
            Path("src/n225m_bt/research/r021.py"),
        )
    }
    manifest = inputs(data_config.gold_root)
    cash, evidence = frozen_calendar()
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R044-Q001",
        "status": "frozen_before_r044_price_statistics_events_or_pnl",
        "seed": SEED,
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation.",
        "duplicate_review": {
            "R001_R043": "R030 reverses all nonzero final-five-minute days with a 60-minute-placebo. R044 requires the fixed prior 25-minute versus final 5-minute opposite-sign state, agreement control, all-day/constant/follow controls, placebo conflict, and fixed continuous-magnitude OLS. No equivalent registered specification exists.",
            "conclusion": "Execute this one fixed rule only; no alternate specification.",
        },
        "hypothesis": "If final five-minute futures return opposes the preceding 25 minutes before the scheduled TSE close, fading the final five-minute direction for ten minutes after close has positive post-cost expectancy and exceeds all-day fade, agreement fade, fixed directions, continuation and the analogous close-minus-60-minute placebo. It tests possible temporary close-related price pressure, not cash prices, auction imbalance, order flow, volume or participants.",
        "institution_and_calendar": evidence,
        "fixed_rule": {
            "boundary": "tC comes solely from the versioned TSE schedule; tB=tC-60 minutes. No 15:00/15:30 inference from code observations.",
            "state": "rP=close(tC-6)-open(tC-30) from 25 exact bars; rL=close(tC-1)-open(tC-5) from 5 exact bars. Nonoverlapping exact scheduled eligible bars, nonzero returns. T=1 iff rP*rL<0; T=0 iff >0. Placebo rPB/rLB is identically measured about tB. Missing, ineligible, isolated or outside data skips without observed-row substitution.",
            "conditions": {
                "A_close_conflict": "T=1, -sign(rL)",
                "B_close_all": "all valid, -sign(rL)",
                "C_close_agreement": "T=0, -sign(rL)",
                "D_buy/E_sell": "A event fixed long/fixed short",
                "F_close_continue": "A event sign(rL)",
                "G_placebo_conflict": "TB=1, -sign(rLB)",
            },
            "execution": "A-F: T-1 close signal, tC open entry, tC+10 open exit. G analogously at tB. A_delay signals one bar later for tC+1 entry without exit extension. One contract/day/position; no Stop/Target/reentry/update/early exit. Runtime changes only the new-entry cutoff from 15 to 0 minutes so the pre-registered delayed T+1 scheduled order can be issued; engine logic is unchanged.",
        },
        "ols": {
            "formula": "y=alpha+beta*T+gamma1(zP-mean(zP))+gamma2(zL-mean(zL))+gamma3(zL-mean(zL))^2+eta*U+calendar-year fixed effects+epsilon",
            "population": "B valid events; y=-sign(rL)*(open(tC+10)-open(tC)); zP=ln(abs(rP)/open(tC-30)); zL=ln(abs(rL)/open(tC-5)); U=1[rP>0]. Centers fixed once over the population; full rank mandatory.",
        },
        "inputs": {
            "physical": manifest,
            "r004_fixed_quarantine": "45 sessions/27,345 bars removed; 2,216 sessions/1,326,086 bars remaining; fixed hash required.",
            "fixed_daily_axis": {
                "source": str(R012_AXIS.relative_to(ROOT)),
                "sha256": digest(R012_AXIS),
                "count": AXIS_COUNT,
            },
            "quality_ceiling": "PASS_LIMITED",
        },
        "costs": {
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "A2_A3": "A at 2/3 ticks per side plus 30 JPY/side",
            "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees; no double deduction.",
        },
        "evaluation": {
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10_000,
                "seed": SEED,
                "common_indices": True,
                "noncircular": True,
                "tail_truncate": True,
                "percentile": "linear",
            },
            "information_gate": "B>=800; A/C>=250; A long/short>=80; close conflict/agreement x rL direction each>=70; G>=250 and long/short>=80.",
            "decision": "BLOCKED for input/synthetic/execution/accounting/OLS failure; INCONCLUSIVE for information failure; otherwise REJECT unless every stated economic and robustness criterion passes, when INVESTIGATE only.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "implementation_files": files,
            "implementation_files_hash": canonical_hash(files),
            "input_manifest_hash": canonical_hash(manifest),
        },
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
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "preregistered_before_r044_price_statistics_events_or_pnl",
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
            "tests/test_r044_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r044.py",
            "src/n225m_bt/strategies/tse_close_conflict_reversal.py",
            "tests/test_r044_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r044.py",
            "src/n225m_bt/strategies/tse_close_conflict_reversal.py",
        ],
    }
    validation: dict[str, Any] = {
        "coverage": "old/new TSE close, holiday, calendar_date/trade_date, 25+5 nonoverlap, boundary/placebo, zero/conflict/agreement, missing/isolation/outside, prefix, matching paths, fixed execution, costs and Holdout lock"
    }
    for name, command in commands.items():
        done = run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        )
        validation[name] = {
            "returncode": done.returncode,
            "stdout": done.stdout,
            "stderr": done.stderr,
        }
    validation["status"] = (
        "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R044 validation failed before Development price access")
    axis = fixed_axis()
    development = load_split(data_config.gold_root, "development")
    view, qaudit, isolated = quarantine(development)
    expected = {
        bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY
    } - {day.isoformat() for day, session in isolated if session is Session.DAY}
    if set(axis) != expected:
        raise ValueError("fixed R012 axis differs from nonisolated Development day universe")
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": qaudit,
            "fixed_target_trade_dates": axis,
            "physical_io": "Development normalized Parquet only",
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    grouped = session_groups(view.bars)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, Any] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(
            view,
            cash,
            BacktestEngine(instrument.instrument.to_spec(), runtime, classifier),
            condition,
            grouped,
            isolated,
            axis,
        )
        daily_values = dict.fromkeys(axis, 0)
        for trade in trades:
            daily_values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(
            OUT / condition, condition, trades, events, audit, view.bars, 1, daily_values
        )
        trades_by[condition], events_by[condition], daily_by[condition], results[condition] = (
            trades,
            events,
            daily_values,
            {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit},
        )
    for name, (ticks, delay) in VARIANTS.items():
        config = runtime.model_copy(
            update={"execution": runtime.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, events, audit = run_condition(
            view,
            cash,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            "A_close_conflict",
            grouped,
            isolated,
            axis,
            delay,
        )
        values = dict.fromkeys(axis, 0)
        for trade in trades:
            values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(OUT / name, name, trades, events, audit, view.bars, ticks, values)
        trades_by[name], events_by[name], results[name] = (
            trades,
            events,
            {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit},
        )
    by_day = {
        name: {cast(str, row["trade_date"]): row for row in rows}
        for name, rows in events_by.items()
    }
    fields = ("side", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")
    checks = {
        "T1_A_B_same": all(
            all(
                by_day["A_close_conflict"][day].get(field) == by_day["B_close_all"][day].get(field)
                for field in fields
            )
            for day in axis
            if by_day["A_close_conflict"][day].get("status") == "filled"
        ),
        "T0_B_C_same": all(
            all(
                by_day["B_close_all"][day].get(field) == by_day["C_close_agreement"][day].get(field)
                for field in fields
            )
            for day in axis
            if by_day["C_close_agreement"][day].get("status") == "filled"
        ),
        "A_F_opposite": all(
            by_day["A_close_conflict"][day].get("side")
            != by_day["F_close_continue"][day].get("side")
            for day in axis
            if by_day["A_close_conflict"][day].get("status") == "filled"
        ),
        "variants_event_side_same": all(
            all(
                by_day["A_close_conflict"][day].get(field) == by_day[name][day].get(field)
                for field in ("rP_points", "rL_points", "side")
            )
            for name in VARIANTS
            for day in axis
        ),
        "one_trade_per_day": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in trades_by.values()
        ),
        "fixed_exit": all(
            row.get("exit_reason") == ExitReason.SIGNAL.value
            and row.get("exit_delay_minutes") == 0
            and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0)
            for name, rows in events_by.items()
            for row in rows
            if row.get("status") == "filled"
        ),
        "no_unfilled": not any(
            row.get("status") == "eligible_order_unfilled"
            for rows in events_by.values()
            for row in rows
        ),
        "accounting": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy and trade.fees_jpy == 60
            for trades in trades_by.values()
            for trade in trades
        ),
    }
    post = {"status": "PASS" if all(checks.values()) else "BLOCKED", "checks": checks}
    write_json(OUT / "post_execution_validation.json", post)
    raw = [
        row
        for row in events_by["B_close_all"]
        if row.get("base_event_status") in {"conflict", "agreement"}
    ]
    if any(row.get("raw_return_reason") != "OK" for row in raw):
        post["status"] = "BLOCKED"
        write_json(OUT / "post_execution_validation.json", post)
    matrix, y, ols = design(raw)
    boot = bootstrap(daily_by, events_by, axis, matrix, y, ols)
    diagnostics = {
        f"{state}_rL_{direction}": {
            "eligible_day_count": sum(
                row.get("base_event_status") == state and row.get("rL_direction") == direction
                for row in raw
            )
        }
        for state in ("conflict", "agreement")
        for direction in ("long", "short")
    }
    placebo = [row for row in events_by["G_placebo_conflict"] if row.get("status") == "filled"]
    decomp = {
        "method": "A Gross split at tC+5 open",
        "gross_0_5_jpy": 0,
        "gross_5_10_jpy": 0,
        "unavailable_trade_count": 0,
    }
    for trade in trades_by["A_close_conflict"]:
        mid = datetime.combine(
            trade.trade_date, datetime.min.time(), tzinfo=trade.entry_ts.tzinfo
        ) + timedelta(hours=trade.entry_ts.hour, minutes=trade.entry_ts.minute + 5)
        bar = {item.ts_jst: item for item in grouped[(trade.trade_date, Session.DAY)]}.get(mid)
        if bar is None or not bar.is_eligible:
            decomp["unavailable_trade_count"] += 1
        else:
            decomp["gross_0_5_jpy"] += (bar.open - trade.entry_fill_price) * trade.side.sign * 100
            decomp["gross_5_10_jpy"] += (trade.exit_fill_price - bar.open) * trade.side.sign * 100
    decomp["all_available"] = decomp["unavailable_trade_count"] == 0
    a_metrics = results["A_close_conflict"]["metrics"]
    overall = a_metrics["overall"]
    years = {
        str(year): sum(
            daily_by["A_close_conflict"][day] for day in axis if day.startswith(str(year))
        )
        for year in range(2021, 2026)
    }
    months = {
        f"{year}-{month:02d}": sum(
            daily_by["A_close_conflict"][day]
            for day in axis
            if day.startswith(f"{year}-{month:02d}")
        )
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }
    def lower(name: str) -> bool:
        return cast(list[float], boot[name]["ci95_percentile_linear"])[0] > 0
    info = {
        "B_at_least_800": len(trades_by["B_close_all"]) >= 800,
        "A_C_at_least_250": len(trades_by["A_close_conflict"]) >= 250
        and len(trades_by["C_close_agreement"]) >= 250,
        "A_long_short_at_least_80": all(
            sum(trade.side.value == side for trade in trades_by["A_close_conflict"]) >= 80
            for side in ("long", "short")
        ),
        "four_main_groups_at_least_70": all(
            value["eligible_day_count"] >= 70 for value in diagnostics.values()
        ),
        "G_at_least_250": len(placebo) >= 250,
        "G_long_short_at_least_80": all(
            sum(row.get("side") == side for row in placebo) >= 80 for side in ("long", "short")
        ),
    }
    gates = {
        "A_net_positive": overall["net_pnl_jpy"] > 0,
        "A_pf_gt_1": overall["profit_factor"] is not None and overall["profit_factor"] > 1,
        **{f"{name}_CI_lower_positive": lower(name) for name in boot if name.endswith("jpy")},
        "A2_A3_delay_expectancy_positive": all(
            results[name]["metrics"]["overall"]["expectancy_jpy"] > 0 for name in VARIANTS
        ),
        "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3,
        "positive_months_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "top10_removed_positive": a_metrics["concentration"]["net_excluding_top10_jpy"] > 0,
        "gross_decomposition_complete": bool(decomp["all_available"]),
    }
    decision = (
        "BLOCKED"
        if post["status"] != "PASS" or not ols["full_rank"]
        else "INCONCLUSIVE"
        if not all(info.values())
        else "INVESTIGATE"
        if all(gates.values())
        else "REJECT"
    )
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {
            "trade_dates": axis,
            **{name: [daily_by[name][day] for day in axis] for name in daily_by},
            "no_trade": "0",
        },
    )
    write_json(OUT / "event_sign_diagnostics.json", diagnostics)
    write_json(OUT / "gross_time_decomposition.json", decomp)
    write_json(OUT / "bootstrap.json", boot)
    write_json(
        OUT / "development_results.json",
        {
            "campaign_id": IDENTIFIER,
            "quality_status": "PASS_LIMITED",
            "decision": decision,
            "information_gates": info,
            "pass_gates": gates,
            "A_year_net_jpy": years,
            "A_month_net_jpy": months,
            "positive_months_of_54": sum(value > 0 for value in months.values()),
            "conditions": results,
            "four_sign_groups": diagnostics,
            "ols": ols,
            "gross_time_decomposition": decomp,
            "scope": "Development only; WFA/OOS/Final Holdout not run",
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
