"""Execute the preregistered Development-only R043-Q001 experiment."""

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
from typing import Any, Literal, cast

import polars as pl

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r043 import precash_cash_conflict_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.precash_cash_conflict_followthrough import (
    PrecashCashConflictFollowthroughStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r043-q001-20260914-precash-cash-conflict-followthrough-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
R021 = ROOT / "results" / "research" / "r021-q001-20260914-opening-cash-close-followthrough-05"
R012_AXIS = ROOT / "results" / "research" / "r012-q001-20260914-night-direction-followthrough-01" / "daily_net_pnl_aligned.json"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20260923
AXIS_COUNT = 1_111
Condition = Literal["A_conflict", "B_all_cash", "C_agreement", "D_buy", "E_sell", "F_precash"]
CONDITIONS: tuple[Condition, ...] = (
    "A_conflict", "B_all_cash", "C_agreement", "D_buy", "E_sell", "F_precash"
)
VARIANTS = {"A2_2tick": (2, 0), "A3_3tick": (3, 0), "A_delay": (1, 1)}


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def fixed_axis() -> list[str]:
    values = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(values, list) or len(values) != AXIS_COUNT or len(set(values)) != AXIS_COUNT:
        raise ValueError("fixed R012 Development day axis unavailable")
    return cast(list[str], values)


def input_manifest(gold: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(gold, "development")]
    return {"status": "frozen_before_r043_price_statistics_events_or_pnl", "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.", "trade_date_range": ["2021-01-01", "2025-06-30"], "files": files, "files_hash": canonical_hash(files)}


def freeze_tse_evidence() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R021 / "institutional_evidence" / "r020_cabinet_office_public_holidays.csv"
    required = (source, R020 / "COMPLETED.json", R021 / "COMPLETED.json")
    if not all(path.exists() for path in required):
        raise ValueError("frozen R020/R021 TSE schedule evidence unavailable")
    folder = OUT / "institutional_evidence"
    folder.mkdir()
    copied = folder / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    if calendar.source_start > date(2021, 1, 1) or calendar.source_end < date(2025, 6, 30):
        raise ValueError("frozen TSE schedule evidence does not cover Development")
    evidence = {"holiday_csv_sha256": digest(copied), "r020_completed_sha256": digest(R020 / "COMPLETED.json"), "r021_completed_sha256": digest(R021 / "COMPLETED.json"), "rule": "TSE open only on weekday not in frozen Cabinet Office holidays, excluding Jan 1-3 and Dec 31; never inferred from OSE observations."}
    write_json(folder / "evidence_manifest.json", evidence)
    return calendar, cast(dict[str, object], evidence | {"evidence_manifest_sha256": digest(folder / "evidence_manifest.json")})


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [bar for key, rows in groups.items() if key not in isolated for bar in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {"parent_data_version": data.data_version, "quarantined_sessions": len(isolated), "quarantined_bars": len(data.bars) - len(included), "included_sessions": len(groups) - len(isolated), "included_bars": len(included), "quarantined_session_list": listed, "quarantined_session_list_hash": canonical_hash(listed), "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included), "rule": "exclude whole (trade_date, session) for any TICK_GRID_VIOLATION"}
    expected = {"parent_data_version": PARENT_HASH, "quarantined_sessions": 45, "quarantined_bars": 27_345, "included_sessions": 2_216, "included_bars": 1_326_086, "quarantined_session_list_hash": QUARANTINE_HASH, "included_tick_grid_violations": 0}
    mismatch = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    audit.update({"expected_match": not mismatch, "mismatches": mismatch})
    if mismatch:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatch}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def implementation() -> dict[str, str]:
    paths = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r043.py"), Path("src/n225m_bt/strategies/precash_cash_conflict_followthrough.py"), Path("tests/test_r043_q001.py")]
    return {str(path): digest(ROOT / path) for path in paths}


def preregistration(source: dict[str, object], inputs: dict[str, object], evidence: dict[str, object], files: dict[str, str]) -> dict[str, object]:
    return {"experiment_id": IDENTIFIER, "study_id": "R043-Q001", "status": "frozen_before_r043_price_statistics_events_or_pnl", "seed": SEED, "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation or unused validation.", "duplicate_review": {"R001_R042": "R024 has the same 08:45--08:59 and 09:00--09:04 windows but uses same-sign confirmation and 09:05--09:30. R031 uses complete night, R041 complete night versus 09:00--09:29, and R042 terminal night state. No registration combines opposite P/C signs, 09:05--10:05 continuation, agreement control and the fixed continuous P/C OLS.", "conclusion": "No equivalent registered specification; execute this one fixed rule only."}, "hypothesis": "When the 08:45--08:59 futures return rP and the 09:00--09:04 futures return rC have opposite signs, following rC from 09:05 to 10:05 has positive post-cost expectancy and exceeds all-day rC-following, agreement-day rC-following, fixed buy/sell and rP-following controls. It tests only a possible short-lived futures price-discovery update, not cash prices, order flow, news or participant identity.", "institution_and_calendar": evidence, "fixed_rule": {"state": "TSE-open d only. Require exactly 15 contiguous eligible day bars 08:45--08:59 and exactly 5 contiguous eligible day bars 09:00--09:04; no observed endpoint substitute. rP=close_0859-open_0845 and rC=close_0904-open_0900. Zero, missing, ineligible, isolated or outside days skip. rP*rC<0 conflict, >0 agreement. No magnitude, gap, night direction, range, weekday, year or volume filter.", "conditions": {"A_conflict": "conflict only sign(rC)", "B_all_cash": "all valid conflict/agreement sign(rC)", "C_agreement": "agreement only sign(rC)", "D_buy": "A event/time fixed long", "E_sell": "A event/time fixed short", "F_precash": "A event/time sign(rP), exactly opposite A", "A2_A3": "A at 2/3 ticks per side", "A_delay": "A signal one scheduled bar late, unchanged absolute exit"}, "execution": "09:04 close signal, next scheduled eligible 09:05 open entry; 10:04 close exit signal, fixed 10:05 open exit. Delayed entry uses 09:06 and never extends exit. One contract, one daily trade and one position; no stop/target/reentry/update/early exit."}, "ols": {"formula": "y=alpha+beta*T+gamma1*(zP-mean(zP))+gamma2*(zC-mean(zC))+gamma3*(zC-mean(zC))^2+eta*U+calendar-year fixed effects+epsilon", "population": "B_all complete eligible Development events; y=sign(rC)*(open_10:05-open_09:05), zP=ln(abs(rP)/open_08:45), zC=ln(abs(rC)/open_09:00), U=1[rP>0]. Each center is computed once over this same population; full rank is mandatory."}, "inputs": {"physical": inputs, "r004_fixed_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa.", "fixed_daily_axis": {"source": str(R012_AXIS.relative_to(ROOT)), "sha256": digest(R012_AXIS), "count": AXIS_COUNT, "rule": "TSE closure, missing, zero, condition failure, cancellation and no trade remain 0 JPY."}, "quality_ceiling": "PASS_LIMITED"}, "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "A3": {"slippage_ticks_per_side": 3, "fee_jpy_per_side": 30}, "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees; never deduct slippage twice."}, "evaluation": {"bootstrap": {"block_length_trade_dates": 20, "repetitions": 10_000, "seed": SEED, "common_indices": True, "noncircular": True, "tail_truncate": True, "percentile": "linear"}, "information_gate": ["B>=800", "A/C>=250", "A long/short>=80", "conflict/agreement x rC direction each>=70"], "pass_requires_all_after_information": ["A Net>0", "A PF>1", "CI lower>0 for A daily mean, A-B, A-C, A-D/E/F and beta", "A2/A3/delay expectancy>0", "2021-24 >=3 positive A years", "positive months>=27/54", "top10 winners removed Net>0"], "decision": "BLOCKED for input/synthetic/execution/accounting/OLS failure; INCONCLUSIVE for information failure; otherwise REJECT on any fixed requirement; all pass is Development-only INVESTIGATE."}, "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": files, "implementation_files_hash": canonical_hash(files), "input_manifest_hash": canonical_hash(inputs)}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}


def selected(condition: Condition, status: str) -> bool:
    return (condition == "B_all_cash" and status in {"conflict", "agreement"}) or (condition in {"A_conflict", "D_buy", "E_sell", "F_precash"} and status == "conflict") or (condition == "C_agreement" and status == "agreement")


def side(condition: Condition, event: dict[str, object]) -> str:
    if condition in {"A_conflict", "B_all_cash", "C_agreement"}:
        return cast(str, event["rC_direction"])
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    return cast(str, event["rP_direction"])


def add_raw_future_return(event: dict[str, object], rows: list[Any]) -> None:
    target = date.fromisoformat(cast(str, event["trade_date"]))
    by_time = {bar.ts_jst: bar for bar in rows}
    start = datetime(target.year, target.month, target.day, 9, 5, tzinfo=JST)
    end = start + timedelta(minutes=60)
    first, last = by_time.get(start), by_time.get(end)
    if first is None or last is None:
        event["raw_return_reason"] = "WINDOW_0905_TO_1005_MISSING"
        return
    if not first.is_eligible or not last.is_eligible or first.trade_date != target or last.trade_date != target:
        event["raw_return_reason"] = "WINDOW_0905_TO_1005_INELIGIBLE"
        return
    if cast(int, event["open_0845_points"]) <= 0 or cast(int, event["open_0900_points"]) <= 0:
        event["raw_return_reason"] = "NONPOSITIVE_LOG_DENOMINATOR"
        return
    adjusted = cast(int, event["rC_sign"]) * (last.open - first.open) * 100
    event.update({"open_0905_points": first.open, "open_1005_points": last.open, "raw_sign_adjusted_0905_1005_jpy": adjusted, "raw_return_reason": "OK"})


def run_condition(data: ResearchData, calendar: TSECashMarketCalendar, engine: BacktestEngine, condition: Condition, groups: dict[tuple[date, Session], list[Any]], isolated: set[tuple[date, Session]], targets: list[str], delay: int = 0) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text_day in targets:
        target = date.fromisoformat(text_day)
        rows = groups.get((target, Session.DAY))
        event = precash_cash_conflict_event(target, rows, calendar, day_quarantined=(target, Session.DAY) in isolated)
        base = cast(str, event["status"])
        event.update({"condition": condition, "base_event_status": base, "variant_delay_minutes": delay})
        if rows is not None and base in {"conflict", "agreement"}:
            add_raw_future_return(event, rows)
        if not selected(condition, base):
            event.update({"status": "skipped", "reason": "CONDITION_FILTER" if base in {"conflict", "agreement"} else event.get("reason")})
            audit[f"skipped_{event['reason']}"] += 1
            records.append(event)
            continue
        if rows is None:
            raise AssertionError("selected R043 event has no day bars")
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(rows, PrecashCashConflictFollowthroughStrategy(f"r043_{condition}", signal, side(condition, event), delay), canonical_hash({"condition": condition, "event": event, "data": data.data_version, "delay": delay}))
        if len(result.trades) > 1:
            raise AssertionError("R043 violated maximum one trade/day")
        audit["canceled_orders"] += result.canceled_orders
        if not result.trades:
            event.update({"status": "eligible_order_unfilled", "reason": "ENGINE_NO_FILL_OR_FIXED_EXIT"})
            audit["eligible_order_unfilled"] += 1
            records.append(event)
            continue
        trade = result.trades[0]
        event.update({"status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_signal_ts_jst": trade.entry_signal_ts.isoformat(), "exit_signal_ts_jst": trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None, "entry_delay_minutes": int((trade.entry_ts - datetime.fromisoformat(cast(str, event["E_planned_entry_jst"]))).total_seconds() // 60), "exit_delay_minutes": int((trade.exit_ts - datetime.fromisoformat(cast(str, event["X_planned_exit_jst"]))).total_seconds() // 60), "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "fees_jpy": trade.fees_jpy, "net_pnl_jpy": trade.net_pnl_jpy})
        trades.append(trade)
        audit["trades"] += 1
        audit[f"exit_{trade.exit_reason.value}"] += 1
        records.append(event)
    ordered = tuple(replace(trade, trade_id=f"trade-{number:06d}") for number, trade in enumerate(sorted(trades, key=lambda row: row.entry_ts), 1))
    return ordered, records, dict(sorted(audit.items()))


def daily(trades: tuple[Trade, ...], axis: list[str]) -> dict[str, int]:
    values = dict.fromkeys(axis, 0)
    for trade in trades:
        values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def write_condition(folder: Path, name: str, trades: tuple[Trade, ...], events: list[dict[str, object]], audit: dict[str, int], bars: list[Any], ticks: int, values: dict[str, int]) -> dict[str, object]:
    folder.mkdir(parents=True)
    metrics = research_metrics(trades, bars)
    write_results(folder, trades, (), {"campaign_id": IDENTIFIER, "condition": name, "cost": f"{ticks} tick/side + 30JPY/side", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "events.json", events)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": list(values), "net_pnl_jpy": list(values.values())}).write_parquet(folder / "daily_net_pnl.parquet")
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    return metrics


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    low, high = floor(point), ceil(point)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def ols_beta(rows: list[dict[str, object]], fixed_means: tuple[float, float] | None = None) -> tuple[float, dict[str, object]]:
    usable = [row for row in rows if row.get("base_event_status") in {"conflict", "agreement"} and row.get("raw_return_reason") == "OK"]
    if not usable:
        raise ValueError("OLS has no complete eligible Development observations")
    zps = [log(abs(float(cast(int, row["rP_points"]))) / float(cast(int, row["open_0845_points"]))) for row in usable]
    zcs = [log(abs(float(cast(int, row["rC_points"]))) / float(cast(int, row["open_0900_points"]))) for row in usable]
    mean_p, mean_c = fixed_means or (fmean(zps), fmean(zcs))
    years = sorted({str(row["trade_date"])[:4] for row in usable})
    matrix: list[list[float]] = []
    response: list[float] = []
    for row, zp, zc in zip(usable, zps, zcs, strict=True):
        centered_c = zc - mean_c
        matrix.append([1.0, 1.0 if row["base_event_status"] == "conflict" else 0.0, zp - mean_p, centered_c, centered_c * centered_c, 1.0 if cast(int, row["rP_sign"]) > 0 else 0.0, *(1.0 if str(row["trade_date"])[:4] == level else 0.0 for level in years[1:])])
        response.append(float(cast(int, row["raw_sign_adjusted_0905_1005_jpy"])))
    width = len(matrix[0])
    normal = [[sum(row[i] * row[j] for row in matrix) for j in range(width)] for i in range(width)]
    target = [sum(row[i] * value for row, value in zip(matrix, response, strict=True)) for i in range(width)]
    rank = 0
    for pivot_col in range(width):
        pivot_row = max(range(pivot_col, width), key=lambda index: abs(normal[index][pivot_col]))
        if abs(normal[pivot_row][pivot_col]) < 1e-10:
            raise ValueError("OLS design matrix is not full rank")
        normal[pivot_col], normal[pivot_row] = normal[pivot_row], normal[pivot_col]
        target[pivot_col], target[pivot_row] = target[pivot_row], target[pivot_col]
        pivot = normal[pivot_col][pivot_col]
        normal[pivot_col] = [value / pivot for value in normal[pivot_col]]
        target[pivot_col] /= pivot
        for row_index in range(width):
            if row_index != pivot_col:
                factor = normal[row_index][pivot_col]
                normal[row_index] = [value - factor * pivot_value for value, pivot_value in zip(normal[row_index], normal[pivot_col], strict=True)]
                target[row_index] -= factor * target[pivot_col]
        rank += 1
    return target[1], {"formula": "y=alpha+beta*T+gamma1*(zP-mean(zP))+gamma2*(zC-mean(zC))+gamma3*(zC-mean(zC))^2+eta*U+calendar-year fixed effects+epsilon", "y": "sign(rC)*(open_10:05-open_09:05), zero-tick pre-fee JPY per contract", "zP": "ln(abs(rP)/open_08:45)", "zC": "ln(abs(rC)/open_09:00)", "means": {"zP": mean_p, "zC": mean_c}, "year_levels": years, "observation_count": len(usable), "design_columns": width, "rank": rank, "full_rank": rank == width, "beta_conflict_jpy": target[1]}


def conditional_mean(records: dict[str, dict[str, int]], axis: list[str], indices: list[int]) -> float:
    values = [records[axis[index]] for index in indices if axis[index] in records]
    if not values:
        raise ValueError("bootstrap condition has no sampled filled observations")
    return fmean(values)


def bootstrap(daily_by: dict[str, dict[str, int]], records_by: dict[str, list[dict[str, object]]], axis: list[str], fixed_means: tuple[float, float]) -> dict[str, object]:
    arrays = {name: [daily_by[name][day] for day in axis] for name in CONDITIONS}
    filled = {name: {cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"]) for row in records_by[name] if row.get("status") == "filled"} for name in ("A_conflict", "B_all_cash", "C_agreement")}
    raw_by_day = {cast(str, row["trade_date"]): row for row in records_by["B_all_cash"] if row.get("raw_return_reason") == "OK"}
    samples: dict[str, list[float]] = {name: [] for name in ("A_daily_mean_net_jpy", "A_minus_B_all_cash_conditional_expectancy_jpy", "A_minus_C_agreement_conditional_expectancy_jpy", "A_minus_D_buy_daily_mean_net_jpy", "A_minus_E_sell_daily_mean_net_jpy", "A_minus_F_precash_daily_mean_net_jpy", "beta_conflict_jpy")}
    rng, count, block = Random(SEED), len(axis), 20
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        indices = indices[:count]
        samples["A_daily_mean_net_jpy"].append(fmean(arrays["A_conflict"][index] for index in indices))
        samples["A_minus_B_all_cash_conditional_expectancy_jpy"].append(conditional_mean(filled["A_conflict"], axis, indices) - conditional_mean(filled["B_all_cash"], axis, indices))
        samples["A_minus_C_agreement_conditional_expectancy_jpy"].append(conditional_mean(filled["A_conflict"], axis, indices) - conditional_mean(filled["C_agreement"], axis, indices))
        for name in ("D_buy", "E_sell", "F_precash"):
            samples[f"A_minus_{name}_daily_mean_net_jpy"].append(fmean(arrays["A_conflict"][index] - arrays[name][index] for index in indices))
        beta, _ = ols_beta([raw_by_day[axis[index]] for index in indices if axis[index] in raw_by_day], fixed_means)
        samples["beta_conflict_jpy"].append(beta)
    estimates = {"A_daily_mean_net_jpy": fmean(arrays["A_conflict"]), "A_minus_B_all_cash_conditional_expectancy_jpy": fmean(filled["A_conflict"].values()) - fmean(filled["B_all_cash"].values()), "A_minus_C_agreement_conditional_expectancy_jpy": fmean(filled["A_conflict"].values()) - fmean(filled["C_agreement"].values()), "A_minus_D_buy_daily_mean_net_jpy": fmean(left - right for left, right in zip(arrays["A_conflict"], arrays["D_buy"], strict=True)), "A_minus_E_sell_daily_mean_net_jpy": fmean(left - right for left, right in zip(arrays["A_conflict"], arrays["E_sell"], strict=True)), "A_minus_F_precash_daily_mean_net_jpy": fmean(left - right for left, right in zip(arrays["A_conflict"], arrays["F_precash"], strict=True))}
    beta, audit = ols_beta(list(raw_by_day.values()), fixed_means)
    estimates["beta_conflict_jpy"] = beta
    return {"method": "20-trade-date moving-block bootstrap with replacement, no wrap, tail truncate", "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10_000, "seed": SEED, "common_indices_all_conditions": True, "percentile_implementation": "linear interpolation at (n-1)*q", "ols": audit, **{name: {"estimate": estimates[name], "ci95_percentile_linear": [percentile(values, .025), percentile(values, .975)]} for name, values in samples.items()}}


def group_diagnostics(events: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for state in ("conflict", "agreement"):
        for direction in ("long", "short"):
            rows = [row for row in events if row.get("base_event_status") == state and row.get("rC_direction") == direction]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = sum(cast(int, row.get("net_pnl_jpy", 0)) for row in filled)
            output[f"{state}_rC_{direction}"] = {"eligible_day_count": len(rows), "trade_count": len(filled), "gross_pnl_jpy": sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in filled), "slippage_cost_jpy": sum(cast(int, row.get("slippage_cost_jpy", 0)) for row in filled), "fees_jpy": sum(cast(int, row.get("fees_jpy", 0)) for row in filled), "net_pnl_jpy": net, "expectancy_jpy": net / len(filled) if filled else None}
    return output


def decomposition(trades: tuple[Trade, ...], groups: dict[tuple[date, Session], list[Any]]) -> dict[str, object]:
    first, second, unavailable = 0, 0, 0
    for trade in trades:
        midpoint = datetime(trade.trade_date.year, trade.trade_date.month, trade.trade_date.day, 9, 30, tzinfo=JST)
        row = {bar.ts_jst: bar for bar in groups[(trade.trade_date, Session.DAY)]}.get(midpoint)
        if row is None or not row.is_eligible:
            unavailable += 1
            continue
        first += (row.open - trade.entry_fill_price) * trade.side.sign * trade.qty * 100
        second += (trade.exit_fill_price - row.open) * trade.side.sign * trade.qty * 100
    return {"method": "A fill-to-fill Gross is split at the exact 09:30 open; the two components sum to Gross when the midpoint is eligible.", "gross_0905_0930_jpy": first, "gross_0930_1005_jpy": second, "gross_sum_jpy": first + second, "unavailable_trade_count": unavailable, "all_available": unavailable == 0}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r043_q001_precash_cash_conflict_followthrough.py')

    if OUT.exists():
        raise FileExistsError(f"append-only experiment output already exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    inputs, files = input_manifest(data_config.gold_root), implementation()
    calendar, evidence = freeze_tse_evidence()
    plan = preregistration(source, inputs, evidence, files)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r043_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": SEED, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r043_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r043.py", "src/n225m_bt/strategies/precash_cash_conflict_followthrough.py", "tests/test_r043_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r043.py", "src/n225m_bt/strategies/precash_cash_conflict_followthrough.py"]}
    validation: dict[str, Any] = {"coverage": "TSE/OSE distinction, calendar/trade date, exact 15/5 windows, zeros, conflict/agreement, missing/isolation/outside, 09:04 causality/prefix, common paths, opposite A/F, next entry/fixed exit/delay/accounting/OOS-Holdout lock"}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R043 validation failed before Development price access")
    axis = fixed_axis()
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    if set(axis) != {bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY} - {day.isoformat() for day, session in isolated if session is Session.DAY}:
        raise ValueError("fixed 1,111-date R012 axis does not equal Development day universe excluding isolation")
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_trade_dates": axis, "physical_io": "Development selected normalized Parquet only; OOS/Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only"})
    groups = session_groups(view.bars)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    records_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    for condition in CONDITIONS:
        trades, records, audit = run_condition(view, calendar, engine, condition, groups, isolated, axis)
        values = daily(trades, axis)
        metrics = write_condition(OUT / condition, condition, trades, records, audit, view.bars, 1, values)
        trades_by[condition], records_by[condition], daily_by[condition] = trades, records, values
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    for name, (ticks, delay) in VARIANTS.items():
        config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})})
        trades, records, audit = run_condition(view, calendar, BacktestEngine(instrument.instrument.to_spec(), config, classifier), "A_conflict", groups, isolated, axis, delay)
        metrics = write_condition(OUT / name, name, trades, records, audit, view.bars, ticks, daily(trades, axis))
        trades_by[name], records_by[name] = trades, records
        results[name] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    path = ("side", "E_planned_entry_jst", "X_planned_exit_jst", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")
    by_day = {name: {cast(str, row["trade_date"]): row for row in records} for name, records in records_by.items()}
    checks = {"conflict_A_B_path_equal": all(all(by_day["A_conflict"][day].get(field) == by_day["B_all_cash"][day].get(field) for field in path) for day in axis if by_day["A_conflict"][day].get("status") == "filled"), "agreement_B_C_path_equal": all(all(by_day["B_all_cash"][day].get(field) == by_day["C_agreement"][day].get(field) for field in path) for day in axis if by_day["C_agreement"][day].get("status") == "filled"), "A_F_opposite_side": all(by_day["A_conflict"][day].get("side") != by_day["F_precash"][day].get("side") for day in axis if by_day["A_conflict"][day].get("status") == "filled"), "A_variants_event_side_equal": all(all(by_day["A_conflict"][day].get(field) == by_day[name][day].get(field) for field in ("rP_points", "rC_points", "side")) for name in VARIANTS for day in axis), "one_trade_per_day": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in trades_by.values()), "fixed_exit_delay_nonextended": all(row.get("exit_reason") == ExitReason.SIGNAL.value and row.get("exit_delay_minutes") == 0 and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0) for name, rows in records_by.items() for row in rows if row.get("status") == "filled"), "no_unfilled_selected_events": not any(row.get("status") == "eligible_order_unfilled" for rows in records_by.values() for row in rows), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades), "slippage_not_double_deducted": all(trade.fees_jpy == 60 for trades in trades_by.values() for trade in trades)}
    post = {"status": "PASS" if all(checks.values()) else "BLOCKED", "checks": checks, "policy": "No successful-fill intersection is selected; every execution or accounting exception blocks."}
    write_json(OUT / "post_execution_validation.json", post)
    groups4 = group_diagnostics(records_by["B_all_cash"])
    raw_rows = [row for row in records_by["B_all_cash"] if row.get("base_event_status") in {"conflict", "agreement"}]
    if any(row.get("raw_return_reason") != "OK" for row in raw_rows):
        post["status"] = "BLOCKED"
        write_json(OUT / "post_execution_validation.json", post)
    beta, ols_audit = ols_beta(raw_rows)
    means = (cast(float, cast(dict[str, object], ols_audit["means"])["zP"]), cast(float, cast(dict[str, object], ols_audit["means"])["zC"]))
    boot = bootstrap(daily_by, records_by, axis, means)
    decomp = decomposition(trades_by["A_conflict"], groups)
    a_metrics = cast(dict[str, Any], results["A_conflict"])["metrics"]
    a_overall = cast(dict[str, Any], a_metrics)["overall"]
    years = {str(year): sum(daily_by["A_conflict"][day] for day in axis if day.startswith(str(year))) for year in range(2021, 2026)}
    months = {f"{year}-{month:02d}": sum(daily_by["A_conflict"][day] for day in axis if day.startswith(f"{year}-{month:02d}")) for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    def lower(name: str) -> bool:
        return cast(list[float], boot[name]["ci95_percentile_linear"])[0] > 0
    information = {"B_trade_count_at_least_800": len(trades_by["B_all_cash"]) >= 800, "A_trade_count_at_least_250": len(trades_by["A_conflict"]) >= 250, "C_trade_count_at_least_250": len(trades_by["C_agreement"]) >= 250, "A_long_at_least_80": sum(trade.side.value == "long" for trade in trades_by["A_conflict"]) >= 80, "A_short_at_least_80": sum(trade.side.value == "short" for trade in trades_by["A_conflict"]) >= 80, "four_conflict_agreement_rC_direction_groups_at_least_70": all(cast(int, value["eligible_day_count"]) >= 70 for value in groups4.values())}
    pass_gates = {"A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_daily_mean_CI_lower_positive": lower("A_daily_mean_net_jpy"), "A_minus_B_CI_lower_positive": lower("A_minus_B_all_cash_conditional_expectancy_jpy"), "A_minus_C_CI_lower_positive": lower("A_minus_C_agreement_conditional_expectancy_jpy"), "A_minus_D_CI_lower_positive": lower("A_minus_D_buy_daily_mean_net_jpy"), "A_minus_E_CI_lower_positive": lower("A_minus_E_sell_daily_mean_net_jpy"), "A_minus_F_CI_lower_positive": lower("A_minus_F_precash_daily_mean_net_jpy"), "beta_CI_lower_positive": lower("beta_conflict_jpy"), "A2_expectancy_positive": cast(dict[str, Any], results["A2_2tick"])["metrics"]["overall"]["expectancy_jpy"] > 0, "A3_expectancy_positive": cast(dict[str, Any], results["A3_3tick"])["metrics"]["overall"]["expectancy_jpy"] > 0, "A_delay_expectancy_positive": cast(dict[str, Any], results["A_delay"])["metrics"]["overall"]["expectancy_jpy"] > 0, "at_least_three_positive_2021_2024_years": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3, "positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "net_excluding_top10_positive": cast(dict[str, Any], a_metrics)["concentration"]["net_excluding_top10_jpy"] > 0, "gross_decomposition_complete": cast(bool, decomp["all_available"])}
    decision = "BLOCKED" if post["status"] != "PASS" or not cast(bool, ols_audit["full_rank"]) else "INCONCLUSIVE" if not all(information.values()) else "INVESTIGATE" if all(pass_gates.values()) else "REJECT"
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": axis, **{name: [daily_by[name][day] for day in axis] for name in CONDITIONS}, "no_trade": "0", "day_session_quarantined": "excluded", "TSE_closed_missing_zero_condition_cancel": "zero"})
    write_json(OUT / "event_sign_diagnostics.json", groups4)
    write_json(OUT / "gross_time_decomposition.json", decomp)
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "information_gates": information, "pass_gates": pass_gates, "A_direction_counts": {"long": sum(trade.side.value == "long" for trade in trades_by["A_conflict"]), "short": sum(trade.side.value == "short" for trade in trades_by["A_conflict"])}, "A_year_net_jpy": years, "A_month_net_jpy": months, "positive_months_of_54": sum(value > 0 for value in months.values()), "conditions": results, "four_sign_groups": groups4, "ols": ols_audit | {"beta_conflict_jpy": beta}, "gross_time_decomposition": decomp, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
