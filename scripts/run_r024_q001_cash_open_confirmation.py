"""Execute the preregistered Development-only R024-Q001 experiment."""

from __future__ import annotations

import json
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
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r024 import cash_open_confirmation_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.cash_open_confirmation import CashOpenConfirmationStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r024-q001-20260914-cash-open-confirmation-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
R021 = ROOT / "results" / "research" / "r021-q001-20260914-opening-cash-close-followthrough-05"
R012_AXIS = ROOT / "results" / "research" / "r012-q001-20260914-night-direction-followthrough-01" / "daily_net_pnl_aligned.json"
EXPECTED_AXIS_COUNT = 1_111
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal["A_confirmed_follow", "B_all_q_follow", "C_confirmed_long", "D_confirmed_short", "F_confirmed_reverse"]
CONDITIONS: tuple[Condition, ...] = (
    "A_confirmed_follow",
    "B_all_q_follow",
    "C_confirmed_long",
    "D_confirmed_short",
    "F_confirmed_reverse",
)


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_tse_evidence() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    manifest = R021 / "institutional_evidence" / "evidence_manifest.json"
    holidays = R021 / "institutional_evidence" / "r020_cabinet_office_public_holidays.csv"
    r020_manifest = R020 / "institutional_evidence" / "evidence_manifest.json"
    if not all(path.exists() for path in (manifest, holidays, r020_manifest)):
        raise ValueError("R020/R021 frozen TSE institutional evidence is unavailable")
    folder = OUT / "institutional_evidence"
    folder.mkdir()
    copied = folder / holidays.name
    copied.write_bytes(holidays.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    if not (calendar.source_start <= date(2021, 1, 1) and calendar.source_end >= date(2025, 6, 30)):
        raise ValueError("frozen TSE holiday evidence does not cover Development")
    evidence = {
        "r020_completed_sha256": sha256_file(R020 / "COMPLETED.json"),
        "r020_evidence_manifest_sha256": sha256_file(r020_manifest),
        "r021_completed_sha256": sha256_file(R021 / "COMPLETED.json"),
        "r021_evidence_manifest_sha256": sha256_file(manifest),
        "holiday_csv_sha256": sha256_file(copied),
        "calendar_rule": "TSE open only on weekdays not in Cabinet Office holidays, excluding Jan 1-3 and Dec 31; never inferred from OSE futures observations.",
    }
    write_json(folder / "evidence_manifest.json", evidence)
    return calendar, evidence | {"evidence_manifest_sha256": sha256_file(folder / "evidence_manifest.json")}


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)} for path in partition_paths(gold_root, "development")]
    return {
        "status": "frozen_before_r024_price_statistics_events_or_pnl",
        "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS, and Final Holdout are prohibited.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [row for key, rows in groups.items() if key not in isolated for row in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(isolated),
        "quarantined_sessions_by_type": dict(sorted(Counter(session.value for _, session in isolated).items())),
        "included_bars": len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
    }
    expected = {
        "parent_data_version": EXPECTED_PARENT_VERSION,
        "quarantined_sessions": 45,
        "quarantined_bars": 27345,
        "included_bars": 1326086,
        "included_sessions": 2216,
        "quarantined_sessions_by_type": {"day": 20, "night": 25},
        "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH,
        "included_tick_grid_violations": 0,
    }
    mismatches = {key: {"actual": audit.get(key), "expected": value} for key, value in expected.items() if audit.get(key) != value}
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def axis_from_r012() -> list[str]:
    values = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(values, list) or len(values) != EXPECTED_AXIS_COUNT or len(set(values)) != len(values):
        raise ValueError("R012 fixed daily axis is unavailable or invalid")
    return values


def implementation_snapshot() -> dict[str, str]:
    names = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r024.py"), Path("src/n225m_bt/strategies/cash_open_confirmation.py"), Path("tests/test_r024_q001.py")]
    return {str(name): sha256_file(ROOT / name) for name in names}


def direction(condition: Condition, event: dict[str, object]) -> str:
    if condition == "B_all_q_follow" or condition == "A_confirmed_follow":
        return cast(str, event["Q_direction"])
    if condition == "C_confirmed_long":
        return "long"
    if condition == "D_confirmed_short":
        return "short"
    return cast(str, event["F_reverse_direction"])


def run_condition(data: ResearchData, calendar: TSECashMarketCalendar, engine: BacktestEngine, condition: Condition, quarantined: set[tuple[date, Session]], targets: list[str]) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    for text_day in targets:
        trade_day, key = date.fromisoformat(text_day), (date.fromisoformat(text_day), Session.DAY)
        event = cash_open_confirmation_event(trade_day, groups.get(key), calendar, day_quarantined=key in quarantined)
        base_status = cast(str, event["status"])
        event.update({"condition": condition, "base_event_status": base_status, "pre_event_status": base_status})
        audit[f"base_{base_status}"] += 1
        tradable = base_status == "confirmed" or (condition == "B_all_q_follow" and base_status == "nonconfirmed")
        if not tradable:
            event.update({"status": "skipped", "reason": "NONCONFIRMATION_FILTER" if base_status == "nonconfirmed" else event.get("reason")})
            audit[f"no_trade_{event['reason']}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(groups[key], CashOpenConfirmationStrategy(f"r024_q001_{condition}", signal, direction(condition, event)), canonical_hash({"condition": condition, "event": event, "data_version": data.data_version}))
        if len(result.trades) > 1:
            raise AssertionError("R024 produced more than one day trade")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update({"status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_delay_minutes": int((trade.entry_ts - signal).total_seconds() // 60 - 1), "exit_delay_minutes": int((trade.exit_ts - signal).total_seconds() // 60 - 26), "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy})
            trades.append(trade)
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
        else:
            event["status"] = "eligible_order_unfilled"
            audit["eligible_order_unfilled"] += 1
        events.append(event)
    ordered = sorted(trades, key=lambda item: item.entry_ts)
    return tuple(replace(trade, trade_id=f"trade-{number:06d}") for number, trade in enumerate(ordered, 1)), events, dict(sorted(audit.items()))


def daily(trades: tuple[Trade, ...], targets: list[str]) -> dict[str, int]:
    values = dict.fromkeys(targets, 0)
    for trade in trades:
        values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def write_condition(folder: Path, condition: str, trades: tuple[Trade, ...], events: list[dict[str, object]], audit: dict[str, int], data: ResearchData, ticks: int, net_daily: dict[str, int]) -> dict[str, object]:
    metrics = research_metrics(trades, data.bars)
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "status": "complete", "data_version": data.data_version, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(net_daily), "net_pnl_jpy": [net_daily[day] for day in sorted(net_daily)]}).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int) -> dict[str, object]:
    expected = [item for item in events if item["base_event_status"] in {"confirmed", "nonconfirmed"} and (item["condition"] == "B_all_q_follow" or item["base_event_status"] == "confirmed")]
    filled = [item for item in events if item.get("status") == "filled"]
    checks = {
        "every_expected_order_filled": len(expected) == len(filled),
        "one_trade_per_filled_event": len(filled) == len(trades),
        "no_unfilled_expected_order": not any(item.get("status") == "eligible_order_unfilled" for item in events),
        "entry_at_or_after_0905_within_max_delay": all(0 <= cast(int, item["entry_delay_minutes"]) <= max_delay for item in filled),
        "fixed_0930_signal_exit_within_max_delay": all(item["exit_reason"] == "signal" and 0 <= cast(int, item["exit_delay_minutes"]) <= max_delay for item in filled),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades),
        "no_force_flat_or_end_of_data": all(trade.exit_reason.value not in {"force_flat", "end_of_data"} for trade in trades),
    }
    return {"checks": checks, "all_pass": all(checks.values()), "accounting": "Gross is fill-to-fill slippage-inclusive; attribution is informational and is not deducted again."}


def group_diagnostics(events: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for p_sign in (-1, 1):
        for q_sign in (-1, 1):
            rows = [item for item in events if item.get("P_sign") == p_sign and item.get("Q_sign") == q_sign]
            filled = [item for item in rows if item.get("status") == "filled"]
            net = sum(cast(int, item.get("net_pnl_jpy", 0)) for item in filled)
            output[f"P_{p_sign}_Q_{q_sign}"] = {"pre_event_count": len(rows), "confirmed_event_count": sum(item.get("base_event_status") == "confirmed" for item in rows), "nonconfirmed_event_count": sum(item.get("base_event_status") == "nonconfirmed" for item in rows), "trade_count": len(filled), "gross_pnl_jpy": sum(cast(int, item.get("gross_pnl_jpy", 0)) for item in filled), "slippage_cost_jpy": sum(cast(int, item.get("slippage_cost_jpy", 0)) for item in filled), "fees_jpy": sum(cast(int, item.get("fees_jpy", 0)) for item in filled), "net_pnl_jpy": net, "expectancy_jpy": net / len(filled) if filled else None}
    return output


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    lower, upper = floor(point), ceil(point)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (point - lower)


def bootstrap(values: dict[str, list[int]]) -> dict[str, object]:
    count, block = len(values["A_confirmed_follow"]), 20
    if count != EXPECTED_AXIS_COUNT or any(len(value) != count for value in values.values()):
        raise ValueError("R024 daily series do not use the fixed 1,111 trade-date axis")
    a, b, c, d, f = (values[name] for name in CONDITIONS)
    series = {"A": a, "A_minus_B_all": [x - y for x, y in zip(a, b, strict=True)], "A_minus_C_buy": [x - y for x, y in zip(a, c, strict=True)], "A_minus_D_sell": [x - y for x, y in zip(a, d, strict=True)], "A_minus_F_reverse": [x - y for x, y in zip(a, f, strict=True)]}
    samples: dict[str, list[float]] = {name: [] for name in series}
    rng = Random(20260913)
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        for name, value in series.items():
            samples[name].append(fmean(value[index] for index in indices[:count]))
    return {"method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate", "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10_000, "seed": 20260913, "common_indices_all_conditions": True, "percentile_implementation": "linear interpolation at (n-1)*q", **{name: {"estimate": fmean(value), "ci95_percentile_linear": [percentile(samples[name], .025), percentile(samples[name], .975)]} for name, value in series.items()}}


def preregistration(source: dict[str, object], inputs: dict[str, object], evidence: dict[str, object], implementation: dict[str, str]) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R024-Q001",
        "status": "frozen_before_r024_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development exploration, not independent confirmation or multiplicity-corrected validation.",
        "duplicate_review": {"R001": "general session opening rule", "R002_R003_R008_R009": "different range, compression, or path rules", "R023": "unconditional -sign(P) from 09:00; it has no 09:00--09:04 confirmation filter", "conclusion": "No R001--R023 registration uses sign(P)=sign(Q), Q=09:00--09:04 futures change, then 09:05--09:30 Q-following with these controls."},
        "hypothesis": "When the 08:45--08:59 futures direction P and the 09:00--09:04 futures direction Q agree, Q-following from 09:05 to 09:30 has positive post-cost expectancy and exceeds unfiltered Q-following plus confirmation-event long, short, and Q-reversal controls. This tests no cash price, order flow, arbitrage, liquidity, or participant mechanism.",
        "institution_and_calendar": evidence,
        "fixed_rule": {"day_only": "TSE-open dates only; every condition skips TSE closures.", "prices": "Futures only. P=close_08:59-open_08:45 from 15 contiguous eligible bars; Q=close_09:04-open_09:00 from 5 contiguous eligible bars. P!=0 and Q!=0 are base eligible events. Equal signs are confirmed; opposite signs are nonconfirmed. No magnitude, range, gap, standardization, weekday, year, cash, volume, external, or other filters.", "conditions": {"A": "confirmed only, sign(Q)", "B_all": "all base events, sign(Q)", "C_buy": "confirmed only, always long", "D_sell": "confirmed only, always short", "F_reverse": "confirmed only, -sign(Q)", "A2": "A rerun at 2 ticks/side"}, "orders": "09:04 close signal, earliest 09:05 open entry; 09:29 close EXIT signal, earliest 09:30 open exit. Entry delay never extends exit. One contract, one position, one trade/day; no Stop/Target/re-entry/early exit.", "prohibited": ["confirmation-window or hold-window variants", "threshold rescue", "additional costs or delays", "WFA", "OOS", "Final Holdout"]},
        "inputs": {"physical": inputs, "r004_fixed_quarantine": "45 sessions/27,345 bars isolated; 2,216 sessions/1,326,086 bars retained; required list hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa.", "fixed_daily_axis": {"source": str(R012_AXIS.relative_to(ROOT)), "sha256": sha256_file(R012_AXIS), "count": EXPECTED_AXIS_COUNT, "rule": "day-isolated dates excluded; TSE closure, missing, zero, nonconfirmation A skip, cancel and no-trade stay zero."}, "quality_ceiling": "PASS_LIMITED"},
        "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "diagnostic": "B_all nonconfirmation subset is rerun at zero tick only to distinguish avoided negative gross from saved costs; not used for decision.", "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees; no double slippage deduction."},
        "evaluation": {"bootstrap": {"block_length_trade_dates": 20, "repetitions": 10000, "seed": 20260913, "common_indices": True, "noncircular": True, "tail_truncate": True, "percentile": "linear"}, "information_gate": ["B_all >=700 trades", "A/C_buy/D_sell/F_reverse each >=300", "A long/short each >=100", "nonconfirmed events >=200", "all P-sign x Q-sign groups >=100 base events"], "pass_requires_all_after_information": ["A Net>0", "A PF>1", "A and all four comparison CI lower bounds >0", "A2 expectancy>0", "positive months>=27/54", "A top10-excluded Net>0", "B_all nonconfirmed zero-tick Gross expectancy<0"], "decision": "BLOCKED for input/synthetic/execution/accounting failure; INCONCLUSIVE for information insufficiency; otherwise REJECT if any required result fails; all pass remains INVESTIGATE."},
        "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": implementation, "implementation_files_hash": canonical_hash(implementation), "input_manifest_hash": canonical_hash(inputs)},
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close)
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R024 specification")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    calendar, evidence = freeze_tse_evidence()
    source, implementation, inputs = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"), implementation_snapshot(), input_manifest(data_config.gold_root)
    with ZipFile(OUT / "r024_implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in implementation:
            archive.write(ROOT / name, name)
    plan = preregistration(source, inputs, evidence, implementation)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r024_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": 20260913, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r024_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r024.py", "src/n225m_bt/strategies/cash_open_confirmation.py", "tests/test_r024_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r024.py", "src/n225m_bt/strategies/cash_open_confirmation.py"]}
    validation: dict[str, Any] = {"coverage": ["TSE/OSE distinction, JST/trade_date, 15/5 contiguous bars, P/Q signs and zero, four sign groups, confirmation/nonconfirmation, missing/quarantine/range rejection, 09:05 signal causality and prefix invariance", "same P changing Q, same Q changing P, same-sign amplitude invariance", "A/B confirmed equality; A skip and B trade on nonconfirmation; next-bar entry, fixed exit, non-extension, one position, A/A2 event-direction identity, costs and no double slippage", "Final Holdout input rejection"]}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R024 validation failed before Development price access")
    targets = axis_from_r012()
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, quarantined = quarantine(development)
    day_isolated = {day.isoformat() for day, session in quarantined if session is Session.DAY}
    if set(targets) != {bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY} - day_isolated:
        raise ValueError("R024 fixed 1,111-day axis does not equal Development day universe excluding isolated days")
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_trade_dates": targets, "physical_io": "Development selected normalized Parquet only; OOS/Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only"})
    baseline_engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, calendar, baseline_engine, condition, quarantined, targets)
        net_daily = daily(trades, targets)
        metrics = write_condition(OUT / condition, condition, trades, events, audit, view, 1, net_daily)
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, net_daily
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    stress = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    a2_trades, a2_events, a2_audit = run_condition(view, calendar, BacktestEngine(instrument.instrument.to_spec(), stress, classifier), "A_confirmed_follow", quarantined, targets)
    a2_daily = daily(a2_trades, targets)
    a2_metrics = write_condition(OUT / "A_confirmed_follow_2tick", "A_confirmed_follow_2tick", a2_trades, a2_events, a2_audit, view, 2, a2_daily)
    results["A_confirmed_follow_2tick"] = {"trade_count": len(a2_trades), "metrics": a2_metrics, "execution_audit": a2_audit}
    zero = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 0})})
    b0_trades, b0_events, b0_audit = run_condition(view, calendar, BacktestEngine(instrument.instrument.to_spec(), zero, classifier), "B_all_q_follow", quarantined, targets)
    b0_metrics = write_condition(OUT / "diagnostics" / "B_all_q_follow_0tick", "B_all_q_follow_0tick_diagnostic", b0_trades, b0_events, b0_audit, view, 0, daily(b0_trades, targets))
    confirmed_a = {item["trade_date"]: item for item in events_by["A_confirmed_follow"] if item.get("status") == "filled"}
    confirmed_b = {item["trade_date"]: item for item in events_by["B_all_q_follow"] if item.get("base_event_status") == "confirmed"}
    nonconfirmed_a = [item for item in events_by["A_confirmed_follow"] if item.get("base_event_status") == "nonconfirmed"]
    nonconfirmed_b = [item for item in events_by["B_all_q_follow"] if item.get("base_event_status") == "nonconfirmed"]
    fields = ("side", "E_planned_entry_jst", "X_planned_exit_jst", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")
    a_b_confirmed_equal = set(confirmed_a) == set(confirmed_b) and all(tuple(confirmed_a[day].get(field) for field in fields) == tuple(confirmed_b[day].get(field) for field in fields) for day in confirmed_a)
    nonconfirmed_b_daily = dict.fromkeys(targets, 0)
    for item in nonconfirmed_b:
        nonconfirmed_b_daily[cast(str, item["trade_date"])] += cast(int, item.get("net_pnl_jpy", 0))
    daily_difference_identity = all(daily_by["A_confirmed_follow"][day] - daily_by["B_all_q_follow"][day] == -nonconfirmed_b_daily[day] for day in targets)
    a_a2_fields = ("trade_date", "base_event_status", "P_points", "Q_points", "Q_direction", "E_planned_entry_jst", "X_planned_exit_jst")
    a_a2_equal = len(a2_events) == len(events_by["A_confirmed_follow"]) and all(tuple(row.get(field) for field in a_a2_fields) == tuple(events_by["A_confirmed_follow"][index].get(field) for field in a_a2_fields) for index, row in enumerate(a2_events))
    audits = {name: path_audit(events_by[name], trades_by[name], baseline.execution.max_fill_delay_minutes) for name in CONDITIONS} | {"A_confirmed_follow_2tick": path_audit(a2_events, a2_trades, baseline.execution.max_fill_delay_minutes), "B_all_q_follow_0tick_diagnostic": path_audit(b0_events, b0_trades, baseline.execution.max_fill_delay_minutes)}
    post = {"status": "PASS" if a_b_confirmed_equal and daily_difference_identity and a_a2_equal and all(cast(bool, audit["all_pass"]) for audit in audits.values()) and all(item.get("status") == "skipped" for item in nonconfirmed_a) and all(item.get("status") == "filled" for item in nonconfirmed_b) else "BLOCKED", "conditions": audits, "A_B_all_confirmed_signal_side_schedule_path_and_1tick_pnl_identical": a_b_confirmed_equal, "A_B_all_daily_difference_equals_negative_B_all_nonconfirmation": daily_difference_identity, "A_nonconfirmation_has_no_order": all(item.get("status") == "skipped" for item in nonconfirmed_a), "B_all_nonconfirmation_trades": all(item.get("status") == "filled" for item in nonconfirmed_b), "A_A2_identical_pre_event_and_direction": a_a2_equal, "policy": "No successful-fill intersection is selected; any execution or accounting failure blocks."}
    write_json(OUT / "post_execution_validation.json", post)
    diagnostics = {name: group_diagnostics(events_by[name]) for name in CONDITIONS} | {"A_confirmed_follow_2tick": group_diagnostics(a2_events), "B_all_q_follow_0tick_diagnostic": group_diagnostics(b0_events)}
    b0_nonconfirmed = [item for item in b0_events if item.get("base_event_status") == "nonconfirmed" and item.get("status") == "filled"]
    b0_gross = sum(cast(int, item.get("gross_pnl_jpy", 0)) for item in b0_nonconfirmed)
    write_json(OUT / "event_sign_diagnostics.json", diagnostics)
    write_json(OUT / "nonconfirmation_diagnostic.json", {"B_all_1tick": group_diagnostics(events_by["B_all_q_follow"]), "B_all_nonconfirmation_0tick": {"trade_count": len(b0_nonconfirmed), "gross_pnl_jpy": b0_gross, "gross_expectancy_jpy": b0_gross / len(b0_nonconfirmed) if b0_nonconfirmed else None, "purpose": "diagnostic only; distinguishes negative gross avoided from fee-only trade reduction."}})
    values = {name: [daily_by[name][day] for day in targets] for name in CONDITIONS}
    boot = bootstrap(values)
    months = {f"{year:04d}-{month:02d}": 0 for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    for day, value in daily_by["A_confirmed_follow"].items():
        months[day[:7]] += value
    a_metrics = cast(dict[str, Any], results["A_confirmed_follow"])["metrics"]
    a_overall = cast(dict[str, Any], a_metrics)["overall"]
    a2_overall = cast(dict[str, Any], a2_metrics)["overall"]
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_confirmed_follow"])
    base_group_counts = group_diagnostics(events_by["B_all_q_follow"])
    def lower(name: str) -> bool:
        return cast(list[float], boot[name]["ci95_percentile_linear"])[0] > 0
    gates = {"B_all_trade_count_at_least_700": len(trades_by["B_all_q_follow"]) >= 700, "A_trade_count_at_least_300": len(trades_by["A_confirmed_follow"]) >= 300, "C_buy_trade_count_at_least_300": len(trades_by["C_confirmed_long"]) >= 300, "D_sell_trade_count_at_least_300": len(trades_by["D_confirmed_short"]) >= 300, "F_reverse_trade_count_at_least_300": len(trades_by["F_confirmed_reverse"]) >= 300, "A_long_at_least_100": long_count >= 100, "A_short_at_least_100": len(trades_by["A_confirmed_follow"]) - long_count >= 100, "nonconfirmed_events_at_least_200": len(nonconfirmed_b) >= 200, "each_P_Q_sign_group_at_least_100": all(cast(int, value["pre_event_count"]) >= 100 for value in base_group_counts.values()), "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower("A"), "A_minus_B_all_bootstrap_lower_above_0": lower("A_minus_B_all"), "A_minus_C_buy_bootstrap_lower_above_0": lower("A_minus_C_buy"), "A_minus_D_sell_bootstrap_lower_above_0": lower("A_minus_D_sell"), "A_minus_F_reverse_bootstrap_lower_above_0": lower("A_minus_F_reverse"), "A_2tick_expectancy_positive": a2_overall["expectancy_jpy"] is not None and a2_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics)["concentration"]["net_excluding_top10_jpy"] > 0, "B_all_nonconfirmed_0tick_gross_expectancy_negative": len(b0_nonconfirmed) > 0 and b0_gross / len(b0_nonconfirmed) < 0}
    information = [key for key in gates if "trade_count" in key or key.startswith("A_long") or key.startswith("A_short") or key.startswith("nonconfirmed") or key.startswith("each_P_Q")]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(gates[key] for key in information) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": targets, **values, "no_trade": "0", "day_session_quarantined": "excluded", "TSE_closed_missing_zero_nonconfirmation_cancel": "zero"})
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "A_direction_counts": {"long": long_count, "short": len(trades_by["A_confirmed_follow"]) - long_count}, "positive_months_of_54": sum(value > 0 for value in months.values()), "monthly_A_net_jpy": months, "conditions": results, "A2": {"metrics": a2_metrics, "execution_audit": a2_audit}, "B_all_0tick_diagnostic": b0_metrics, "P_Q_sign_diagnostics": diagnostics, "A_year_month_long_short_metrics": {"year": a_metrics["segments"]["year"], "month": a_metrics["segments"]["month"], "side": a_metrics["segments"]["side"]}, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
