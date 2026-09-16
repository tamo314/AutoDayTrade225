"""Execute the preregistered Development-only R031-Q001 experiment."""

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
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r031 import night_cash_open_confirmation_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.night_cash_open_confirmation import NightCashOpenConfirmationStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r031-q001-20260914-night-total-cash-open-confirmation-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
R021 = ROOT / "results" / "research" / "r021-q001-20260914-opening-cash-close-followthrough-05"
R012_AXIS = ROOT / "results" / "research" / "r012-q001-20260914-night-direction-followthrough-01" / "daily_net_pnl_aligned.json"
EXPECTED_AXIS_COUNT = 1_111
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal["A_confirmed_night_follow", "B_night_all", "C_cash_all", "D_confirmed_buy", "E_confirmed_sell", "F_confirmed_reverse"]
CONDITIONS: tuple[Condition, ...] = ("A_confirmed_night_follow", "B_night_all", "C_cash_all", "D_confirmed_buy", "E_confirmed_sell", "F_confirmed_reverse")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def axis() -> list[str]:
    values = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if not isinstance(values, list) or len(values) != EXPECTED_AXIS_COUNT or len(set(values)) != len(values):
        raise ValueError("R012 fixed daily axis is unavailable or invalid")
    return cast(list[str], values)


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(gold_root, "development")]
    return {"status": "frozen_before_r031_price_statistics_events_or_pnl", "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS and Final Holdout are prohibited.", "trade_date_range": ["2021-01-01", "2025-06-30"], "files": files, "files_hash": canonical_hash(files)}


def freeze_tse_evidence() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R021 / "institutional_evidence" / "r020_cabinet_office_public_holidays.csv"
    required = (source, R020 / "COMPLETED.json", R021 / "COMPLETED.json")
    if not all(path.exists() for path in required):
        raise ValueError("frozen R020/R021 TSE evidence is unavailable")
    folder = OUT / "institutional_evidence"
    folder.mkdir()
    copied = folder / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    if not (calendar.source_start <= date(2021, 1, 1) and calendar.source_end >= date(2025, 6, 30)):
        raise ValueError("frozen TSE evidence does not cover Development")
    evidence = {"holiday_csv_sha256": digest(copied), "r020_completed_sha256": digest(R020 / "COMPLETED.json"), "r021_completed_sha256": digest(R021 / "COMPLETED.json"), "rule": "TSE open only on weekdays not in frozen Cabinet Office holidays, excluding Jan 1-3 and Dec 31; never inferred from OSE observations."}
    write_json(folder / "evidence_manifest.json", evidence)
    return calendar, cast(dict[str, object], evidence | {"evidence_manifest_sha256": digest(folder / "evidence_manifest.json")})


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [row for key, rows in groups.items() if key not in isolated for row in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {"parent_data_version": data.data_version, "parent_bars": len(data.bars), "quarantined_bars": len(data.bars) - len(included), "quarantined_sessions": len(isolated), "included_bars": len(included), "included_sessions": len(groups) - len(isolated), "quarantined_session_list": listed, "quarantined_session_list_hash": canonical_hash(listed), "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included), "rule": "exclude whole (trade_date, session) for any TICK_GRID_VIOLATION"}
    expected = {"parent_data_version": EXPECTED_PARENT_VERSION, "quarantined_sessions": 45, "quarantined_bars": 27_345, "included_sessions": 2_216, "included_bars": 1_326_086, "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH, "included_tick_grid_violations": 0}
    mismatches = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def implementation() -> dict[str, str]:
    names = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r031.py"), Path("src/n225m_bt/strategies/night_cash_open_confirmation.py"), Path("tests/test_r031_q001.py")]
    return {str(name): digest(ROOT / name) for name in names}


def preregistration(source: dict[str, object], inputs: dict[str, object], evidence: dict[str, object], files: dict[str, str]) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER, "study_id": "R031-Q001", "status": "frozen_before_r031_price_statistics_events_or_pnl", "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation or multiplicity-corrected validation.",
        "duplicate_review": {"R001_R030": "No equivalent registered rule exists. R024 uses 08:45--08:59 direction, R026/R027 use night extremes, and R006 uses the night-close to 08:45 gap; none conditions the full scheduled night open-to-final-normal-close direction on 09:00--09:04 agreement and trades 09:05--10:05.", "conclusion": "Proceed only with this one fixed specification; no alternatives."},
        "hypothesis": "If the complete prior scheduled night session open-to-final-normal-close direction S agrees with 09:00--09:04 futures direction Q, following S from 09:05 to 10:05 has post-cost expectancy and exceeds unconfirmed night-following, unconfirmed cash-five-minute-following, confirmed-event buy, sell, and reverse controls. It tests persistent futures price discovery only and does not identify cash prices, basis, order flow, volume, liquidity, arbitrage, or participant behavior.",
        "institution_and_calendar": evidence,
        "fixed_rule": {"event": "TSE-open day only. Exactly one night scheduled for the same trade_date is selected from the versioned ExchangeCalendar; no older-night fallback. Require every scheduled minute from night open through its final normal bar and five contiguous eligible day bars 09:00--09:04. S=final-normal-night-close-night-open, Q=close_09:04-open_09:00. S and Q nonzero are base events; same sign is confirmed, opposite sign nonconfirmed.", "conditions": {"A": "confirmed sign(S)", "B_night": "all base sign(S)", "C_cash": "all base sign(Q)", "D_buy": "confirmed always long", "E_sell": "confirmed always short", "F_reverse": "confirmed -sign(S)", "A2": "A at 2 tick/side"}, "orders": "09:04 close signal; earliest 09:05 open entry. 10:04 close EXIT signal; earliest 10:05 open fixed exit. Entry delay never extends exit. One contract, one position and one trade/day; no Stop/Target/re-entry/updates/early exit.", "prohibited": ["night definition, confirmation window, direction, hold-window or threshold rescue", "additional delay/cost", "WFA", "OOS", "Final Holdout"]},
        "inputs": {"physical": inputs, "r004_fixed_quarantine": "45 sessions/27,345 bars excluded; 2,216 sessions/1,326,086 bars retained; list hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa.", "fixed_daily_axis": {"source": str(R012_AXIS.relative_to(ROOT)), "sha256": digest(R012_AXIS), "count": EXPECTED_AXIS_COUNT, "rule": "exclude day-isolated dates only; TSE closure, missing reference/window, zero, A nonconfirmation, cancel and no trade remain zero."}, "quality_ceiling": "PASS_LIMITED"},
        "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "diagnostics": "B_night and C_cash nonconfirmation subsets rerun at zero tick only; not a decision input except their gross expectancy signs.", "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; slippage attribution is never deducted again."},
        "evaluation": {"bootstrap": {"block_length_trade_dates": 20, "repetitions": 10_000, "seed": 20260913, "common_indices": True, "noncircular": True, "tail_truncate": True, "percentile": "linear"}, "information_gate": ["B_night/C_cash >=700", "A/D/E/F >=300", "A long/short >=100", "nonconfirmed >=250", "each S-sign x Q-sign group >=100"], "pass_requires_all_after_information": ["A Net>0", "A PF>1", "A and all five control-difference CI lower bounds >0", "A2 expectancy>0", "positive months>=27/54", "A top10-excluded Net>0", "both nonconfirmed B_night and C_cash zero-tick Gross expectancy<0"], "decision": "BLOCKED for input/synthetic/execution/accounting failure; INCONCLUSIVE for information insufficiency; otherwise REJECT if any required result fails; all pass remains INVESTIGATE only."},
        "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": files, "implementation_files_hash": canonical_hash(files), "input_manifest_hash": canonical_hash(inputs), "seed": 20260913}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
    }


def direction(condition: Condition, event: dict[str, object]) -> str:
    if condition in {"A_confirmed_night_follow", "B_night_all"}:
        return cast(str, event["S_direction"])
    if condition == "C_cash_all":
        return cast(str, event["Q_direction"])
    if condition == "D_confirmed_buy":
        return "long"
    if condition == "E_confirmed_sell":
        return "short"
    return cast(str, event["F_reverse_direction"])


def run_condition(data: ResearchData, calendar: TSECashMarketCalendar, engine: BacktestEngine, condition: Condition, quarantined: set[tuple[date, Session]], targets: list[str]) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    for text_day in targets:
        trade_day, day_key, night_key = date.fromisoformat(text_day), (date.fromisoformat(text_day), Session.DAY), (date.fromisoformat(text_day), Session.NIGHT)
        event = night_cash_open_confirmation_event(engine.classifier, calendar, trade_day, groups.get(day_key), groups.get(night_key), day_quarantined=day_key in quarantined, night_quarantined=night_key in quarantined)
        base = cast(str, event["status"])
        event.update({"condition": condition, "base_event_status": base, "pre_event_status": base})
        audit[f"base_{base}"] += 1
        tradable = base == "confirmed" or (condition in {"B_night_all", "C_cash_all"} and base == "nonconfirmed")
        if not tradable:
            event.update({"status": "skipped", "reason": "NONCONFIRMATION_FILTER" if base == "nonconfirmed" else event.get("reason")})
            audit[f"no_trade_{event['reason']}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(groups[day_key], NightCashOpenConfirmationStrategy(f"r031_q001_{condition}", signal, direction(condition, event)), canonical_hash({"condition": condition, "event": event, "data_version": data.data_version}))
        if len(result.trades) > 1:
            raise AssertionError("R031 produced more than one day trade")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update({"status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "entry_delay_minutes": int((trade.entry_ts - signal).total_seconds() // 60 - 1), "exit_delay_minutes": int((trade.exit_ts - signal).total_seconds() // 60 - 61), "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy})
            trades.append(trade)
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
        else:
            event["status"] = "eligible_order_unfilled"
            audit["eligible_order_unfilled"] += 1
        events.append(event)
    ordered = sorted(trades, key=lambda trade: trade.entry_ts)
    return tuple(replace(trade, trade_id=f"trade-{number:06d}") for number, trade in enumerate(ordered, 1)), events, dict(sorted(audit.items()))


def daily(trades: tuple[Trade, ...], targets: list[str]) -> dict[str, int]:
    values = dict.fromkeys(targets, 0)
    for trade in trades:
        values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def write_condition(folder: Path, condition: str, trades: tuple[Trade, ...], events: list[dict[str, object]], audit: dict[str, int], data: ResearchData, ticks: int, daily_values: dict[str, int]) -> dict[str, object]:
    from n225m_bt.research.metrics import research_metrics

    metrics = research_metrics(trades, data.bars)
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "status": "complete", "data_version": data.data_version, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(daily_values), "net_pnl_jpy": [daily_values[day] for day in sorted(daily_values)]}).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    low, high = floor(point), ceil(point)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def bootstrap(values: dict[str, list[int]]) -> dict[str, object]:
    count, block = len(values["A_confirmed_night_follow"]), 20
    if count != EXPECTED_AXIS_COUNT or any(len(value) != count for value in values.values()):
        raise ValueError("R031 daily series do not use the fixed 1,111-day axis")
    a = values["A_confirmed_night_follow"]
    series = {"A_daily_mean_net_jpy": a} | {f"A_minus_{name}_daily_mean_net_jpy": [left - right for left, right in zip(a, values[name], strict=True)] for name in CONDITIONS if name != "A_confirmed_night_follow"}
    samples: dict[str, list[float]] = {name: [] for name in series}
    rng = Random(20260913)
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        for name, values_ in series.items():
            samples[name].append(fmean(values_[index] for index in indices[:count]))
    return {"method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate", "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10_000, "seed": 20260913, "common_indices_all_conditions": True, "percentile_implementation": "linear interpolation at (n-1)*q", **{name: {"estimate": fmean(series[name]), "ci95_percentile_linear": [percentile(samples[name], .025), percentile(samples[name], .975)]} for name in series}}


def sign_diagnostics(events: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for s_sign in (-1, 1):
        for q_sign in (-1, 1):
            rows = [row for row in events if row.get("S_sign") == s_sign and row.get("Q_sign") == q_sign]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = sum(cast(int, row.get("net_pnl_jpy", 0)) for row in filled)
            output[f"S_{s_sign}_Q_{q_sign}"] = {"pre_event_count": len(rows), "confirmed_event_count": sum(row.get("base_event_status") == "confirmed" for row in rows), "nonconfirmed_event_count": sum(row.get("base_event_status") == "nonconfirmed" for row in rows), "trade_count": len(filled), "gross_pnl_jpy": sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in filled), "fees_jpy": sum(cast(int, row.get("fees_jpy", 0)) for row in filled), "net_pnl_jpy": net, "expectancy_jpy": net / len(filled) if filled else None}
    return output


def path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int) -> dict[str, object]:
    expected = [row for row in events if row["base_event_status"] in {"confirmed", "nonconfirmed"} and (row["condition"] in {"B_night_all", "C_cash_all"} or row["base_event_status"] == "confirmed")]
    filled = [row for row in events if row.get("status") == "filled"]
    checks = {"every_expected_order_filled": len(expected) == len(filled), "one_trade_per_filled_event": len(filled) == len(trades), "no_unfilled_expected_order": not any(row.get("status") == "eligible_order_unfilled" for row in events), "entry_at_or_after_0905_within_max_delay": all(0 <= cast(int, row["entry_delay_minutes"]) <= max_delay for row in filled), "fixed_1005_signal_exit_within_max_delay": all(row["exit_reason"] == "signal" and 0 <= cast(int, row["exit_delay_minutes"]) <= max_delay for row in filled), "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades), "no_force_flat_or_end_of_data": all(trade.exit_reason.value not in {"force_flat", "end_of_data"} for trade in trades)}
    return {"checks": checks, "all_pass": all(checks.values()), "accounting": "Gross is fill-to-fill slippage-inclusive; attribution is informational and not deducted again."}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r031_q001_night_cash_open_confirmation.py')

    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close) != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from R031 frozen specification")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    calendar, evidence = freeze_tse_evidence()
    source, files, inputs = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"), implementation(), input_manifest(data_config.gold_root)
    with ZipFile(OUT / "r031_implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT / name, name)
    plan = preregistration(source, inputs, evidence, files)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r031_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": 20260913, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r031_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r031.py", "src/n225m_bt/strategies/night_cash_open_confirmation.py", "tests/test_r031_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r031.py", "src/n225m_bt/strategies/night_cash_open_confirmation.py"]}
    validation: dict[str, Any] = {"coverage": ["night-to-day mapping, TSE closure/weekend, JST/calendar_date/trade_date and schedule changes", "all scheduled night normal minutes/final normal bar, S/Q signs and zero, four sign groups, confirmation/nonconfirmation, missing/quarantine/development rejection, 09:04 causality and prefix", "same S changing Q and same Q changing S", "A/B/C equality on confirmation; A no order and B/C opposite sides on nonconfirmation; next-bar entry, fixed 10:05 exit, non-extension, A/A2 identity, one position, costs/no double slippage", "Final Holdout rejection"]}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R031 validation failed before Development price access")
    targets, development = axis(), load_split(data_config.gold_root, "development")
    view, quarantine_audit, quarantined = quarantine(development)
    day_isolated = {day.isoformat() for day, session in quarantined if session is Session.DAY}
    if set(targets) != {bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY} - day_isolated:
        raise ValueError("R031 fixed 1,111-day axis does not equal Development day universe excluding isolated days")
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_trade_dates": targets, "physical_io": "Development selected normalized Parquet only; OOS/Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only"})
    baseline_engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, calendar, baseline_engine, condition, quarantined, targets)
        values = daily(trades, targets)
        metrics = write_condition(OUT / condition, condition, trades, events, audit, view, 1, values)
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, values
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    stress = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    a2_trades, a2_events, a2_audit = run_condition(view, calendar, BacktestEngine(instrument.instrument.to_spec(), stress, classifier), "A_confirmed_night_follow", quarantined, targets)
    a2_metrics = write_condition(OUT / "A_confirmed_night_follow_2tick", "A_confirmed_night_follow_2tick", a2_trades, a2_events, a2_audit, view, 2, daily(a2_trades, targets))
    zero = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 0})})
    b0_trades, b0_events, b0_audit = run_condition(view, calendar, BacktestEngine(instrument.instrument.to_spec(), zero, classifier), "B_night_all", quarantined, targets)
    c0_trades, c0_events, c0_audit = run_condition(view, calendar, BacktestEngine(instrument.instrument.to_spec(), zero, classifier), "C_cash_all", quarantined, targets)
    b0_metrics = write_condition(OUT / "diagnostics" / "B_night_all_0tick", "B_night_all_0tick", b0_trades, b0_events, b0_audit, view, 0, daily(b0_trades, targets))
    c0_metrics = write_condition(OUT / "diagnostics" / "C_cash_all_0tick", "C_cash_all_0tick", c0_trades, c0_events, c0_audit, view, 0, daily(c0_trades, targets))
    confirmed_a = {row["trade_date"]: row for row in events_by["A_confirmed_night_follow"] if row.get("status") == "filled"}
    confirmed_b, confirmed_c = {row["trade_date"]: row for row in events_by["B_night_all"] if row.get("base_event_status") == "confirmed"}, {row["trade_date"]: row for row in events_by["C_cash_all"] if row.get("base_event_status") == "confirmed"}
    non_a = [row for row in events_by["A_confirmed_night_follow"] if row.get("base_event_status") == "nonconfirmed"]
    non_b = [row for row in events_by["B_night_all"] if row.get("base_event_status") == "nonconfirmed"]
    non_c = [row for row in events_by["C_cash_all"] if row.get("base_event_status") == "nonconfirmed"]
    fields = ("side", "E_planned_entry_jst", "X_planned_exit_jst", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")
    confirmed_equal = all(set(confirmed_a) == set(control) and all(tuple(confirmed_a[key].get(field) for field in fields) == tuple(control[key].get(field) for field in fields) for key in confirmed_a) for control in (confirmed_b, confirmed_c))
    def non_daily(rows: list[dict[str, object]]) -> dict[str, int]:
        values = dict.fromkeys(targets, 0)
        for row in rows:
            values[cast(str, row["trade_date"])] += cast(int, row.get("net_pnl_jpy", 0))
        return values
    b_non_daily, c_non_daily = non_daily(non_b), non_daily(non_c)
    identities = all(daily_by["A_confirmed_night_follow"][day] - daily_by["B_night_all"][day] == -b_non_daily[day] and daily_by["A_confirmed_night_follow"][day] - daily_by["C_cash_all"][day] == -c_non_daily[day] for day in targets)
    a2_fields = ("trade_date", "base_event_status", "S_points", "Q_points", "S_direction", "E_planned_entry_jst", "X_planned_exit_jst")
    a2_equal = len(a2_events) == len(events_by["A_confirmed_night_follow"]) and all(tuple(row.get(field) for field in a2_fields) == tuple(events_by["A_confirmed_night_follow"][index].get(field) for field in a2_fields) for index, row in enumerate(a2_events))
    audits = {name: path_audit(events_by[name], trades_by[name], baseline.execution.max_fill_delay_minutes) for name in CONDITIONS} | {"A2": path_audit(a2_events, a2_trades, baseline.execution.max_fill_delay_minutes), "B0": path_audit(b0_events, b0_trades, baseline.execution.max_fill_delay_minutes), "C0": path_audit(c0_events, c0_trades, baseline.execution.max_fill_delay_minutes)}
    opposite_non = all(row.get("status") == "skipped" for row in non_a) and all(left.get("status") == right.get("status") == "filled" and left.get("side") != right.get("side") for left, right in zip(non_b, non_c, strict=True))
    post = {"status": "PASS" if confirmed_equal and identities and a2_equal and opposite_non and all(cast(bool, audit["all_pass"]) for audit in audits.values()) else "BLOCKED", "conditions": audits, "A_B_night_C_cash_confirmed_signal_side_schedule_path_and_1tick_pnl_identical": confirmed_equal, "A_minus_B_and_A_minus_C_equal_negative_nonconfirmation_control_daily_pnl": identities, "nonconfirmation_A_no_order_B_C_opposite_side_trades": opposite_non, "A_A2_identical_pre_event_and_direction": a2_equal, "policy": "No fill intersection selection; any execution or accounting failure blocks."}
    write_json(OUT / "post_execution_validation.json", post)
    diagnostics = {name: sign_diagnostics(events_by[name]) for name in CONDITIONS} | {"A2": sign_diagnostics(a2_events), "B0": sign_diagnostics(b0_events), "C0": sign_diagnostics(c0_events)}
    b0_non, c0_non = [row for row in b0_events if row.get("base_event_status") == "nonconfirmed" and row.get("status") == "filled"], [row for row in c0_events if row.get("base_event_status") == "nonconfirmed" and row.get("status") == "filled"]
    b0_gross, c0_gross = sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in b0_non), sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in c0_non)
    write_json(OUT / "event_sign_diagnostics.json", diagnostics)
    write_json(OUT / "nonconfirmation_diagnostic.json", {"B_night_nonconfirmed_0tick": {"trade_count": len(b0_non), "gross_pnl_jpy": b0_gross, "gross_expectancy_jpy": b0_gross / len(b0_non) if b0_non else None}, "C_cash_nonconfirmed_0tick": {"trade_count": len(c0_non), "gross_pnl_jpy": c0_gross, "gross_expectancy_jpy": c0_gross / len(c0_non) if c0_non else None}, "purpose": "diagnostic zero-tick Gross distinguishes avoided adverse raw return from fee saving."})
    bootstrap_values: dict[str, list[int]] = {name: [daily_by[name][day] for day in targets] for name in CONDITIONS}
    boot = bootstrap(bootstrap_values)
    months = {f"{year:04d}-{month:02d}": 0 for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    for day, value in daily_by["A_confirmed_night_follow"].items():
        months[day[:7]] += value
    a_metrics, a2_overall = cast(dict[str, Any], results["A_confirmed_night_follow"])["metrics"], cast(dict[str, Any], a2_metrics)["overall"]
    a_overall, long_count = cast(dict[str, Any], a_metrics)["overall"], sum(trade.side.value == "long" for trade in trades_by["A_confirmed_night_follow"])
    groups = sign_diagnostics(events_by["B_night_all"])
    def lower(name: str) -> bool:
        return cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
    gates = {"B_night_trade_count_at_least_700": len(trades_by["B_night_all"]) >= 700, "C_cash_trade_count_at_least_700": len(trades_by["C_cash_all"]) >= 700, "A_trade_count_at_least_300": len(trades_by["A_confirmed_night_follow"]) >= 300, "D_buy_trade_count_at_least_300": len(trades_by["D_confirmed_buy"]) >= 300, "E_sell_trade_count_at_least_300": len(trades_by["E_confirmed_sell"]) >= 300, "F_reverse_trade_count_at_least_300": len(trades_by["F_confirmed_reverse"]) >= 300, "A_long_at_least_100": long_count >= 100, "A_short_at_least_100": len(trades_by["A_confirmed_night_follow"]) - long_count >= 100, "nonconfirmed_events_at_least_250": len(non_b) >= 250, "each_S_Q_sign_group_at_least_100": all(cast(int, cast(dict[str, object], value)["pre_event_count"]) >= 100 for value in groups.values()), "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"), "A_minus_B_night_bootstrap_lower_above_0": lower("A_minus_B_night_all_daily_mean_net_jpy"), "A_minus_C_cash_bootstrap_lower_above_0": lower("A_minus_C_cash_all_daily_mean_net_jpy"), "A_minus_D_buy_bootstrap_lower_above_0": lower("A_minus_D_confirmed_buy_daily_mean_net_jpy"), "A_minus_E_sell_bootstrap_lower_above_0": lower("A_minus_E_confirmed_sell_daily_mean_net_jpy"), "A_minus_F_reverse_bootstrap_lower_above_0": lower("A_minus_F_confirmed_reverse_daily_mean_net_jpy"), "A_2tick_expectancy_positive": a2_overall["expectancy_jpy"] is not None and a2_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics)["concentration"]["net_excluding_top10_jpy"] > 0, "B_night_nonconfirmed_0tick_gross_expectancy_negative": bool(b0_non) and b0_gross / len(b0_non) < 0, "C_cash_nonconfirmed_0tick_gross_expectancy_negative": bool(c0_non) and c0_gross / len(c0_non) < 0}
    information = [key for key in gates if "trade_count" in key or key.startswith("A_long") or key.startswith("A_short") or key.startswith("nonconfirmed") or key.startswith("each_S_Q")]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(gates[key] for key in information) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": targets, **bootstrap_values, "no_trade": "0", "day_session_quarantined": "excluded", "TSE_closed_missing_zero_nonconfirmation_cancel": "zero"})
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "A_direction_counts": {"long": long_count, "short": len(trades_by["A_confirmed_night_follow"]) - long_count}, "positive_months_of_54": sum(value > 0 for value in months.values()), "monthly_A_net_jpy": months, "conditions": results, "A2": {"metrics": a2_metrics, "execution_audit": a2_audit}, "B_night_0tick": b0_metrics, "C_cash_0tick": c0_metrics, "S_Q_sign_diagnostics": diagnostics, "A_year_month_long_short_metrics": {"year": cast(dict[str, Any], a_metrics)["segments"]["year"], "month": cast(dict[str, Any], a_metrics)["segments"]["month"], "side": cast(dict[str, Any], a_metrics)["segments"]["side"]}, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
