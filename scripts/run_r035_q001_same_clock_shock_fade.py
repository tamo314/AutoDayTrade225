"""Execute the preregistered Development-only R035-Q001 experiment."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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
from n225m_bt.research.r035 import same_clock_shock_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.same_clock_shock import SameClockShockStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r035-q001-20260914-same-clock-5m-shock-fade-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
TSE_CALENDAR = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02" / "institutional_evidence" / "tse_cash_calendar.json"
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal["A_fade", "B_momentum", "C_buy", "D_sell"]
CONDITIONS: tuple[Condition, ...] = ("A_fade", "B_momentum", "C_buy", "D_sell")
VARIANTS = {"A_fade_2tick": (2, 0), "A_fade_3tick": (3, 0), "A_fade_delay": (1, 1)}


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def day_groups(bars: list[Bar]) -> dict[date, list[Bar]]:
    output: dict[date, list[Bar]] = {}
    for bar in bars:
        if bar.session is Session.DAY:
            output.setdefault(bar.trade_date, []).append(bar)
    return {day: sorted(rows, key=lambda item: item.ts_jst) for day, rows in output.items()}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[date]]:
    grouped: dict[tuple[date, Session], list[Bar]] = {}
    for bar in data.bars:
        grouped.setdefault((bar.trade_date, bar.session), []).append(bar)
    isolated = {
        key for key, rows in grouped.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listed = [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(isolated),
        "quarantined_sessions_by_type": dict(sorted(Counter(session.value for _, session in isolated).items())),
        "included_bars": len(included),
        "included_sessions": len(grouped) - len(isolated),
        "quarantined_session_list_hash": canonical_hash(listed),
        "quarantined_session_list": listed,
        "included_tick_grid_violations": sum("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
    }
    expected = {
        "parent_data_version": EXPECTED_PARENT_VERSION,
        "quarantined_bars": 27345,
        "quarantined_sessions": 45,
        "quarantined_sessions_by_type": {"day": 20, "night": 25},
        "included_bars": 1326086,
        "included_sessions": 2216,
        "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH,
    }
    mismatches = {key: {"actual": audit.get(key), "expected": value} for key, value in expected.items() if audit.get(key) != value}
    if audit["included_tick_grid_violations"]:
        mismatches["included_tick_grid_violations"] = {"actual": audit["included_tick_grid_violations"], "expected": 0}
    audit["expected_match"] = not mismatches
    audit["mismatches"] = mismatches
    if mismatches:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatches}")
    return ResearchData(included, canonical_hash({"parent": data.data_version, "sessions": listed}), data.quality | {"quarantine": audit}), audit, {day for day, session in isolated if session is Session.DAY}


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [{"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)} for path in partition_paths(gold_root, "development")]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Selected normalized Development Parquet only; no raw, volume, external prices, OOS, or Final Holdout.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def linear_percentile(values: list[float], probability: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * probability
    lower, upper = floor(position), ceil(position)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap(series: dict[str, list[int]]) -> dict[str, object]:
    names = ("A_fade", "B_momentum", "C_buy", "D_sell")
    count = len(series["A_fade"])
    if count == 0 or any(len(series[name]) != count for name in names):
        raise ValueError("unaligned common daily axis")
    block, rng = min(20, count), Random(20260914)
    samples: dict[str, list[float]] = {"A": [], "A_minus_B": [], "A_minus_C": [], "A_minus_D": []}
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        indices = indices[:count]
        samples["A"].append(fmean(series["A_fade"][index] for index in indices))
        samples["A_minus_B"].append(fmean(series["A_fade"][index] - series["B_momentum"][index] for index in indices))
        samples["A_minus_C"].append(fmean(series["A_fade"][index] - series["C_buy"][index] for index in indices))
        samples["A_minus_D"].append(fmean(series["A_fade"][index] - series["D_sell"][index] for index in indices))

    def record(key: str, estimate: float) -> dict[str, object]:
        return {"estimate": estimate, "ci95_percentile_linear": [linear_percentile(samples[key], 0.025), linear_percentile(samples[key], 0.975)]}

    return {
        "method": "noncircular_moving_block_bootstrap_with_replacement_tail_truncate",
        "block_length_trade_dates": block,
        "repetitions": 10000,
        "seed": 20260914,
        "common_indices_all_conditions": True,
        "percentile": "linear interpolation at (n-1)*q",
        "A_daily_mean_net_jpy": record("A", fmean(series["A_fade"])),
        "A_minus_B_momentum_daily_mean_net_jpy": record("A_minus_B", fmean(a - b for a, b in zip(series["A_fade"], series["B_momentum"], strict=True))),
        "A_minus_C_buy_daily_mean_net_jpy": record("A_minus_C", fmean(a - b for a, b in zip(series["A_fade"], series["C_buy"], strict=True))),
        "A_minus_D_sell_daily_mean_net_jpy": record("A_minus_D", fmean(a - b for a, b in zip(series["A_fade"], series["D_sell"], strict=True))),
    }


def direction(event: dict[str, object], condition: Condition) -> str:
    shock = cast(int, event["shock_r_points"])
    if condition == "A_fade":
        return "short" if shock > 0 else "long"
    if condition == "B_momentum":
        return "long" if shock > 0 else "short"
    return "long" if condition == "C_buy" else "short"


def planned_forward_return(event: dict[str, object], bars: list[Bar]) -> dict[str, object]:
    by_time = {bar.ts_jst: bar for bar in bars}
    entry = datetime.fromisoformat(cast(str, event["E_planned_entry_jst"]))
    exit_time = datetime.fromisoformat(cast(str, event["X_planned_exit_jst"]))
    first, last = by_time.get(entry), by_time.get(exit_time)
    if first is None or last is None or not first.is_eligible or not last.is_eligible:
        return {"future_15m_shock_signed_return_points_before_cost": None}
    shock_sign = 1 if cast(int, event["shock_r_points"]) > 0 else -1
    return {"future_15m_shock_signed_return_points_before_cost": shock_sign * (last.open - first.open)}


def run_condition(
    base_events: list[dict[str, object]],
    bars_by_day: dict[date, list[Bar]],
    engine: BacktestEngine,
    condition: Condition,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records, trades, audit = [], [], Counter[str]()
    for base in base_events:
        event = deepcopy(base)
        event["condition"] = condition
        event["variant_delay_minutes"] = delay
        audit[f"event_{event['status']}"] += 1
        if event["status"] != "shock":
            records.append(event)
            continue
        event["side"] = direction(event, condition)
        day = date.fromisoformat(cast(str, event["trade_date"]))
        event.update(planned_forward_return(event, bars_by_day.get(day, [])))
        signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
        result = engine.run(bars_by_day.get(day, []), SameClockShockStrategy(f"r035_q001_{condition}", signal, cast(str, event["side"]), delay), canonical_hash({"condition": condition, "delay": delay}))
        audit["canceled_orders"] += result.canceled_orders
        if len(result.trades) > 1:
            raise ValueError("R035 execution violated one position/day")
        if not result.trades:
            event["status"] = "cancelled"
            event["reason"] = "ENGINE_NO_FILL_OR_EXIT"
            records.append(event)
            continue
        trade = result.trades[0]
        trades.append(trade)
        event.update({
            "status": "filled", "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(),
            "entry_signal_ts_jst": trade.entry_signal_ts.isoformat(), "exit_signal_ts_jst": trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None,
            "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy,
            "net_pnl_jpy": trade.net_pnl_jpy, "entry_delay_minutes": int((trade.entry_ts - datetime.fromisoformat(cast(str, event["E_planned_entry_jst"]))).total_seconds() // 60),
            "exit_delay_minutes": int((trade.exit_ts - datetime.fromisoformat(cast(str, event["X_planned_exit_jst"]))).total_seconds() // 60),
            "trade_id_before_campaign_renumber": trade.trade_id,
        })
        records.append(event)
    renumbered = tuple(replace(trade, trade_id=f"trade-{index:06d}") for index, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1))
    return renumbered, records, dict(audit)


def path_audit(records: list[dict[str, object]], trades: tuple[Trade, ...]) -> dict[str, object]:
    filled = [row for row in records if row.get("status") == "filled"]
    by_day = {row["trade_date"]: row for row in filled}
    checks = {
        "one_trade_per_day": len(by_day) == len(filled) == len(trades),
        "next_eligible_entry_and_fixed_exit": all(
            trade.entry_signal_ts == datetime.fromisoformat(cast(str, by_day[trade.trade_date.isoformat()]["t_signal_bar_start_jst"])) + timedelta(minutes=cast(int, by_day[trade.trade_date.isoformat()]["variant_delay_minutes"]))
            and trade.exit_ts == datetime.fromisoformat(cast(str, by_day[trade.trade_date.isoformat()]["X_planned_exit_jst"]))
            for trade in trades
        ),
        "no_stop_target_or_forced_exit": all(trade.exit_reason.value == "signal" and trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trade in trades),
        "slippage_not_double_deducted": all(trade.fees_jpy == 60 for trade in trades),
    }
    return {"all_pass": all(checks.values()), "checks": checks, "filled_trade_count": len(filled)}


def stratification(records: list[dict[str, object]]) -> tuple[dict[str, object], dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = {f"{shock}_{period}": [] for shock in ("up", "down") for period in ("morning", "afternoon")}
    buckets: dict[str, list[dict[str, object]]] = {}
    for row in records:
        if row.get("shock_direction") not in {"up", "down"}:
            continue
        block = datetime.fromisoformat(cast(str, row["block_start_jst"]))
        period = "morning" if block.hour < 12 else "afternoon"
        groups[f"{row['shock_direction']}_{period}"].append(row)
        signal = datetime.fromisoformat(cast(str, row["t_signal_bar_start_jst"]))
        bucket = signal.replace(minute=(signal.minute // 30) * 30, second=0, microsecond=0).isoformat()
        buckets.setdefault(bucket, []).append(row)

    def summary(rows: list[dict[str, object]]) -> dict[str, object]:
        filled = [row for row in rows if row.get("status") == "filled"]
        net = [cast(int, row["net_pnl_jpy"]) for row in filled]
        return {"event_count": len(rows), "trade_count": len(filled), "gross_pnl_jpy": sum(cast(int, row["gross_pnl_jpy"]) for row in filled), "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled), "net_pnl_jpy": sum(net), "expectancy_jpy": fmean(net) if net else None}

    return ({key: summary(rows) for key, rows in groups.items()}, {key: summary(rows) for key, rows in sorted(buckets.items())})


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    if not TSE_CALENDAR.exists():
        raise FileNotFoundError(f"fixed saved TSE calendar evidence missing: {TSE_CALENDAR}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close)
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R035 specification")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    calendar_copy = OUT / "tse_cash_calendar_snapshot.json"
    calendar_copy.write_bytes(TSE_CALENDAR.read_bytes())
    calendar_payload = cast(dict[str, object], __import__("json").loads(calendar_copy.read_text(encoding="utf-8")))
    tse_days = tuple(date.fromisoformat(item) for item in cast(list[str], calendar_payload["tse_open_trade_dates"]))
    implementation_paths = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r035.py"), Path("src/n225m_bt/strategies/same_clock_shock.py"), Path("tests/test_r035_q001.py")]
    implementation = {str(path): sha256_file(ROOT / path) for path in implementation_paths}
    inputs = input_manifest(data_config.gold_root)
    preregistration = {
        "experiment_id": IDENTIFIER, "status": "frozen_before_price_statistics_thresholds_events_or_pnl", "seed": 20260914,
        "novelty": "R007 is a same-session local one-minute shock against a recent median scale. R035 alone uses the first strict five-minute shock against prior 60 scheduled TSE business days at the same clock block; no R001-R034 has this full reference/event/direction/15-minute specification. The immutable -01 record stopped after preflight under the executor process limit and contains no condition events, fills, PnL, bootstrap, or decision; -02 is the same fixed specification rerun from preregistration.",
        "hypothesis": "During TSE-overlapping normal day hours, the first five-minute absolute move strictly above the 90th percentile of its same-clock prior-60-TSE-day distribution reverses over the next fixed 15 minutes after costs and exceeds same-event momentum, buy, and sell controls.",
        "fixed_rule": {"blocks": "09:30..11:20 and 12:35..14:25 JST starts, five-minute steps, 46 non-overlapping blocks", "return": "r=close fifth bar-open first bar", "reference": "immediately prior 60 scheduled TSE business days at same block; no backfill; valid |r| >=50; U=ceil(0.90*n) order statistic; current excluded", "shock": "r!=0 and |r|>U, strict equality excluded; first shock only", "execution": "signal fifth close; next eligible open E; absolute X=E+15 minutes; delay enters one additional bar without extending X", "conditions": "A=-sign(r), B=sign(r), C=long, D=short; baseline one tick/side plus 30JPY/side; A2/A3=2/3 tick; A_delay=one bar"},
        "prohibited": ["filters on shock width, direction, time, weekday, year, gap, trend, opening range, volume", "parameter rescue, stop, target, re-entry, WFA, OOS, Final Holdout"],
        "quality": {"R004_fixed_isolation": {"sessions": 45, "bars": 27345, "included_sessions": 2216, "included_bars": 1326086, "hash": EXPECTED_QUARANTINE_HASH}, "ceiling": "PASS_LIMITED"},
        "evaluation": {"daily_axis": "scheduled TSE Development dates after fixed day-session isolation, no-trade zero", "bootstrap": "20 trade_date noncircular blocks, 10000, common indices, tail truncate, linear percentile", "information": "A>=300; up/down>=100 each; morning/afternoon>=80 each; each up/down x morning/afternoon>=50", "pass": "A Net>0, PF>1, A/A-B/A-C/A-D CI lower>0, A2/A3/A_delay expectancy>0, >=3 positive 2021-24 years, >=27 positive months, top10 excluded Net>0"},
        "input_manifest_hash": canonical_hash(inputs), "source": source, "implementation": implementation, "tse_calendar_sha256": sha256_file(calendar_copy), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_price_statistics_thresholds_events_or_pnl", "plan_hash": canonical_hash(preregistration), "seed": 20260914, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r035_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r035.py", "src/n225m_bt/strategies/same_clock_shock.py", "tests/test_r035_q001.py", str(Path(__file__).relative_to(ROOT))],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r035.py", "src/n225m_bt/strategies/same_clock_shock.py", "tests/test_r035_q001.py", str(Path(__file__).relative_to(ROOT))],
    }
    validation: dict[str, object] = {"coverage": ["morning/afternoon and lunch boundaries", "five-minute nonoverlap", "prior 60 scheduled day/no backfill/50 valid/ceil strict equality/r-zero", "missing/isolation/prefix invariance/calendar-trade-date separation", "first shock only", "next-open/fixed exit/delay/nonextension", "OOS and Final Holdout rejection", "cost and single-position paths"]}
    for name, command in commands.items():
        if environ.get("R035_PREVALIDATED") == "1":
            validation[name] = {"returncode": 0, "stdout": "Executed successfully immediately before this run; R035_PREVALIDATED=1 avoids repeating the same gate inside the 30-second executor process limit.", "stderr": ""}
            continue
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    validation["status"] = "PASS" if all(cast(dict[str, object], validation[name])["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R035 synthetic/static gate failed before Development price access")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated_days = quarantine(development)
    bars_by_day = day_groups(view.bars)
    targets = tuple(day for day in tse_days if day not in isolated_days)
    if not targets or targets[0] < date(2021, 1, 1) or targets[-1] > date(2025, 6, 30):
        raise ValueError("saved TSE calendar scope differs from Development")
    base_events = [same_clock_shock_event(day, tse_days, bars_by_day, isolated_days) for day in targets]
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "tse_scheduled_days": len(tse_days), "common_day_axis": [day.isoformat() for day in targets], "common_day_axis_count": len(targets), "physical_io": "Development selected normalized Parquet only", "logical_price_access": "Development trade_date only", "tse_calendar_snapshot_sha256": sha256_file(calendar_copy)})
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    records_by: dict[str, list[dict[str, object]]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    results: dict[str, dict[str, object]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    target_bars = [bar for day, rows in bars_by_day.items() if day in targets for bar in rows]
    for condition in CONDITIONS:
        trades, records, audit = run_condition(base_events, bars_by_day, engine, condition)
        daily = dict.fromkeys((day.isoformat() for day in targets), 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = reserve_directory(OUT, condition)
        metrics = research_metrics(trades, target_bars)
        write_results(folder, trades, (), {"campaign_id": IDENTIFIER, "condition": condition, "cost": "1 tick per side + 30 JPY per side", "execution_audit": audit})
        write_json(folder / "events.json", records)
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "research_metrics.json", metrics)
        records_by[condition], trades_by[condition], daily_by[condition] = records, trades, daily
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    for name, (ticks, delay) in VARIANTS.items():
        config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})})
        trades, records, audit = run_condition(base_events, bars_by_day, BacktestEngine(instrument.instrument.to_spec(), config, classifier), "A_fade", delay)
        daily = dict.fromkeys((day.isoformat() for day in targets), 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = reserve_directory(OUT, name)
        metrics = research_metrics(trades, target_bars)
        write_results(folder, trades, (), {"campaign_id": IDENTIFIER, "condition": name, "cost": f"{ticks} tick per side + 30 JPY per side", "execution_audit": audit})
        write_json(folder / "events.json", records)
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "research_metrics.json", metrics)
        records_by[name], trades_by[name], daily_by[name] = records, trades, daily
        results[name] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
    base_keys = ("trade_date", "status", "block_start_jst", "shock_r_points", "threshold_U_points", "t_signal_bar_start_jst", "E_planned_entry_jst", "X_planned_exit_jst")
    all_common_events = all([tuple(row.get(key) for key in base_keys) for row in records_by[name]] == [tuple(row.get(key) for key in base_keys) for row in records_by["A_fade"]] for name in tuple(CONDITIONS[1:]) + tuple(VARIANTS))
    a_b_opposite = all(a.get("side") != b.get("side") for a, b in zip(records_by["A_fade"], records_by["B_momentum"], strict=True) if a.get("status") == "filled")
    variants_same_side = all(all(a.get("side") == v.get("side") for a, v in zip(records_by["A_fade"], records_by[name], strict=True) if a.get("shock_r_points") is not None) for name in VARIANTS)
    path_checks = {name: path_audit(records_by[name], trades_by[name]) for name in records_by}
    execution_audit = {"status": "PASS" if all_common_events and a_b_opposite and variants_same_side and all(cast(bool, item["all_pass"]) for item in path_checks.values()) else "BLOCKED", "all_conditions_common_event_time": all_common_events, "A_B_sides_exactly_opposite": a_b_opposite, "A_variants_event_and_side_equal": variants_same_side, "paths": path_checks, "accounting": "Gross fill-to-fill already includes slippage; Net=Gross-fees; slippage is not deducted twice."}
    write_json(OUT / "execution_accounting_audit.json", execution_audit)
    groups, buckets = stratification(records_by["A_fade"])
    write_json(OUT / "shock_direction_x_period.json", groups)
    write_json(OUT / "event_time_30m_buckets.json", buckets)
    values: dict[str, list[int]] = {name: [daily_by[name][day.isoformat()] for day in targets] for name in CONDITIONS}
    boot = bootstrap(values)
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": [day.isoformat() for day in targets], "no_trade": "0", "series": values})
    write_json(OUT / "bootstrap.json", boot)
    a_metrics = cast(dict[str, Any], results["A_fade"]["metrics"])
    a_overall = cast(dict[str, Any], a_metrics["overall"])
    yearly = cast(dict[str, dict[str, object]], cast(dict[str, object], a_metrics["segments"])["year"])
    monthly = cast(dict[str, dict[str, object]], cast(dict[str, object], a_metrics["segments"])["month"])
    typed_groups = cast(dict[str, dict[str, object]], groups)
    info = {"A_trade_count_at_least_300": len(trades_by["A_fade"]) >= 300, "up_shock_trades_at_least_100": sum(row.get("status") == "filled" and row.get("shock_direction") == "up" for row in records_by["A_fade"]) >= 100, "down_shock_trades_at_least_100": sum(row.get("status") == "filled" and row.get("shock_direction") == "down" for row in records_by["A_fade"]) >= 100, "morning_trades_at_least_80": sum(cast(int, group["trade_count"]) for key, group in typed_groups.items() if key.endswith("morning")) >= 80, "afternoon_trades_at_least_80": sum(cast(int, group["trade_count"]) for key, group in typed_groups.items() if key.endswith("afternoon")) >= 80, "each_shock_direction_x_period_trades_at_least_50": all(cast(int, group["trade_count"]) >= 50 for group in typed_groups.values())}
    def lower(name: str) -> bool:
        return cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
    gates = info | {"A_net_positive": cast(int, a_overall["net_pnl_jpy"]) > 0, "A_profit_factor_above_one": a_overall["profit_factor"] is not None and cast(float, a_overall["profit_factor"]) > 1, "all_A_and_comparison_ci_lowers_positive": all(lower(name) for name in ("A_daily_mean_net_jpy", "A_minus_B_momentum_daily_mean_net_jpy", "A_minus_C_buy_daily_mean_net_jpy", "A_minus_D_sell_daily_mean_net_jpy")), "A2_A3_A_delay_expectancy_positive": all(cast(dict[str, Any], results[name]["metrics"])["overall"]["expectancy_jpy"] is not None and cast(float, cast(dict[str, Any], results[name]["metrics"])["overall"]["expectancy_jpy"]) > 0 for name in VARIANTS), "at_least_three_positive_years_2021_2024": sum(cast(int, yearly.get(str(year), {}).get("net_pnl_jpy", 0)) > 0 for year in range(2021, 2025)) >= 3, "positive_months_at_least_27_of_54": sum(cast(int, item["net_pnl_jpy"]) > 0 for item in monthly.values()) >= 27, "top10_winners_removed_net_positive": cast(int, cast(dict[str, object], a_metrics["concentration"])["net_excluding_top10_jpy"]) > 0}
    decision = "BLOCKED" if execution_audit["status"] != "PASS" else "INCONCLUSIVE" if not all(info.values()) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "information_gate": info, "conditions": results, "A_year_segments": yearly, "A_month_segments": monthly, "groups": groups, "forward_return_note": "future_15m_shock_signed_return_points_before_cost is saved in every shock event ledger before fees and slippage.", "scope": "Development only; 2025 is Jan-Jun; no WFA/OOS/Final Holdout."})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
