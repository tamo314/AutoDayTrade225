"""Execute the preregistered Development-only R020-Q001 experiment."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, Literal, cast
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZipFile

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
from n225m_bt.research.r020 import TSECashMarketCalendar, tse_lunch_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.lunch_reversal import LunchReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r020-q001-20260914-tse-lunch-reversal-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
R012_AXIS = ROOT / "results" / "research" / "r012-q001-20260914-night-direction-followthrough-01" / "daily_net_pnl_aligned.json"
EXPECTED_AXIS_COUNT = 1_111
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal["A_lunch_reversal", "B_always_long", "C_always_short", "D_pre_lunch_reversal", "F_lunch_follow"]
CONDITIONS: tuple[Condition, ...] = ("A_lunch_reversal", "B_always_long", "C_always_short", "D_pre_lunch_reversal", "F_lunch_follow")
EVIDENCE = {
    "jpx_tse_trading_hours.pdf": "https://www.jpx.co.jp/english/equities/trading/domestic/tvdivq0000006blj-att/tradinghours_eg.pdf",
    "cabinet_office_public_holidays.csv": "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv",
}


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_institutional_evidence() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    folder = reserve_directory(OUT, "institutional_evidence")
    records: list[dict[str, object]] = []
    for name, url in EVIDENCE.items():
        try:
            with urlopen(url, timeout=30) as response:
                payload = response.read()
        except Exception as exc:  # evidence is an input gate, never silently substituted
            raise RuntimeError(f"institutional evidence download failed for {url}: {exc}") from exc
        path = folder / name
        path.write_bytes(payload)
        records.append({"name": name, "url": url, "sha256": sha256_file(path), "bytes": len(payload)})
    holiday_text = (folder / "cabinet_office_public_holidays.csv").read_text(encoding="cp932")
    cash_calendar = TSECashMarketCalendar.from_cabinet_office_csv(holiday_text)
    if not (cash_calendar.source_start <= date(2021, 1, 1) and cash_calendar.source_end >= date(2025, 6, 30)):
        raise ValueError("official holiday evidence does not cover Development")
    calendar_dates: list[str] = []
    cursor = date(2021, 1, 1)
    while cursor <= date(2025, 6, 30):
        if cash_calendar.is_open(cursor):
            calendar_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    derived = {
        "calendar_name": "TSE_cash_market_business_days_R020",
        "range": ["2021-01-01", "2025-06-30"],
        "open_day_rule": "weekday excluding official Cabinet Office national holidays and JPX year-end/new-year non-business dates Dec31 and Jan1-Jan3; do not use OSE observed dates to infer TSE status",
        "tse_open_trade_dates": calendar_dates,
        "count": len(calendar_dates),
        "source_coverage": [cash_calendar.source_start.isoformat(), cash_calendar.source_end.isoformat()],
    }
    write_json(folder / "tse_cash_calendar.json", derived)
    manifest: dict[str, object] = {
        "status": "frozen_before_r020_price_statistics_events_or_pnl",
        "records": records,
        "records_hash": canonical_hash(records),
        "derived_calendar_sha256": sha256_file(folder / "tse_cash_calendar.json"),
        "derived_calendar_hash": canonical_hash(derived),
        "asserted_institutional_interpretation": "JPX TSE cash-market schedule establishes the 11:30 first-session end and 12:30 second-session start for the studied historical interval; the Cabinet Office calendar establishes national holidays. Cash-market closure skips every condition even if OSE has a day session.",
    }
    write_json(folder / "evidence_manifest.json", manifest)
    return cash_calendar, manifest


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)} for path in partition_paths(gold_root, "development")]
    return {"status": "frozen_before_price_statistics_events_or_pnl", "scope": "Only selected normalized Development Parquet partitions; no raw, OOS, Final Holdout, external price, or volume inputs.", "trade_date_range": ["2021-01-01", "2025-06-30"], "files": files, "files_hash": canonical_hash(files)}


def implementation_snapshot() -> dict[str, str]:
    paths = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r020.py"), Path("src/n225m_bt/strategies/lunch_reversal.py"), Path("tests/test_r020_q001.py")]
    return {str(path): sha256_file(ROOT / path) for path in paths}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [row for key, rows in groups.items() if key not in isolated for row in rows]
    listed = [{"trade_date": item.isoformat(), "session": session.value} for item, session in sorted(isolated)]
    audit: dict[str, object] = {"parent_data_version": data.data_version, "parent_bars": len(data.bars), "quarantined_bars": len(data.bars) - len(included), "quarantined_sessions": len(isolated), "quarantined_sessions_by_type": dict(sorted(Counter(session.value for _, session in isolated).items())), "included_bars": len(included), "included_sessions": len(groups) - len(isolated), "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included), "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION", "quarantined_session_list": listed, "quarantined_session_list_hash": canonical_hash(listed)}
    expected = {"parent_data_version": EXPECTED_PARENT_VERSION, "quarantined_sessions": 45, "quarantined_bars": 27345, "included_bars": 1326086, "included_sessions": 2216, "quarantined_sessions_by_type": {"day": 20, "night": 25}, "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH}
    mismatches = {key: {"actual": audit.get(key), "expected": value} for key, value in expected.items() if audit.get(key) != value}
    if audit["included_tick_grid_violations"]:
        mismatches["included_tick_grid_violations"] = {"actual": audit["included_tick_grid_violations"], "expected": 0}
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def axis_from_r012() -> list[str]:
    if not R012_AXIS.exists():
        raise ValueError("R012 fixed 1,111-day axis artifact is missing")
    import json

    target = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(target, list) or len(target) != EXPECTED_AXIS_COUNT or len(set(target)) != len(target):
        raise ValueError("R012 daily axis is not the fixed unique 1,111 trade_date universe")
    return target


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = floor(position), ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap(values: dict[str, list[int]]) -> dict[str, object]:
    count = len(values["A_lunch_reversal"])
    if count != EXPECTED_AXIS_COUNT or any(len(series) != count for series in values.values()):
        raise ValueError("R020 daily series are not the aligned 1,111 axis")
    rng, block = Random(20260913), 20
    samples: dict[str, list[float]] = {name: [] for name in ("A", "A_minus_B", "A_minus_C", "A_minus_D", "A_minus_F")}
    a, b, c, d, f = (values[name] for name in CONDITIONS)
    for _ in range(10_000):
        indexes: list[int] = []
        while len(indexes) < count:
            start = rng.randrange(count - block + 1)
            indexes.extend(range(start, start + block))
        indexes = indexes[:count]
        samples["A"].append(fmean(a[index] for index in indexes))
        samples["A_minus_B"].append(fmean(a[index] - b[index] for index in indexes))
        samples["A_minus_C"].append(fmean(a[index] - c[index] for index in indexes))
        samples["A_minus_D"].append(fmean(a[index] - d[index] for index in indexes))
        samples["A_minus_F"].append(fmean(a[index] - f[index] for index in indexes))
    observed = {"A": fmean(a), "A_minus_B": fmean(x - y for x, y in zip(a, b, strict=True)), "A_minus_C": fmean(x - y for x, y in zip(a, c, strict=True)), "A_minus_D": fmean(x - y for x, y in zip(a, d, strict=True)), "A_minus_F": fmean(x - y for x, y in zip(a, f, strict=True))}
    labels = {"A": "A_daily_mean_net_jpy", "A_minus_B": "A_minus_B_daily_mean_net_jpy", "A_minus_C": "A_minus_C_daily_mean_net_jpy", "A_minus_D": "A_minus_D_daily_mean_net_jpy", "A_minus_F": "A_minus_F_daily_mean_net_jpy"}
    return {"method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate", "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10_000, "seed": 20260913, "common_indices_all_conditions": True, "percentile_implementation": "linear interpolation at (n-1)*q", **{labels[key]: {"estimate": observed[key], "ci95_percentile_linear": [percentile(samples[key], .025), percentile(samples[key], .975)]} for key in labels}}


def direction_for(condition: Condition, event: dict[str, object]) -> str:
    if condition == "B_always_long":
        return "long"
    if condition == "C_always_short":
        return "short"
    return cast(str, event[{"A_lunch_reversal": "A_direction", "D_pre_lunch_reversal": "D_direction", "F_lunch_follow": "F_direction"}[condition]])


def run_condition(data: ResearchData, cash_calendar: TSECashMarketCalendar, engine: BacktestEngine, condition: Condition, targets: list[str]) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups = session_groups(data.bars)
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text_day in targets:
        trade_day = date.fromisoformat(text_day)
        event = tse_lunch_event(trade_day, groups.get((trade_day, Session.DAY), []), cash_calendar)
        event.update({"condition": condition, "pre_event_status": event["status"]})
        audit[f"event_{event['status']}"] += 1
        if event["status"] != "eligible":
            audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(groups[(trade_day, Session.DAY)], LunchReversalStrategy(f"r020_q001_{condition}", signal, direction_for(condition, event)), canonical_hash({"condition": condition, "event": event, "data_version": data.data_version}))
        if len(result.trades) > 1:
            raise AssertionError("R020 produced more than one day trade")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update({"status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_delay_minutes": int((trade.entry_ts - signal - timedelta(minutes=1)).total_seconds() // 60), "exit_delay_minutes": int((trade.exit_ts - signal - timedelta(minutes=61)).total_seconds() // 60), "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy})
            trades.append(trade)
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
        else:
            event["status"] = "eligible_order_unfilled"
            audit["eligible_order_unfilled"] += 1
        events.append(event)
    stable = tuple(replace(item, trade_id=f"trade-{number:06d}") for number, item in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1))
    return stable, events, dict(sorted(audit.items()))


def write_condition(folder: Path, condition: str, trades: tuple[Trade, ...], events: list[dict[str, object]], metrics: dict[str, object], audit: dict[str, int], data: ResearchData, ticks: int, daily: dict[str, int]) -> None:
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "status": "complete", "data_version": data.data_version, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(daily), "net_pnl_jpy": [daily[item] for item in sorted(daily)]}).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int) -> dict[str, object]:
    eligible = [item for item in events if item.get("pre_event_status") == "eligible"]
    filled = [item for item in events if item.get("status") == "filled"]
    checks = {"every_eligible_event_filled": len(eligible) == len(filled), "one_trade_per_filled_event": len(filled) == len(trades), "no_canceled_or_unfilled_eligible_order": not any(item.get("status") == "eligible_order_unfilled" for item in events), "entry_within_max_delay": all(0 <= cast(int, item["entry_delay_minutes"]) <= max_delay for item in filled), "fixed_signal_exit_within_max_delay": all(item["exit_reason"] == "signal" and 0 <= cast(int, item["exit_delay_minutes"]) <= max_delay for item in filled), "net_equals_gross_minus_fees": all(item.net_pnl_jpy == item.gross_pnl_jpy - item.fees_jpy for item in trades), "no_force_flat_or_end_of_data": all(item.exit_reason.value not in {"force_flat", "end_of_data"} for item in trades)}
    return {"checks": checks, "all_pass": all(checks.values()), "accounting": "slippage attribution is informational and is never additionally deducted"}


def preregistration(source: dict[str, object], inputs: dict[str, object], evidence: dict[str, object], implementation: dict[str, str]) -> dict[str, object]:
    return {"experiment_id": IDENTIFIER, "study_id": "R020-Q001", "status": "frozen_before_r020_price_statistics_events_or_pnl", "supersedes_stopped_unregistered_attempt": {"id": "r020-q001-20260914-tse-lunch-reversal-01", "reason": "calendar construction attempted invalid month-end dates before preregistration or Development price access", "price_statistics_events_orders_fills_trades_pnl_bootstrap": "NOT_CREATED"}, "scope": "Development trade_date 2021-01-01..2025-06-30 only; additional known-Development exploration, not independent confirmation, unused validation, or research-wide multiplicity-corrected validation.", "novelty_correspondence": {"R001": "session opening direction rules, not 10:30-12:29 cash-lunch futures movement", "R006": "night-close/day-open gap reversal", "R007": "local one-minute shock reversal", "R011": "S+119 close/previous-60-close mean deviation", "conclusion": "No R001-R019 registration or implementation takes the fixed 11:30-12:30 futures change, reverses it at 12:30, and compares it at the same event/time with fixed long/short, lunch follow, and pre-lunch reversal controls."}, "hypothesis": "The 11:30-12:30 Nikkei 225 futures change while the TSE cash market is closed contains a component that reverses after the 12:30 cash-market reopening. This is a fixed predictive-rule comparison, not a claim to observe cash prices, arbitrage, volume, liquidity, order flow, or a causal institutional-boundary effect.", "institution_and_calendar": evidence, "fixed_rule": {"day_only": "Use only official-TSE-open trade_dates. TSE cash holidays skip all conditions even if OSE has a day session.", "windows": "Require 120 contiguous eligible day-session bars 10:30..12:29 JST. P=close_11:29-open_10:30; L=close_12:29-open_11:30. P=0 or L=0 skips all conditions. No threshold, standardization, gap fill, volatility, weekday/year/direction filter, volume, cash price, arbitrage, liquidity, or order-flow input.", "conditions": {"A": "-sign(L)", "B": "always long", "C": "always short", "D": "-sign(P)", "F": "sign(L)", "A2": "A rerun by unchanged engine with 2 ticks/side"}, "orders": "Signal after 12:29 close; earliest entry 12:30 open. Signal EXIT after 13:29 close; earliest exit 13:30 open. Delay never extends X; no new entry at/after X. Preserve engine maximum-delay, conflict, forced-flat, cutoff and one-position contracts.", "prohibited": ["Stop", "Target", "reference-price exit", "re-entry", "direction/time/window/threshold rescue", "additional costs or delays", "WFA", "OOS", "Final Holdout"]}, "inputs": {"physical": inputs, "r004_fixed_quarantine": "Must reproduce hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa, 45 sessions/27,345 bars isolated, 2,216 sessions/1,326,086 bars retained.", "fixed_daily_axis": {"source": str(R012_AXIS.relative_to(ROOT)), "sha256": sha256_file(R012_AXIS), "count": EXPECTED_AXIS_COUNT, "rule": "R006/R012/R015 day-trade-date axis; day-isolated dates excluded, TSE-closed/window-missing/zero/cancel/no-trade remain zero."}, "quality_ceiling": "PASS_LIMITED; inherited continuous-series, real-contract/roll/adjustment, provider timestamp and post-quality whole-session isolation limits."}, "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; do not deduct slippage attribution twice."}, "evaluation": {"bootstrap": {"block_length_trade_dates": 20, "repetitions": 10_000, "seed": 20260913, "common_indices_all_conditions": True, "sampling": "non-circular moving blocks with replacement and tail truncation", "ci": "linear percentile"}, "information_gate": ["A/B/C/D/F >=200 trades", "A long/short >=50", "each P-sign x L-sign eligible group >=50 pre-events"], "pass_requires_all": ["A Net>0", "A PF>1", "A and A-B/A-C/A-D/A-F all 95% lower bounds >0", "A2 expectancy>0", "A positive months>=27/54", "A Net excluding top10 winners>0"], "decision": "BLOCKED on input/institution/synthetic/execution/accounting failure; INCONCLUSIVE on information failure; otherwise REJECT if any primary condition fails. All passing is Development primary/PASS_LIMITED only and INVESTIGATE, never automatic CANDIDATE."}, "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": implementation, "implementation_files_hash": canonical_hash(implementation), "input_manifest_hash": canonical_hash(inputs)}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close)
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R020 specification")
    cash_calendar, evidence = freeze_institutional_evidence()
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    implementation = implementation_snapshot()
    with ZipFile(OUT / "r020_implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in implementation:
            archive.write(ROOT / name, name)
    inputs = input_manifest(data_config.gold_root)
    plan = preregistration(source, inputs, evidence, implementation)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r020_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": 20260913, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r020_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r020.py", "src/n225m_bt/strategies/lunch_reversal.py", "tests/test_r020_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r020.py", "src/n225m_bt/strategies/lunch_reversal.py", "tests/test_r020_q001.py", str(Path(__file__).relative_to(ROOT))]}
    validation: dict[str, object] = {"coverage": ["official-TSE-open versus OSE-holiday distinction", "JST/trade_date, 10:30/11:29/11:30/12:29 boundaries and 120 bars", "P/L signs/zeros, missing/ineligible and prefix invariance", "same P/different L and same L/different P direction separation", "next-bar entry, fixed exit, delayed non-extension, cutoff-held exit, single position and accounting", "Final Holdout input rejection"]}
    for name, command in commands.items():
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    validation["status"] = "PASS" if all(cast(dict[str, int], validation[name])["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R020 synthetic/static validation failed before Development price access")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    development = load_split(data_config.gold_root, "development")
    parent_groups = session_groups(development.bars)
    view, quarantine_audit, isolated = quarantine(development)
    targets = axis_from_r012()
    actual_targets = sorted(day.isoformat() for day, session in parent_groups if session is Session.DAY and (day, Session.DAY) not in isolated)
    if targets != actual_targets:
        raise ValueError("R006/R012/R015 fixed axis does not reproduce this R004 day-session universe")
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_trade_dates": targets, "target_count": len(targets), "physical_io": "Development selected normalized Parquet only; OOS and Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only", "institutional_gate": evidence})
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, dict[str, object]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, cash_calendar, engine, condition, targets)
        daily = dict.fromkeys(targets, 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = research_metrics(trades, view.bars)
        write_condition(reserve_directory(OUT, condition), condition, trades, events, metrics, audit, view, 1, daily)
        results[condition], events_by[condition], trades_by[condition], daily_by[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}, events, trades, daily
    stress_config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    stress_trades, stress_events, stress_audit = run_condition(view, cash_calendar, BacktestEngine(instrument.instrument.to_spec(), stress_config, classifier), "A_lunch_reversal", targets)
    stress_daily = dict.fromkeys(targets, 0)
    for trade in stress_trades:
        stress_daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    stress_metrics = research_metrics(stress_trades, view.bars)
    write_condition(reserve_directory(OUT, "A_lunch_reversal_2tick"), "A_lunch_reversal_2tick", stress_trades, stress_events, stress_metrics, stress_audit, view, 2, stress_daily)
    results["A_lunch_reversal_2tick"] = {"trade_count": len(stress_trades), "metrics": stress_metrics, "execution_audit": stress_audit}
    audits = {condition: path_audit(events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes) for condition in CONDITIONS} | {"A_lunch_reversal_2tick": path_audit(stress_events, stress_trades, baseline.execution.max_fill_delay_minutes)}
    shared_fields = ("trade_date", "pre_event_status", "reason", "P_points", "L_points", "E_planned_entry_jst", "X_planned_exit_jst")
    shared = all(len(events_by[condition]) == len(events_by["A_lunch_reversal"]) and all(tuple(row.get(field) for field in shared_fields) == tuple(events_by[condition][index].get(field) for field in shared_fields) for index, row in enumerate(events_by["A_lunch_reversal"])) for condition in CONDITIONS[1:])
    a_events, d_events = events_by["A_lunch_reversal"], events_by["D_pre_lunch_reversal"]
    diagnostic: dict[str, object] = {"P_sign_x_L_sign": {}}
    for p_sign in ("positive", "negative"):
        for l_sign in ("positive", "negative"):
            selected = [row for row in a_events if row.get("pre_event_status") == "eligible" and (cast(int, row.get("P_points", 0)) > 0) == (p_sign == "positive") and (cast(int, row.get("L_points", 0)) > 0) == (l_sign == "positive")]
            filled = [row for row in selected if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            cast(dict[str, object], diagnostic["P_sign_x_L_sign"])[f"P_{p_sign}_L_{l_sign}"] = {"pre_event_count": len(selected), "trade_count": len(filled), "entry_delay_minutes_total": sum(cast(int, row["entry_delay_minutes"]) for row in filled), "exit_delay_minutes_total": sum(cast(int, row["exit_delay_minutes"]) for row in filled), "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled), "net_pnl_jpy": sum(net), "expectancy_jpy": fmean(net) if net else None}
    same: list[tuple[dict[str, object], dict[str, object]]] = []
    opposite: list[tuple[dict[str, object], dict[str, object]]] = []
    for a, d in zip(a_events, d_events, strict=True):
        if a.get("pre_event_status") == "eligible":
            (same if (cast(int, a["P_points"]) > 0) == (cast(int, a["L_points"]) > 0) else opposite).append((a, d))
    diagnostic["A_D_same_sign_P_L"] = {"count": len(same), "side_path_net_identical": all(a.get("side") == d.get("side") and a.get("net_pnl_jpy") == d.get("net_pnl_jpy") for a, d in same), "A_minus_D_net_jpy": sum(cast(int, a.get("net_pnl_jpy", 0)) - cast(int, d.get("net_pnl_jpy", 0)) for a, d in same)}
    diagnostic["A_D_opposite_sign_P_L"] = {"count": len(opposite), "A_minus_D_net_jpy": sum(cast(int, a.get("net_pnl_jpy", 0)) - cast(int, d.get("net_pnl_jpy", 0)) for a, d in opposite)}
    post = {"status": "PASS" if shared and all(cast(bool, audit["all_pass"]) for audit in audits.values()) and cast(bool, cast(dict[str, object], diagnostic["A_D_same_sign_P_L"])["side_path_net_identical"]) and cast(int, cast(dict[str, object], diagnostic["A_D_same_sign_P_L"])["A_minus_D_net_jpy"]) == 0 else "BLOCKED", "conditions": audits, "all_conditions_identical_pre_event": shared, "P_L_same_sign_A_equals_D": diagnostic["A_D_same_sign_P_L"], "P_L_opposite_sign_A_D_difference": diagnostic["A_D_opposite_sign_P_L"], "policy": "No successful-fill intersection is used; all axis no-trades are retained as zero."}
    write_json(OUT / "post_execution_validation.json", post)
    write_json(OUT / "p_l_direction_diagnostics.json", diagnostic)
    values: dict[str, list[int]] = {condition: [daily_by[condition][day] for day in targets] for condition in CONDITIONS}
    boot = bootstrap(values)
    months = {f"{year:04d}-{month:02d}": 0 for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    for day, amount in daily_by["A_lunch_reversal"].items():
        months[day[:7]] += amount
    a_metrics = cast(dict[str, Any], results["A_lunch_reversal"]["metrics"])
    a_overall, stress_overall = cast(dict[str, Any], a_metrics["overall"]), cast(dict[str, Any], stress_metrics["overall"])
    def lower(key: str) -> bool:
        estimate = cast(dict[str, object], boot[key])
        return cast(list[float], estimate["ci95_percentile_linear"])[0] > 0
    group_gate = all(cast(int, item["pre_event_count"]) >= 50 for item in cast(dict[str, dict[str, object]], diagnostic["P_sign_x_L_sign"]).values())
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_lunch_reversal"])
    gates: dict[str, bool] = {"A_trade_count_at_least_200": len(trades_by["A_lunch_reversal"]) >= 200, "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200, "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200, "D_trade_count_at_least_200": len(trades_by["D_pre_lunch_reversal"]) >= 200, "F_trade_count_at_least_200": len(trades_by["F_lunch_follow"]) >= 200, "A_long_at_least_50": long_count >= 50, "A_short_at_least_50": len(trades_by["A_lunch_reversal"]) - long_count >= 50, "each_P_sign_x_L_sign_pre_event_at_least_50": group_gate, "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"), "A_minus_B_bootstrap_lower_above_0": lower("A_minus_B_daily_mean_net_jpy"), "A_minus_C_bootstrap_lower_above_0": lower("A_minus_C_daily_mean_net_jpy"), "A_minus_D_bootstrap_lower_above_0": lower("A_minus_D_daily_mean_net_jpy"), "A_minus_F_bootstrap_lower_above_0": lower("A_minus_F_daily_mean_net_jpy"), "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None and stress_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])["net_excluding_top10_jpy"] > 0}
    info = [key for key in gates if "trade_count" in key or key in {"A_long_at_least_50", "A_short_at_least_50", "each_P_sign_x_L_sign_pre_event_at_least_50"}]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(gates[key] for key in info) else "DEVELOPMENT_PRIMARY_CONDITIONS_PASSED_PASS_LIMITED" if all(gates.values()) else "REJECT"
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": targets, "no_trade": "0", "day_isolated": "excluded", "TSE_closed_or_missing_or_zero_or_unfilled": "0", **values})
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "A_direction_counts": {"long": long_count, "short": len(trades_by["A_lunch_reversal"]) - long_count}, "positive_months_of_54": sum(value > 0 for value in months.values()), "monthly_A_net_jpy": months, "A_required_segments": a_metrics["segments"], "conditions": results, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; day only; 2025 Jan-Jun; no WFA/OOS/Final Holdout"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
