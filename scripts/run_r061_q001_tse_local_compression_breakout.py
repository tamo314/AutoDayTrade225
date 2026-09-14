"""Execute the preregistered Development-only R061-Q001 experiment."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, floor
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
from n225m_bt.domain import Bar, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r053 import R053QNotIdentifiableError, fwl_delta
from n225m_bt.research.r061 import BASE, R061Specification, r061_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r061-q001-20260915-tse-local-compression-breakout-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
SEED, BLOCK = 20261006, 20
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
BASE_CONDITIONS = ("A", "D", "B", "A_fade", "A_buy", "A_sell")


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def input_manifest(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "physical_scope": "Normalized Development Parquet only; OOS and Final Holdout unselected.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key for key, bars in grouped.items() if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in bars)
    }
    included = [bar for key, bars in grouped.items() if key not in isolated for bar in bars]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included),
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
    mismatches = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    audit.update(expected_match=not mismatches, mismatches=mismatches)
    if mismatches:
        raise ValueError(f"BLOCKED: R004 fixed quarantine mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = ROOT / "results" / "research" / "r025-q001-20260914-prior-tse-day-range-acceptance-03" / "institutional_evidence" / "r020_cabinet_office_public_holidays.csv"
    if not source.exists():
        raise FileNotFoundError("frozen TSE holiday evidence is unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copied = destination / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    evidence: dict[str, object] = {"source": str(source.relative_to(ROOT)), "sha256": digest(copied), "rule": "Frozen official TSE-business-day schedule only; no bar-derived history."}
    write_json(destination / "evidence_manifest.json", evidence)
    return calendar, evidence


def scheduled_days(calendar: ExchangeCalendar) -> list[date]:
    return [item.trade_date for item in calendar.trading_days() if date(2021, 1, 1) <= item.trade_date <= date(2025, 6, 30)]


def as_float(value: object) -> float:
    return float(cast(float, value))


def as_int(value: object) -> int:
    return int(cast(int, value))


def tse_history(cash: TSECashMarketCalendar, target: date) -> list[date]:
    days: list[date] = []
    current = date(2021, 1, 1)
    while current < target:
        if cash.is_open(current):
            days.append(current)
        current = current.fromordinal(current.toordinal() + 1)
    return days[-120:]


def make_events(
    classifier: CalendarClassifier,
    cash: TSECashMarketCalendar,
    groups: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    axis: list[date],
    specification: R061Specification,
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for target in axis:
        history_dates = tse_history(cash, target)
        history = [(day, groups.get((day, Session.DAY)), (day, Session.DAY) in isolated) for day in history_dates]
        previous = None
        try:
            from n225m_bt.research.r025 import previous_tse_open_date
            previous = previous_tse_open_date(cash, target)
        except ValueError:
            previous = None
        output.append(r061_event(classifier, cash, target, groups.get((target, Session.DAY)), history, groups.get((previous, Session.DAY)) if previous else None, specification=specification, tick_size=5, quarantined=(target, Session.DAY) in isolated, prior_quarantined=(previous, Session.DAY) in isolated if previous else False))
    return output


def qualifies(condition: str, event: dict[str, object], low_percentile: int = 35) -> bool:
    if event.get("base_event_status", event.get("status")) != "event":
        return False
    low = as_float(event[f"q{low_percentile}"])
    high = as_float(event["q65"])
    w = as_float(event["w_bps"])
    a, d = w <= low, w >= high and w > as_float(event["q35"])
    return (condition == "A" and a) or (condition == "D" and d) or (condition == "B" and (a or d)) or (condition in {"A_fade", "A_buy", "A_sell"} and a)


def side(condition: str, event: dict[str, object]) -> str:
    if condition == "A_buy":
        return "long"
    if condition == "A_sell":
        return "short"
    s = cast(int, event["s"])
    if condition == "A_fade":
        s = -s
    return "long" if s == 1 else "short"


def run_condition(
    name: str, events: list[dict[str, object]], groups: dict[tuple[date, Session], list[Bar]], engine: BacktestEngine, data_version: str, holding: int, delay: int, low_percentile: int = 35,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    output: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    root_condition = "A" if name.startswith("A_") or name in {"A2", "A3", "A_delay"} else name
    for source in events:
        event = dict(source)
        event["base_event_status"] = source["status"]
        event.update(condition=name, condition_eligible=qualifies(root_condition, event, low_percentile))
        if not event["condition_eligible"]:
            event.update(status="skipped", reason=event.get("reason", "EVENT_FILTER"))
            audit[f"skip_{event['reason']}"] += 1
            output.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["signal_bar_start_jst"]))
        execution_condition = name if name in {"A_fade", "A_buy", "A_sell"} else root_condition
        result = engine.run(groups[(date.fromisoformat(cast(str, event["trade_date"])), Session.DAY)], OpeningRangeCompressionBreakoutStrategy(f"r061_{name}", signal, side(execution_condition, event), delay, holding), canonical_hash({"condition": name, "event": event, "delay": delay, "data": data_version}))
        if len(result.trades) > 1:
            raise AssertionError("R061 maximum one position violated")
        audit["canceled_orders"] += result.canceled_orders
        if not result.trades:
            event["status"] = "eligible_order_unfilled"
            audit["eligible_order_unfilled"] += 1
        else:
            trade = result.trades[0]
            event.update(status="filled", side=trade.side.value, entry_ts_jst=trade.entry_ts.isoformat(), exit_ts_jst=trade.exit_ts.isoformat(), exit_reason=trade.exit_reason.value, gross_pnl_jpy=trade.gross_pnl_jpy, slippage_cost_jpy=trade.slippage_cost_jpy, fees_jpy=trade.fees_jpy, net_pnl_jpy=trade.net_pnl_jpy)
            trades.append(trade)
            audit["trades"] += 1
        output.append(event)
    return tuple(replace(trade, trade_id=f"trade-{number:06d}") for number, trade in enumerate(sorted(trades, key=lambda value: value.entry_ts), 1)), output, dict(sorted(audit.items()))


def daily(trades: tuple[Trade, ...], axis: list[date]) -> dict[str, int]:
    values = dict.fromkeys((day.isoformat() for day in axis), 0)
    for trade in trades:
        values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def write_condition(name: str, trades: tuple[Trade, ...], events: list[dict[str, object]], audit: dict[str, int], values: dict[str, int], metric_data: ResearchData, ticks: int) -> dict[str, object]:
    folder = OUT / name
    folder.mkdir()
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": name, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    metrics = research_metrics(trades, metric_data.bars)
    write_json(folder / "research_metrics.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(values), "net_pnl_jpy": [values[key] for key in sorted(values)]}).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    low, high = floor(position), ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def regression(events: list[dict[str, object]], trades: tuple[Trade, ...], multiplier: int) -> tuple[np.ndarray[Any, np.dtype[np.float64]], np.ndarray[Any, np.dtype[np.float64]], np.ndarray[Any, np.dtype[np.float64]], list[str]]:
    by_day = {trade.trade_date.isoformat(): trade for trade in trades}
    rows: list[tuple[float, float, list[float], str]] = []
    for event in events:
        if not qualifies("B", event):
            continue
        trade = by_day.get(cast(str, event["trade_date"]))
        if trade is None:
            raise ValueError("BLOCKED: B event was not filled for regression")
        s = cast(int, event["s"])
        rows.append((float(qualifies("A", event)), float(s * (trade.exit_reference_price - trade.entry_reference_price) * multiplier), [as_float(event["w_bps"]), as_float(event["breakout_search_minutes"]), as_float(event["signal_close_overshoot_ticks"]), as_float(event["compression_return_s_adjusted_bps"]), as_float(event["first60_range_bps"]), as_float(event["tse_open_gap_s_adjusted_bps"]), float(s == 1)], cast(str, event["trade_date"])))
    if not rows:
        raise ValueError("BLOCKED: no B regression observations")
    years = sorted({int(item[3][:4]) for item in rows})
    q = np.asarray([item[0] for item in rows], dtype=np.float64)
    y = np.asarray([item[1] for item in rows], dtype=np.float64)
    nuisance = np.asarray([[1.0, *item[2], *[float(int(item[3][:4]) == year) for year in years]] for item in rows], dtype=np.float64)
    return q, y, nuisance, [item[3] for item in rows]


def bootstrap(daily_by: dict[str, dict[str, int]], events: list[dict[str, object]], trades: tuple[Trade, ...], multiplier: int) -> dict[str, object]:
    axis = sorted(daily_by["A"])
    q, y, nuisance, row_days = regression(events, trades, multiplier)
    delta, ss = fwl_delta(q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12)
    rows_by_day: dict[str, list[int]] = {}
    for index, item in enumerate(row_days):
        rows_by_day.setdefault(item, []).append(index)
    series = {"A_daily_mean_net_jpy": [float(daily_by["A"][day]) for day in axis]}
    for name in ("D", "B", "A_fade", "A_buy", "A_sell"):
        series[f"A_minus_{name}_daily_mean_net_jpy"] = [float(daily_by["A"][day] - daily_by[name][day]) for day in axis]
    samples: dict[str, list[float]] = {name: [] for name in (*series, "delta_fwl_jpy")}
    index_store = np.empty((10_000, len(axis)), dtype=np.uint16)
    rng = Random(SEED)
    for replicate in range(10_000):
        picked: list[int] = []
        while len(picked) < len(axis):
            start = rng.randrange(len(axis) - BLOCK + 1)
            picked.extend(range(start, start + BLOCK))
        picked = picked[:len(axis)]
        index_store[replicate] = picked
        for name, values in series.items():
            samples[name].append(fmean(values[index] for index in picked))
        selected = [row for index in picked for row in rows_by_day.get(axis[index], [])]
        try:
            samples["delta_fwl_jpy"].append(fwl_delta(q[selected], y[selected], nuisance[selected], pinv_rcond=1e-12, residual_ss_tolerance=1e-12)[0])
        except R053QNotIdentifiableError as error:
            write_json(OUT / "technical_fwl_gate.json", {"status": "BLOCKED", "replicate": replicate, "error": str(error)})
            raise ValueError(f"BLOCKED: FWL identification failed in bootstrap replicate {replicate}") from error
    np.save(OUT / "bootstrap_common_day_indices.npy", index_store)
    write_json(OUT / "regression_ledger.json", {"trade_dates": row_days, "q_low_compression": q.tolist(), "y_s_adjusted_0tick_gross_jpy": y.tolist(), "nuisance_columns": ["intercept", "w_bps", "breakout_search_minutes", "signal_close_overshoot_ticks", "compression_return_s_adjusted_bps", "first60_range_bps", "tse_open_gap_s_adjusted_bps", "upper_breakout", *[f"year_{year}" for year in sorted({int(day[:4]) for day in row_days})]], "nuisance": nuisance.tolist(), "fwl": {"delta": delta, "q_residual_ss": ss, "pinv_rcond": 1e-12, "residual_ss_tolerance": 1e-12}})
    output: dict[str, object] = {"method": "20 trade_date noncircular moving-block bootstrap with replacement, tail truncate, linear percentile", "seed": SEED, "repetitions": 10_000, "block_length_trade_dates": BLOCK, "target_trade_dates": len(axis), "common_index": True, "index_file": "bootstrap_common_day_indices.npy", "full_sample_q_residual_ss": ss}
    for name, values in series.items():
        output[name] = {"estimate": fmean(values), "ci95_percentile_linear": [percentile(samples[name], .025), percentile(samples[name], .975)]}
    output["delta_fwl_jpy"] = {"estimate": delta, "ci95_percentile_linear": [percentile(samples["delta_fwl_jpy"], .025), percentile(samples["delta_fwl_jpy"], .975)]}
    write_json(OUT / "technical_fwl_gate.json", {"status": "PASS", "observed_q_residual_ss": ss, "repetitions": 10_000, "tolerance": 1e-12})
    return output


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable R061 output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.allow_cross_session_pending_order) != (1, 30, False):
        raise ValueError("BLOCKED: execution/cost contract differs from R061 preregistration")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    files = {str(path): digest(ROOT / path) for path in (Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r061.py"), Path("src/n225m_bt/research/r053.py"), Path("src/n225m_bt/research/r020.py"), Path("src/n225m_bt/research/r025.py"), Path("src/n225m_bt/strategies/opening_range_compression_breakout.py"), Path("tests/test_r061_q001.py"))}
    source, inputs, cash = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"), input_manifest(data_config.gold_root), cash_calendar()
    cash_schedule, evidence = cash
    plan: dict[str, object] = {"experiment_id": IDENTIFIER, "study_id": "R061-Q001", "status": "frozen_before_price_statistics_events_or_pnl", "prior_immutable_attempt": "…-01 failed its A_fade opposite-side audit. …-02 corrected only that side wiring and passed execution/accounting, but its FWL builder tested the post-execution `filled` status instead of the fixed event status, hence zero B regression rows and BLOCKED before bootstrap/decision. …-03 preserves `base_event_status` before execution; no hypothesis, price, event, cost, seed, or gate changes.", "scope": "Development 2021-01-01..2025-06-30 only; repeated Development exploration, not independent reproduction.", "duplicate_review": "R001-R060 reviewed before price statistics. R008 uses three local 30-minute windows without a 120-business-day rolling range quantile. R033 uses ordinal 1-30, a 20-day order statistic, search 31-90, and a 60-minute hold. No registered study combines ordinal 61-90 compression, 120-day nearest-rank low range, first close breakout in 91-120, next-open entry, and 30-minute hold.", "hypothesis": "A low-volatility local equilibrium over planned TSE ordinals 61-90 whose first 1-tick close breakout in 91-120 is followed by 30 minutes of same-direction continuation exceeds high-volatility breakouts and matched reversal/fixed-side controls.", "rule": "a=open ordinal 61, H/L=max/min 61-90, w=10000(H-L)/a. Exact prior 120 scheduled TSE business days, valid>=100, nearest-rank q30/q35/q40/q65, no target inclusion/no historical supplementation/equality low side. First close >=H+1tick or <=L-1tick in 91-120 signals; next planned open entry, absolute entry+30 open exit.", "common_E": "History plus same-TSE segment ordinals 1-166 support all 20/30/40 blocks, search, delayed entry, and 15/30/45 exits; missing/isolation/nonpositive/segment-cross excluded.", "conditions": {"A": "w<=q35, follow s", "D": "w>=q65, follow s", "B": "A union D, follow s", "A_fade": "A event -s", "A_buy": "A event fixed long", "A_sell": "A event fixed short"}, "costs": "One tick + JPY30 per side; A2/A3=2/3 ticks; A_delay one planned bar with original exit.", "only_sensitivities": "q30/q40; independently rebuilt 20/40 blocks; A hold15/hold45.", "ols": "B events: y=s-adjusted 0-tick pre-cost 30-minute gross, Q=1[w<=q35], nuisance intercept/w/breakout elapsed/overshoot/compression s-return/first60 range/gap/upper/year FE; R053-Q002 nuisance-only FWL rcond/tolerance 1e-12; any nonidentification BLOCKED.", "bootstrap": {"seed": SEED, "repetitions": 10000, "block_trade_dates": BLOCK, "noncircular": True, "tail_truncate": True, "common_index": True, "percentile": "linear"}, "information_gate": "E>=800, B>=350, A>=90, D>=90, A upper/lower>=25, q30 A>=70, window20/40 A>=60.", "decision": "Information failure INCONCLUSIVE; otherwise any fixed gate failure REJECT; all pass Development-only INVESTIGATE. No WFA/OOS/Final Holdout/rescue exploration.", "inputs": inputs, "institutional_evidence": evidence, "implementation": files, "source": source, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_price_access", "seed": SEED, "plan_hash": canonical_hash(plan), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r053_q001.py", "tests/test_r061_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r061.py", "tests/test_r061_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r061.py", str(Path(__file__).relative_to(ROOT))]}
    validation: dict[str, Any] = {name: {"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr} for name, command in commands.items() for item in [run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=os.environ | {"PYTHONPATH": str(ROOT / "src")})]}
    validation["status"] = "PASS" if all(cast(int, value["returncode"]) == 0 for value in validation.values() if isinstance(value, dict)) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("BLOCKED: R061 pre-execution validation failed")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    scheduled, axis = scheduled_days(calendar), [day for day in scheduled_days(calendar) if (day, Session.DAY) not in isolated]
    if len(scheduled) != 1131 or len(axis) != 1111:
        raise ValueError("BLOCKED: fixed 1,111 trade_date PnL axis mismatch")
    groups = session_groups(view.bars)
    metric_view = ResearchData([bar for bar in view.bars if bar.trade_date in set(axis)], view.data_version, view.quality)
    base_events, events20, events40 = make_events(classifier, cash_schedule, groups, isolated, axis, BASE), make_events(classifier, cash_schedule, groups, isolated, axis, R061Specification(compression_minutes=20)), make_events(classifier, cash_schedule, groups, isolated, axis, R061Specification(compression_minutes=40))
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "quarantine": quarantine_audit, "fixed_target_day_count": len(axis), "fixed_target_trade_dates": [day.isoformat() for day in axis], "physical_io": "Development normalized Parquet only; OOS and Final Holdout unselected."})
    write_json(OUT / "all_eligible_day_ledger.json", base_events)
    variants: dict[str, tuple[list[dict[str, object]], int, int, int, int]] = {name: (base_events, 1, 0, 30, 35) for name in BASE_CONDITIONS} | {"A2": (base_events, 2, 0, 30, 35), "A3": (base_events, 3, 0, 30, 35), "A_delay": (base_events, 1, 1, 30, 35), "A_q30": (base_events, 1, 0, 30, 30), "A_q40": (base_events, 1, 0, 30, 40), "A_window20": (events20, 1, 0, 30, 35), "A_window40": (events40, 1, 0, 30, 35), "A_hold15": (base_events, 1, 0, 15, 35), "A_hold45": (base_events, 1, 0, 45, 35)}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    ledgers: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    for name, (events, ticks, delay, holding, quantile) in variants.items():
        config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})})
        trades, ledger, audit = run_condition(name, events, groups, BacktestEngine(instrument.instrument.to_spec(), config, classifier), view.data_version, holding, delay, quantile)
        values = daily(trades, axis)
        metrics = write_condition(name, trades, ledger, audit, values, metric_view, ticks)
        trades_by[name], ledgers[name], daily_by[name], results[name] = trades, ledger, values, {"trade_count": len(trades), "metrics": metrics, "audit": audit}
    checks = {"A_D_exclusive": all(not (a["condition_eligible"] and d["condition_eligible"]) for a, d in zip(ledgers["A"], ledgers["D"], strict=True)), "A_B_D_subset": all(bool(b["condition_eligible"]) == (bool(a["condition_eligible"]) or bool(d["condition_eligible"])) for a, b, d in zip(ledgers["A"], ledgers["B"], ledgers["D"], strict=True)), "A_controls_event_entry_exit": all(all(ledgers[name][index].get(field) == a.get(field) for field in ("condition_eligible", "signal_bar_start_jst", "entry_ts_jst", "exit_ts_jst")) for name in ("A_fade", "A_buy", "A_sell") for index, a in enumerate(ledgers["A"]) if a["condition_eligible"]), "A_fade_opposite_side": all(a.get("side") != f.get("side") for a, f in zip(ledgers["A"], ledgers["A_fade"], strict=True) if a.get("status") == "filled"), "sensitivity_rebuilt": all(ledgers[name][0].get("compression_minutes") == minutes for name, minutes in (("A_window20", 20), ("A_window40", 40))), "fixed_axis": all(len(values) == 1111 for values in daily_by.values()), "one_trade_per_day": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in trades_by.values()), "signal_exit": all(trade.exit_reason.value == "signal" for trades in trades_by.values() for trade in trades), "delay_nonextension": all(trade.exit_ts == next(item.exit_ts for item in trades_by["A"] if item.trade_date == trade.trade_date) for trade in trades_by["A_delay"]), "accounting_no_double_slippage": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades)}
    write_json(OUT / "execution_accounting_audit.json", {"checks": checks, "all_pass": all(checks.values()), "note": "Gross is slippage-inclusive; Net=Gross-fees."})
    if not all(checks.values()):
        raise ValueError("BLOCKED: R061 execution/accounting audit failed")
    try:
        boot = bootstrap(daily_by, ledgers["B"], trades_by["B"], instrument.instrument.contract_multiplier)
    except ValueError as error:
        if not str(error).startswith("BLOCKED:"):
            raise
        write_json(OUT / "decision.json", {"status": "BLOCKED", "reason": str(error), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "decision": "BLOCKED"})
        return
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "all_candidate_event_ledger.json", ledgers["B"])
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": sorted(daily_by["A"]), "series": daily_by})
    metrics = cast(dict[str, object], cast(dict[str, object], results["A"])["metrics"])
    overall, segments, concentration = cast(dict[str, object], metrics["overall"]), cast(dict[str, object], metrics["segments"]), cast(dict[str, object], metrics["concentration"])
    sides, years = cast(dict[str, dict[str, object]], segments["side"]), cast(dict[str, dict[str, object]], segments["year"])
    def positive(name: str) -> bool:
        item = cast(dict[str, object], cast(dict[str, object], results[name])["metrics"])["overall"]
        return as_int(cast(dict[str, object], item)["net_pnl_jpy"]) > 0 and as_float(cast(dict[str, object], item)["profit_factor"] or 0) > 1
    def ci(name: str) -> bool:
        return cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
    common_e = sum(event.get("status") in {"E", "event"} for event in base_events)
    sufficient = common_e >= 800 and len(trades_by["B"]) >= 350 and len(trades_by["A"]) >= 90 and len(trades_by["D"]) >= 90 and all(as_int(sides.get(value, {}).get("trade_count", 0)) >= 25 for value in ("long", "short")) and len(trades_by["A_q30"]) >= 70 and len(trades_by["A_window20"]) >= 60 and len(trades_by["A_window40"]) >= 60
    gates = {"A_net_positive": as_int(overall["net_pnl_jpy"]) > 0, "A_pf_gt_one": as_float(overall["profit_factor"] or 0) > 1, "A_mean_AminusD_AminusB_delta_CI": all(ci(name) for name in ("A_daily_mean_net_jpy", "A_minus_D_daily_mean_net_jpy", "A_minus_B_daily_mean_net_jpy", "delta_fwl_jpy")), "same_event_controls_CI": all(ci(f"A_minus_{name}_daily_mean_net_jpy") for name in ("A_fade", "A_buy", "A_sell")), "all_cost_delay_threshold_window_hold_sensitivities_positive": all(positive(name) for name in ("A2", "A3", "A_delay", "A_q30", "A_q40", "A_window20", "A_window40", "A_hold15", "A_hold45")), "three_positive_years_2021_2024": sum(as_int(years.get(str(year), {}).get("net_pnl_jpy", 0)) > 0 for year in range(2021, 2025)) >= 3, "positive_months_at_least_27": as_float(concentration["positive_month_fraction"] or 0) >= .5, "top10_excluded_net_positive": as_int(concentration["net_excluding_top10_jpy"]) > 0}
    decision = "INCONCLUSIVE" if not sufficient else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "breakdowns.json", {"A_year": segments["year"], "A_month": segments["month"], "A_side": sides, "positive_months": sum(as_int(value.get("net_pnl_jpy", 0)) > 0 for value in cast(dict[str, dict[str, object]], segments["month"]).values()), "concentration": concentration})
    write_json(OUT / "development_results.json", {"experiment_id": IDENTIFIER, "decision": decision, "information_sufficient": sufficient, "E": common_e, "conditions": results, "fixed_gates": gates, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "decision.json", {"status": decision, "information_sufficient": sufficient, "information": {"E": common_e, "B": len(trades_by["B"]), "A": len(trades_by["A"]), "D": len(trades_by["D"]), "A_side": sides, "A_q30": len(trades_by["A_q30"]), "A_window20": len(trades_by["A_window20"]), "A_window40": len(trades_by["A_window40"])}, "fixed_gates": gates, "bootstrap": boot, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": decision, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
