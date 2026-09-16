"""Execute the preregistered Development-only R045-Q001 experiment."""

from __future__ import annotations

import json
import os
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
from n225m_bt.research.r045 import TSE_LUNCH_SCHEDULE, tse_lunch_placebo_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.tse_lunch_placebo_reversal import TSELunchPlaceboReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r045-q001-20260914-tse-lunch-placebo-reversal-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
R021 = ROOT / "results" / "research" / "r021-q001-20260914-opening-cash-close-followthrough-05"
R012_AXIS = ROOT / "results" / "research" / "r012-q001-20260914-night-direction-followthrough-01" / "daily_net_pnl_aligned.json"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED, AXIS_COUNT = 20260925, 1_111
Condition = Literal["A_lunch_reverse", "B_lunch_continue", "C_buy", "D_sell", "G_placebo_reverse"]
CONDITIONS: tuple[Condition, ...] = ("A_lunch_reverse", "B_lunch_continue", "C_buy", "D_sell", "G_placebo_reverse")
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
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(gold, "development")]
    return {"status": "frozen_before_r045_price_statistics_events_or_pnl", "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.", "trade_date_range": ["2021-01-01", "2025-06-30"], "files": files, "files_hash": canonical_hash(files)}


def frozen_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    holiday = R020 / "institutional_evidence" / "cabinet_office_public_holidays.csv"
    jpx = R021 / "institutional_evidence" / "jpx_tse_trading_hours.pdf"
    if not holiday.exists() or not jpx.exists():
        raise ValueError("frozen R020/R021 TSE evidence unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copyfile(holiday, destination / holiday.name)
    copyfile(jpx, destination / jpx.name)
    schedule = {"schedule_id": "R045-LUNCH-1", "effective_start": TSE_LUNCH_SCHEDULE.effective_start.isoformat(), "effective_end": TSE_LUNCH_SCHEDULE.effective_end.isoformat(), "front_close_jst": TSE_LUNCH_SCHEDULE.front_close.isoformat(), "afternoon_open_jst": TSE_LUNCH_SCHEDULE.afternoon_open.isoformat(), "source": TSE_LUNCH_SCHEDULE.source}
    write_json(destination / "tse_lunch_schedule.json", schedule)
    calendar = TSECashMarketCalendar.from_cabinet_office_csv((destination / holiday.name).read_text(encoding="cp932"))
    if calendar.source_start > date(2021, 1, 1) or calendar.source_end < date(2025, 6, 30):
        raise ValueError("frozen holiday evidence does not cover Development")
    evidence = {"holiday_csv_sha256": digest(destination / holiday.name), "jpx_schedule_sha256": digest(destination / jpx.name), "lunch_schedule_sha256": digest(destination / "tse_lunch_schedule.json"), "r020_completed_sha256": digest(R020 / "COMPLETED.json"), "r021_completed_sha256": digest(R021 / "COMPLETED.json"), "schedule": schedule}
    write_json(destination / "evidence_manifest.json", evidence)
    return calendar, evidence


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {"parent_data_version": data.data_version, "quarantined_sessions": len(isolated), "quarantined_bars": len(data.bars) - len(included), "included_sessions": len(grouped) - len(isolated), "included_bars": len(included), "quarantined_session_list": listed, "quarantined_session_list_hash": canonical_hash(listed), "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included), "rule": "exclude whole (trade_date, session) for any TICK_GRID_VIOLATION"}
    expected = {"parent_data_version": PARENT_HASH, "quarantined_sessions": 45, "quarantined_bars": 27_345, "included_sessions": 2_216, "included_bars": 1_326_086, "quarantined_session_list_hash": QUARANTINE_HASH, "included_tick_grid_violations": 0}
    mismatches = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    audit.update(expected_match=not mismatches, mismatches=mismatches)
    if mismatches:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def direction(condition: str, event: dict[str, object]) -> str:
    if condition == "C_buy":
        return "long"
    if condition == "D_sell":
        return "short"
    follow = cast(str, event["placebo_direction" if condition == "G_placebo_reverse" else "lunch_direction"])
    if condition == "B_lunch_continue":
        return follow
    return "short" if follow == "long" else "long"


def attach_raw_returns(event: dict[str, object], rows: list[Any]) -> None:
    if event.get("base_event_status") != "eligible":
        return
    by_time = {bar.ts_jst: bar for bar in rows}
    for label, sign_key, entry_key, exit_key in (("lunch", "rL_sign", "A_planned_entry_jst", "A_planned_exit_jst"), ("placebo", "rP_sign", "G_planned_entry_jst", "G_planned_exit_jst")):
        entry, exit_ = (datetime.fromisoformat(cast(str, event[key])) for key in (entry_key, exit_key))
        first, last = by_time.get(entry), by_time.get(exit_)
        if first is None or last is None or not first.is_eligible or not last.is_eligible:
            event[f"raw_{label}_reason"] = "POST_WINDOW_PATH_MISSING_OR_INELIGIBLE"
            continue
        event.update({f"raw_{label}_reason": "OK", f"open_{label}_entry_points": first.open, f"open_{label}_exit_points": last.open, f"raw_{label}_y_jpy": -cast(int, event[sign_key]) * (last.open - first.open) * 100})


def run_condition(data: ResearchData, cash: TSECashMarketCalendar, engine: BacktestEngine, condition: str, grouped: dict[tuple[date, Session], list[Any]], isolated: set[tuple[date, Session]], axis: list[str], delay: int = 0) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text_day in axis:
        day = date.fromisoformat(text_day)
        rows = grouped.get((day, Session.DAY))
        event = tse_lunch_placebo_event(day, rows, cash, day_quarantined=(day, Session.DAY) in isolated)
        event.update(condition=condition, base_event_status=event.get("status"), variant_delay_minutes=delay)
        if rows is not None:
            attach_raw_returns(event, rows)
        if event["base_event_status"] != "eligible":
            event.update(status="skipped", reason_for_condition=event.get("reason"))
            audit[f"skipped_{event['reason_for_condition']}"] += 1
            records.append(event)
            continue
        if rows is None:
            raise AssertionError("eligible R045 event lacks day rows")
        prefix = "G" if condition == "G_placebo_reverse" else "A"
        signal, exit_ = (datetime.fromisoformat(cast(str, event[f"{prefix}_{key}"])) for key in ("signal_bar_start_jst", "planned_exit_jst"))
        result = engine.run(rows, TSELunchPlaceboReversalStrategy(f"r045_{condition}", signal, exit_, direction(condition, event), delay), canonical_hash({"condition": condition, "event": event, "data": data.data_version, "delay": delay}))
        if len(result.trades) > 1:
            raise AssertionError("R045 violated one position/day")
        audit["canceled_orders"] += result.canceled_orders
        if not result.trades:
            event.update(status="eligible_order_unfilled", reason_for_condition="ENGINE_NO_FILL_OR_FIXED_EXIT")
            audit["eligible_order_unfilled"] += 1
            records.append(event)
            continue
        trade = result.trades[0]
        planned_entry = datetime.fromisoformat(cast(str, event[f"{prefix}_planned_entry_jst"]))
        planned_exit = datetime.fromisoformat(cast(str, event[f"{prefix}_planned_exit_jst"]))
        event.update(status="filled", side=trade.side.value, entry_ts_jst=trade.entry_ts.isoformat(), exit_ts_jst=trade.exit_ts.isoformat(), entry_signal_ts_jst=trade.entry_signal_ts.isoformat(), exit_signal_ts_jst=trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None, entry_delay_minutes=int((trade.entry_ts - planned_entry).total_seconds() // 60), exit_delay_minutes=int((trade.exit_ts - planned_exit).total_seconds() // 60), exit_reason=trade.exit_reason.value, gross_pnl_jpy=trade.gross_pnl_jpy, slippage_cost_jpy=trade.slippage_cost_jpy, fees_jpy=trade.fees_jpy, net_pnl_jpy=trade.net_pnl_jpy)
        trades.append(trade)
        audit["trades"] += 1
        audit[f"exit_{trade.exit_reason.value}"] += 1
        records.append(event)
    return tuple(replace(trade, trade_id=f"trade-{number:06d}") for number, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)), records, dict(sorted(audit.items()))


def write_condition(folder: Path, name: str, trades: tuple[Trade, ...], events: list[dict[str, object]], audit: dict[str, int], bars: list[Any], ticks: int, daily: dict[str, int]) -> dict[str, object]:
    folder.mkdir(parents=True)
    metrics = research_metrics(trades, bars)
    write_results(folder, trades, (), {"campaign_id": IDENTIFIER, "condition": name, "cost": f"{ticks} tick/side + 30JPY/side", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "events.json", events)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": list(daily), "net_pnl_jpy": list(daily.values())}).write_parquet(folder / "daily_net_pnl.parquet")
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    return metrics


def direction_performance(events_by: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    """Summarize every condition by the preregistered lunch/placebo directions."""
    output: dict[str, object] = {}
    for window, sign_key in (("lunch", "rL_sign"), ("placebo", "rP_sign")):
        by_condition: dict[str, object] = {}
        for condition, events in events_by.items():
            by_side: dict[str, object] = {}
            for sign, label in ((1, "up"), (-1, "down")):
                rows = [row for row in events if row.get("status") == "filled" and row.get(sign_key) == sign]
                count = len(rows)
                by_side[label] = {
                    "trade_count": count,
                    "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in rows),
                    "slippage_cost_jpy": sum(cast(int, row["slippage_cost_jpy"]) for row in rows),
                    "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in rows),
                    "net_pnl_jpy": sum(cast(int, row["net_pnl_jpy"]) for row in rows),
                    "expectancy_jpy": sum(cast(int, row["net_pnl_jpy"]) for row in rows) / count if count else None,
                }
            by_condition[condition] = by_side
        output[window] = by_condition
    return output


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    lo, hi = floor(point), ceil(point)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (point - lo)


def ols_rows(events: list[dict[str, object]], means: float | None = None) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], list[str], dict[str, object]]:
    raw: list[dict[str, object]] = []
    for event in events:
        if event.get("base_event_status") != "eligible":
            continue
        for label, l_value in (("lunch", 1), ("placebo", 0)):
            if event.get(f"raw_{label}_reason") != "OK":
                raise ValueError("R045 OLS post-boundary price path is incomplete")
            r = float(cast(int, event["rL_points" if label == "lunch" else "rP_points"]))
            p = float(cast(int, event["open_L_points" if label == "lunch" else "open_P_points"]))
            if p <= 0:
                raise ValueError("R045 OLS log denominator is nonpositive")
            raw.append({"trade_date": event["trade_date"], "L": l_value, "z": log(abs(r) / p), "U": float(r > 0), "y": float(cast(int, event[f"raw_{label}_y_jpy"]))})
    if not raw:
        raise ValueError("R045 OLS has no complete eligible observations")
    center = fmean(cast(float, item["z"]) for item in raw) if means is None else means
    years = sorted({cast(str, item["trade_date"])[:4] for item in raw})
    matrix = np.array([[1.0, item["L"], cast(float, item["z"]) - center, (cast(float, item["z"]) - center) ** 2, item["U"], *(float(cast(str, item["trade_date"])[:4] == year) for year in years[1:])] for item in raw], dtype=float)
    y = np.array([item["y"] for item in raw], dtype=float)
    rank = int(np.linalg.matrix_rank(matrix))
    audit: dict[str, object] = {"formula": "y=alpha+beta*L+gamma1*(z-mean(z))+gamma2*(z-mean(z))^2+eta*U+calendar-year fixed effects+epsilon", "population": "two rows per common E date: lunch and placebo; zero-tick pre-fee direction-adjusted future return", "mean_z": center, "year_levels": years, "observation_count": len(raw), "design_columns": matrix.shape[1], "rank": rank, "full_rank": rank == matrix.shape[1]}
    if not audit["full_rank"]:
        raise ValueError("R045 OLS design matrix is not full rank")
    return matrix, y, [cast(str, item["trade_date"]) for item in raw], audit


def bootstrap(daily: dict[str, dict[str, int]], events: dict[str, list[dict[str, object]]], axis: list[str], matrix: np.ndarray[Any, Any], y: np.ndarray[Any, Any], ols_dates: list[str], audit: dict[str, object]) -> dict[str, object]:
    arrays = {name: np.array([daily[name][day] for day in axis], dtype=float) for name in CONDITIONS}
    mask = np.array([next(row.get("status") == "filled" for row in events["A_lunch_reverse"] if row["trade_date"] == day) for day in axis])
    indices = {day: np.flatnonzero(np.array(ols_dates) == day) for day in axis}
    if any(len(indices[day]) not in {0, 2} for day in axis) or int(mask.sum()) * 2 != matrix.shape[0]:
        raise ValueError("R045 bootstrap OLS/common-E alignment failure")
    samples: dict[str, list[float]] = {name: [] for name in ("A_daily_mean_net_jpy", "A_minus_B_conditional_expectancy_jpy", "A_minus_C_conditional_expectancy_jpy", "A_minus_D_conditional_expectancy_jpy", "A_minus_G_conditional_expectancy_jpy", "beta_lunch_boundary_jpy")}
    rng, count, block, empty_year_columns = Random(SEED), len(axis), 20, 0
    for _ in range(10_000):
        picked: list[int] = []
        while len(picked) < count:
            start = rng.randrange(count - block + 1)
            picked.extend(range(start, start + block))
        selected = np.array(picked[:count])
        usable = selected[mask[selected]]
        samples["A_daily_mean_net_jpy"].append(float(arrays["A_lunch_reverse"][selected].mean()))
        for other, label in (("B_lunch_continue", "B"), ("C_buy", "C"), ("D_sell", "D"), ("G_placebo_reverse", "G")):
            samples[f"A_minus_{label}_conditional_expectancy_jpy"].append(float(arrays["A_lunch_reverse"][usable].mean() - arrays[other][usable].mean()))
        rows = np.concatenate([indices[axis[index]] for index in usable])
        sampled_design = matrix[rows]
        active = np.any(sampled_design != 0.0, axis=0)
        active[:5] = True
        empty_year_columns += int((~active).sum())
        reduced_design = sampled_design[:, active]
        if np.linalg.matrix_rank(reduced_design) != reduced_design.shape[1]:
            raise ValueError("R045 bootstrap OLS design is rank deficient after absent-year fixed effects are removed")
        coefficient, *_ = np.linalg.lstsq(reduced_design, y[rows], rcond=None)
        samples["beta_lunch_boundary_jpy"].append(float(coefficient[1]))
    coefficient, *_ = np.linalg.lstsq(matrix, y, rcond=None)
    estimates = {"A_daily_mean_net_jpy": float(arrays["A_lunch_reverse"].mean()), **{f"A_minus_{label}_conditional_expectancy_jpy": float(arrays["A_lunch_reverse"][mask].mean() - arrays[other][mask].mean()) for other, label in (("B_lunch_continue", "B"), ("C_buy", "C"), ("D_sell", "D"), ("G_placebo_reverse", "G"))}, "beta_lunch_boundary_jpy": float(coefficient[1])}
    audit["beta_lunch_boundary_jpy"] = float(coefficient[1])
    return {"method": "20-trade-date noncircular moving-block bootstrap with replacement; tail truncate; conditional sum/count and OLS recomputed per replicate. Fixed global mean(z) is retained; an absent calendar-year dummy is removed only within that replicate because it is an unobserved factor level.", "block_length_trade_dates": block, "repetitions": 10_000, "seed": SEED, "common_indices_all_conditions": True, "percentile": "linear", "bootstrap_absent_year_dummy_columns_removed": empty_year_columns, "ols": audit, **{name: {"estimate": estimates[name], "ci95_percentile_linear": [percentile(values, 0.025), percentile(values, 0.975)]} for name, values in samples.items()}}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r045_q001_tse_lunch_placebo_reversal.py')

    if OUT.exists():
        raise FileExistsError(f"append-only output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    runtime = baseline.model_copy(update={"risk": baseline.risk.model_copy(update={"new_entry_cutoff_minutes_before_session_close": 0})})
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    implementation = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r045.py"), Path("src/n225m_bt/strategies/tse_lunch_placebo_reversal.py"), Path("tests/test_r045_q001.py"), Path("src/n225m_bt/research/r020.py")]
    files = {str(path): digest(ROOT / path) for path in implementation}
    manifest, cash, evidence = inputs(data_config.gold_root), *frozen_calendar()
    plan: dict[str, object] = {"experiment_id": IDENTIFIER, "study_id": "R045-Q001", "status": "frozen_before_r045_price_statistics_events_or_pnl", "seed": SEED, "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation.", "supersedes_incomplete": "...-01 preserved after post-execution bootstrap stopped because 13 replicates had an absent calendar-year dummy. ...-02 retains every trading, input, cost, seed, evaluation and decision rule; only a dummy for a calendar-year level absent from an individual resample is omitted from that resample's otherwise identical fixed-effects OLS. ...-03 retains all of ...-02 and adds the preregistered lunch-direction and placebo-direction count/performance summary artifact that was omitted from the completed report.", "duplicate_review": {"R001_R044": "R020 has the 60-minute lunch return but combines it with a 60-minute pre-lunch return and holds 12:30--13:30. R038 uses a strict-extreme 30-minute continuation rule. R029 adds a five-minute confirmation. None uses common E, a 15-minute post-reopen reversal, fixed-direction/continuation controls, same-length 30-minute-before-lunch placebo, and the fixed pooled OLS together.", "conclusion": "No registered equivalent; execute only this fixed rule."}, "hypothesis": "A full scheduled TSE cash-lunch futures displacement reverses during the first 15 minutes after cash reopening and has post-cost expectancy exceeding continuation, fixed long, fixed short, and the same-day same-length pre-lunch placebo reversal. This tests possible temporary futures displacement, not cash prices, arbitrage, order flow, news, volume, or participant mechanisms.", "institution_and_calendar": evidence, "fixed_rule": {"boundary": "tS/tR are obtained only from frozen versioned R045-LUNCH-1 schedule; Delta=tR-tS and tB=tS-30 minutes. No futures-observation inference or hard-coded event boundary is used.", "state": "rL=close(tR-1)-open(tS); rP=close(tB-1)-open(tB-Delta), exact contiguous scheduled eligible minute bars. Common E requires both nonzero windows plus complete planned post-entry/fixed-exit paths. No observed-row or other-day substitution.", "conditions": {"A_lunch_reverse": "-sign(rL) at tR", "B_lunch_continue": "sign(rL) at tR", "C_buy/D_sell": "fixed long/fixed short at same A event/time", "G_placebo_reverse": "-sign(rP) at tB on the identical common E dates"}, "execution": "Signal after prior window final close; entry at boundary open; fixed exit at boundary+15 open. A_delay enters tR+1 and exits tR+15 without extension. A2/A3 use 2/3 tick per side. One contract/day/position; no Stop/Target/reentry/update/early exit."}, "ols": {"formula": "y=alpha+beta*L+gamma1(z-mean(z))+gamma2(z-mean(z))^2+eta*U+calendar-year fixed effects+epsilon", "population": "two rows per E date. y is zero-tick, pre-fee, direction-adjusted future return; L=1 lunch/0 placebo; z=ln(abs(r)/P); U=1[r>0]; mean(z) is fixed once over both rows; only factor levels absent from a bootstrap resample are omitted in that replicate."}, "inputs": {"physical": manifest, "r004_fixed_quarantine": "45 sessions/27,345 bars removed; 2,216 sessions/1,326,086 bars remaining; fixed hash required.", "fixed_daily_axis": {"source": str(R012_AXIS.relative_to(ROOT)), "sha256": digest(R012_AXIS), "count": AXIS_COUNT}, "quality_ceiling": "PASS_LIMITED"}, "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2_A3": "A at 2/3 ticks per side plus 30 JPY/side", "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees; no double deduction."}, "evaluation": {"bootstrap": {"block_length_trade_dates": 20, "repetitions": 10_000, "seed": SEED, "common_indices": True, "noncircular": True, "tail_truncate": True, "percentile": "linear"}, "information_gate": "E>=800; rL up/down each>=250; rP up/down each>=250.", "decision": "BLOCKED for input/synthetic/execution/accounting/OLS failure; INCONCLUSIVE for information failure; otherwise REJECT unless every stated economic and robustness criterion passes, when INVESTIGATE only."}, "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": files, "implementation_files_hash": canonical_hash(files), "input_manifest_hash": canonical_hash(manifest)}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "input_manifest.json", manifest)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": runtime.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r045_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": SEED, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r045_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r045.py", "src/n225m_bt/strategies/tse_lunch_placebo_reversal.py", "tests/test_r045_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r045.py", "src/n225m_bt/strategies/tse_lunch_placebo_reversal.py"]}
    validation: dict[str, Any] = {"coverage": "schedule boundaries, holiday, calendar_date/trade_date, Delta equal-length placebo, endpoints, zero/missing/isolation/outside, prefix, next-bar execution, fixed/delayed exit, costs and holdout lock"}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R045 validation failed before Development price access")
    axis = fixed_axis()
    development = load_split(data_config.gold_root, "development")
    view, qaudit, isolated = quarantine(development)
    expected = {bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY} - {day.isoformat() for day, session in isolated if session is Session.DAY}
    if set(axis) != expected:
        raise ValueError("fixed R012 axis differs from nonisolated Development day universe")
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": qaudit, "fixed_target_trade_dates": axis, "physical_io": "Development normalized Parquet only", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    grouped = session_groups(view.bars)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily: dict[str, dict[str, int]] = {}
    results: dict[str, Any] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, cash, BacktestEngine(instrument.instrument.to_spec(), runtime, classifier), condition, grouped, isolated, axis)
        values = dict.fromkeys(axis, 0)
        for trade in trades:
            values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(OUT / condition, condition, trades, events, audit, view.bars, 1, values)
        trades_by[condition], events_by[condition], daily[condition], results[condition] = trades, events, values, {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    for name, (ticks, delay) in VARIANTS.items():
        config = runtime.model_copy(update={"execution": runtime.execution.model_copy(update={"slippage_ticks": ticks})})
        trades, events, audit = run_condition(view, cash, BacktestEngine(instrument.instrument.to_spec(), config, classifier), "A_lunch_reverse", grouped, isolated, axis, delay)
        values = dict.fromkeys(axis, 0)
        for trade in trades:
            values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = write_condition(OUT / name, name, trades, events, audit, view.bars, ticks, values)
        trades_by[name], events_by[name], results[name] = trades, events, {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    by_day = {name: {cast(str, row["trade_date"]): row for row in rows} for name, rows in events_by.items()}
    filled = [day for day in axis if by_day["A_lunch_reverse"][day].get("status") == "filled"]
    checks = {"common_E_all_conditions": all(by_day[name][day].get("status") == "filled" for name in CONDITIONS for day in filled), "A_B_opposite_side": all(by_day["A_lunch_reverse"][day].get("side") != by_day["B_lunch_continue"][day].get("side") for day in filled), "A_C_D_same_event_time": all(all(by_day["A_lunch_reverse"][day].get(field) == by_day[name][day].get(field) for field in ("tS_jst", "tR_jst", "entry_ts_jst", "exit_ts_jst")) for name in ("C_buy", "D_sell") for day in filled), "A_G_same_common_date": all(by_day["A_lunch_reverse"][day].get("rL_points") is not None and by_day["G_placebo_reverse"][day].get("rP_points") is not None for day in filled), "A_B_opposite_signal": all(by_day["A_lunch_reverse"][day].get("side") != by_day["B_lunch_continue"][day].get("side") for day in filled), "variants_event_side_same": all(by_day[name][day].get(field) == by_day["A_lunch_reverse"][day].get(field) for name in VARIANTS for day in axis for field in ("rL_points", "rP_points", "side")), "one_trade_per_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in trades_by.values()), "fixed_exit_and_delay": all(row.get("exit_reason") == ExitReason.SIGNAL.value and row.get("exit_delay_minutes") == 0 and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0) for name, rows in events_by.items() for row in rows if row.get("status") == "filled"), "no_unfilled": not any(row.get("status") == "eligible_order_unfilled" for rows in events_by.values() for row in rows), "accounting_no_double_slippage": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy and trade.fees_jpy == 60 for trades in trades_by.values() for trade in trades)}
    post = {"status": "PASS" if all(checks.values()) else "BLOCKED", "checks": checks, "eligible_E_count": len(filled)}
    write_json(OUT / "post_execution_validation.json", post)
    matrix, y, ols_dates, ols = ols_rows(events_by["A_lunch_reverse"])
    boot = bootstrap(daily, events_by, axis, matrix, y, ols_dates, ols)
    direction_counts = {"rL_long": sum(row.get("rL_sign") == 1 for row in events_by["A_lunch_reverse"] if row.get("base_event_status") == "eligible"), "rL_short": sum(row.get("rL_sign") == -1 for row in events_by["A_lunch_reverse"] if row.get("base_event_status") == "eligible"), "rP_long": sum(row.get("rP_sign") == 1 for row in events_by["A_lunch_reverse"] if row.get("base_event_status") == "eligible"), "rP_short": sum(row.get("rP_sign") == -1 for row in events_by["A_lunch_reverse"] if row.get("base_event_status") == "eligible")}
    direction_summary = direction_performance(events_by)
    decomp: dict[str, object] = {"method": "A Gross fill-to-fill split at tR+5 open", "gross_0_5_jpy": 0, "gross_5_15_jpy": 0, "unavailable_trade_count": 0}
    for trade in trades_by["A_lunch_reverse"]:
        mid = trade.entry_ts + timedelta(minutes=5)
        middle = {bar.ts_jst: bar for bar in grouped[(trade.trade_date, Session.DAY)]}.get(mid)
        if middle is None or not middle.is_eligible:
            decomp["unavailable_trade_count"] = cast(int, decomp["unavailable_trade_count"]) + 1
        else:
            decomp["gross_0_5_jpy"] = cast(int, decomp["gross_0_5_jpy"]) + (middle.open - trade.entry_fill_price) * trade.side.sign * 100
            decomp["gross_5_15_jpy"] = cast(int, decomp["gross_5_15_jpy"]) + (trade.exit_fill_price - middle.open) * trade.side.sign * 100
    decomp["all_available"] = decomp["unavailable_trade_count"] == 0
    a_metrics = results["A_lunch_reverse"]["metrics"]
    years = {str(year): sum(daily["A_lunch_reverse"][day] for day in axis if day.startswith(str(year))) for year in range(2021, 2026)}
    months = {f"{year}-{month:02d}": sum(daily["A_lunch_reverse"][day] for day in axis if day.startswith(f"{year}-{month:02d}")) for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    def lower(name: str) -> bool:
        return cast(list[float], boot[name]["ci95_percentile_linear"])[0] > 0
    information = {"E_at_least_800": len(filled) >= 800, "rL_up_down_each_250": direction_counts["rL_long"] >= 250 and direction_counts["rL_short"] >= 250, "rP_up_down_each_250": direction_counts["rP_long"] >= 250 and direction_counts["rP_short"] >= 250}
    gates = {"A_net_positive": a_metrics["overall"]["net_pnl_jpy"] > 0, "A_pf_above_1": a_metrics["overall"]["profit_factor"] is not None and a_metrics["overall"]["profit_factor"] > 1, **{f"{name}_CI_lower_positive": lower(name) for name in boot if name.endswith("jpy")}, "A2_A3_A_delay_expectancy_positive": all(results[name]["metrics"]["overall"]["expectancy_jpy"] > 0 for name in VARIANTS), "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3, "positive_months_27_of_54": sum(value > 0 for value in months.values()) >= 27, "net_excluding_top10_positive": a_metrics["concentration"]["net_excluding_top10_jpy"] > 0, "gross_decomposition_complete": bool(decomp["all_available"])}
    decision = "BLOCKED" if post["status"] != "PASS" or not ols["full_rank"] else "INCONCLUSIVE" if not all(information.values()) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": axis, **{name: [daily[name][day] for day in axis] for name in CONDITIONS}, "no_trade": "0", "day_session_quarantined": "excluded", "TSE_closed_missing_zero_condition_cancel": "zero"})
    write_json(OUT / "direction_diagnostics.json", direction_counts)
    write_json(OUT / "direction_performance.json", direction_summary)
    write_json(OUT / "gross_time_decomposition.json", decomp)
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "information_gates": information, "pass_gates": gates, "E_direction_counts": direction_counts, "direction_performance": direction_summary, "A_year_net_jpy": years, "A_month_net_jpy": months, "positive_months_of_54": sum(value > 0 for value in months.values()), "conditions": results, "ols": ols, "gross_time_decomposition": decomp, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
