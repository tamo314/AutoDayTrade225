"""Execute the preregistered Development-only R062-Q002 fixed-U experiment."""

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
from n225m_bt.research.r053 import fwl_delta
from n225m_bt.research.r062_q002 import (
    COMMON_DAY_BARS,
    LOOKBACK,
    r062_q002_event,
    rolling_u_ledger,
    u_observation,
)
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r062_overnight_inventory_rejection import (
    R062OvernightInventoryRejectionStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r062-q002-20260915-overnight-inventory-rejection-fixed-u-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
SEED = 20261006
BLOCK = 20
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
BASE_CONDITIONS = ("A", "C", "D", "M", "A_follow", "A_buy", "A_sell", "REG")


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def as_float(value: object) -> float:
    return float(cast(float, value))


def as_int(value: object) -> int:
    return int(cast(int, value))


def input_manifest(root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(root, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, OOS, and Final Holdout prohibited.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)}
    included = [row for key, rows in grouped.items() if key not in isolated for row in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in row.quality_flags for row in included),
    }
    expected = {
        "parent_data_version": PARENT_HASH, "quarantined_sessions": 45,
        "quarantined_bars": 27345, "included_sessions": 2216,
        "included_bars": 1326086, "quarantined_session_list_hash": QUARANTINE_HASH,
        "included_tick_grid_violations": 0,
    }
    mismatches = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    audit.update(expected_match=not mismatches, mismatches=mismatches)
    if mismatches:
        raise ValueError(f"BLOCKED: R004 fixed isolation mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, isolated


def scheduled_axis(calendar: ExchangeCalendar, isolated: set[tuple[date, Session]]) -> tuple[list[date], list[date]]:
    all_days = [item.trade_date for item in calendar.trading_days() if date(2021, 1, 1) <= item.trade_date <= date(2025, 6, 30)]
    axis = [day for day in all_days if (day, Session.DAY) not in isolated]
    if len(all_days) != 1131 or len(axis) != 1111:
        raise ValueError("BLOCKED: fixed 1,111 trade_date axis mismatch")
    return all_days, axis


def u_candidates(classifier: CalendarClassifier, groups: dict[tuple[date, Session], list[Bar]], isolated: set[tuple[date, Session]], all_days: list[date]) -> list[dict[str, object]]:
    return rolling_u_ledger([
        u_observation(classifier, day, groups.get((day, Session.NIGHT)), night_quarantined=(day, Session.NIGHT) in isolated)
        for day in all_days
    ])


def make_events(classifier: CalendarClassifier, groups: dict[tuple[date, Session], list[Bar]], isolated: set[tuple[date, Session]], axis: list[date], by_u_day: dict[date, dict[str, object]], reaction_minutes: int = 15, rejection_ticks: int = 1) -> list[dict[str, object]]:
    return [r062_q002_event(classifier, target, groups.get((target, Session.DAY)), by_u_day[target], day_quarantined=(target, Session.DAY) in isolated, reaction_minutes=reaction_minutes, rejection_ticks=rejection_ticks) for target in axis]


def qualifies(condition: str, event: dict[str, object], threshold: int = 75) -> bool:
    if event.get("base_event_status", event.get("status")) != "E" or not event.get("direction_eligible"):
        return False
    x, q50 = as_float(event["x"]), as_float(event["q50"])
    high, medium = x >= as_float(event[f"q{threshold}"]), q50 <= x < as_float(event["q75"])
    rejection, confirmation = event.get("opening_response") == "rejection", event.get("opening_response") == "confirmation"
    return (
        (condition == "A" and high and rejection)
        or (condition == "C" and medium and rejection)
        or (condition == "D" and high and confirmation)
        or (condition == "M" and medium and confirmation)
        or (condition in {"A_follow", "A_buy", "A_sell"} and high and rejection)
        or (condition == "REG" and (high or medium) and (rejection or confirmation))
    )


def direction(condition: str, event: dict[str, object]) -> str:
    if condition == "A_buy":
        return "long"
    if condition == "A_sell":
        return "short"
    sign = as_int(event["night_sign"])
    if condition == "A_follow":
        sign = -sign
    # A/C/D/M/REG all measure the prespecified rejection (opposite-night) direction.
    return "short" if sign == 1 else "long"


def run_condition(name: str, events: list[dict[str, object]], groups: dict[tuple[date, Session], list[Bar]], engine: BacktestEngine, data_version: str, *, ticks: int = 1, delay: int = 0, hold: int = 30, threshold: int = 75) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    output: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    root = "A" if name.startswith("A_") or name in {"A2", "A3", "A_delay", "A_rejection2", "A_hold15", "A_hold45"} else name
    for source in events:
        event = dict(source)
        event["base_event_status"] = source["status"]
        event.update(condition=name, condition_eligible=qualifies(root, event, threshold))
        if not event["condition_eligible"]:
            event.update(status="skipped", reason=event.get("reason", "EVENT_FILTER"))
            audit[f"skip_{event['reason']}"] += 1
            output.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["planned_signal_jst"]))
        exit_ = datetime.fromisoformat(cast(str, event[f"planned_exit{hold}_jst"]))
        execution_condition = name if name in {"A_follow", "A_buy", "A_sell"} else root
        result = engine.run(groups[(date.fromisoformat(cast(str, event["trade_date"])), Session.DAY)], R062OvernightInventoryRejectionStrategy(f"r062_q002_{name}", signal, direction(execution_condition, event), exit_, delay), canonical_hash({"condition": name, "event": event, "ticks": ticks, "delay": delay, "hold": hold, "data": data_version}))
        if len(result.trades) > 1:
            raise AssertionError("R062-Q002 maximum one position violated")
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
    numbered = sorted(trades, key=lambda item: item.entry_ts)
    return tuple(replace(trade, trade_id=f"trade-{number:06d}") for number, trade in enumerate(numbered, 1)), output, dict(sorted(audit.items()))


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


def mbb_indices(axis_length: int) -> np.ndarray[Any, np.dtype[np.uint16]]:
    rng = Random(SEED)
    indices = np.empty((10_000, axis_length), dtype=np.uint16)
    for replicate in range(10_000):
        picked: list[int] = []
        while len(picked) < axis_length:
            start = rng.randrange(axis_length - BLOCK + 1)
            picked.extend(range(start, start + BLOCK))
        indices[replicate] = picked[:axis_length]
    return indices


def daily_bootstrap(daily_by: dict[str, dict[str, int]]) -> dict[str, object]:
    axis = sorted(daily_by["A"])
    series = {"A_daily_mean_net_jpy": [float(daily_by["A"][day]) for day in axis]}
    for name in ("C", "D", "A_follow", "A_buy", "A_sell"):
        series[f"A_minus_{name}_daily_mean_net_jpy"] = [float(daily_by["A"][day] - daily_by[name][day]) for day in axis]
    indices = mbb_indices(len(axis))
    np.save(OUT / "bootstrap_common_day_indices.npy", indices)
    output: dict[str, object] = {"method": "20 trade_date noncircular moving-block bootstrap with replacement, tail truncate, linear percentile", "seed": SEED, "repetitions": 10_000, "block_length_trade_dates": BLOCK, "target_trade_dates": len(axis), "common_index": True, "index_file": "bootstrap_common_day_indices.npy"}
    for name, values in series.items():
        samples = [
            fmean(values[int(indices[replicate, index])] for index in range(len(axis)))
            for replicate in range(10_000)
        ]
        output[name] = {"estimate": fmean(values), "ci95_percentile_linear": [percentile(samples, .025), percentile(samples, .975)]}
    return output


def regression(events: list[dict[str, object]], trades: tuple[Trade, ...], multiplier: int) -> tuple[np.ndarray[Any, np.dtype[np.float64]], np.ndarray[Any, np.dtype[np.float64]], np.ndarray[Any, np.dtype[np.float64]], list[str], list[str]]:
    by_day = {trade.trade_date.isoformat(): trade for trade in trades}
    rows: list[tuple[float, float, list[float], str]] = []
    for event in events:
        if not qualifies("REG", event):
            continue
        trade = by_day.get(cast(str, event["trade_date"]))
        if trade is None:
            raise ValueError("BLOCKED: eligible A/C/D/M regression event was not filled")
        sign = as_int(event["night_sign"])
        rejection = float(event["opening_response"] == "rejection")
        extreme = float(as_float(event["x"]) >= as_float(event["q75"]))
        rows.append((
            extreme * rejection,
            float(-sign * (trade.exit_reference_price - trade.entry_reference_price) * multiplier),
            [1.0, extreme, rejection, as_float(event["x"]) * 10_000, as_float(event["rN"]) * 10_000, as_float(event["tse_opening_gap_s_adjusted_bps"]), as_float(event["tse_opening_range_bps"]), float(sign == 1)],
            cast(str, event["trade_date"]),
        ))
    if not rows:
        raise ValueError("BLOCKED: no A/C/D/M regression observations")
    years = sorted({int(item[3][:4]) for item in rows})
    nuisance_names = ["intercept", "extreme_main", "rejection_main", "x_bps", "night_trend_bps", "tse_open_gap_s_adjusted_bps", "opening_volatility_bps", "night_up", *[f"year_{year}" for year in years]]
    q = np.asarray([item[0] for item in rows], dtype=np.float64)
    y = np.asarray([item[1] for item in rows], dtype=np.float64)
    nuisance = np.asarray([[*item[2], *[float(int(item[3][:4]) == year) for year in years]] for item in rows], dtype=np.float64)
    return q, y, nuisance, [item[3] for item in rows], nuisance_names


def delta_bootstrap(events: list[dict[str, object]], trades: tuple[Trade, ...], multiplier: int, axis: list[date]) -> dict[str, object]:
    q, y, nuisance, row_days, nuisance_names = regression(events, trades, multiplier)
    delta, ss = fwl_delta(q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12)
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=1e-12)
    q_residual = q - projection @ q
    residual = y - nuisance @ (np.linalg.pinv(nuisance, rcond=1e-12) @ y) - q_residual * delta
    by_day = {day.isoformat(): index for index, day in enumerate(axis)}
    row_indices = np.asarray([by_day[day] for day in row_days], dtype=np.int32)
    rng = np.random.default_rng(SEED)
    signs = np.empty((10_000, len(axis)), dtype=np.int8)
    estimates: list[float] = []
    for replicate in range(10_000):
        blocks = rng.choice(np.array([-1, 1], dtype=np.int8), size=ceil(len(axis) / BLOCK))
        weights = np.repeat(blocks, BLOCK)[: len(axis)]
        signs[replicate] = weights
        estimates.append(delta + float(q_residual @ (residual * weights[row_indices]) / ss))
    np.save(OUT / "bootstrap_delta_block_wild_signs.npy", signs)
    write_json(OUT / "regression_ledger.json", {"trade_dates": row_days, "Q_extreme_x_rejection": q.tolist(), "y_opposite_night_0tick_gross_jpy": y.tolist(), "nuisance_columns": nuisance_names, "nuisance": nuisance.tolist(), "fwl": {"delta": delta, "q_residual_ss": ss, "pinv_rcond": 1e-12, "residual_ss_tolerance": 1e-12}})
    write_json(OUT / "technical_fwl_gate.json", {"status": "PASS", "observed_q_residual_ss": ss, "repetitions": 10_000, "method": "fixed-design 20-trade-date block-wild score bootstrap", "sign_file": "bootstrap_delta_block_wild_signs.npy", "tolerance": 1e-12})
    return {"estimate": delta, "ci95_percentile_linear": [percentile(estimates, .025), percentile(estimates, .975)], "method": "fixed-design 20-trade-date block-wild score bootstrap", "seed": SEED, "repetitions": 10_000, "block_length_trade_dates": BLOCK, "q_residual_ss": ss}


def positive_metrics(metrics: dict[str, object]) -> bool:
    overall = cast(dict[str, object], metrics["overall"])
    return as_int(overall["net_pnl_jpy"]) > 0 and as_float(overall["profit_factor"] or 0) > 1


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r062_q002_overnight_inventory_rejection_fixed_u.py')

    if OUT.exists():
        raise FileExistsError(f"immutable R062-Q002 output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.allow_cross_session_pending_order) != (1, 30, False):
        raise ValueError("BLOCKED: execution/cost contract differs from R062-Q002 preregistration")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    inputs = input_manifest(data_config.gold_root)
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    files = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r062_q002.py"), Path("src/n225m_bt/research/r053.py"), Path("src/n225m_bt/strategies/r062_overnight_inventory_rejection.py"), Path("tests/test_r062_q002.py")]
    implementation = {str(path): digest(ROOT / path) for path in files}
    plan: dict[str, object] = {
        "experiment_id": IDENTIFIER, "study_id": "R062-Q002", "status": "frozen_before_price_statistics_events_or_pnl", "seed": SEED,
        "scope": "Development 2021-01-01..2025-06-30 only; repeated Development exploration, not independent reproduction.",
        "allowed_q001_carry_forward": "Only E=0, night-invalid=237, R004-isolated=14, rolling-valid-x-insufficient=860, and the absence of PnL/orders/fills/regression. No Q001 price, direction, event candidate, or future-return artifact was read.",
        "prior_immutable_attempt": "…-01 stopped at its post-fill execution audit before bootstrap or decision because A_follow/A_buy/A_sell were dispatched using root A rather than their fixed control side. …-02 changes only that control-side dispatch and adds its synthetic side assertion; no hypothesis, input, U, event, execution, cost, sensitivity, inference, seed, or gate changes.",
        "correction": "The sole pre-trade change is a separately defined U. U requires only a uniquely scheduled TSE->OSE-night->next-TSE triplet and eligible, non-isolated, positive oN/cN normal-night endpoints. U never requires a next-TSE bar, its threshold, entry, exit, sensitivity path, or any future information. Each target uses its immediately prior up-to-120 U members, never itself and never older supplementation; threshold is valid at >=100.",
        "hypothesis": "After an extreme full OSE-night move, a 15-minute next-TSE rejection continues opposite the night direction for 30 minutes and exceeds medium-night rejection, extreme-night confirmation, and same-event follow/fixed-side controls.",
        "rule": "E independently requires full target night, target TSE ordinals 0..65, 10/15/20-minute reaction, base/delayed entry and 15/30/45-minute exits, and valid prior-U threshold. A=x>=q75/rejection; C=q50<=x<q75/rejection; D=x>=q75/confirmation; M=q50<=x<q75/confirmation; strict 1 tick thresholds and equality upper. Signal is ordinal-15 close, entry ordinal-16 open, exit 30 planned minutes later open; A is -s, A_follow is s, fixed buy/sell controls share A events.",
        "costs_and_sensitivities": "One contract, maximum one position, no Stop/Target/reentry/early exit; 1 tick plus JPY30/side. Only 2/3 ticks, one-bar nonextended delay, q70/q80, two-tick rejection, 10/20 reaction, and 15/45 holds are evaluated.",
        "inference": "A/C/D/M interaction Q=extreme*rejection uses opposite-night 0-tick pre-fee 30-minute gross and fixed nuisance: extreme/rejection mains, night magnitude, signed night trend, signed TSE-open gap, opening volatility, fixed direction, year FE. delta uses fixed-design 20-trade-date block-wild score bootstrap 10,000; other contrasts use 20-day noncircular MBB 10,000; seed 20261006 and 1,111-day axis.",
        "information_gate": "E>=700; A/C/D/M>=70; A long/short>=25; q80 A>=45; reaction10/reaction20 A>=50. Any information failure is INCONCLUSIVE; otherwise any fixed economic gate failure is REJECT; all pass is Development-only INVESTIGATE.",
        "inputs": inputs, "implementation": implementation, "source": source, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_price_access", "seed": SEED, "plan_hash": canonical_hash(plan), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r062_q002.py", "tests/test_exit_after_entry_cutoff.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r062_q002.py", "src/n225m_bt/strategies/r062_overnight_inventory_rejection.py", "tests/test_r062_q002.py", str(Path(__file__).relative_to(ROOT))],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r062_q002.py", "src/n225m_bt/strategies/r062_overnight_inventory_rejection.py", str(Path(__file__).relative_to(ROOT))],
    }
    validation: dict[str, Any] = {name: {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr} for name, command in commands.items() for result in [run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=os.environ | {"PYTHONPATH": str(ROOT / "src")})]}
    validation["coverage"] = "U night-only membership, threshold at prior-U 100, prefix invariance, unique schedule triplet, common E, next-open/fixed-exit/delay, accounting, max one position, fixed axis, OOS/Final lock"
    validation["status"] = "PASS" if all(cast(int, value["returncode"]) == 0 for value in validation.values() if isinstance(value, dict) and "returncode" in value) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("BLOCKED: R062-Q002 pre-execution validation failed")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    all_days, axis = scheduled_axis(calendar, isolated)
    groups = session_groups(view.bars)
    u_rows = u_candidates(classifier, groups, isolated, all_days)
    by_u_day = {date.fromisoformat(cast(str, row["trade_date"])): row for row in u_rows}
    mapping_failures = [row for row in u_rows if row.get("u_reason") == "TRIPLET_MAPPING_UNAVAILABLE"]
    if mapping_failures:
        write_json(OUT / "u_membership_ledger.json", u_rows)
        write_json(OUT / "BLOCKED.json", {"experiment_id": IDENTIFIER, "status": "BLOCKED", "stage": "schedule_triplet_mapping", "mapping_failures": mapping_failures, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return
    causality = {"all_reference_dates_strictly_prior": all(all(day < str(row["trade_date"]) for day in cast(list[str], row["rolling_u_reference_trade_dates"])) for row in u_rows), "no_current_u_in_reference": all(str(row["trade_date"]) not in cast(list[str], row["rolling_u_reference_trade_dates"]) for row in u_rows), "threshold_starts_at_100_prior_u": all(bool(row["rolling_u_valid"]) == (as_int(row["rolling_u_reference_count"]) >= 100) for row in u_rows), "lookback_never_exceeds_120": all(as_int(row["rolling_u_reference_count"]) <= LOOKBACK for row in u_rows)}
    write_json(OUT / "u_membership_ledger.json", u_rows)
    write_json(OUT / "rolling_u_causality_audit.json", causality)
    if not all(causality.values()):
        write_json(OUT / "BLOCKED.json", {"experiment_id": IDENTIFIER, "status": "BLOCKED", "stage": "rolling_U_causality", "audit": causality, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return
    base_events = make_events(classifier, groups, isolated, axis, by_u_day)
    counts = Counter(str(event.get("reason", event.get("status"))) for event in base_events)
    common_e = sum(event.get("status") == "E" for event in base_events)
    write_json(OUT / "all_candidate_event_ledger.json", base_events)
    write_json(OUT / "eligibility_audit.json", {"fixed_axis_trade_dates": len(axis), "reason_counts_exclusive": dict(sorted(counts.items())), "reason_count_sum": sum(counts.values()), "E": common_e, "all_candidate_rows": len(base_events), "common_day_bars": COMMON_DAY_BARS})
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_axis_trade_dates": [day.isoformat() for day in axis], "physical_io": "Development normalized Parquet only"})
    if common_e == 0:
        write_json(OUT / "BLOCKED.json", {"experiment_id": IDENTIFIER, "status": "BLOCKED", "stage": "observational_E_gate_before_pnl", "failure": "E_ZERO", "eligibility": dict(sorted(counts.items())), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return
    events10 = make_events(classifier, groups, isolated, axis, by_u_day, reaction_minutes=10)
    events20 = make_events(classifier, groups, isolated, axis, by_u_day, reaction_minutes=20)
    events_rej2 = make_events(classifier, groups, isolated, axis, by_u_day, rejection_ticks=2)
    variants: dict[str, tuple[list[dict[str, object]], int, int, int, int]] = {name: (base_events, 1, 0, 30, 75) for name in BASE_CONDITIONS} | {
        "A2": (base_events, 2, 0, 30, 75), "A3": (base_events, 3, 0, 30, 75), "A_delay": (base_events, 1, 1, 30, 75), "A_q70": (base_events, 1, 0, 30, 70), "A_q80": (base_events, 1, 0, 30, 80), "A_rejection2": (events_rej2, 1, 0, 30, 75), "A_window10": (events10, 1, 0, 30, 75), "A_window20": (events20, 1, 0, 30, 75), "A_hold15": (base_events, 1, 0, 15, 75), "A_hold45": (base_events, 1, 0, 45, 75),
    }
    metric_data = ResearchData([bar for bar in view.bars if bar.trade_date in set(axis)], view.data_version, view.quality)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    ledgers: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, dict[str, object]] = {}
    for name, (events, ticks, delay, hold, threshold) in variants.items():
        config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})})
        trades, ledger, audit = run_condition(name, events, groups, BacktestEngine(instrument.instrument.to_spec(), config, classifier), view.data_version, ticks=ticks, delay=delay, hold=hold, threshold=threshold)
        values = daily(trades, axis)
        metrics = write_condition(name, trades, ledger, audit, values, metric_data, ticks)
        trades_by[name], ledgers[name], daily_by[name], results[name] = trades, ledger, values, {"trade_count": len(trades), "metrics": metrics, "audit": audit}
    checks = {
        "A_C_D_M_exclusive": all(sum(qualifies(name, event) for name in ("A", "C", "D", "M")) <= 1 for event in base_events),
        "controls_share_A_event_entry_exit": all(all(ledgers[name][index].get(field) == a.get(field) for name in ("A_follow", "A_buy", "A_sell") for field in ("condition_eligible", "entry_ts_jst", "exit_ts_jst")) for index, a in enumerate(ledgers["A"]) if a["condition_eligible"]),
        "follow_opposite_A_side": all(a.get("side") != follow.get("side") for a, follow in zip(ledgers["A"], ledgers["A_follow"], strict=True) if a.get("status") == "filled"),
        "sensitivity_events_rebuilt": all(ledger[index].get("reaction_minutes") == minutes for ledger, minutes in ((ledgers["A_window10"], 10), (ledgers["A_window20"], 20)) for index in range(len(ledger))),
        "fixed_axis": all(len(values) == 1111 for values in daily_by.values()),
        "one_trade_per_day": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in trades_by.values()),
        "signal_exit": all(trade.exit_reason.value == "signal" for trades in trades_by.values() for trade in trades),
        "delay_nonextension": all(trade.exit_ts == next(item.exit_ts for item in trades_by["A"] if item.trade_date == trade.trade_date) for trade in trades_by["A_delay"]),
        "accounting_no_double_slippage": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades),
    }
    write_json(OUT / "execution_accounting_audit.json", {"checks": checks, "all_pass": all(checks.values()), "note": "Gross is slippage-inclusive; Net=Gross-fees."})
    if not all(checks.values()):
        raise ValueError("BLOCKED: R062-Q002 execution/accounting audit failed")
    a_segments = cast(dict[str, object], cast(dict[str, object], results["A"]["metrics"])["segments"])
    sides = cast(dict[str, dict[str, object]], a_segments["side"])
    information = {"E": common_e, "A": len(trades_by["A"]), "C": len(trades_by["C"]), "D": len(trades_by["D"]), "M": len(trades_by["M"]), "A_side": sides, "A_q80": len(trades_by["A_q80"]), "A_window10": len(trades_by["A_window10"]), "A_window20": len(trades_by["A_window20"])}
    sufficient = common_e >= 700 and all(as_int(information[name]) >= 70 for name in ("A", "C", "D", "M")) and all(as_int(sides.get(side, {}).get("trade_count", 0)) >= 25 for side in ("long", "short")) and as_int(information["A_q80"]) >= 45 and as_int(information["A_window10"]) >= 50 and as_int(information["A_window20"]) >= 50
    boot = daily_bootstrap(daily_by)
    if sufficient:
        try:
            boot["delta_fwl_jpy"] = delta_bootstrap(ledgers["REG"], trades_by["REG"], instrument.instrument.contract_multiplier, axis)
        except ValueError as error:
            write_json(OUT / "technical_fwl_gate.json", {"status": "BLOCKED", "error": str(error)})
            write_json(OUT / "decision.json", {"status": "BLOCKED", "reason": str(error), "information": information, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
            write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": "BLOCKED"})
            return
    else:
        boot["delta_fwl_jpy"] = {"status": "NOT_RUN_INFORMATION_INSUFFICIENT"}
        write_json(OUT / "technical_fwl_gate.json", {"status": "NOT_RUN_INFORMATION_INSUFFICIENT", "reason": "A/C/D/M information gate not met; no respecification"})
    write_json(OUT / "bootstrap.json", boot)
    metrics_a = cast(dict[str, object], results["A"]["metrics"])
    segments, overall, concentration = cast(dict[str, object], metrics_a["segments"]), cast(dict[str, object], metrics_a["overall"]), cast(dict[str, object], metrics_a["concentration"])
    def ci(name: str) -> bool:
        item = cast(dict[str, object], boot[name])
        return as_float(cast(list[float], item["ci95_percentile_linear"])[0]) > 0
    delta_ci = bool(sufficient and ci("delta_fwl_jpy"))
    economic = {
        "A_net_positive": as_int(overall["net_pnl_jpy"]) > 0, "A_pf_gt_one": as_float(overall["profit_factor"] or 0) > 1,
        "A_and_AminusC_AminusD_delta_CI": all(ci(name) for name in ("A_daily_mean_net_jpy", "A_minus_C_daily_mean_net_jpy", "A_minus_D_daily_mean_net_jpy")) and delta_ci,
        "same_event_controls_CI": all(ci(f"A_minus_{name}_daily_mean_net_jpy") for name in ("A_follow", "A_buy", "A_sell")),
        "cost_delay_and_all_fixed_sensitivities_positive_pf": all(positive_metrics(cast(dict[str, object], results[name]["metrics"])) for name in ("A2", "A3", "A_delay", "A_q70", "A_q80", "A_rejection2", "A_window10", "A_window20", "A_hold15", "A_hold45")),
        "three_positive_years_2021_2024": sum(as_int(cast(dict[str, dict[str, object]], segments["year"]).get(str(year), {}).get("net_pnl_jpy", 0)) > 0 for year in range(2021, 2025)) >= 3,
        "positive_months_at_least_27": sum(as_int(value.get("net_pnl_jpy", 0)) > 0 for value in cast(dict[str, dict[str, object]], segments["month"]).values()) >= 27,
        "top10_excluded_net_positive": as_int(concentration["net_excluding_top10_jpy"]) > 0,
    }
    decision = "INCONCLUSIVE" if not sufficient else "INVESTIGATE" if all(economic.values()) else "REJECT"
    write_json(OUT / "breakdowns.json", {"A_year": segments["year"], "A_month": segments["month"], "A_side": sides, "positive_months": sum(as_int(value.get("net_pnl_jpy", 0)) > 0 for value in cast(dict[str, dict[str, object]], segments["month"]).values()), "concentration": concentration})
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": sorted(daily_by["A"]), "series": daily_by})
    write_json(OUT / "development_results.json", {"experiment_id": IDENTIFIER, "decision": decision, "information_sufficient": sufficient, "information": information, "conditions": results, "fixed_economic_gates": economic, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "decision.json", {"status": decision, "information_sufficient": sufficient, "information": information, "fixed_economic_gates": economic if sufficient else "NOT_EVALUATED_INFORMATION_INSUFFICIENT", "bootstrap": boot, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": decision, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
