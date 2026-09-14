"""Execute the preregistered Development-only R036-Q001 experiment."""

from __future__ import annotations

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
from n225m_bt.research.r036 import prior_day_failed_breakout_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.prior_day_failed_breakout import PriorDayFailedBreakoutStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r036-q001-20260914-prior-day-failed-breakout-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
R025 = ROOT / "results" / "research" / "r025-q001-20260914-prior-tse-day-range-acceptance-03"
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal["A_rejection_fade", "B_all_fade", "C_persistent_fade", "D_buy", "E_sell", "F_continue"]
CONDITIONS: tuple[Condition, ...] = ("A_rejection_fade", "B_all_fade", "C_persistent_fade", "D_buy", "E_sell", "F_continue")


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def input_manifest(gold: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(gold, "development")]
    return {"status": "frozen_before_r036_price_statistics_events_or_pnl", "trade_date_range": ["2021-01-01", "2025-06-30"], "scope": "Selected normalized Development Parquet only; raw, volume, external prices, OOS and Final Holdout prohibited.", "files": files, "files_hash": canonical_hash(files)}


def frozen_cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R025 / "institutional_evidence" / "r020_cabinet_office_public_holidays.csv"
    if not source.exists():
        raise ValueError("frozen official TSE holiday evidence is unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copied = destination / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    if not (calendar.source_start <= date(2020, 12, 31) and calendar.source_end >= date(2025, 6, 30)):
        raise ValueError("frozen TSE evidence does not cover the Development boundary")
    evidence = {"holiday_csv_sha256": digest(copied), "r020_completed_sha256": digest(R020 / "COMPLETED.json"), "r025_completed_sha256": digest(R025 / "COMPLETED.json"), "rule": "Immediate prior TSE day from frozen Cabinet Office holidays plus weekend, Jan 1-3 and Dec 31; never inferred from observations or OSE schedule."}
    write_json(destination / "evidence_manifest.json", evidence)
    return calendar, evidence


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [row for key, rows in grouped.items() if key not in isolated for row in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {"parent_data_version": data.data_version, "quarantined_sessions": len(isolated), "quarantined_bars": len(data.bars) - len(included), "included_sessions": len(grouped) - len(isolated), "included_bars": len(included), "quarantined_session_list": listed, "quarantined_session_list_hash": canonical_hash(listed), "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included), "rule": "exclude whole (trade_date, session) for any TICK_GRID_VIOLATION"}
    expected = {"parent_data_version": PARENT_HASH, "quarantined_sessions": 45, "quarantined_bars": 27345, "included_sessions": 2216, "included_bars": 1326086, "quarantined_session_list_hash": QUARANTINE_HASH, "included_tick_grid_violations": 0}
    mismatch = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    audit.update({"expected_match": not mismatch, "mismatches": mismatch})
    if mismatch:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatch}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def implementation() -> dict[str, str]:
    names = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r020.py"), Path("src/n225m_bt/research/r022.py"), Path("src/n225m_bt/research/r025.py"), Path("src/n225m_bt/research/r036.py"), Path("src/n225m_bt/strategies/prior_day_failed_breakout.py"), Path("tests/test_r036_q001.py")]
    return {str(name): digest(ROOT / name) for name in names}


def preregistration(source: dict[str, object], inputs: dict[str, object], evidence: dict[str, object], files: dict[str, str]) -> dict[str, object]:
    return {"experiment_id": IDENTIFIER, "study_id": "R036-Q001", "status": "frozen_before_r036_price_statistics_events_or_pnl", "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development exploratory evidence, not independent confirmation.", "duplicate_review": {"R001_R035": "R025 uses the prior TSE day range only for an 08:45 outside opening and 08:59 persistence. R034 uses the current day opening range. R035 uses same-clock absolute five-minute shocks. None uses the immediate prior TSE-day full normal range, the first strict close break in the first 60 scheduled day minutes, fixed b+5 rejection, and fixed 60-minute follow-through.", "conclusion": "No duplicate; execute this fixed specification only."}, "hypothesis": "If the first strict close breakout of the immediate prior TSE business day's full normal futures day range in the first 60 scheduled day minutes is back inside at exactly b+5, following the opposite direction for 60 minutes has positive post-cost expectancy and exceeds unconditional fading, persistent-event fading, always buy, always sell, and continuation. It does not identify order flow, liquidity, stops, or cash-market behavior.", "institution_and_calendar": evidence, "fixed_rule": {"reference": "For each TSE-open day, use exactly its immediate preceding TSE business day, never an older observed day. Require every scheduled normal futures day bar from scheduled open through the final normal bar; auction, force-flat and observed-final-row substitutions are prohibited. PDH=max(high), PDL=min(low).", "event": "In scheduled bars 1..60 from target day open, select only the first close>PDH (upper) or close<PDL (lower); equal/touch and high/low-only contact are excluded. Require b+1..b+5 continuous eligible bars. Upper is rejected iff close_k<=PDH, lower iff close_k>=PDL; opposite-boundary passage is therefore rejection. Otherwise it is persistent. Do not use earlier re-entry or add filters.", "orders": "k close signal; normal entry is next eligible open E=k+1; EXIT signal k+60 and intended X=E+60=k+61. Delay adds one signal bar but does not extend X. One contract, at most one trade/day/condition and one position; no stop, target, reentry, updates or early exit.", "conditions": {"A": "rejected fade", "B_all": "all classified fade", "C_persistent": "persistent fade", "D_buy": "A events always long", "E_sell": "A events always short", "F_continue": "A events breakout direction", "A2_A3": "A at 2/3 ticks per side", "A_delay": "A signal delayed one bar, same X"}}, "inputs": {"physical": inputs, "r004_fixed_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; list hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa", "quality_ceiling": "PASS_LIMITED", "common_axis": "All scheduled Development day trade_dates except day-isolated dates; TSE closures, reference failure, missing, no breakout, filter failure, cancellation and no trade are retained as zero."}, "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "A3": {"slippage_ticks_per_side": 3, "fee_jpy_per_side": 30}, "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees; slippage is never deducted twice."}, "evaluation": {"bootstrap": {"block_length_trade_dates": 20, "repetitions": 10000, "seed": 20260914, "common_indices": True, "noncircular": True, "tail_truncate": True, "percentile": "linear"}, "information_gate": "B_all>=400; A/C>=150; A upper/lower>=50; rejected/persistent x upper/lower each>=40.", "pass_requires_all_after_information": "A Net>0, PF>1, requested CI lower bounds>0, A2/A3/A_delay expectancy>0, >=3 positive years 2021-2024, >=27/54 positive months, and top10-winner-excluded Net>0.", "decision": "BLOCKED for input/synthetic/execution/accounting failure; INCONCLUSIVE for information failure; otherwise REJECT for any failed requirement; all pass is INVESTIGATE only."}, "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": files, "implementation_files_hash": canonical_hash(files), "input_manifest_hash": canonical_hash(inputs), "seed": 20260914}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}


def side(condition: Condition, event: dict[str, object]) -> str:
    direction = cast(str, event["breakout_direction"])
    if condition in {"A_rejection_fade", "B_all_fade", "C_persistent_fade"}:
        return "short" if direction == "long" else "long"
    return "long" if condition == "D_buy" else "short" if condition == "E_sell" else direction


def selectable(condition: Condition, status: str) -> bool:
    return (condition == "B_all_fade" and status in {"rejected", "persistent"}) or (condition == "C_persistent_fade" and status == "persistent") or (condition in {"A_rejection_fade", "D_buy", "E_sell", "F_continue"} and status == "rejected")


def run_condition(data: ResearchData, cash: TSECashMarketCalendar, engine: BacktestEngine, condition: Condition, groups: dict[tuple[date, Session], list[Any]], isolated: set[tuple[date, Session]], targets: list[date], delay: int = 0) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    from n225m_bt.research.r025 import previous_tse_open_date
    for target in targets:
        key, prior_day = (target, Session.DAY), previous_tse_open_date(cash, target)
        prior = (prior_day, Session.DAY) if prior_day else None
        event = prior_day_failed_breakout_event(engine.classifier, cash, target, groups.get(key), groups.get(prior) if prior else None, day_quarantined=key in isolated, prior_day_quarantined=prior in isolated if prior else False)
        status = cast(str, event["status"])
        event.update({"condition": condition, "base_event_status": status, "pre_event_status": status})
        if not selectable(condition, status):
            event.update({"status": "skipped", "reason": event.get("reason", "EVENT_FILTER")})
            audit[f"no_trade_{event['reason']}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["k_ts_jst"]))
        result = engine.run(groups[key], PriorDayFailedBreakoutStrategy(f"r036_{condition}", signal, side(condition, event), delay), canonical_hash({"condition": condition, "event": event, "data": data.data_version, "delay": delay}))
        if len(result.trades) > 1:
            raise AssertionError("R036 produced more than one daily trade")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update({"status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_delay_minutes": int((trade.entry_ts-signal).total_seconds()//60-1), "exit_delay_minutes": int((trade.exit_ts-signal).total_seconds()//60-61), "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "fees_jpy": trade.fees_jpy, "net_pnl_jpy": trade.net_pnl_jpy})
            trades.append(trade); audit["trades"] += 1
        else:
            event["status"] = "eligible_order_unfilled"; audit["eligible_order_unfilled"] += 1
        events.append(event)
    return tuple(replace(trade, trade_id=f"trade-{number:06d}") for number, trade in enumerate(sorted(trades, key=lambda row: row.entry_ts), 1)), events, dict(sorted(audit.items()))


def daily(trades: tuple[Trade, ...], days: list[date]) -> dict[str, int]:
    values = dict.fromkeys((day.isoformat() for day in days), 0)
    for trade in trades: values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def group_summary(events: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for status in ("rejected", "persistent"):
        for direction, label in (("long", "upper"), ("short", "lower")):
            rows = [row for row in events if row.get("base_event_status") == status and row.get("breakout_direction") == direction]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            output[f"{status}_{label}"] = {"event_count": len(rows), "trade_count": len(filled), "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in filled), "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled), "net_pnl_jpy": sum(net), "expectancy_jpy": fmean(net) if net else None, "breakout_30m_bucket": dict(sorted(Counter(cast(str, row["breakout_30m_bucket"]) for row in rows).items()))}
    return output


def write_condition(folder: Path, name: str, trades: tuple[Trade, ...], events: list[dict[str, object]], audit: dict[str, int], data: ResearchData, ticks: int, values: dict[str, int]) -> dict[str, object]:
    folder.mkdir(parents=True, exist_ok=True)
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": name, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    metrics = research_metrics(trades, data.bars)
    write_json(folder / "metrics_research.json", metrics); write_json(folder / "execution_audit.json", audit); write_json(folder / "event_group_summary.json", group_summary(events))
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(values), "net_pnl_jpy": [values[key] for key in sorted(values)]}).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def percentile(values: list[float], q: float) -> float:
    rows, point = sorted(values), (len(values)-1)*q; low, high = floor(point), ceil(point)
    return rows[low] if low == high else rows[low] + (rows[high]-rows[low])*(point-low)


def bootstrap(values: dict[str, dict[str, int]], a_events: list[dict[str, object]], c_events: list[dict[str, object]]) -> dict[str, object]:
    keys = sorted(values["A_rejection_fade"]); a = [float(values["A_rejection_fade"][key]) for key in keys]
    series: dict[str, list[float]] = {"A_daily_mean_net_jpy": a} | {f"A_minus_{name}_daily_mean_net_jpy": [left-values[name][key] for left, key in zip(a, keys, strict=True)] for name in ("B_all_fade", "D_buy", "E_sell", "F_continue")}
    def ledger(events: list[dict[str, object]]) -> tuple[dict[str, int], dict[str, int]]:
        by_day = {cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"]) for row in events if row.get("status") == "filled"}
        return by_day, {key: int(key in by_day) for key in keys}
    a_net, a_count = ledger(a_events); c_net, c_count = ledger(c_events)
    if not a_net or not c_net: raise ValueError("empty conditional bootstrap ledger")
    samples: dict[str, list[float]] = {name: [] for name in [*series, "A_minus_C_persistent_conditional_expectancy_jpy"]}
    rng, count, block = Random(20260914), len(keys), 20
    for _ in range(10000):
        picked: list[int] = []
        while len(picked) < count:
            start = rng.randrange(count - block + 1)
            picked.extend(range(start, start + block))
        picked = picked[:count]
        for name, rows in series.items(): samples[name].append(fmean(rows[index] for index in picked))
        an, ac = sum(a_net.get(keys[index], 0) for index in picked), sum(a_count[keys[index]] for index in picked)
        cn, cc = sum(c_net.get(keys[index], 0) for index in picked), sum(c_count[keys[index]] for index in picked)
        samples["A_minus_C_persistent_conditional_expectancy_jpy"].append(an/ac-cn/cc if ac and cc else 0.0)
    result: dict[str, object] = {"method": "noncircular moving blocks with replacement, tail truncate; A-C recomputes conditional sums/counts", "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10000, "seed": 20260914, "common_indices_all_conditions": True, "percentile": "linear"}
    for name, rows in series.items(): result[name] = {"estimate": fmean(rows), "ci95_percentile_linear": [percentile(samples[name], .025), percentile(samples[name], .975)]}
    result["A_minus_C_persistent_conditional_expectancy_jpy"] = {"estimate": sum(a_net.values())/len(a_net)-sum(c_net.values())/len(c_net), "ci95_percentile_linear": [percentile(samples["A_minus_C_persistent_conditional_expectancy_jpy"], .025), percentile(samples["A_minus_C_persistent_conditional_expectancy_jpy"], .975)]}
    return result


def main() -> None:
    if OUT.exists(): raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order) != (1, 30, 10, False): raise ValueError("active execution/cost contract differs from R036")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    cash, evidence = frozen_cash_calendar(); source, files, inputs = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"), implementation(), input_manifest(data_config.gold_root)
    plan = preregistration(source, inputs, evidence, files)
    write_json(OUT / "input_manifest.json", inputs); write_json(OUT / "preregistration.json", plan); write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")}); write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r036_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": 20260914, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r036_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r036.py", "src/n225m_bt/strategies/prior_day_failed_breakout.py", "tests/test_r036_q001.py"], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r036.py", "src/n225m_bt/strategies/prior_day_failed_breakout.py"]}
    validation: dict[str, Any] = {name: {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr} for name, command in commands.items() for done in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]}
    validation["coverage"] = "TSE/holiday and immediate reference; full normal reference; strict upper/lower/equality; first break, b+5, opposite pass, missing/isolation/outside, prefix; next-open, fixed exit, nonextended delay, cost and holdout lock."
    validation["status"] = "PASS" if all(cast(dict[str, object], value)["returncode"] == 0 for key, value in validation.items() if key in commands) else "BLOCKED"; write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS": raise ValueError("R036 validation failed before Development price access")
    development = load_split(data_config.gold_root, "development"); view, quarantine_audit, isolated = quarantine(development)
    scheduled = [row.trade_date for row in calendar.trading_days() if date(2021,1,1) <= row.trade_date <= date(2025,6,30)]; targets = [day for day in scheduled if (day, Session.DAY) not in isolated]
    if len(scheduled) != 1131 or len(targets) != 1111: raise ValueError(f"unexpected fixed axis: scheduled={len(scheduled)}, targets={len(targets)}")
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_day_trade_dates": [day.isoformat() for day in targets], "fixed_target_day_count": len(targets), "physical_io": "Development normalized Parquet only; OOS/Final Holdout never selected"})
    groups, engine, trades_by, events_by, daily_by, results = session_groups(view.bars), BacktestEngine(instrument.instrument.to_spec(), baseline, classifier), {}, {}, {}, {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, cash, engine, condition, groups, isolated, targets); values = daily(trades, targets); metrics = write_condition(OUT / condition, condition, trades, events, audit, view, 1, values); trades_by[condition], events_by[condition], daily_by[condition], results[condition] = trades, events, values, {"trade_count": len(trades), "metrics": metrics, "audit": audit}
    for ticks, name, delay in ((2, "A_rejection_fade_2tick", 0), (3, "A_rejection_fade_3tick", 0), (1, "A_rejection_fade_delay", 1)):
        config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}); trades, events, audit = run_condition(view, cash, BacktestEngine(instrument.instrument.to_spec(), config, classifier), "A_rejection_fade", groups, isolated, targets, delay); values = daily(trades, targets); metrics = write_condition(OUT / name, name, trades, events, audit, view, ticks, values); trades_by[name], events_by[name], daily_by[name], results[name] = trades, events, values, {"trade_count": len(trades), "metrics": metrics, "audit": audit}
    def equal(left: dict[str, object], right: dict[str, object], fields: tuple[str, ...]) -> bool: return all(left.get(field) == right.get(field) for field in fields)
    audit_checks = {"rejected_A_B_path_equal": all(equal(a,b,("breakout_direction","k_ts_jst","side","entry_ts_jst","exit_ts_jst","net_pnl_jpy")) for a,b in zip(events_by["A_rejection_fade"],events_by["B_all_fade"],strict=True) if a.get("base_event_status") == "rejected"), "persistent_B_C_path_equal": all(equal(b,c,("breakout_direction","k_ts_jst","side","entry_ts_jst","exit_ts_jst","net_pnl_jpy")) for b,c in zip(events_by["B_all_fade"],events_by["C_persistent_fade"],strict=True) if b.get("base_event_status") == "persistent"), "A_variants_event_side_equal": all(equal(row,events_by["A_rejection_fade"][index],("trade_date","breakout_direction","k_ts_jst","side")) for name in ("A_rejection_fade_2tick","A_rejection_fade_3tick","A_rejection_fade_delay") for index,row in enumerate(events_by[name])), "fixed_exit_max_one_position": all(trade.exit_reason.value == "signal" and trade.exit_ts == datetime.fromisoformat(cast(str,next(row for row in events_by[name] if row.get("status") == "filled" and row.get("trade_date") == trade.trade_date.isoformat())["X_planned_exit_jst"])) for name,trades in trades_by.items() for trade in trades), "accounting_no_double_slippage": all(trade.net_pnl_jpy == trade.gross_pnl_jpy-trade.fees_jpy for trades in trades_by.values() for trade in trades)}
    write_json(OUT / "execution_accounting_audit.json", {"checks": audit_checks, "all_pass": all(audit_checks.values()), "accounting": "Gross is slippage-inclusive; Net=Gross-fees."})
    if not all(audit_checks.values()): raise ValueError("R036 execution/accounting audit failed")
    boot, stratification = bootstrap(daily_by, events_by["A_rejection_fade"], events_by["C_persistent_fade"]), group_summary(events_by["B_all_fade"]); write_json(OUT / "bootstrap.json", boot); write_json(OUT / "event_stratification.json", stratification); write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": [day.isoformat() for day in targets], "no_trade": "0", "series": daily_by}); write_json(OUT / "development_results.json", results)
    a_metrics = cast(dict[str, object], results["A_rejection_fade"])["metrics"]; a_overall = cast(dict[str, object], cast(dict[str, object], a_metrics)["overall"]); segments = cast(dict[str, object], cast(dict[str, object], a_metrics)["segments"]); side_metrics = cast(dict[str, dict[str, object]], segments["side"]); yearly = cast(dict[str, dict[str, object]], segments["year"]); concentration = cast(dict[str, object], cast(dict[str, object], a_metrics)["concentration"])
    sufficient = len(trades_by["B_all_fade"]) >= 400 and len(trades_by["A_rejection_fade"]) >= 150 and len(trades_by["C_persistent_fade"]) >= 150 and all(cast(int,side_metrics.get(value,{}).get("trade_count",0)) >= 50 for value in ("long","short")) and all(cast(int,row["trade_count"]) >= 40 for row in cast(dict[str,dict[str,object]],stratification).values())
    lower = lambda name: cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
    gates = {"A_net_positive": cast(int,a_overall["net_pnl_jpy"]) > 0, "A_pf_gt_one": a_overall["profit_factor"] is not None and cast(float,a_overall["profit_factor"]) > 1, "all_requested_CI_lowers_positive": all(lower(name) for name in ("A_daily_mean_net_jpy","A_minus_B_all_fade_daily_mean_net_jpy","A_minus_D_buy_daily_mean_net_jpy","A_minus_E_sell_daily_mean_net_jpy","A_minus_F_continue_daily_mean_net_jpy","A_minus_C_persistent_conditional_expectancy_jpy")), "A2_A3_delay_expectancy_positive": all(cast(float,cast(dict[str,object],cast(dict[str,object],results[name])["metrics"])["overall"]["expectancy_jpy"]) > 0 for name in ("A_rejection_fade_2tick","A_rejection_fade_3tick","A_rejection_fade_delay")), "three_positive_years_2021_2024": sum(cast(int,yearly.get(str(year),{}).get("net_pnl_jpy",0)) > 0 for year in range(2021,2025)) >= 3, "positive_months_at_least_27": cast(float,concentration["positive_month_fraction"]) >= .5, "top10_excluded_net_positive": cast(int,concentration["net_excluding_top10_jpy"]) > 0}
    decision = "INCONCLUSIVE" if not sufficient else "INVESTIGATE" if all(gates.values()) else "REJECT"; write_json(OUT / "decision.json", {"status": decision, "information_sufficient": sufficient, "information_gate": {"B_all_trades": len(trades_by["B_all_fade"]), "A_trades": len(trades_by["A_rejection_fade"]), "C_trades": len(trades_by["C_persistent_fade"]), "four_groups": stratification}, "fixed_gates": gates, "A_overall": a_overall, "bootstrap": boot, "fixed_rule_note": "No rescue exploration, WFA, OOS or Final Holdout executed."}); write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": decision, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__": main()
