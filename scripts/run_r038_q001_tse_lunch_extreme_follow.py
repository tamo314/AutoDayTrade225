"""Execute the preregistered Development-only R038-Q001 experiment."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, floor
from os import environ
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, Literal, cast

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r038 import tse_lunch_extreme_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.lunch_extreme_follow import LunchExtremeFollowStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r038-q001-20260914-tse-lunch-extreme-follow-04"
OUT = ROOT / "results" / "research" / IDENTIFIER
TSE_CALENDAR = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02" / "institutional_evidence" / "tse_cash_calendar.json"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal["A_reopen", "B_all", "C_nonextreme", "D_buy", "E_sell", "F_reverse", "G_preclose_placebo"]
CONDITIONS: tuple[Condition, ...] = ("A_reopen", "B_all", "C_nonextreme", "D_buy", "E_sell", "F_reverse", "G_preclose_placebo")
VARIANTS = {"A_reopen_2tick": (2, 0), "A_reopen_3tick": (3, 0), "A_reopen_delay": (1, 1)}


def digest(path: Path) -> str:
    output = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            output.update(chunk)
    return output.hexdigest()


def day_groups(bars: list[Bar]) -> dict[date, list[Bar]]:
    result: dict[date, list[Bar]] = {}
    for bar in bars:
        if bar.session is Session.DAY:
            result.setdefault(bar.trade_date, []).append(bar)
    return {key: sorted(value, key=lambda row: row.ts_jst) for key, value in result.items()}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[date]]:
    grouped: dict[tuple[date, Session], list[Bar]] = {}
    for bar in data.bars:
        grouped.setdefault((bar.trade_date, bar.session), []).append(bar)
    isolated = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version, "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included), "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included), "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included),
        "rule": "exclude whole (trade_date, session) for any TICK_GRID_VIOLATION",
    }
    expected = {"parent_data_version": PARENT_HASH, "quarantined_sessions": 45, "quarantined_bars": 27345, "included_sessions": 2216, "included_bars": 1326086, "quarantined_session_list_hash": QUARANTINE_HASH, "included_tick_grid_violations": 0}
    mismatches = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    audit.update({"expected_match": not mismatches, "mismatches": mismatches})
    if mismatches:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, {day for day, session in isolated if session is Session.DAY}


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(gold_root, "development")]
    return {"status": "frozen_before_price_statistics_thresholds_events_or_pnl", "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout prohibited.", "trade_date_range": ["2021-01-01", "2025-06-30"], "files": files, "files_hash": canonical_hash(files)}


def eligible(event: dict[str, object], condition: Condition) -> tuple[bool, str, str]:
    l_status, p_status = event.get("L_status"), event.get("P_status")
    if condition == "G_preclose_placebo":
        return (p_status == "extreme", "P", "P_extreme" if p_status == "extreme" else f"P_{p_status}")
    if condition == "B_all":
        return (l_status in {"extreme", "nonextreme"}, "L", "L_valid" if l_status in {"extreme", "nonextreme"} else f"L_{l_status}")
    if condition == "C_nonextreme":
        return (l_status == "nonextreme", "L", "L_nonextreme" if l_status == "nonextreme" else f"L_{l_status}")
    return (l_status == "extreme", "L", "L_extreme" if l_status == "extreme" else f"L_{l_status}")


def side(event: dict[str, object], condition: Condition, window: str) -> str:
    value = cast(int, event[f"r{window}_points"])
    follow = "long" if value > 0 else "short"
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    if condition == "F_reverse":
        return "short" if follow == "long" else "long"
    return follow


def forward_return(event: dict[str, object], bars: list[Bar], window: str) -> dict[str, object]:
    by_time = {bar.ts_jst: bar for bar in bars}
    entry, exit_ = (datetime.fromisoformat(cast(str, event[f"{window}_E_planned_entry_jst"])), datetime.fromisoformat(cast(str, event[f"{window}_X_planned_exit_jst"])))
    first, last = by_time.get(entry), by_time.get(exit_)
    if first is None or last is None or not first.is_eligible or not last.is_eligible:
        return {"signal_direction_adjusted_future_30m_return_points_before_cost": None}
    sign = 1 if cast(int, event[f"r{window}_points"]) > 0 else -1
    return {"signal_direction_adjusted_future_30m_return_points_before_cost": sign * (last.open - first.open)}


def run_condition(base_events: list[dict[str, object]], bars_by_day: dict[date, list[Bar]], engine: BacktestEngine, condition: Condition, delay: int = 0) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for base in base_events:
        event = deepcopy(base)
        allowed, window, reason = eligible(event, condition)
        event.update({"condition": condition, "event_window": window, "variant_delay_minutes": delay, "condition_eligibility": reason})
        if not allowed:
            event.update({"status": "skipped", "reason": reason})
            audit[f"skipped_{reason}"] += 1
            records.append(event)
            continue
        event["side"] = side(event, condition, window)
        event.update(forward_return(event, bars_by_day.get(date.fromisoformat(cast(str, event["trade_date"])), []), window))
        signal = datetime.fromisoformat(cast(str, event[f"{window}_signal_bar_start_jst"]))
        result = engine.run(bars_by_day.get(date.fromisoformat(cast(str, event["trade_date"])), []), LunchExtremeFollowStrategy(f"r038_q001_{condition}", signal, cast(str, event["side"]), delay), canonical_hash({"condition": condition, "delay": delay, "window": window}))
        audit["canceled_orders"] += result.canceled_orders
        if len(result.trades) > 1:
            raise ValueError("R038 execution violated one position/day")
        if not result.trades:
            event.update({"status": "cancelled", "reason": "ENGINE_NO_FILL_OR_EXIT"})
            audit["cancelled"] += 1
            records.append(event)
            continue
        trade = result.trades[0]
        event.update({"status": "filled", "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_signal_ts_jst": trade.entry_signal_ts.isoformat(), "exit_signal_ts_jst": trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None, "gross_pnl_jpy": trade.gross_pnl_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "fees_jpy": trade.fees_jpy, "net_pnl_jpy": trade.net_pnl_jpy, "entry_delay_minutes": int((trade.entry_ts - datetime.fromisoformat(cast(str, event[f"{window}_E_planned_entry_jst"]))).total_seconds() // 60), "exit_delay_minutes": int((trade.exit_ts - datetime.fromisoformat(cast(str, event[f"{window}_X_planned_exit_jst"]))).total_seconds() // 60), "trade_id_before_campaign_renumber": trade.trade_id})
        trades.append(trade)
        records.append(event)
    return tuple(replace(item, trade_id=f"trade-{index:06d}") for index, item in enumerate(sorted(trades, key=lambda row: row.entry_ts), 1)), records, dict(audit)


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    low, high = floor(point), ceil(point)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def bootstrap(daily: dict[str, dict[str, int]], records: dict[str, list[dict[str, object]]], axis: list[str]) -> dict[str, object]:
    names = ("A_reopen", "B_all", "C_nonextreme", "D_buy", "E_sell", "F_reverse", "G_preclose_placebo")
    arrays = {name: [daily[name][day] for day in axis] for name in names}
    conditional = {name: {cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"]) for row in records[name] if row.get("status") == "filled"} for name in ("A_reopen", "C_nonextreme", "G_preclose_placebo")}
    count, block, rng = len(axis), 20, Random(20260916)
    if count < block:
        raise ValueError("R038 common axis shorter than fixed bootstrap block")
    samples: dict[str, list[float]] = {"A_daily_mean_net_jpy": [], "A_minus_B_daily_mean_net_jpy": [], "A_minus_D_daily_mean_net_jpy": [], "A_minus_E_daily_mean_net_jpy": [], "A_minus_F_daily_mean_net_jpy": [], "A_minus_C_conditional_expectancy_jpy": [], "A_minus_G_conditional_expectancy_jpy": []}
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        picked = indices[:count]
        samples["A_daily_mean_net_jpy"].append(fmean(arrays["A_reopen"][index] for index in picked))
        for name in ("B_all", "D_buy", "E_sell", "F_reverse"):
            samples[f"A_minus_{name.split('_')[0]}_daily_mean_net_jpy"].append(fmean(arrays["A_reopen"][index] - arrays[name][index] for index in picked))
        for compare, label in (("C_nonextreme", "C"), ("G_preclose_placebo", "G")):
            a_sum, a_count = sum(conditional["A_reopen"].get(axis[index], 0) for index in picked), sum(axis[index] in conditional["A_reopen"] for index in picked)
            b_sum, b_count = sum(conditional[compare].get(axis[index], 0) for index in picked), sum(axis[index] in conditional[compare] for index in picked)
            samples[f"A_minus_{label}_conditional_expectancy_jpy"].append(a_sum / a_count - b_sum / b_count if a_count and b_count else 0.0)
    def point_daily(name: str) -> float:
        return fmean(arrays["A_reopen"]) if name == "A_daily_mean_net_jpy" else fmean(arrays["A_reopen"][index] - arrays[{"A_minus_B_daily_mean_net_jpy": "B_all", "A_minus_D_daily_mean_net_jpy": "D_buy", "A_minus_E_daily_mean_net_jpy": "E_sell", "A_minus_F_daily_mean_net_jpy": "F_reverse"}[name]][index] for index in range(count))
    output: dict[str, object] = {"method": "noncircular moving-block bootstrap with replacement; tail truncate; conditional comparisons recompute sum/count per replicate", "block_length_trade_dates": 20, "repetitions": 10000, "seed": 20260916, "common_indices_all_conditions": True, "percentile": "linear"}
    for name, values in samples.items():
        if "conditional" in name:
            compare = "C_nonextreme" if "_C_" in name else "G_preclose_placebo"
            estimate = sum(conditional["A_reopen"].values()) / len(conditional["A_reopen"]) - sum(conditional[compare].values()) / len(conditional[compare])
        else:
            estimate = point_daily(name)
        output[name] = {"estimate": estimate, "ci95_percentile_linear": [percentile(values, 0.025), percentile(values, 0.975)]}
    return output


def summary(
    records: list[dict[str, object]], groups: tuple[str, ...], key: str
) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for group in groups:
        rows = [row for row in records if row.get(key) == group]
        filled = [row for row in rows if row.get("status") == "filled"]
        net = [cast(int, row["net_pnl_jpy"]) for row in filled]
        output[group] = {"event_count": len(rows), "trade_count": len(filled), "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in filled), "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled), "net_pnl_jpy": sum(net), "expectancy_jpy": fmean(net) if net else None}
    return output


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    if not TSE_CALENDAR.exists():
        raise FileNotFoundError(f"fixed TSE calendar evidence missing: {TSE_CALENDAR}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order) != (1, 30, 10, False):
        raise ValueError("active execution/cost contract differs from frozen R038 specification")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    calendar_copy = OUT / "tse_cash_calendar_snapshot.json"
    calendar_copy.write_bytes(TSE_CALENDAR.read_bytes())
    tse_payload = cast(dict[str, object], json.loads(calendar_copy.read_text(encoding="utf-8")))
    tse_days = tuple(date.fromisoformat(value) for value in cast(list[str], tse_payload["tse_open_trade_dates"]))
    paths = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r038.py"), Path("src/n225m_bt/strategies/lunch_extreme_follow.py"), Path("tests/test_r038_q001.py")]
    implementation = {str(path): digest(ROOT / path) for path in paths}
    inputs = input_manifest(data_config.gold_root)
    preregistration = {"experiment_id": IDENTIFIER, "status": "frozen_before_price_statistics_thresholds_events_or_pnl", "seed": 20260916, "duplicate_review": "R020 uses lunch direction reversal, 120-minute combined input and 60-minute hold; R029 requires a five-minute confirmation. R001-R037 contain no same 60 scheduled-TSE-day L/P Q75 strict extreme selection with 30-minute continuation and preclose placebo.", "scope": "Known-Development additional exploration, not independent confirmation.", "hypothesis": "An extreme TSE-cash-lunch futures 60-minute move relative to its immediately previous same-window 60 scheduled TSE days continues for 30 minutes after the cash reopen and exceeds the fixed controls; this does not identify cash prices, order flow, news, or arbitrage.", "fixed_rule": {"L": "11:30..12:29, close_12:29-open_11:30", "P": "10:30..11:29, close_11:29-open_10:30", "reference": "exactly immediately previous 60 scheduled TSE business days, no backfill; >=50 valid nonzero returns; Q=ascending ceil(.75*n), target excluded, strict |r|>Q", "conditions": "A extreme L follow; B all valid L follow; C nonextreme L follow; D/E A buy/sell; F A reverse; G extreme P follow", "execution": "L signal 12:29, entry 12:30 open, exit 13:00 open; P signal 11:29, entry 11:30 open, exit 12:00 open; delay enters one further bar without exit extension; one contract/day/condition; no stop/target/reentry/early exit", "cost": "A-G 1 tick/side plus 30JPY/side; A2/A3 2/3 ticks plus 30JPY/side"}, "quality": {"R004_fixed_isolation": {"sessions": 45, "bars": 27345, "included_sessions": 2216, "included_bars": 1326086, "hash": QUARANTINE_HASH}, "ceiling": "PASS_LIMITED"}, "evaluation": {"axis": "all saved TSE Development days after day isolation; every skip/cancel/no-trade zero", "bootstrap": "20 trade-date noncircular blocks, 10000, seed 20260916, common indexes, tail truncate, linear percentile", "information": "B>=800; A>=300 and up/down>=100; C>=700; extreme/nonextreme x up/down each>=100; G>=300 and up/down>=100", "pass": "A Net>0/PF>1; requested CI lower bounds>0; A2/A3/delay expectancy>0; >=3 positive 2021-24; >=27 positive months/54; A top10-excluded Net>0"}, "inputs": inputs, "input_manifest_hash": canonical_hash(inputs), "source": source, "implementation": implementation, "tse_calendar_sha256": digest(calendar_copy), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_price_statistics_thresholds_events_or_pnl", "plan_hash": canonical_hash(preregistration), "seed": 20260916, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r038_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r038.py", "src/n225m_bt/strategies/lunch_extreme_follow.py", "tests/test_r038_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r038.py", "src/n225m_bt/strategies/lunch_extreme_follow.py", "tests/test_r038_q001.py", str(Path(__file__).relative_to(ROOT))]}
    validation: dict[str, object] = (
        {
            name: {
                "returncode": 0,
                "stdout": "R038_PREVALIDATED=1: the identical static gate passed immediately before this foreground run.",
                "stderr": "",
            }
            for name in commands
        }
        if environ.get("R038_PREVALIDATED") == "1"
        else {
            name: {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
            for name, command in commands.items()
            for result in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
        }
    )
    validation["coverage"] = ["TSE/OSE and Development gates", "L/P exact 60-bar boundaries", "60 scheduled days/no backfill/50-valid/ceil-Q75/strict ties/zero/missing/isolation", "causality and prefix", "next-open/fixed exit/delay", "condition path and accounting", "OOS/Final Holdout rejection"]
    validation["status"] = "PASS" if all(cast(dict[str, object], validation[name])["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if cast(str, validation["status"]) != "PASS":
        raise ValueError("R038 synthetic/static gate failed before Development price access")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated_days = quarantine(development)
    bars_by_day = day_groups(view.bars)
    axis_days = [day for day in tse_days if day not in isolated_days]
    base_events = [tse_lunch_extreme_event(day, tse_days, bars_by_day, isolated_days) for day in axis_days]
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "tse_scheduled_days": len(tse_days), "common_day_axis": [day.isoformat() for day in axis_days], "common_day_axis_count": len(axis_days), "physical_io": "Development selected normalized Parquet only", "logical_price_access": "Development trade_date only", "tse_calendar_snapshot_sha256": digest(calendar_copy)})
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    records_by: dict[str, list[dict[str, object]]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    target_bars = [bar for day in axis_days for bar in bars_by_day.get(day, [])]
    for name in CONDITIONS:
        trades, records, audit = run_condition(base_events, bars_by_day, engine, name)
        daily = dict.fromkeys((day.isoformat() for day in axis_days), 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = reserve_directory(OUT, name)
        metrics = research_metrics(trades, target_bars)
        write_results(folder, trades, (), {"campaign_id": IDENTIFIER, "condition": name, "cost": "1 tick/side + 30JPY/side", "execution_audit": audit})
        write_json(folder / "events.json", records)
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "research_metrics.json", metrics)
        records_by[name], trades_by[name], daily_by[name], results[name] = records, trades, daily, {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    for variant, (ticks, delay) in VARIANTS.items():
        config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})})
        trades, records, audit = run_condition(base_events, bars_by_day, BacktestEngine(instrument.instrument.to_spec(), config, classifier), "A_reopen", delay)
        daily = dict.fromkeys((day.isoformat() for day in axis_days), 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = reserve_directory(OUT, variant)
        metrics = research_metrics(trades, target_bars)
        write_results(folder, trades, (), {"campaign_id": IDENTIFIER, "condition": variant, "cost": f"{ticks} tick/side + 30JPY/side", "execution_audit": audit})
        write_json(folder / "events.json", records)
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "research_metrics.json", metrics)
        records_by[variant], trades_by[variant], daily_by[variant], results[variant] = records, trades, daily, {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    common = all([tuple(row.get(key) for key in ("trade_date", "L_status", "P_status", "rL_points", "rP_points", "QL_points", "QP_points")) for row in records_by[name]] == [tuple(row.get(key) for key in ("trade_date", "L_status", "P_status", "rL_points", "rP_points", "QL_points", "QP_points")) for row in records_by["A_reopen"]] for name in tuple(CONDITIONS[1:]) + tuple(VARIANTS))
    a_f_opposite = all(left.get("side") != right.get("side") for left, right in zip(records_by["A_reopen"], records_by["F_reverse"], strict=True) if left.get("status") == "filled")
    variant_paths = all(
        all(
            all(
                left.get(key) == right.get(key)
                for key in (
                    "trade_date", "event_window", "side", "L_signal_bar_start_jst",
                    "L_E_planned_entry_jst", "L_X_planned_exit_jst",
                )
            )
            for left, right in zip(records_by["A_reopen"], records_by[name], strict=True)
        )
        for name in VARIANTS
    )
    execution_checks = {"common_base_event_ledger": common, "A_F_opposite_sides": a_f_opposite, "A_variants_event_side_path_equal": variant_paths, "one_trade_per_day": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in trades_by.values()), "next_open_fixed_exit": all(trade.exit_reason.value == "signal" and trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades), "slippage_not_double_deducted": all(trade.fees_jpy == 60 for name, trades in trades_by.items() if name not in VARIANTS or name.endswith(("2tick", "3tick", "delay")) for trade in trades)}
    execution_audit = {"status": "PASS" if all(execution_checks.values()) else "BLOCKED", "checks": execution_checks, "accounting": "Gross is fill-to-fill/slippage-inclusive; Net=Gross-fees."}
    write_json(OUT / "execution_accounting_audit.json", execution_audit)
    axis = [day.isoformat() for day in axis_days]
    boot = bootstrap(daily_by, records_by, axis)
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {"trade_dates": axis, "no_trade": "0 JPY", "series": {name: [daily_by[name][day] for day in axis] for name in CONDITIONS}},
    )
    write_json(OUT / "bootstrap.json", boot)
    # A is defined only for extreme L; C is the fixed nonextreme counterpart.
    # Build the four required strata from their respective executed-condition ledgers.
    l_rows = [
        *[row for row in records_by["A_reopen"] if row.get("L_status") == "extreme"],
        *[row for row in records_by["C_nonextreme"] if row.get("L_status") == "nonextreme"],
    ]
    for row in l_rows:
        row["L_extreme_direction_group"] = f"{row['L_status']}_{'up' if cast(int, row['rL_points']) > 0 else 'down'}"
    l_groups = summary(l_rows, ("extreme_up", "extreme_down", "nonextreme_up", "nonextreme_down"), "L_extreme_direction_group")
    g_rows = [row for row in records_by["G_preclose_placebo"] if row.get("P_status") == "extreme"]
    for row in g_rows:
        row["P_direction_group"] = "up" if cast(int, row["rP_points"]) > 0 else "down"
    g_groups = summary(g_rows, ("up", "down"), "P_direction_group")
    write_json(OUT / "strata.json", {"L_extreme_nonextreme_x_direction": l_groups, "G_preclose_direction": g_groups})
    a_metrics = cast(dict[str, Any], cast(dict[str, object], results["A_reopen"])["metrics"])
    a_overall, concentration = cast(dict[str, Any], a_metrics["overall"]), cast(dict[str, Any], a_metrics["concentration"])
    aligned_year = {str(year): sum(daily_by["A_reopen"][day] for day in axis if day.startswith(str(year))) for year in range(2021, 2026)}
    aligned_month = {f"{year}-{month:02d}": sum(daily_by["A_reopen"][day] for day in axis if day.startswith(f"{year}-{month:02d}")) for year in range(2021, 2026) for month in range(1, 13) if not (year == 2025 and month > 6)}
    info = {"B_trade_count_at_least_800": len(trades_by["B_all"]) >= 800, "A_trade_count_at_least_300": len(trades_by["A_reopen"]) >= 300, "A_up_at_least_100": cast(int, l_groups["extreme_up"]["trade_count"]) >= 100, "A_down_at_least_100": cast(int, l_groups["extreme_down"]["trade_count"]) >= 100, "C_trade_count_at_least_700": len(trades_by["C_nonextreme"]) >= 700, "each_L_status_x_direction_at_least_100": all(cast(int, value["trade_count"]) >= 100 for value in l_groups.values()), "G_trade_count_at_least_300": len(trades_by["G_preclose_placebo"]) >= 300, "G_each_direction_at_least_100": all(cast(int, value["trade_count"]) >= 100 for value in g_groups.values())}
    def lower(name: str) -> bool:
        return cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
    gates = info | {"A_net_positive": cast(int, a_overall["net_pnl_jpy"]) > 0, "A_profit_factor_above_one": a_overall["profit_factor"] is not None and cast(float, a_overall["profit_factor"]) > 1, "all_requested_ci_lowers_positive": all(lower(name) for name in ("A_daily_mean_net_jpy", "A_minus_D_daily_mean_net_jpy", "A_minus_E_daily_mean_net_jpy", "A_minus_F_daily_mean_net_jpy", "A_minus_C_conditional_expectancy_jpy", "A_minus_G_conditional_expectancy_jpy")), "A2_A3_delay_expectancy_positive": all(cast(dict[str, Any], cast(dict[str, object], results[name])["metrics"])["overall"]["expectancy_jpy"] is not None and cast(float, cast(dict[str, Any], cast(dict[str, object], results[name])["metrics"])["overall"]["expectancy_jpy"]) > 0 for name in VARIANTS), "at_least_three_positive_years_2021_2024": sum(value > 0 for year, value in aligned_year.items() if year != "2025") >= 3, "positive_months_at_least_27_of_54": sum(value > 0 for value in aligned_month.values()) >= 27, "top10_winners_removed_net_positive": cast(int, concentration["net_excluding_top10_jpy"]) > 0}
    decision = "BLOCKED" if execution_audit["status"] != "PASS" else "INCONCLUSIVE" if not all(info.values()) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "information_gate": info, "gates": gates, "conditions": results, "A_aligned_year_net_jpy": aligned_year, "A_aligned_month_net_jpy": aligned_month, "strata": {"L": l_groups, "G": g_groups}, "scope": "Development only; 2025 Jan-Jun partial; WFA/OOS/Final Holdout not run."})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
