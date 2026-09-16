"""Execute the preregistered Development-only R041-Q001 experiment."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, Literal, cast
from zipfile import ZIP_DEFLATED, ZipFile

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
from n225m_bt.research.r041 import night_conflict_open_followthrough_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.night_conflict_open_followthrough import (
    NightConflictOpenFollowthroughStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r041-q001-20260914-night-conflict-opening-followthrough-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
R021 = ROOT / "results" / "research" / "r021-q001-20260914-opening-cash-close-followthrough-05"
R012_AXIS = ROOT / "results" / "research" / "r012-q001-20260914-night-direction-followthrough-01" / "daily_net_pnl_aligned.json"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20260919
AXIS_COUNT = 1_111
Condition = Literal["A_conflict", "B_all_open", "C_agreement", "D_buy", "E_sell", "F_night"]
CONDITIONS: tuple[Condition, ...] = ("A_conflict", "B_all_open", "C_agreement", "D_buy", "E_sell", "F_night")
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
    return {"status": "frozen_before_r041_price_statistics_events_or_pnl", "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.", "trade_date_range": ["2021-01-01", "2025-06-30"], "files": files, "files_hash": canonical_hash(files)}


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
    paths = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r041.py"), Path("src/n225m_bt/strategies/night_conflict_open_followthrough.py"), Path("tests/test_r041_q001.py")]
    return {str(path): digest(ROOT / path) for path in paths}


def preregistration(source: dict[str, object], inputs: dict[str, object], evidence: dict[str, object], files: dict[str, str]) -> dict[str, object]:
    return {"experiment_id": IDENTIFIER, "study_id": "R041-Q001", "status": "frozen_before_r041_price_statistics_events_or_pnl", "seed": SEED, "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation or unused validation.", "duplicate_review": {"R001_R040": "R031 has complete night S with 09:00--09:04 Q same-sign confirmation and 09:05--10:05 execution; R024 uses 08:45--08:59; R021 uses the opening direction before cash close. No registration combines full same-trade-date normal night rN, 09:00--09:29 rO opposite signs, 09:30--10:30 rO continuation, and a prior-60-day diagnostic median.", "conclusion": "No equivalent registered specification; execute this one fixed rule only."}, "hypothesis": "When the complete corresponding normal night return rN and the 09:00--09:29 futures return rO have opposite signs, following rO for the next 60 minutes has positive post-cost expectancy and exceeds unconditional rO-following, agreement-day rO-following, fixed buy/sell and rN-following controls. It tests a possible short-lived price-discovery update only, not cash prices, order flow, news or participant identity.", "institution_and_calendar": evidence, "fixed_rule": {"reference": "TSE-open d only. Select exactly one scheduled night with same trade_date; require every scheduled continuous normal night minute, never auction/force-flat/observed endpoint or older-night fallback. rN=final normal close-first normal open. Require exactly 30 eligible day bars 09:00--09:29; rO=09:29 close-09:00 open. rN/rO zero skips. rN*rO<0 conflict, >0 agreement. 08:45--08:59 and gap are unused.", "diagnostic_magnitude": "From exactly the preceding 60 scheduled TSE days before d, do not backfill invalid/outside/quarantined/missing/zero rO. With >=50 valid abs(rO), QM is ascending ceil(.5*n)-th value; ties are low (abs(rO)<=QM), target excluded. Low/high is diagnostic only, never a filter.", "conditions": {"A_conflict": "conflict only sign(rO)", "B_all_open": "all valid conflict/agreement sign(rO)", "C_agreement": "agreement only sign(rO)", "D_buy": "A event/time fixed long", "E_sell": "A event/time fixed short", "F_night": "A event/time sign(rN), exactly opposite A", "A2_A3": "A at 2/3 ticks per side", "A_delay": "A signal one scheduled bar late, unchanged absolute exit"}, "execution": "09:29 close signal, next scheduled eligible 09:30 open entry; 10:29 close exit signal, fixed 10:30 open exit. Delayed entry uses 09:31 and never extends exit. One contract, one daily trade and one position; no stop/target/reentry/update/early exit.", "prohibited": ["magnitude or any other filter", "window/direction/hold rescue", "WFA", "OOS", "Final Holdout"]}, "inputs": {"physical": inputs, "r004_fixed_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; list hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa.", "fixed_daily_axis": {"source": str(R012_AXIS.relative_to(ROOT)), "sha256": digest(R012_AXIS), "count": AXIS_COUNT, "rule": "day-isolated dates excluded; TSE closure, missing, zero, rolling failure, condition failure, cancel and no trade remain 0 JPY."}, "quality_ceiling": "PASS_LIMITED"}, "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "A3": {"slippage_ticks_per_side": 3, "fee_jpy_per_side": 30}, "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees; never deduct slippage twice."}, "evaluation": {"bootstrap": {"block_length_trade_dates": 20, "repetitions": 10_000, "seed": SEED, "common_indices": True, "noncircular": True, "tail_truncate": True, "percentile": "linear"}, "information_gate": ["B>=800", "A/C>=300", "A long/short>=100", "rN/rO state x rO direction four groups>=75", "each low/high x conflict/agreement group>=80 for DeltaM"], "pass_requires_all_after_information": ["A Net>0", "A PF>1", "CI lower>0 for A daily mean, A-B, A-C, A-D/E/F and DeltaM", "A2/A3/delay expectancy>0", "2021-24 >=3 positive A years", "positive months>=27/54", "top10 winners removed Net>0"], "decision": "BLOCKED for input/synthetic/execution/accounting failure; INCONCLUSIVE for any information failure; otherwise REJECT on any fixed requirement; all pass is INVESTIGATE only."}, "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": files, "implementation_files_hash": canonical_hash(files), "input_manifest_hash": canonical_hash(inputs)}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}


def event_for_day(classifier: CalendarClassifier, calendar: TSECashMarketCalendar, target: date, groups: dict[tuple[date, Session], list[Any]], isolated: set[tuple[date, Session]], tse_schedule: list[date]) -> dict[str, object]:
    index = tse_schedule.index(target) if target in tse_schedule else -1
    history = [] if index < 60 else [(item, groups.get((item, Session.DAY)), (item, Session.DAY) in isolated) for item in tse_schedule[index - 1:index - 61:-1]]
    return night_conflict_open_followthrough_event(classifier, calendar, target, groups.get((target, Session.DAY)), groups.get((target, Session.NIGHT)), history, day_quarantined=(target, Session.DAY) in isolated, night_quarantined=(target, Session.NIGHT) in isolated)


def selected(condition: Condition, status: str) -> bool:
    return (condition == "B_all_open" and status in {"conflict", "agreement"}) or (condition in {"A_conflict", "D_buy", "E_sell", "F_night"} and status == "conflict") or (condition == "C_agreement" and status == "agreement")


def side(condition: Condition, event: dict[str, object]) -> str:
    if condition in {"A_conflict", "B_all_open", "C_agreement"}:
        return cast(str, event["rO_direction"])
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    return cast(str, event["rN_direction"])


def run_condition(data: ResearchData, calendar: TSECashMarketCalendar, engine: BacktestEngine, condition: Condition, groups: dict[tuple[date, Session], list[Any]], isolated: set[tuple[date, Session]], targets: list[str], tse_schedule: list[date], delay: int = 0) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text_day in targets:
        target = date.fromisoformat(text_day)
        event = event_for_day(engine.classifier, calendar, target, groups, isolated, tse_schedule)
        base = cast(str, event["status"])
        event.update({"condition": condition, "base_event_status": base, "variant_delay_minutes": delay})
        if not selected(condition, base):
            event.update({"status": "skipped", "reason": "CONDITION_FILTER" if base in {"conflict", "agreement"} else event.get("reason")})
            audit[f"skipped_{event['reason']}"] += 1
            records.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(groups[(target, Session.DAY)], NightConflictOpenFollowthroughStrategy(f"r041_{condition}", signal, side(condition, event), delay), canonical_hash({"condition": condition, "event": event, "data": data.data_version, "delay": delay}))
        if len(result.trades) > 1:
            raise AssertionError("R041 violated maximum one trade/day")
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


def bootstrap(daily_by: dict[str, dict[str, int]], records_by: dict[str, list[dict[str, object]]], axis: list[str], multiplier: int) -> dict[str, object]:
    arrays = {name: [daily_by[name][day] for day in axis] for name in CONDITIONS}
    filled = {name: {cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"]) for row in records_by[name] if row.get("status") == "filled"} for name in ("A_conflict", "B_all_open", "C_agreement")}
    reference = records_by["B_all_open"]
    measure: dict[str, dict[str, int]] = {}
    for row in reference:
        if row.get("base_event_status") not in {"conflict", "agreement"} or row.get("opening_magnitude_layer") not in {"low", "high"}:
            continue
        # Engine fills are slippage-inclusive. DeltaM is separately a 0-tick, pre-fee price return.
        measure[cast(str, row["trade_date"])] = {"layer": 0 if row["opening_magnitude_layer"] == "low" else 1, "state": 0 if row["base_event_status"] == "conflict" else 1, "adjusted_return_jpy": cast(int, row.get("raw_adjusted_future_return_jpy", 0))}
    samples: dict[str, list[float]] = {name: [] for name in ("A_daily_mean_net_jpy", "A_minus_B_all_open_conditional_expectancy_jpy", "A_minus_C_agreement_conditional_expectancy_jpy", "A_minus_D_buy_daily_mean_net_jpy", "A_minus_E_sell_daily_mean_net_jpy", "A_minus_F_night_daily_mean_net_jpy", "DeltaM_jpy")}
    unavailable_delta = 0
    rng, count, block = Random(SEED), len(axis), 20
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        indices = indices[:count]
        samples["A_daily_mean_net_jpy"].append(fmean(arrays["A_conflict"][index] for index in indices))
        for control in ("D_buy", "E_sell", "F_night"):
            samples[f"A_minus_{control}_daily_mean_net_jpy"].append(fmean(arrays["A_conflict"][index] - arrays[control][index] for index in indices))
        for control in ("B_all_open", "C_agreement"):
            a_values = [filled["A_conflict"][axis[index]] for index in indices if axis[index] in filled["A_conflict"]]
            c_values = [filled[control][axis[index]] for index in indices if axis[index] in filled[control]]
            samples[f"A_minus_{control}_conditional_expectancy_jpy"].append(fmean(a_values) - fmean(c_values) if a_values and c_values else 0.0)
        cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        for index in indices:
            item = measure.get(axis[index])
            if item is not None:
                cells[(item["layer"], item["state"])].append(item["adjusted_return_jpy"])
        if all(cells[(layer, state)] for layer in (0, 1) for state in (0, 1)):
            samples["DeltaM_jpy"].append(fmean(fmean(cells[(layer, 0)]) - fmean(cells[(layer, 1)]) for layer in (0, 1)))
        else:
            unavailable_delta += 1
    estimates: dict[str, float | None] = {"A_daily_mean_net_jpy": fmean(arrays["A_conflict"]), "A_minus_B_all_open_conditional_expectancy_jpy": fmean(filled["A_conflict"].values()) - fmean(filled["B_all_open"].values()), "A_minus_C_agreement_conditional_expectancy_jpy": fmean(filled["A_conflict"].values()) - fmean(filled["C_agreement"].values()), "A_minus_D_buy_daily_mean_net_jpy": fmean(left - right for left, right in zip(arrays["A_conflict"], arrays["D_buy"], strict=True)), "A_minus_E_sell_daily_mean_net_jpy": fmean(left - right for left, right in zip(arrays["A_conflict"], arrays["E_sell"], strict=True)), "A_minus_F_night_daily_mean_net_jpy": fmean(left - right for left, right in zip(arrays["A_conflict"], arrays["F_night"], strict=True)), "DeltaM_jpy": None}
    actual_cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for item in measure.values():
        actual_cells[(item["layer"], item["state"])].append(item["adjusted_return_jpy"])
    if all(actual_cells[(layer, state)] for layer in (0, 1) for state in (0, 1)):
        estimates["DeltaM_jpy"] = fmean(fmean(actual_cells[(layer, 0)]) - fmean(actual_cells[(layer, 1)]) for layer in (0, 1))
    return {"method": "noncircular moving-block bootstrap with replacement, common 20 trade_date indices, tail truncate, linear percentiles; conditional differences recompute sum/count each replicate", "repetitions": 10_000, "seed": SEED, "DeltaM_unavailable_replicates": unavailable_delta, **{name: {"estimate": estimates[name], "ci95_percentile_linear": [percentile(values, .025), percentile(values, .975)] if values else None} for name, values in samples.items()}}


def attach_raw_returns(records: list[dict[str, object]], groups: dict[tuple[date, Session], list[Any]], multiplier: int) -> None:
    for row in records:
        if row.get("base_event_status") not in {"conflict", "agreement"}:
            continue
        target = date.fromisoformat(cast(str, row["trade_date"]))
        by_time = {bar.ts_jst: bar for bar in groups.get((target, Session.DAY), [])}
        start = datetime(target.year, target.month, target.day, 9, 30, tzinfo=next(iter(by_time.values())).ts_jst.tzinfo) if by_time else None
        end = start.replace(hour=10, minute=30) if start else None
        if start and end and start in by_time and end in by_time:
            row["raw_future_return_points"] = by_time[end].open - by_time[start].open
            row["raw_adjusted_future_return_jpy"] = cast(int, row["raw_future_return_points"]) * cast(int, row["rO_sign"]) * multiplier
        else:
            row["raw_return_reason"] = "FIXED_0930_1030_OPEN_MISSING"


def stratum_table(records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for layer in ("low", "high"):
        for status in ("conflict", "agreement"):
            rows = [row for row in records if row.get("opening_magnitude_layer") == layer and row.get("base_event_status") == status]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            output[f"{layer}_{status}"] = {"event_count": len(rows), "trade_count": len(filled), "gross_pnl_jpy": sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in filled), "fees_jpy": sum(cast(int, row.get("fees_jpy", 0)) for row in filled), "net_pnl_jpy": sum(net), "expectancy_jpy": fmean(net) if net else None, "raw_adjusted_future_return_mean_jpy": fmean(cast(int, row["raw_adjusted_future_return_jpy"]) for row in rows if "raw_adjusted_future_return_jpy" in row) if any("raw_adjusted_future_return_jpy" in row for row in rows) else None}
    return output


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r041_q001_night_conflict_open_followthrough.py')

    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order) != (1, 30, 10, False):
        raise ValueError("active execution/cost contract differs from R041 frozen specification")
    calendar, evidence = freeze_tse_evidence()
    source, files, inputs = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"), implementation(), input_manifest(data_config.gold_root)
    with ZipFile(OUT / "r041_implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT / name, name)
    plan = preregistration(source, inputs, evidence, files)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r041_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": SEED, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r041_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r041.py", "src/n225m_bt/strategies/night_conflict_open_followthrough.py", "tests/test_r041_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r041.py", "src/n225m_bt/strategies/night_conflict_open_followthrough.py"]}
    validation: dict[str, object] = {"coverage": "schedule night-to-day/TSE dates, calendar_date/trade_date, full normal night, 30 bars, rN/rO signs/zero/conflict/agreement, preceding-60/no-backfill/50/QM/tie/layers, missing/isolation/outside/prefix, next entry/fixed exit/delay/accounting/OOS-Holdout lock"}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(cast(dict[str, object], validation[name])["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R041 synthetic/static gate failed before Development price access")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    axis = fixed_axis()
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    if set(axis) != {bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY} - {day.isoformat() for day, session in isolated if session is Session.DAY}:
        raise ValueError("R041 fixed daily axis differs from Development day universe excluding isolation")
    groups = session_groups(view.bars)
    tse_schedule = [item.trade_date for item in classifier.exchange_calendar.trading_days() if calendar.is_open(item.trade_date)]
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_trade_dates": axis, "fixed_target_count": len(axis), "tse_schedule_source": "frozen Cabinet Office evidence + versioned OSE schedule", "physical_io": "Development normalized Parquet only; OOS and Final Holdout never selected"})
    all_day_bars = [bar for text in axis for bar in groups.get((date.fromisoformat(text), Session.DAY), [])]
    trades_by: dict[str, tuple[Trade, ...]] = {}
    records_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, calendar, engine, condition, groups, isolated, axis, tse_schedule)
        attach_raw_returns(events, groups, instrument.instrument.contract_multiplier)
        values = daily(trades, axis)
        metrics = write_condition(OUT / condition, condition, trades, events, audit, all_day_bars, 1, values)
        trades_by[condition], records_by[condition], daily_by[condition] = trades, events, values
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    for variant, (ticks, delay) in VARIANTS.items():
        stress = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})})
        trades, events, audit = run_condition(view, calendar, BacktestEngine(instrument.instrument.to_spec(), stress, classifier), "A_conflict", groups, isolated, axis, tse_schedule, delay)
        attach_raw_returns(events, groups, instrument.instrument.contract_multiplier)
        values = daily(trades, axis)
        metrics = write_condition(OUT / variant, variant, trades, events, audit, all_day_bars, ticks, values)
        trades_by[variant], records_by[variant], daily_by[variant] = trades, events, values
        results[variant] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    by_day = {name: {cast(str, row["trade_date"]): row for row in rows} for name, rows in records_by.items()}
    path = ("side", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")
    checks = {"conflict_A_B_path_equal": all(all(by_day["A_conflict"][day].get(field) == by_day["B_all_open"][day].get(field) for field in path) for day in axis if by_day["A_conflict"][day].get("status") == "filled"), "agreement_B_C_path_equal": all(all(by_day["B_all_open"][day].get(field) == by_day["C_agreement"][day].get(field) for field in path) for day in axis if by_day["C_agreement"][day].get("status") == "filled"), "A_F_opposite_side": all(by_day["A_conflict"][day].get("side") != by_day["F_night"][day].get("side") for day in axis if by_day["A_conflict"][day].get("status") == "filled"), "A_variants_event_side_equal": all(all(by_day["A_conflict"][day].get(field) == by_day[name][day].get(field) for field in ("rN_points", "rO_points", "QM_abs_rO_points", "opening_magnitude_layer", "side")) for name in VARIANTS for day in axis), "one_trade_per_day": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in trades_by.values()), "fixed_exit_delay_nonextended": all(row.get("exit_reason") == ExitReason.SIGNAL.value and row.get("exit_delay_minutes") == 0 and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0) for name, rows in records_by.items() for row in rows if row.get("status") == "filled"), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades), "slippage_not_double_deducted": all(trade.fees_jpy == 60 for name, trades in trades_by.items() if name not in VARIANTS for trade in trades)}
    execution = {"status": "PASS" if all(checks.values()) else "BLOCKED", "checks": checks, "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees."}
    write_json(OUT / "execution_accounting_audit.json", execution)
    boot = bootstrap(daily_by, records_by, axis, instrument.instrument.contract_multiplier)
    strata = stratum_table(records_by["B_all_open"])
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "strata.json", strata)
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": axis, "no_trade": "0 JPY", "series": {name: [daily_by[name][day] for day in axis] for name in records_by}})
    a_metrics = cast(dict[str, Any], results["A_conflict"])["metrics"]
    years = {str(year): sum(daily_by["A_conflict"][day] for day in axis if day.startswith(str(year))) for year in range(2021, 2026)}
    months = {f"{year}-{month:02d}": sum(daily_by["A_conflict"][day] for day in axis if day.startswith(f"{year}-{month:02d}")) for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    four_sign = {f"rN_{n}_rO_{o}": len([row for row in records_by["B_all_open"] if row.get("rN_sign") == n and row.get("rO_sign") == o]) for n in (-1, 1) for o in (-1, 1)}
    information = {"B_trade_count_at_least_800": len(trades_by["B_all_open"]) >= 800, "A_trade_count_at_least_300": len(trades_by["A_conflict"]) >= 300, "C_trade_count_at_least_300": len(trades_by["C_agreement"]) >= 300, "A_long_at_least_100": sum(trade.side.value == "long" for trade in trades_by["A_conflict"]) >= 100, "A_short_at_least_100": sum(trade.side.value == "short" for trade in trades_by["A_conflict"]) >= 100, "four_rN_rO_direction_groups_at_least_75": all(value >= 75 for value in four_sign.values()), "DeltaM_low_high_conflict_agreement_each_at_least_80": all(cast(int, value["event_count"]) >= 80 for value in strata.values())}
    def lower(name: str) -> bool:
        values = cast(dict[str, object], boot[name])["ci95_percentile_linear"]
        return values is not None and cast(list[float], values)[0] > 0
    overall = cast(dict[str, Any], a_metrics)["overall"]
    gates = information | {"A_net_positive": overall["net_pnl_jpy"] > 0, "A_profit_factor_above_one": overall["profit_factor"] is not None and overall["profit_factor"] > 1, "all_requested_ci_lowers_positive": all(lower(name) for name in ("A_daily_mean_net_jpy", "A_minus_B_all_open_conditional_expectancy_jpy", "A_minus_C_agreement_conditional_expectancy_jpy", "A_minus_D_buy_daily_mean_net_jpy", "A_minus_E_sell_daily_mean_net_jpy", "A_minus_F_night_daily_mean_net_jpy", "DeltaM_jpy")), "A2_A3_delay_expectancy_positive": all(cast(dict[str, Any], results[name])["metrics"]["overall"]["expectancy_jpy"] is not None and cast(dict[str, Any], results[name])["metrics"]["overall"]["expectancy_jpy"] > 0 for name in VARIANTS), "at_least_three_positive_years_2021_2024": sum(value > 0 for year, value in years.items() if year != "2025") >= 3, "positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "top10_winners_removed_net_positive": cast(dict[str, Any], a_metrics)["concentration"]["net_excluding_top10_jpy"] > 0}
    decision = "BLOCKED" if execution["status"] != "PASS" else "INCONCLUSIVE" if not all(information.values()) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "information_gate": information, "gates": gates, "conditions": results, "A_year_net_jpy": years, "A_month_net_jpy": months, "rN_rO_sign_groups": four_sign, "strata": strata, "A_year_month_side_metrics": cast(dict[str, Any], a_metrics)["segments"], "scope": "Development only; 2025 Jan-Jun partial; no WFA/OOS/Final Holdout."})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
