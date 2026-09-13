"""Execute the preregistered Development-only R019-Q001 experiment."""

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
from n225m_bt.research.r019 import prior_range_regime_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.prior_range_regime import PriorRangeRegimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r019-q001-20260914-prior-same-session-range-regime-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
EXPECTED_TARGET_DATES = 1120
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
EXPECTED_QUARANTINE = {
    "quarantined_sessions": 45,
    "quarantined_bars": 27345,
    "included_bars": 1326086,
    "included_sessions": 2216,
    "quarantined_sessions_by_type": {"day": 20, "night": 25},
}
Condition = Literal[
    "A_regime_switch", "B_always_long", "C_always_short", "D_initial_follow", "F_initial_reverse"
]
CONDITIONS: tuple[Condition, ...] = (
    "A_regime_switch",
    "B_always_long",
    "C_always_short",
    "D_initial_follow",
    "F_initial_reverse",
)


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, Any], set[tuple[date, Session]]]:
    """Reproduce R004's immutable whole-session tick-grid isolation exactly."""
    groups = session_groups(data.bars)
    isolated = {
        key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [row for key, rows in groups.items() if key not in isolated for row in rows]
    listed = [{"trade_date": item.isoformat(), "session": session.value} for item, session in sorted(isolated)]
    audit: dict[str, Any] = {
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
    expected = EXPECTED_QUARANTINE | {
        "parent_data_version": EXPECTED_PARENT_VERSION,
        "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH,
    }
    mismatches = {key: {"actual": audit.get(key), "expected": value} for key, value in expected.items() if audit.get(key) != value}
    if audit["included_tick_grid_violations"]:
        mismatches["included_tick_grid_violations"] = audit["included_tick_grid_violations"]
    audit["expected_match"] = not mismatches
    audit["mismatches"] = mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    version = canonical_hash({"parent": data.data_version, "rule": audit["rule"], "sessions": listed})
    return ResearchData(included, version, data.quality | {"quarantine": audit}), audit, isolated


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_manifest(gold_root: Path) -> dict[str, object]:
    paths = partition_paths(gold_root, "development")
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)}
        for path in paths
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Only selected normalized Development Parquet partition files; no raw, OOS, Final Holdout, external price, or volume inputs.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def implementation_snapshot() -> dict[str, str]:
    return {
        str(Path(__file__).relative_to(ROOT)): sha256_file(Path(__file__)),
        "src/n225m_bt/research/r019.py": sha256_file(ROOT / "src/n225m_bt/research/r019.py"),
        "src/n225m_bt/strategies/prior_range_regime.py": sha256_file(ROOT / "src/n225m_bt/strategies/prior_range_regime.py"),
        "tests/test_r019_q001.py": sha256_file(ROOT / "tests/test_r019_q001.py"),
    }


def write_implementation_snapshot(files: dict[str, str]) -> None:
    with ZipFile(OUT / "r019_implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT / name, name)


def month_keys() -> list[str]:
    return [
        f"{year:04d}-{month:02d}"
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    ]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    location = (len(ordered) - 1) * q
    lower, upper = floor(location), ceil(location)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (location - lower)


def bootstrap(values: dict[str, list[int]]) -> dict[str, Any]:
    count = len(values["A_regime_switch"])
    if count != EXPECTED_TARGET_DATES or any(len(series) != count for series in values.values()):
        raise ValueError("fixed 1,120 trade-date daily universe is not aligned")
    rng = Random(20260913)
    block = 20
    a, b, c, d, f = (values[name] for name in CONDITIONS)
    samples: dict[str, list[float]] = {name: [] for name in ("A", "A_minus_B", "A_minus_C", "A_minus_D", "A_minus_F")}
    for _ in range(10_000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - block + 1)
            indices.extend(range(start, start + block))
        indices = indices[:count]
        samples["A"].append(fmean(a[index] for index in indices))
        samples["A_minus_B"].append(fmean(a[index] - b[index] for index in indices))
        samples["A_minus_C"].append(fmean(a[index] - c[index] for index in indices))
        samples["A_minus_D"].append(fmean(a[index] - d[index] for index in indices))
        samples["A_minus_F"].append(fmean(a[index] - f[index] for index in indices))

    def estimate(name: str, observed: float) -> dict[str, object]:
        return {"estimate": observed, "ci95_percentile_linear": [percentile(samples[name], 0.025), percentile(samples[name], 0.975)]}

    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10_000,
        "seed": 20260913,
        "common_indices_all_conditions": True,
        "percentile_implementation": "linear interpolation at (n-1)*q",
        "A_daily_mean_net_jpy": estimate("A", fmean(a)),
        "A_minus_B_daily_mean_net_jpy": estimate("A_minus_B", fmean(x - y for x, y in zip(a, b, strict=True))),
        "A_minus_C_daily_mean_net_jpy": estimate("A_minus_C", fmean(x - y for x, y in zip(a, c, strict=True))),
        "A_minus_D_daily_mean_net_jpy": estimate("A_minus_D", fmean(x - y for x, y in zip(a, d, strict=True))),
        "A_minus_F_daily_mean_net_jpy": estimate("A_minus_F", fmean(x - y for x, y in zip(a, f, strict=True))),
    }


def preregistration(
    source: dict[str, object], inputs: dict[str, object], implementation: dict[str, str]
) -> dict[str, Any]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R019-Q001",
        "status": "frozen_before_r019_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation, an unused sample, or research-wide multiplicity-corrected validation.",
        "novelty_correspondence": {
            "R003": "current-opening compression plus breakout, not prior same-type scheduled-session 60-minute high-low range change",
            "R008": "current-session compression and breakout, not a 20-session historical regime",
            "R009": "current close-change path consistency, not previous-session high-low ranges",
            "R013": "one immediate prior same-type early direction, not 20 non-overlapping previous range windows nor a state-conditioned follow/reverse rule",
            "conclusion": "No R001-R018 registration, code, or result uses V=3*sum(R1..R5)-sum(R6..R20), where each Ri is a scheduled prior same-type session's first 60-bar high-low range, to switch sign(M) at S+29 between follow and reverse.",
        },
        "hypothesis": "When prior same-type session range is expanding, following the current initial movement is favorable; when it is contracting, reversing it is favorable. The fixed state-conditioned rule has post-cost expectancy and exceeds always-long, always-short, unconditional initial follow, and unconditional initial reverse controls. Persistent price-variation state is an unobserved explanation only; this does not assert observed order flow or participant behavior.",
        "fixed_rule": {
            "schedule": "For both day and night: S is versioned scheduled open; t=S+29, E=S+30, X=S+90. Check E<=new-entry cutoff and X<=F using schedule only.",
            "history": "p1..p20 are exactly the prior 20 calendar-scheduled same-type sessions, most recent first, and must all have ended before S. Do not derive the list from rows. Missing, quarantined, ineligible, out-of-Development, or invalid p_i rejects the current event; never skip it and backfill older history.",
            "state": "Each Ri=max(high)-min(low) over exactly the 60 scheduled bars [P_i,P_i+59], requiring contiguous eligible bars. Ri=0 is observed and retained. V=3*sum(R1..R5)-sum(R6..R20); V>0 expanded, V<0 contracted, V=0 skip.",
            "current": "Require the 30 contiguous eligible bars [S,S+29]; M=close_t-open_S, and M=0 skips. Current prices do not enter V. No absolute threshold, current-range filter, weekday/year/direction filter, normalization, volume, or external input.",
            "conditions": {"A": "V>0: sign(M); V<0: -sign(M)", "B": "always long", "C": "always short", "D": "sign(M)", "F": "-sign(M)", "A2": "A re-executed by unchanged engine with 2 ticks/side"},
            "orders": "Issue entry after t close; earliest next eligible E open. Issue EXIT after X-1 close; earliest X open. Delay never extends X. No entry at or after X; retain engine max-delay, conflicts, forced-flat, one-position and cutoff behavior.",
            "prohibited": ["Stop", "Target", "re-entry", "state-direction reversal", "window/time/threshold rescue", "additional cost or delay conditions", "WFA", "OOS", "Final Holdout"],
        },
        "inputs": {"physical": inputs, "r004_fixed_quarantine": "Must reproduce hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa, 45 sessions/27,345 bars excluded, 2,216 sessions/1,326,086 bars retained.", "quality_ceiling": "PASS_LIMITED; inherited continuous-series, real-contract/roll/adjustment and post-quality whole-session-isolation limits."},
        "costs": {"baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30}, "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; never deduct slippage attribution twice."},
        "evaluation": {"daily": "Fixed 1,120 trade-date axis; day and night sum; common skips/no-trades are 0. Exclude only dates where both sessions are R004-isolated; retain a nonisolated session when only one is isolated.", "bootstrap": {"block": 20, "repetitions": 10_000, "seed": 20260913, "common_indices": True, "sampling": "non-circular moving blocks with replacement and tail truncation", "ci": "linear percentile"}, "information_gate": ["A/B/C/D/F >=200 trades", "each V-sign x M-sign eligible group >=50 pre-events"], "pass_requires_all": ["A Net>0", "A PF>1", "all A/A-B/A-C/A-D/A-F 95% lower bounds>0", "A2 expectancy>0", "A positive months>=27/54", "A Net excluding top10 winners>0"], "decision": "BLOCKED on input/synthetic/execution/accounting failure; INCONCLUSIVE on information failure; otherwise REJECT if any primary condition fails. All passing is Development primary/PASS_LIMITED only, never automatic CANDIDATE."},
        "audit_requirements": "Save all condition events/orders/fills/trades, skip/cancel reasons, planned/actual times, delays, exit reasons; verify common pre-events, one position, no successful-fill intersection, and direction/path/PnL equality A=D in expanded and A=F in contracted. Attribute A-D differences only to contracted and A-F only to expanded states.",
        "identifiers_before_run": {"git_commit": source["git_commit"], "source_hash": source["source_hash"], "implementation_files": implementation, "implementation_files_hash": canonical_hash(implementation), "input_manifest_hash": canonical_hash(inputs)},
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def direction_for(condition: Condition, event: dict[str, object]) -> str:
    if condition == "B_always_long":
        return "long"
    if condition == "C_always_short":
        return "short"
    key = {
        "A_regime_switch": "A_direction",
        "D_initial_follow": "D_direction",
        "F_initial_reverse": "F_direction",
    }[condition]
    return cast(str, event[key])


def run_condition(
    data: ResearchData,
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    condition: Condition,
    isolated: set[tuple[date, Session]],
    targets: list[str],
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups = session_groups(data.bars)
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text_day in targets:
        trade_day = date.fromisoformat(text_day)
        for session in (Session.DAY, Session.NIGHT):
            if (trade_day, session) in isolated:
                continue
            history = {day: rows for (day, type_), rows in groups.items() if type_ is session}
            event = prior_range_regime_event(classifier, trade_day, session, groups.get((trade_day, session), []), history, isolated)
            event.update({"condition": condition, "pre_event_status": event["status"]})
            audit[f"event_{event['status']}"] += 1
            if event["status"] != "eligible":
                audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
                events.append(event)
                continue
            signal = datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"]))
            result = engine.run(groups[(trade_day, session)], PriorRangeRegimeStrategy(f"r019_q001_{condition}", signal, direction_for(condition, event)), canonical_hash({"condition": condition, "event": event, "data_version": data.data_version}))
            if len(result.trades) > 1:
                raise AssertionError("R019 produced more than one trade per session")
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


def daily(trades: tuple[Trade, ...], targets: list[str]) -> dict[str, int]:
    output = dict.fromkeys(targets, 0)
    for trade in trades:
        output[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return output


def write_condition(folder: Path, condition: str, trades: tuple[Trade, ...], events: list[dict[str, object]], metrics: dict[str, object], audit: dict[str, int], data: ResearchData, ticks: int, net_daily: dict[str, int]) -> None:
    write_results(folder, trades, (), {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "condition": condition, "status": "complete", "data_version": data.data_version, "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame({"trade_date": sorted(net_daily), "net_pnl_jpy": [net_daily[item] for item in sorted(net_daily)]}).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int) -> dict[str, object]:
    eligible = [item for item in events if item.get("pre_event_status") == "eligible"]
    filled = [item for item in events if item.get("status") == "filled"]
    checks = {"every_eligible_event_filled": len(eligible) == len(filled), "one_trade_per_filled_event": len(filled) == len(trades), "no_canceled_or_unfilled_eligible_order": not any(item.get("status") == "eligible_order_unfilled" for item in events), "entry_within_max_delay": all(0 <= cast(int, item["entry_delay_minutes"]) <= max_delay for item in filled), "fixed_signal_exit_within_max_delay": all(item["exit_reason"] == "signal" and 0 <= cast(int, item["exit_delay_minutes"]) <= max_delay for item in filled), "net_equals_gross_minus_fees": all(item.net_pnl_jpy == item.gross_pnl_jpy - item.fees_jpy for item in trades), "no_force_flat_or_end_of_data": all(item.exit_reason.value not in {"force_flat", "end_of_data"} for item in trades)}
    return {"checks": checks, "all_pass": all(checks.values()), "accounting": "slippage attribution is informational and is never additionally deducted"}


def common_pre_events(events: dict[str, list[dict[str, object]]]) -> bool:
    fields = ("trade_date", "session", "pre_event_status", "reason", "scheduled_prior_trade_dates_p1_to_p20", "R_points_p1_to_p20", "V_points", "M_points")
    base = events["A_regime_switch"]
    return all(len(events[name]) == len(base) and all(tuple(row.get(field) for field in fields) == tuple(events[name][index].get(field) for field in fields) for index, row in enumerate(base)) for name in CONDITIONS[1:])


def state_diagnostics(a_events: list[dict[str, object]], d_events: list[dict[str, object]], f_events: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for state, control, label in (("expanded", d_events, "A_D"), ("contracted", f_events, "A_F")):
        pairs = [(a, b) for a, b in zip(a_events, control, strict=True) if a.get("range_regime") == state]
        output[f"{label}_{state}_same_path"] = {"pre_event_count": len(pairs), "A_filled": sum(a.get("status") == "filled" for a, _ in pairs), "control_filled": sum(b.get("status") == "filled" for _, b in pairs), "side_match": all(a.get("side") == b.get("side") for a, b in pairs if a.get("status") == b.get("status") == "filled"), "A_minus_control_net_jpy": sum(cast(int, a.get("net_pnl_jpy", 0)) - cast(int, b.get("net_pnl_jpy", 0)) for a, b in pairs)}
    for label, control in (("A_D", d_events), ("A_F", f_events)):
        for state in ("expanded", "contracted"):
            pairs = [(a, b) for a, b in zip(a_events, control, strict=True) if a.get("range_regime") == state]
            output[f"{label}_{state}_contribution"] = {"pre_event_count": len(pairs), "A_minus_control_net_jpy": sum(cast(int, a.get("net_pnl_jpy", 0)) - cast(int, b.get("net_pnl_jpy", 0)) for a, b in pairs)}
    groups: dict[str, object] = {}
    for v_sign in ("positive", "negative"):
        for m_sign in ("positive", "negative"):
            rows = [row for row in a_events if (cast(int, row.get("V_points", 0)) > 0) == (v_sign == "positive") and (cast(int, row.get("M_points", 0)) > 0) == (m_sign == "positive") and row.get("pre_event_status") == "eligible"]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            groups[f"V_{v_sign}_M_{m_sign}"] = {"pre_event_count": len(rows), "trade_count": len(filled), "entry_delay_minutes_total": sum(cast(int, row["entry_delay_minutes"]) for row in filled), "exit_delay_minutes_total": sum(cast(int, row["exit_delay_minutes"]) for row in filled), "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled), "net_pnl_jpy": sum(net), "expectancy_jpy": fmean(net) if net else None}
    output["V_sign_x_M_sign"] = groups
    return output


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (baseline.execution.slippage_ticks, baseline.fees.jpy_per_side_per_contract, baseline.execution.max_fill_delay_minutes, baseline.execution.allow_cross_session_pending_order, baseline.risk.new_entry_cutoff_minutes_before_session_close, baseline.risk.force_flat_minutes_before_session_close)
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R019 specification")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    implementation = implementation_snapshot()
    write_implementation_snapshot(implementation)
    inputs = input_manifest(data_config.gold_root)
    write_json(OUT / "input_manifest.json", inputs)
    plan = preregistration(source, inputs, implementation)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "effective_config.json", {"instrument": instrument.model_dump(mode="json"), "backtest": baseline.model_dump(mode="json")})
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "started_at": datetime.now(timezone.utc).isoformat(), "status": "preregistered_before_r019_price_statistics_events_or_pnl", "source": source, "plan_hash": canonical_hash(plan), "seed": 20260913, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r019_q001.py", "tests/test_exit_after_entry_cutoff.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r019.py", "src/n225m_bt/strategies/prior_range_regime.py", "tests/test_r019_q001.py", str(Path(__file__).relative_to(ROOT))], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r019.py", "src/n225m_bt/strategies/prior_range_regime.py"]}
    validation: dict[str, Any] = {"coverage": ["calendar p1..p20 order, same-type and Development boundary", "non-overlapping 5/15 past ranges, V signs/zero, Ri=0 and past-level shift invariance", "missing/ineligible/quarantined references never backfill; current contiguous 30 bars and M signs/zero", "next-bar entry, fixed exit, delayed non-extension, cutoff EXIT, single position and accounting", "Final Holdout input rejection"]}
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    validation["status"] = "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R019 synthetic/static validation failed before Development price access")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    development = load_split(data_config.gold_root, "development")
    parent_groups = session_groups(development.bars)
    view, quarantine_audit, isolated = quarantine(development)
    targets = sorted(
        {
            item.isoformat()
            for item, _ in parent_groups
            if not ((item, Session.DAY) in isolated and (item, Session.NIGHT) in isolated)
        }
    )
    if len(targets) != EXPECTED_TARGET_DATES:
        raise ValueError(f"R019 target trade-date count {len(targets)} != {EXPECTED_TARGET_DATES}")
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": development.quality, "quarantine": quarantine_audit, "fixed_target_trade_dates": targets, "physical_io": "Development selected normalized Parquet only; OOS and Final Holdout never selected", "logical_price_access": "trade_date 2021-01-01..2025-06-30 only"})
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    results: dict[str, dict[str, object]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(view, classifier, engine, condition, isolated, targets)
        metrics, net_daily = research_metrics(trades, view.bars), daily(trades, targets)
        write_condition(reserve_directory(OUT, condition), condition, trades, events, metrics, audit, view, 1, net_daily)
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit}
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, net_daily
    stress_config = baseline.model_copy(update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})})
    stress_trades, stress_events, stress_audit = run_condition(view, classifier, BacktestEngine(instrument.instrument.to_spec(), stress_config, classifier), "A_regime_switch", isolated, targets)
    stress_metrics, stress_daily = research_metrics(stress_trades, view.bars), daily(stress_trades, targets)
    write_condition(reserve_directory(OUT, "A_regime_switch_2tick"), "A_regime_switch_2tick", stress_trades, stress_events, stress_metrics, stress_audit, view, 2, stress_daily)
    results["A_regime_switch_2tick"] = {"trade_count": len(stress_trades), "metrics": stress_metrics, "execution_audit": stress_audit}
    values = {condition: [daily_by[condition][item] for item in targets] for condition in CONDITIONS}
    boot = bootstrap(values)
    audits = {condition: path_audit(events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes) for condition in CONDITIONS} | {"A_regime_switch_2tick": path_audit(stress_events, stress_trades, baseline.execution.max_fill_delay_minutes)}
    diagnostics = state_diagnostics(events_by["A_regime_switch"], events_by["D_initial_follow"], events_by["F_initial_reverse"])
    same_expanded = cast(dict[str, object], diagnostics["A_D_expanded_same_path"])
    same_contracted = cast(dict[str, object], diagnostics["A_F_contracted_same_path"])
    contribution_checks = cast(int, cast(dict[str, object], diagnostics["A_D_expanded_contribution"])["A_minus_control_net_jpy"]) == 0 and cast(int, cast(dict[str, object], diagnostics["A_F_contracted_contribution"])["A_minus_control_net_jpy"]) == 0
    same_checks = all(cast(bool, item["side_match"]) and cast(int, item["A_minus_control_net_jpy"]) == 0 for item in (same_expanded, same_contracted))
    shared = common_pre_events(events_by)
    post = {"status": "PASS" if shared and all(cast(bool, audit["all_pass"]) for audit in audits.values()) and same_checks and contribution_checks else "BLOCKED", "conditions": audits, "all_conditions_identical_pre_event": shared, "state_diagnostics": diagnostics, "expanded_A_equals_D_and_contracted_A_equals_F": same_checks, "A_D_difference_only_contracted_and_A_F_difference_only_expanded": contribution_checks, "policy": "Any execution/accounting mismatch blocks the economic decision; no successful-fill intersection is used."}
    write_json(OUT / "post_execution_validation.json", post)
    write_json(OUT / "state_direction_diagnostics.json", diagnostics)
    monthly = {month: 0 for month in month_keys()}
    for day, amount in daily_by["A_regime_switch"].items():
        monthly[day[:7]] += amount
    a_metrics = cast(dict[str, Any], results["A_regime_switch"]["metrics"])
    a_overall = cast(dict[str, Any], a_metrics["overall"])
    stress_overall = cast(dict[str, Any], stress_metrics["overall"])
    def lower(name: str) -> bool:
        return cast(list[float], boot[name]["ci95_percentile_linear"])[0] > 0
    group_gate = all(cast(int, item["pre_event_count"]) >= 50 for item in cast(dict[str, dict[str, object]], diagnostics["V_sign_x_M_sign"]).values())
    gates = {"A_trade_count_at_least_200": len(trades_by["A_regime_switch"]) >= 200, "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200, "C_trade_count_at_least_200": len(trades_by["C_always_short"]) >= 200, "D_trade_count_at_least_200": len(trades_by["D_initial_follow"]) >= 200, "F_trade_count_at_least_200": len(trades_by["F_initial_reverse"]) >= 200, "each_V_sign_x_M_sign_pre_event_at_least_50": group_gate, "A_net_positive": a_overall["net_pnl_jpy"] > 0, "A_profit_factor_above_1": a_overall["profit_factor"] is not None and a_overall["profit_factor"] > 1, "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"), "A_minus_B_bootstrap_lower_above_0": lower("A_minus_B_daily_mean_net_jpy"), "A_minus_C_bootstrap_lower_above_0": lower("A_minus_C_daily_mean_net_jpy"), "A_minus_D_bootstrap_lower_above_0": lower("A_minus_D_daily_mean_net_jpy"), "A_minus_F_bootstrap_lower_above_0": lower("A_minus_F_daily_mean_net_jpy"), "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None and stress_overall["expectancy_jpy"] > 0, "A_positive_months_at_least_27_of_54": sum(amount > 0 for amount in monthly.values()) >= 27, "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])["net_excluding_top10_jpy"] > 0}
    information = [name for name in gates if "trade_count" in name or name == "each_V_sign_x_M_sign_pre_event_at_least_50"]
    decision = "BLOCKED" if post["status"] != "PASS" else "INCONCLUSIVE" if not all(gates[name] for name in information) else "DEVELOPMENT_PRIMARY_CONDITIONS_PASSED_PASS_LIMITED" if all(gates.values()) else "REJECT"
    write_json(OUT / "daily_net_pnl_aligned.json", {"trade_dates": targets, "no_trade": "0", "both_sessions_quarantined": "excluded", "one_session_quarantined": "remaining session retained", **values})
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": decision, "gates": gates, "positive_months_of_54": sum(amount > 0 for amount in monthly.values()), "monthly_A_net_jpy": monthly, "A_required_segments": {"year_month_day_night_long_short": a_metrics["segments"], "state_diagnostics": diagnostics}, "conditions": results, "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction", "scope": "Development only; 2025 is Jan-Jun; no WFA/OOS/Final Holdout"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_complete", "decision": decision, "quality_status": "PASS_LIMITED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
