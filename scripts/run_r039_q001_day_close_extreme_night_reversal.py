"""Execute the preregistered Development-only R039-Q001 experiment."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, cast

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r039 import day_close_night_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.day_close_night_reversal import DayCloseNightReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r039-q001-20260914-day-close-extreme-night-reversal-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
CONDITIONS = (
    "A_close_reverse",
    "B_all_reverse",
    "C_nonextreme",
    "D_buy",
    "E_sell",
    "F_continue",
    "G_prior_window_placebo",
)
VARIANTS = {"A2_2tick": (2, 0), "A3_3tick": (3, 0), "A_delay": (1, 1)}


def digest(path: Path) -> str:
    output = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            output.update(chunk)
    return output.hexdigest()


def groups(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    output: dict[tuple[date, Session], list[Bar]] = {}
    for bar in bars:
        output.setdefault((bar.trade_date, bar.session), []).append(bar)
    return {key: sorted(value, key=lambda row: row.ts_jst) for key, value in output.items()}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = groups(data.bars)
    isolated = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
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
        "rule": "exclude whole (trade_date, session) for any TICK_GRID_VIOLATION",
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
    audit.update({"expected_match": not mismatches, "mismatches": mismatches})
    if mismatches:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)} for path in partition_paths(gold_root, "development")]
    return {
        "status": "frozen_before_price_statistics_thresholds_events_or_pnl",
        "scope": "Selected normalized Development Parquet only; raw, volume, external prices, OOS and Final Holdout prohibited.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def eligible(event: dict[str, object], condition: str) -> tuple[bool, str, str]:
    c_status, p_status = event.get("C_status"), event.get("P_status")
    if condition == "G_prior_window_placebo":
        return p_status == "extreme", "P", "P_extreme" if p_status == "extreme" else f"P_{p_status}"
    if condition == "B_all_reverse":
        return c_status in {"extreme", "nonextreme"}, "C", "C_valid" if c_status in {"extreme", "nonextreme"} else f"C_{c_status}"
    if condition == "C_nonextreme":
        return c_status == "nonextreme", "C", "C_nonextreme" if c_status == "nonextreme" else f"C_{c_status}"
    return c_status == "extreme", "C", "C_extreme" if c_status == "extreme" else f"C_{c_status}"


def side(event: dict[str, object], condition: str, window: str) -> str:
    value = cast(int, event[f"r{window}_points"])
    continuation = "long" if value > 0 else "short"
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    if condition == "F_continue":
        return continuation
    return "short" if continuation == "long" else "long"


def exact_rows(
    bars: list[Bar] | None, start: datetime, count: int, trade_day: date, session: Session
) -> list[Bar] | None:
    by_time = {bar.ts_jst: bar for bar in bars or []}
    rows = [by_time.get(start + timedelta(minutes=index)) for index in range(count)]
    if any(row is None for row in rows):
        return None
    concrete = [row for row in rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_day or row.session is not session for row in concrete):
        return None
    return concrete


def execution_bars(
    event: dict[str, object], bars_by_session: dict[tuple[date, Session], list[Bar]], window: str
) -> list[Bar] | None:
    reference = date.fromisoformat(cast(str, event["reference_day_trade_date"]))
    target = date.fromisoformat(cast(str, event["trade_date"]))
    day_rows = exact_rows(
        bars_by_session.get((reference, Session.DAY)),
        datetime.fromisoformat(cast(str, event[f"{window}_window_start_jst"])),
        30,
        reference,
        Session.DAY,
    )
    night_rows = exact_rows(
        bars_by_session.get((target, Session.NIGHT)),
        datetime.fromisoformat(cast(str, event["night_E_planned_entry_jst"])),
        31,
        target,
        Session.NIGHT,
    )
    return [*day_rows, *night_rows] if day_rows is not None and night_rows is not None else None


def forward_return(event: dict[str, object], rows: list[Bar], direction: str) -> dict[str, object]:
    entry = datetime.fromisoformat(cast(str, event["night_E_planned_entry_jst"]))
    exit_ = datetime.fromisoformat(cast(str, event["night_X_planned_exit_jst"]))
    by_time = {bar.ts_jst: bar for bar in rows}
    first, last = by_time.get(entry), by_time.get(exit_)
    if first is None or last is None:
        return {"signal_direction_adjusted_future_30m_return_points_before_cost": None}
    return {"signal_direction_adjusted_future_30m_return_points_before_cost": (1 if direction == "long" else -1) * (last.open - first.open)}


def run_condition(
    base_events: list[dict[str, object]],
    bars_by_session: dict[tuple[date, Session], list[Bar]],
    engine: BacktestEngine,
    condition: str,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
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
        path = execution_bars(event, bars_by_session, window)
        if path is None:
            event.update({"status": "skipped", "reason": "SCHEDULED_DAY_OR_NIGHT_EXECUTION_PATH_MISSING"})
            audit["skipped_execution_path"] += 1
            records.append(event)
            continue
        event["side"] = side(event, condition, window)
        event.update(forward_return(event, path, cast(str, event["side"])))
        entry_signal = datetime.fromisoformat(cast(str, event[f"{window}_window_last_bar_start_jst"]))
        if delay:
            entry_signal = datetime.fromisoformat(cast(str, event["night_E_planned_entry_jst"]))
        exit_signal = datetime.fromisoformat(cast(str, event["night_exit_signal_bar_start_jst"]))
        result = engine.run(
            path,
            DayCloseNightReversalStrategy(f"r039_q001_{condition}", entry_signal, exit_signal, cast(str, event["side"])),
            canonical_hash({"condition": condition, "delay": delay, "window": window}),
        )
        audit["canceled_orders"] += result.canceled_orders
        if len(result.trades) > 1:
            raise ValueError("R039 execution violated one position/target night")
        if not result.trades:
            event.update({"status": "cancelled", "reason": "ENGINE_NO_FILL_OR_EXIT"})
            audit["cancelled"] += 1
            records.append(event)
            continue
        trade = result.trades[0]
        planned_entry = datetime.fromisoformat(cast(str, event["night_E_planned_entry_jst"]))
        event.update({
            "status": "filled", "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(),
            "entry_signal_ts_jst": trade.entry_signal_ts.isoformat(), "exit_signal_ts_jst": trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None,
            "gross_pnl_jpy": trade.gross_pnl_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy,
            "fees_jpy": trade.fees_jpy, "net_pnl_jpy": trade.net_pnl_jpy,
            "entry_delay_minutes": int((trade.entry_ts - planned_entry).total_seconds() // 60),
            "exit_delay_minutes": int((trade.exit_ts - datetime.fromisoformat(cast(str, event["night_X_planned_exit_jst"]))).total_seconds() // 60),
            "exit_reason": trade.exit_reason.value, "trade_id_before_campaign_renumber": trade.trade_id,
        })
        trades.append(trade)
        records.append(event)
    ordered = sorted(trades, key=lambda row: row.entry_ts)
    return tuple(replace(item, trade_id=f"trade-{index:06d}") for index, item in enumerate(ordered, 1)), records, dict(audit)


def percentile(values: list[float], q: float) -> float:
    ordered, point = sorted(values), (len(values) - 1) * q
    low, high = floor(point), ceil(point)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def bootstrap(daily: dict[str, dict[str, int]], records: dict[str, list[dict[str, object]]], axis: list[str]) -> dict[str, object]:
    arrays = {name: [daily[name][day] for day in axis] for name in CONDITIONS}
    conditional = {
        name: {cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"]) for row in records[name] if row.get("status") == "filled"}
        for name in ("A_close_reverse", "B_all_reverse", "C_nonextreme", "G_prior_window_placebo")
    }
    count, block, rng = len(axis), 20, Random(20260917)
    if count < block:
        raise ValueError("R039 common axis shorter than fixed bootstrap block")
    labels = ("A_daily_mean_net_jpy", "A_minus_D_daily_mean_net_jpy", "A_minus_E_daily_mean_net_jpy", "A_minus_F_daily_mean_net_jpy", "A_minus_B_conditional_expectancy_jpy", "A_minus_C_conditional_expectancy_jpy", "A_minus_G_conditional_expectancy_jpy")
    samples: dict[str, list[float]] = {name: [] for name in labels}
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        picked = indices[:count]
        samples["A_daily_mean_net_jpy"].append(fmean(arrays["A_close_reverse"][index] for index in picked))
        for name, label in (("D_buy", "D"), ("E_sell", "E"), ("F_continue", "F")):
            samples[f"A_minus_{label}_daily_mean_net_jpy"].append(fmean(arrays["A_close_reverse"][index] - arrays[name][index] for index in picked))
        for name, label in (("B_all_reverse", "B"), ("C_nonextreme", "C"), ("G_prior_window_placebo", "G")):
            a_sum = sum(conditional["A_close_reverse"].get(axis[index], 0) for index in picked)
            a_count = sum(axis[index] in conditional["A_close_reverse"] for index in picked)
            b_sum = sum(conditional[name].get(axis[index], 0) for index in picked)
            b_count = sum(axis[index] in conditional[name] for index in picked)
            samples[f"A_minus_{label}_conditional_expectancy_jpy"].append(a_sum / a_count - b_sum / b_count if a_count and b_count else 0.0)
    output: dict[str, object] = {
        "method": "noncircular moving-block bootstrap with replacement; tail truncate; conditional comparisons recompute sum/count per replicate",
        "block_length_trade_dates": 20, "repetitions": 10000, "seed": 20260917,
        "common_indices_all_conditions": True, "percentile": "linear",
    }
    for name, values in samples.items():
        if "conditional" in name:
            control = {"B": "B_all_reverse", "C": "C_nonextreme", "G": "G_prior_window_placebo"}[name.split("_")[2]]
            estimate = sum(conditional["A_close_reverse"].values()) / len(conditional["A_close_reverse"]) - sum(conditional[control].values()) / len(conditional[control])
        elif name == "A_daily_mean_net_jpy":
            estimate = fmean(arrays["A_close_reverse"])
        else:
            control = {"D": "D_buy", "E": "E_sell", "F": "F_continue"}[name.split("_")[2]]
            estimate = fmean(arrays["A_close_reverse"][index] - arrays[control][index] for index in range(count))
        output[name] = {"estimate": estimate, "ci95_percentile_linear": [percentile(values, 0.025), percentile(values, 0.975)]}
    return output


def summary(records: list[dict[str, object]], expected: tuple[str, ...], key: str) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for name in expected:
        selected = [row for row in records if row.get(key) == name]
        filled = [row for row in selected if row.get("status") == "filled"]
        net = [cast(int, row["net_pnl_jpy"]) for row in filled]
        output[name] = {
            "event_count": len(selected), "trade_count": len(filled),
            "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in filled),
            "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled),
            "net_pnl_jpy": sum(net), "expectancy_jpy": fmean(net) if net else None,
        }
    return output


def same_fields(left: dict[str, object], right: dict[str, object], fields: tuple[str, ...]) -> bool:
    return all(left.get(field) == right.get(field) for field in fields)


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract) != (1, 30):
        raise ValueError("active cost contract differs from R039 specification")
    execution = baseline.execution.model_copy(update={"allow_cross_session_pending_order": True, "max_fill_delay_minutes": 180})
    risk = baseline.risk.model_copy(update={"new_entry_cutoff_minutes_before_session_close": 0})
    runtime = baseline.model_copy(update={"execution": execution, "risk": risk})
    if (runtime.execution.slippage_ticks, runtime.fees.jpy_per_side_per_contract, runtime.execution.max_fill_delay_minutes, runtime.execution.allow_cross_session_pending_order, runtime.risk.new_entry_cutoff_minutes_before_session_close) != (1, 30, 180, True, 0):
        raise ValueError("R039 scheduled cross-session execution contract was not applied")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    implementation_paths = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r039.py"), Path("src/n225m_bt/strategies/day_close_night_reversal.py"), Path("tests/test_r039_q001.py")]
    implementation = {str(path): digest(ROOT / path) for path in implementation_paths}
    inputs = input_manifest(data_config.gold_root)
    preregistration = {
        "experiment_id": IDENTIFIER, "status": "frozen_before_price_statistics_thresholds_events_or_pnl", "seed": 20260917,
        "duplicate_review": "R001-R038 were reviewed before price access. No registered study combines the final 30 scheduled continuous day minutes, prior-60 strict Q75 selection, next scheduled night-open entry, 30-minute reverse direction, fixed directional controls, and the 60-to-31-minute same-day placebo. R038 is a TSE lunch extreme continuation study, not this boundary reversal.",
        "scope": "Known-Development additional exploration, not independent confirmation or unused validation.",
        "hypothesis": "An extreme final-30-minute scheduled day futures move reverses during the first 30 minutes of the directly following scheduled night session after costs. The economic interpretation is possible temporary close-related flow or inventory adjustment; this does not observe or identify order flow, participant type, cash close, arbitrage, or causality.",
        "fixed_rule": {
            "C": "The 30 bars ending one minute before the schedule's normal continuous day endpoint; rC=last.close-first.open. regular_end is used where versioned; session_close is used only where the schedule has no separate regular_end/auction metadata. Observed final rows and force-flat prices never define a window.",
            "P": "The 30 bars from 60 through 31 minutes before that same scheduled endpoint; rP=last.close-first.open. P has its own independent threshold and never selects A.",
            "reference": "For each target night use exactly its ExchangeCalendar.previous_trade_date day. QC/QP use exactly the 60 linked scheduled days immediately before that reference day; outside Development, missing, ineligible, quarantined, or zero references are retained as invalid and never replaced. Require >=50 valid nonzero |r|; Q=ascending ceil(.75*n); strict |r|>Q; current reference day excluded.",
            "conditions": "A extreme C -sign(rC); B all valid C -sign(rC); C nonextreme C -sign(rC); D/E A-event fixed buy/sell; F A-event sign(rC); G extreme P -sign(rP).",
            "execution": "The C/P signal is known at its final scheduled day bar. A-G enter at the target night's first scheduled normal bar open and exit at that open plus 30 minutes. A_delay sends the already-known order at night open, fills the second scheduled bar, and retains the same exit. Exact C/P 30 bars and night 31 bars are required; no gap price, update, or cancellation rule is used. One contract, one position, no stop/target/re-entry/early exit.",
            "engine_configuration": "Unmodified shared BacktestEngine with allow_cross_session_pending_order=true, maximum fill delay=180 minutes, and day new-entry cutoff=0 minutes. These are required solely for the preregistered scheduled day-to-night path; day/auction intervening bars are excluded from the supplied execution path and missing planned night first bars reject execution.",
        },
        "costs": {"A_to_G": "1 tick/side + 30 JPY/side", "A2": "2 ticks/side + 30 JPY/side", "A3": "3 ticks/side + 30 JPY/side", "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; slippage is not deducted twice."},
        "quality": {"R004_fixed_isolation": {"sessions": 45, "bars": 27345, "included_sessions": 2216, "included_bars": 1326086, "hash": QUARANTINE_HASH}, "ceiling": "PASS_LIMITED"},
        "evaluation": {"axis": "scheduled Development target nights excluding isolated target-night sessions; every reference insufficiency, invalid window, condition failure, cancellation, and no-trade remains zero", "bootstrap": "20 trade-date noncircular blocks, 10000, seed 20260917, common indices, tail truncate, linear percentile", "information": "B>=800; A>=180 with C-up/C-down >=60; C>=550; C extreme/nonextreme x direction each>=60; G>=180 with P-up/P-down>=60", "pass": "A Net>0/PF>1; CI lower bounds >0 for A daily, A-D/E/F daily, A-C and A-G conditional; A2/A3/delay expectancy>0; >=3 positive 2021-24; >=27 positive months/54; top10-excluded Net>0"},
        "inputs": inputs, "input_manifest_hash": canonical_hash(inputs), "source": source, "implementation": implementation,
        "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_price_statistics_thresholds_events_or_pnl", "plan_hash": canonical_hash(preregistration), "seed": 20260917, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r039_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r039.py", "src/n225m_bt/strategies/day_close_night_reversal.py", "tests/test_r039_q001.py", str(Path(__file__).relative_to(ROOT))],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r039.py", "src/n225m_bt/strategies/day_close_night_reversal.py", "tests/test_r039_q001.py", str(Path(__file__).relative_to(ROOT))],
    }
    validation: dict[str, object] = {name: {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr} for name, command in commands.items() for result in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]}
    validation["coverage"] = ["schedule versions and calendar_date/trade_date day-to-night linkage", "C/P exact 30-bar boundaries and auction exclusion", "60 linked days/no backfill/50-valid/nearest-rank/strict ties/zero/missing/isolation", "causality and prefix", "night-first entry/fixed exit/delay", "condition paths/accounting", "OOS/Final Holdout rejection"]
    validation["status"] = "PASS" if all(cast(dict[str, object], validation[name])["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R039 synthetic/static gate failed before Development price access")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars_by_session = groups(view.bars)
    target_days = [item.trade_date for item in classifier.exchange_calendar.trading_days() if date(2021, 1, 1) <= item.trade_date <= date(2025, 6, 30) and item.night_calendar_start_date is not None and (item.trade_date, Session.NIGHT) not in isolated]
    base_events = [day_close_night_event(classifier, classifier.exchange_calendar, day, bars_by_session, isolated) for day in target_days]
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "common_target_night_axis": [day.isoformat() for day in target_days], "common_target_night_axis_count": len(target_days), "physical_io": "Development selected normalized Parquet only", "logical_price_access": "Development trade_date only", "schedule_snapshot_sha256": digest(ROOT / "config" / "sessions.yaml"), "calendar_snapshot_sha256": digest(ROOT / "config" / "local_calendar.yaml")})
    records_by: dict[str, list[dict[str, object]]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    axis = [day.isoformat() for day in target_days]
    target_bars = [bar for day in target_days for bar in bars_by_session.get((day, Session.NIGHT), [])]
    for name in CONDITIONS:
        trades, records, audit = run_condition(base_events, bars_by_session, BacktestEngine(instrument.instrument.to_spec(), runtime, classifier), name)
        daily = dict.fromkeys(axis, 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = reserve_directory(OUT, name)
        metrics = research_metrics(trades, target_bars)
        write_results(folder, trades, (), {"campaign_id": IDENTIFIER, "condition": name, "cost": "1 tick/side + 30JPY/side", "execution_audit": audit, "runtime_execution": runtime.model_dump(mode="json")})
        write_json(folder / "events.json", records)
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "research_metrics.json", metrics)
        records_by[name], trades_by[name], daily_by[name], results[name] = records, trades, daily, {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    for name, (ticks, delay) in VARIANTS.items():
        cost = runtime.model_copy(update={"execution": runtime.execution.model_copy(update={"slippage_ticks": ticks})})
        trades, records, audit = run_condition(base_events, bars_by_session, BacktestEngine(instrument.instrument.to_spec(), cost, classifier), "A_close_reverse", delay)
        daily = dict.fromkeys(axis, 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = reserve_directory(OUT, name)
        metrics = research_metrics(trades, target_bars)
        write_results(folder, trades, (), {"campaign_id": IDENTIFIER, "condition": name, "cost": f"{ticks} tick/side + 30JPY/side", "execution_audit": audit, "runtime_execution": cost.model_dump(mode="json")})
        write_json(folder / "events.json", records)
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "research_metrics.json", metrics)
        records_by[name], trades_by[name], daily_by[name], results[name] = records, trades, daily, {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    shared = ("trade_date", "reference_day_trade_date", "C_status", "P_status", "rC_points", "rP_points", "QC_points", "QP_points", "night_E_planned_entry_jst", "night_X_planned_exit_jst")
    common_base = all([tuple(row.get(field) for field in shared) for row in records_by[name]] == [tuple(row.get(field) for field in shared) for row in records_by["A_close_reverse"]] for name in (*CONDITIONS[1:], *VARIANTS))
    by_date = {name: {cast(str, row["trade_date"]): row for row in records} for name, records in records_by.items()}
    compare_fields = ("side", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")
    extreme_a_b = all(same_fields(by_date["A_close_reverse"][day], by_date["B_all_reverse"][day], compare_fields) for day in axis if by_date["A_close_reverse"][day].get("status") == "filled")
    nonextreme_b_c = all(same_fields(by_date["B_all_reverse"][day], by_date["C_nonextreme"][day], compare_fields) for day in axis if by_date["C_nonextreme"][day].get("status") == "filled")
    a_f_opposite = all(by_date["A_close_reverse"][day].get("side") != by_date["F_continue"][day].get("side") for day in axis if by_date["A_close_reverse"][day].get("status") == "filled")
    variant_paths = all(all(same_fields(by_date["A_close_reverse"][day], by_date[name][day], ("trade_date", "reference_day_trade_date", "side", "C_status", "rC_points", "QC_points", "night_E_planned_entry_jst", "night_X_planned_exit_jst")) for day in axis) for name in VARIANTS)
    exact_execution = all(row.get("exit_reason") == ExitReason.SIGNAL.value and row.get("entry_delay_minutes") == (1 if name == "A_delay" else 0) and row.get("exit_delay_minutes") == 0 for name, records in records_by.items() for row in records if row.get("status") == "filled")
    checks = {"common_base_event_ledger": common_base, "extreme_C_A_B_path_equal": extreme_a_b, "nonextreme_C_B_C_path_equal": nonextreme_b_c, "A_F_opposite_sides": a_f_opposite, "A_variants_event_side_path_equal": variant_paths, "one_trade_per_target_night": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in trades_by.values()), "night_first_open_fixed_exit_and_delay_nonextension": exact_execution, "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades), "slippage_not_double_deducted": all(trade.fees_jpy == 60 for trades in trades_by.values() for trade in trades)}
    execution_audit = {"status": "PASS" if all(checks.values()) else "BLOCKED", "checks": checks, "accounting": "Gross is fill-to-fill/slippage-inclusive; Net=Gross-fees."}
    write_json(OUT / "execution_accounting_audit.json", execution_audit)
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": axis, "no_trade": "0 JPY", "series": {name: [daily_by[name][day] for day in axis] for name in CONDITIONS}})
    boot = bootstrap(daily_by, records_by, axis)
    write_json(OUT / "bootstrap.json", boot)
    c_rows = [*[row for row in records_by["A_close_reverse"] if row.get("C_status") == "extreme"], *[row for row in records_by["C_nonextreme"] if row.get("C_status") == "nonextreme"]]
    for row in c_rows:
        row["C_extreme_direction_group"] = f"{row['C_status']}_{'up' if cast(int, row['rC_points']) > 0 else 'down'}"
    c_groups = summary(c_rows, ("extreme_up", "extreme_down", "nonextreme_up", "nonextreme_down"), "C_extreme_direction_group")
    p_rows = [row for row in records_by["G_prior_window_placebo"] if row.get("P_status") == "extreme"]
    for row in p_rows:
        row["P_direction_group"] = "up" if cast(int, row["rP_points"]) > 0 else "down"
    p_groups = summary(p_rows, ("up", "down"), "P_direction_group")
    write_json(OUT / "strata.json", {"C_extreme_nonextreme_x_direction": c_groups, "G_P_direction": p_groups})
    a_metrics = cast(dict[str, Any], cast(dict[str, object], results["A_close_reverse"])["metrics"])
    overall, concentration = cast(dict[str, Any], a_metrics["overall"]), cast(dict[str, Any], a_metrics["concentration"])
    years = {str(year): sum(daily_by["A_close_reverse"][day] for day in axis if day.startswith(str(year))) for year in range(2021, 2026)}
    months = {f"{year}-{month:02d}": sum(daily_by["A_close_reverse"][day] for day in axis if day.startswith(f"{year}-{month:02d}")) for year in range(2021, 2026) for month in range(1, 13) if not (year == 2025 and month > 6)}
    information = {"B_trade_count_at_least_800": len(trades_by["B_all_reverse"]) >= 800, "A_trade_count_at_least_180": len(trades_by["A_close_reverse"]) >= 180, "A_up_at_least_60": cast(int, c_groups["extreme_up"]["trade_count"]) >= 60, "A_down_at_least_60": cast(int, c_groups["extreme_down"]["trade_count"]) >= 60, "C_trade_count_at_least_550": len(trades_by["C_nonextreme"]) >= 550, "each_C_status_x_direction_at_least_60": all(cast(int, value["trade_count"]) >= 60 for value in c_groups.values()), "G_trade_count_at_least_180": len(trades_by["G_prior_window_placebo"]) >= 180, "G_each_direction_at_least_60": all(cast(int, value["trade_count"]) >= 60 for value in p_groups.values())}
    def lower(name: str) -> bool:
        return cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
    gates = information | {"A_net_positive": cast(int, overall["net_pnl_jpy"]) > 0, "A_profit_factor_above_one": overall["profit_factor"] is not None and cast(float, overall["profit_factor"]) > 1, "all_requested_ci_lowers_positive": all(lower(name) for name in ("A_daily_mean_net_jpy", "A_minus_D_daily_mean_net_jpy", "A_minus_E_daily_mean_net_jpy", "A_minus_F_daily_mean_net_jpy", "A_minus_C_conditional_expectancy_jpy", "A_minus_G_conditional_expectancy_jpy")), "A2_A3_delay_expectancy_positive": all(cast(dict[str, Any], cast(dict[str, object], results[name])["metrics"])["overall"]["expectancy_jpy"] is not None and cast(float, cast(dict[str, Any], cast(dict[str, object], results[name])["metrics"])["overall"]["expectancy_jpy"]) > 0 for name in VARIANTS), "at_least_three_positive_years_2021_2024": sum(value > 0 for year, value in years.items() if year != "2025") >= 3, "positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27, "top10_winners_removed_net_positive": cast(int, concentration["net_excluding_top10_jpy"]) > 0}
    decision = "BLOCKED" if execution_audit["status"] != "PASS" else "INCONCLUSIVE" if not all(information.values()) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "information_gate": information, "gates": gates, "conditions": results, "A_aligned_year_net_jpy": years, "A_aligned_month_net_jpy": months, "strata": {"C": c_groups, "G": p_groups}, "scope": "Development only; 2025 Jan-Jun partial; WFA/OOS/Final Holdout not run."})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
