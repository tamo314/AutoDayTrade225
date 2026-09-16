"""Execute the preregistered Development-only R027-Q001 experiment."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from math import ceil, floor
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, Literal, cast
from zipfile import ZIP_DEFLATED, ZipFile

import run_r025_q001_prior_tse_day_range_acceptance as common

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r027 import prior_night_acceptance_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.prior_night_rejection import PriorNightRejectionStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r027-q001-20260914-prior-night-range-acceptance-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
Condition = Literal[
    "A_confirmed_outward",
    "B_all_outward",
    "C_confirmed_buy",
    "D_confirmed_sell",
    "F_confirmed_inward",
]
CONDITIONS: tuple[Condition, ...] = (
    "A_confirmed_outward",
    "B_all_outward",
    "C_confirmed_buy",
    "D_confirmed_sell",
    "F_confirmed_inward",
)


def sha256_file(path: Path) -> str:
    return common.sha256_file(path)


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)} for path in partition_paths(gold_root, "development")]
    return {
        "status": "frozen_before_r027_price_statistics_events_or_pnl",
        "scope": "Selected normalized Development Parquet only; raw, volume, cash/external prices, OOS, and Final Holdout are prohibited.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def implementation_snapshot() -> dict[str, str]:
    names = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r027.py"),
        Path("src/n225m_bt/strategies/prior_night_rejection.py"),
        Path("tests/test_r027_q001.py"),
    ]
    return {str(name): sha256_file(ROOT / name) for name in names}


def direction(condition: Condition, event: dict[str, object]) -> str:
    if condition in {"A_confirmed_outward", "B_all_outward"}:
        return cast(str, event["base_direction"])
    if condition == "C_confirmed_buy":
        return "long"
    if condition == "D_confirmed_sell":
        return "short"
    return cast(str, event["F_inward_direction"])


def run_condition(data: ResearchData, cash: TSECashMarketCalendar, classifier: CalendarClassifier, engine: BacktestEngine, condition: Condition, isolated: set[tuple[date, Session]], targets: list[str]) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    for text_day in targets:
        day = date.fromisoformat(text_day)
        key, night_key = (day, Session.DAY), (day, Session.NIGHT)
        event = prior_night_acceptance_event(classifier, cash, day, groups.get(key), groups.get(night_key), day_quarantined=key in isolated, night_quarantined=night_key in isolated)
        base_status = cast(str, event["status"])
        event.update({"condition": condition, "base_event_status": base_status, "pre_event_status": base_status})
        audit[f"base_{base_status}"] += 1
        tradable = base_status == "confirmed" or (condition == "B_all_outward" and base_status == "nonconfirmed")
        if not tradable:
            event.update({"status": "skipped", "reason": "NONCONFIRMATION_FILTER" if base_status == "nonconfirmed" else event.get("reason")})
            audit[f"no_trade_{event['reason']}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(groups[key], PriorNightRejectionStrategy(f"r027_q001_{condition}", signal, direction(condition, event)), canonical_hash({"condition": condition, "event": event, "data_version": data.data_version}))
        if len(result.trades) > 1:
            raise AssertionError("R027 produced more than one daily trade")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update({
                "status": "filled", "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(),
                "entry_delay_minutes": int((trade.entry_ts - signal).total_seconds() // 60 - 1), "exit_delay_minutes": int((trade.exit_ts - signal).total_seconds() // 60 - 61),
                "exit_reason": trade.exit_reason.value, "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy,
            })
            trades.append(trade)
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
        else:
            event["status"] = "eligible_order_unfilled"
            audit["eligible_order_unfilled"] += 1
        events.append(event)
    ordered = sorted(trades, key=lambda trade: trade.entry_ts)
    return tuple(replace(trade, trade_id=f"trade-{number:06d}") for number, trade in enumerate(ordered, 1)), events, dict(sorted(audit.items()))


def preregistration(source: dict[str, object], inputs: dict[str, object], evidence: dict[str, object], implementation: dict[str, str]) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER, "study_id": "R027-Q001", "status": "frozen_before_r027_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; R026-following known-Development competing-explanation check, not independent confirmation or multiplicity-corrected validation.",
        "duplicate_review": {
            "R001-R024": "No registered rule uses complete immediately preceding scheduled night H/L with the specified 09:00--09:14 one-sided breach and strict exterior 09:14 acceptance.",
            "R025": "prior TSE day range and 08:45/08:59 acceptance; different reference, event and timing.",
            "R026": "same night range/breach/entry/exit base event, but strict 09:14 return inside and inward direction; R027 strict external close and outward direction is a registered competing explanation, not a duplicate.",
            "conclusion": "No equivalent previously registered external-acceptance/outward-follow specification exists.",
        },
        "hypothesis": "If the 09:00--09:14 window breaches exactly one complete immediately preceding scheduled night extreme and its 09:14 close remains strictly outside that breached extreme, outward trading from 09:15 through 10:15 has positive post-cost expectancy and exceeds unconditional outward, always-buy, always-sell and inward controls. The exterior close is a price-acceptance proxy only; it does not identify cash prices, order flow, volume, arbitrage, liquidity or participants.",
        "institution_and_calendar": evidence,
        "fixed_rule": {
            "reference": "For a TSE-open trade_date, n is exactly the schedule-designated night session with the same trade_date; it must be complete by 09:00 and every scheduled normal minute through its scheduled final normal bar must be present, eligible and night-labelled. No observed-bar inference, auction, force-flat, observed-final-row substitute or older-night fallback.",
            "event": "U=max(high), D=min(low) over 15 contiguous eligible day bars 09:00--09:14; K=close_09:14. Upper base: U>H and D>=L, d=long. Lower base: D<L and U<=H, d=short. Both/neither skip. Confirmed iff upper K>H or lower K<L strictly. Equality, in-range or opposite-side K is nonconfirmed. H=L skips. No filters.",
            "conditions": {"A": "confirmed outward d", "B_all": "all base outward d", "C_buy": "confirmed always long", "D_sell": "confirmed always short", "F_inward": "confirmed -d", "A2": "A rerun at 2 ticks/side"},
            "orders": "09:14 close signal / earliest 09:15 entry; 10:14 close EXIT signal / earliest 10:15 exit; delayed entry never extends exit; one contract/position/trade day; no Stop, Target, re-entry or early exit.",
            "prohibited": ["window/range/direction/holding/boundary/threshold rescue", "additional cost or delay", "WFA", "OOS", "Final Holdout"],
        },
        "inputs": {"physical": inputs, "r004_fixed_quarantine": "45 sessions/27,345 bars isolated; 2,216 sessions/1,326,086 bars retained; required hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa.", "fixed_daily_axis": {"source": "results/research/r012-q001-20260914-night-direction-followthrough-01/daily_net_pnl_aligned.json", "sha256": sha256_file(common.R012_AXIS), "count": 1111}, "quality_ceiling": "PASS_LIMITED"},
        "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "diagnostic": "B_all nonconfirmed zero-tick gross only.", "accounting": "Gross is slippage-inclusive fill-to-fill; Net=Gross-fees; no double deduction."},
        "evaluation": {"bootstrap": {"block_length_trade_dates": 20, "repetitions": 10000, "seed": 20260913, "common_indices": True, "noncircular": True, "tail_truncate": True, "percentile": "linear"}, "information_gate": ["B_all>=300", "A/C_buy/D_sell/F_inward>=150", "A long/short>=40", "nonconfirmed>=150", "upper/lower base>=100"], "pass_requires_all_after_information": ["A Net>0", "A PF>1", "A and four comparison CI lower>0", "A2 expectancy>0", "positive months>=27/54", "A excluding top10 Net>0", "B_all nonconfirmed 0tick gross expectancy<0"], "decision": "BLOCKED for gates; INCONCLUSIVE for information insufficiency; else REJECT on any failure; all pass INVESTIGATE."},
        "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": implementation, "implementation_files_hash": canonical_hash(implementation), "input_manifest_hash": canonical_hash(inputs)},
        "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
    }


def audit(events: list[dict[str, object]], trades: tuple[Trade, ...], condition: str, max_delay: int) -> dict[str, object]:
    expected = [row for row in events if row["base_event_status"] in {"confirmed", "nonconfirmed"} and (condition == "B_all_outward" or row["base_event_status"] == "confirmed")]
    filled = [row for row in events if row.get("status") == "filled"]
    checks = {
        "every_expected_order_filled": len(expected) == len(filled), "one_trade_per_filled_event": len(filled) == len(trades), "no_unfilled_expected_order": not any(row.get("status") == "eligible_order_unfilled" for row in events),
        "entry_at_or_after_0915_within_max_delay": all(0 <= cast(int, row["entry_delay_minutes"]) <= max_delay for row in filled),
        "fixed_1015_signal_exit_within_max_delay": all(row["exit_reason"] == "signal" and 0 <= cast(int, row["exit_delay_minutes"]) <= max_delay for row in filled),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades), "no_force_flat_or_end_of_data": all(trade.exit_reason.value not in {"force_flat", "end_of_data"} for trade in trades),
    }
    return {"checks": checks, "all_pass": all(checks.values()), "accounting": "Gross is fill-to-fill slippage-inclusive and attribution is not deducted again."}


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    lower, upper = floor(point), ceil(point)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (point - lower)


def bootstrap(values: dict[str, list[int]]) -> dict[str, object]:
    count, block = len(values["A_confirmed_outward"]), 20
    if count != 1_111 or any(len(value) != count for value in values.values()):
        raise ValueError("R027 daily series do not use the fixed 1,111 trade-date axis")
    a, b, c, d, f = (values[name] for name in CONDITIONS)
    series = {"A": a, "A_minus_B_all": [x - y for x, y in zip(a, b, strict=True)], "A_minus_C_buy": [x - y for x, y in zip(a, c, strict=True)], "A_minus_D_sell": [x - y for x, y in zip(a, d, strict=True)], "A_minus_F_inward": [x - y for x, y in zip(a, f, strict=True)]}
    samples: dict[str, list[float]] = {name: [] for name in series}
    prefixes: dict[str, list[int]] = {}
    for name, value in series.items():
        prefix = [0]
        for item in value:
            prefix.append(prefix[-1] + item)
        prefixes[name] = prefix
    rng = Random(20260913)
    for _ in range(10_000):
        blocks: list[tuple[int, int]] = []
        selected = 0
        while selected < count:
            start = rng.randrange(count - block + 1)
            size = min(block, count - selected)
            blocks.append((start, size))
            selected += size
        for name, prefix in prefixes.items():
            samples[name].append(sum(prefix[start + size] - prefix[start] for start, size in blocks) / count)
    return {"method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate", "target_trade_dates": count, "block_length_trade_dates": block, "repetitions": 10_000, "seed": 20260913, "common_indices_all_conditions": True, "percentile_implementation": "linear interpolation at (n-1)*q", **{name: {"estimate": fmean(value), "ci95_percentile_linear": [percentile(samples[name], 0.025), percentile(samples[name], 0.975)]} for name, value in series.items()}}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r027_q001_prior_night_range_acceptance.py')

    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    common.IDENTIFIER, common.OUT, common.CONDITIONS = IDENTIFIER, OUT, CONDITIONS
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order) != (1, 30, 10, False):
        raise ValueError("active execution/cost contract differs from frozen R027 specification")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    cash, evidence = common.freeze_tse_evidence()
    source, implementation, inputs = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"), implementation_snapshot(), input_manifest(data_config.gold_root)
    with ZipFile(OUT / "r027_implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in implementation:
            archive.write(ROOT / name, name)
    plan = preregistration(source, inputs, evidence, implementation)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r027_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": 20260913, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r027_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r027.py", "tests/test_r027_q001.py", str(Path(__file__).relative_to(ROOT))],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r027.py"],
    }
    validation: dict[str, Any] = {"coverage": ["schedule night-to-day mapping, holidays/weekends, calendar_date/trade_date, scheduled normal night through final bar, H/L, upper/lower/both/neither, strict external/boundary/internal/opposite K, H=L, missing/isolation/outside rejection and prefix", "K-only counterfactual acceptance change", "A/B identity, delayed fixed exit, one position, A/A2, accounting/no-double-slippage and Holdout rejection"]}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R027 validation failed before Development price access")
    targets, development = common.axis(), load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = common.quarantine(development)
    day_isolated = {day.isoformat() for day, session in isolated if session is Session.DAY}
    if set(targets) != {bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY} - day_isolated:
        raise ValueError("fixed 1,111-day axis mismatch")
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_trade_dates": targets, "physical_io": "Development selected normalized Parquet only; OOS/Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only"})
    runs: dict[str, tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int], dict[str, object], dict[str, int]]] = {}
    for condition in CONDITIONS:
        trades, events, execution = run_condition(view, cash, classifier, BacktestEngine(instrument.instrument.to_spec(), baseline, classifier), condition, isolated, targets)
        net = common.daily(trades, targets)
        metrics = common.write_condition(OUT / condition, condition, trades, events, execution, view, 1, net)
        runs[condition] = trades, events, net, metrics, execution
    stress = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    a2_trades, a2_events, a2_execution = run_condition(view, cash, classifier, BacktestEngine(instrument.instrument.to_spec(), stress, classifier), "A_confirmed_outward", isolated, targets)
    a2_metrics = common.write_condition(OUT / "A_confirmed_outward_2tick", "A_confirmed_outward_2tick", a2_trades, a2_events, a2_execution, view, 2, common.daily(a2_trades, targets))
    zero = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 0})})
    b0_trades, b0_events, b0_execution = run_condition(view, cash, classifier, BacktestEngine(instrument.instrument.to_spec(), zero, classifier), "B_all_outward", isolated, targets)
    b0_metrics = common.write_condition(OUT / "diagnostics" / "B_all_outward_0tick", "B_all_outward_0tick_diagnostic", b0_trades, b0_events, b0_execution, view, 0, common.daily(b0_trades, targets))
    a_events, b_events = runs["A_confirmed_outward"][1], runs["B_all_outward"][1]
    confirmed_a = {row["trade_date"]: row for row in a_events if row.get("status") == "filled"}
    confirmed_b = {row["trade_date"]: row for row in b_events if row.get("base_event_status") == "confirmed"}
    non_a = [row for row in a_events if row.get("base_event_status") == "nonconfirmed"]
    non_b = [row for row in b_events if row.get("base_event_status") == "nonconfirmed"]
    fields = ("side", "E_planned_entry_jst", "X_planned_exit_jst", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")
    same_confirmed = set(confirmed_a) == set(confirmed_b) and all(tuple(confirmed_a[day].get(field) for field in fields) == tuple(confirmed_b[day].get(field) for field in fields) for day in confirmed_a)
    non_daily = dict.fromkeys(targets, 0)
    for row in non_b:
        non_daily[cast(str, row["trade_date"])] += cast(int, row.get("net_pnl_jpy", 0))
    daily_identity = all(runs["A_confirmed_outward"][2][day] - runs["B_all_outward"][2][day] == -non_daily[day] for day in targets)
    a2_fields = ("trade_date", "base_event_status", "H_points", "L_points", "U_points", "D_points", "K_close_0914_points", "base_direction", "E_planned_entry_jst", "X_planned_exit_jst")
    a2_same = len(a2_events) == len(a_events) and all(tuple(row.get(field) for field in a2_fields) == tuple(a_events[index].get(field) for field in a2_fields) for index, row in enumerate(a2_events))
    audits = {condition: audit(runs[condition][1], runs[condition][0], condition, baseline.execution.max_fill_delay_minutes) for condition in CONDITIONS} | {"A_confirmed_outward_2tick": audit(a2_events, a2_trades, "A_confirmed_outward", baseline.execution.max_fill_delay_minutes), "B_all_outward_0tick_diagnostic": audit(b0_events, b0_trades, "B_all_outward", baseline.execution.max_fill_delay_minutes)}
    post = {"status": "PASS" if same_confirmed and daily_identity and a2_same and all(cast(bool, item["all_pass"]) for item in audits.values()) and all(row.get("status") == "skipped" for row in non_a) and all(row.get("status") == "filled" for row in non_b) else "BLOCKED", "conditions": audits, "A_B_all_confirmed_signal_side_schedule_path_and_1tick_pnl_identical": same_confirmed, "A_B_all_daily_difference_equals_negative_B_all_nonconfirmation": daily_identity, "A_nonconfirmation_has_no_order": all(row.get("status") == "skipped" for row in non_a), "B_all_nonconfirmation_trades": all(row.get("status") == "filled" for row in non_b), "A_A2_identical_pre_event_and_direction": a2_same, "policy": "No successful-fill intersection is selected; execution/accounting failure blocks."}
    write_json(OUT / "post_execution_validation.json", post)
    diagnostics = {condition: common.group_diagnostics(runs[condition][1]) for condition in CONDITIONS} | {"A_confirmed_outward_2tick": common.group_diagnostics(a2_events), "B_all_outward_0tick_diagnostic": common.group_diagnostics(b0_events)}
    write_json(OUT / "event_group_diagnostics.json", diagnostics)
    b0_non = [row for row in b0_events if row.get("base_event_status") == "nonconfirmed" and row.get("status") == "filled"]
    b0_gross = sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in b0_non)
    write_json(OUT / "nonconfirmation_diagnostic.json", {"B_all_1tick": common.group_diagnostics(b_events), "B_all_nonconfirmation_0tick": {"trade_count": len(b0_non), "gross_pnl_jpy": b0_gross, "gross_expectancy_jpy": b0_gross / len(b0_non) if b0_non else None, "purpose": "diagnostic only"}})
    values = {condition: [runs[condition][2][day] for day in targets] for condition in CONDITIONS}
    boot = bootstrap(values)
    months = {f"{year:04d}-{month:02d}": 0 for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    for day, value in runs["A_confirmed_outward"][2].items():
        months[day[:7]] += value
    a_metrics = runs["A_confirmed_outward"][3]
    a_overall, a2_overall = cast(dict[str, Any], a_metrics)["overall"], cast(dict[str, Any], a2_metrics)["overall"]
    long_count = sum(trade.side.value == "long" for trade in runs["A_confirmed_outward"][0])
    groups = common.group_diagnostics(b_events)
    def lower(name: str) -> bool:
        return cast(list[float], boot[name]["ci95_percentile_linear"])[0] > 0
    gates = {
        "B_all_trade_count_at_least_300": len(runs["B_all_outward"][0]) >= 300, "A_trade_count_at_least_150": len(runs["A_confirmed_outward"][0]) >= 150, "C_buy_trade_count_at_least_150": len(runs["C_confirmed_buy"][0]) >= 150, "D_sell_trade_count_at_least_150": len(runs["D_confirmed_sell"][0]) >= 150, "F_inward_trade_count_at_least_150": len(runs["F_confirmed_inward"][0]) >= 150,
        "A_long_at_least_40": long_count >= 40, "A_short_at_least_40": len(runs["A_confirmed_outward"][0]) - long_count >= 40, "nonconfirmed_events_at_least_150": len(non_b) >= 150,
        "upper_lower_base_events_each_at_least_100": all(sum(cast(int, groups[f"{side}_{status}"]["pre_event_count"]) for status in ("confirmed", "nonconfirmed")) >= 100 for side in ("upper", "lower")),
        "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower("A"), "A_minus_B_all_bootstrap_lower_above_0": lower("A_minus_B_all"), "A_minus_C_buy_bootstrap_lower_above_0": lower("A_minus_C_buy"), "A_minus_D_sell_bootstrap_lower_above_0": lower("A_minus_D_sell"), "A_minus_F_inward_bootstrap_lower_above_0": lower("A_minus_F_inward"), "A_2tick_expectancy_positive": a2_overall["expectancy_jpy"] is not None and a2_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics)["concentration"]["net_excluding_top10_jpy"] > 0, "B_all_nonconfirmed_0tick_gross_expectancy_negative": bool(b0_non) and b0_gross / len(b0_non) < 0,
    }
    information = [key for key in gates if "trade_count" in key or key.startswith("A_long") or key.startswith("A_short") or key.startswith("nonconfirmed") or key.startswith("upper_lower")]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(gates[key] for key in information) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": targets, **values, "no_trade": "0", "day_session_quarantined": "excluded", "TSE_closed_missing_nonbase_nonconfirmation_cancel": "zero"})
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "A_direction_counts": {"long": long_count, "short": len(runs["A_confirmed_outward"][0]) - long_count}, "positive_months_of_54": sum(value > 0 for value in months.values()), "monthly_A_net_jpy": months, "conditions": {condition: {"trade_count": len(runs[condition][0]), "metrics": runs[condition][3], "execution_audit": runs[condition][4]} for condition in CONDITIONS}, "A2": {"metrics": a2_metrics, "execution_audit": a2_execution}, "B_all_0tick_diagnostic": b0_metrics, "range_side_confirmation_diagnostics": diagnostics, "A_year_month_long_short_metrics": {"year": a_metrics["segments"]["year"], "month": a_metrics["segments"]["month"], "side": a_metrics["segments"]["side"]}, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; WFA/OOS/Final Holdout not run"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
