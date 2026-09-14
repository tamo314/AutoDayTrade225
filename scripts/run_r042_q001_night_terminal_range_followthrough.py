"""Execute the preregistered Development-only R042-Q002 experiment."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
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
from n225m_bt.domain import ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r042 import night_terminal_range_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.night_terminal_range_followthrough import (
    NightTerminalRangeFollowthroughStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r042-q002-20260914-night-terminal-range-followthrough-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED = 20260921
REQUIRE_ROLLING_RANGE_LAYER = True
Q003_CONTINUOUS_RANGE_ADJUSTMENT = False
IMPLEMENTATION_SCRIPT: Path | None = None
EXTRA_IMPLEMENTATION_FILES: tuple[Path, ...] = ()
Condition = Literal["A_terminal", "B_all", "C_nonterminal", "D_buy", "E_sell", "F_reverse"]
CONDITIONS: tuple[Condition, ...] = (
    "A_terminal",
    "B_all",
    "C_nonterminal",
    "D_buy",
    "E_sell",
    "F_reverse",
)
VARIANTS = {
    "A2_2tick": (2, 0, "08:45"),
    "A3_3tick": (3, 0, "08:45"),
    "A_delay": (1, 1, "08:45"),
    "A_tse": (1, 0, "08:59"),
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def manifest(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_r042_q002_price_statistics_events_or_pnl",
        "scope": "Selected normalized Development Parquet and versioned OSE schedule only; raw, volume, external prices, OOS and Final Holdout prohibited.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    kept = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listed = [
        {"trade_date": item.isoformat(), "session": session.value}
        for item, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(kept),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(kept),
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in bar.quality_flags for bar in kept
        ),
        "rule": "exclude complete (trade_date,session) containing TICK_GRID_VIOLATION",
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
    mismatch = {
        key: {"actual": audit[key], "expected": expected[key]}
        for key in expected
        if audit[key] != expected[key]
    }
    audit.update({"expected_match": not mismatch, "mismatches": mismatch})
    if mismatch:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatch}")
    return (
        ResearchData(
            kept,
            canonical_hash({"parent": data.data_version, "isolated": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def preregistration(
    source: dict[str, object], inputs: dict[str, object], files: dict[str, str]
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R042-Q002",
        "status": "frozen_before_r042_q002_price_statistics_events_or_pnl",
        "seed": SEED,
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation.",
        "supersedes": {
            "study": "R042-Q001",
            "reason": "Q001 was BLOCKED before price statistics because its CN-originated cross-session pending order was incompatible with the immutable execution contract. Q002 preserves state and thresholds but emits a fresh day-session order after 08:45 bar confirmation.",
        },
        "duplicate_review": {
            "R001_R042_Q001": "R012 lacks terminal outer-quartile state and 08:46/09:46 path; R022 has different session-state mapping; R041 uses 09:00--09:29 day information and 09:30 entry. No registered R001--R042 rule combines full same-trade-date normal-night ON/CN/HN/LN directional equality-inclusive outer quartile, prior-60 RN/ON median diagnostic, fresh 08:45 signal, 08:46 entry, and 09:46 exit.",
            "conclusion": "No duplicate; no alternative specification created.",
        },
        "hypothesis": "When the complete normal night close stays in the outer directional quartile of its night-return direction, fixed sign(rN) follow-through from 08:46 to 09:46 has post-cost expectancy, exceeds all valid nights, nonterminal nights, fixed directions and reverse, and remains positive after 09:00. It is a price-only hypothesis, not an identification of volume, cash, news or participants.",
        "fixed_rule": {
            "state": "For exactly one preceding same-trade-date scheduled normal night require every scheduled eligible minute. ON first scheduled normal open; CN last normal close; HN/LN extrema; rN=CN-ON; RN=HN-LN. Never substitute observed endpoints, auction/force-flat, or an older night. Exclude isolated, outside, missing, RN<=0, rN=0. rN>0 terminal iff 4*(CN-LN)>=3*RN; rN<0 terminal iff 4*(HN-CN)>=3*RN; equality terminal.",
            "range_layer": "Exactly preceding 60 scheduled nights, newest-first; no target, invalid replacement, or old-night backfill. Valid is complete nonisolated Development normal night with RN/ON defined. >=50: nearest-rank median QM; RN/ON<=QM low, else high. Diagnostic only, never a filter.",
            "conditions": {
                "A_terminal": "terminal sign(rN)",
                "B_all": "all valid sign(rN)",
                "C_nonterminal": "nonterminal sign(rN)",
                "D_buy": "A event always long",
                "E_sell": "A event always short",
                "F_reverse": "A event -sign(rN)",
                "A2_A3": "A at 2/3 ticks side plus 30JPY",
                "A_delay": "A 08:47 entry same 09:46 exit",
                "A_tse": "same A state reissued after 08:59, 09:00 entry same 09:46 exit",
            },
            "execution": "08:45 bar close only emits a fresh day-session signal; its price never affects state, side, selection or cancellation. Fill 08:46 open; exit signal 09:45 / fill 09:46 open. A_tse signal 08:59 / fill 09:00. One contract/trade_date/position, no cross-session pending, stop/target/reentry/update/early exit.",
        },
        "evaluation": {
            "common_axis": "Scheduled Development OSE day dates excluding day-isolated sessions; all missing/state/history/day-execution failures and no-trades retained as 0JPY.",
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10000,
                "seed": SEED,
                "common_indices": True,
                "noncircular": True,
                "tail_truncate": True,
                "percentile": "linear",
            },
            "information_gate": "B>=700; A/C>=200; A long/short>=70; terminal/nonterminal x rN sign all>=60; low/high x terminal/nonterminal all>=50.",
            "decision": "BLOCKED on input/synthetic/execution/accounting failure; INCONCLUSIVE on information failure; otherwise REJECT unless every fixed criterion is met; all pass is INVESTIGATE only.",
        },
        "inputs": {
            "physical": inputs,
            "r004_fixed_isolation": "45 sessions/27,345 bars removed; 2,216 sessions/1,326,086 bars retained; hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
            "quality_ceiling": "PASS_LIMITED",
        },
        "costs": {
            "baseline": "1 tick/side + 30JPY/side",
            "A2_A3": "2/3 ticks/side + 30JPY/side",
            "accounting": "Gross is slippage-inclusive; Net=Gross-fees; no double slippage.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "implementation_files": files,
            "implementation_files_hash": canonical_hash(files),
            "input_manifest_hash": canonical_hash(inputs),
            "seed": SEED,
        },
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def selectable(condition: Condition, state: str) -> bool:
    return (
        (condition == "B_all" and state in {"terminal", "nonterminal"})
        or (condition == "C_nonterminal" and state == "nonterminal")
        or (condition in {"A_terminal", "D_buy", "E_sell", "F_reverse"} and state == "terminal")
    )


def side(condition: Condition, event: dict[str, object]) -> str:
    if condition == "D_buy":
        return "long"
    if condition == "E_sell":
        return "short"
    if condition == "F_reverse":
        return cast(str, event["F_reverse_direction"])
    return cast(str, event["A_direction"])


def event_for(
    classifier: CalendarClassifier,
    target: date,
    schedule: list[date],
    groups: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    normal_cache: dict[date, tuple[list[Any] | None, dict[str, object], str | None]],
) -> dict[str, object]:
    index = schedule.index(target)
    history = [
        (
            schedule[position],
            groups.get((schedule[position], Session.NIGHT)),
            (schedule[position], Session.NIGHT) in isolated,
        )
        for position in range(index - 1, index - 61, -1)
        if position >= 0
    ]
    return night_terminal_range_event(
        classifier,
        target,
        groups.get((target, Session.NIGHT)),
        history,
        day_quarantined=(target, Session.DAY) in isolated,
        night_quarantined=(target, Session.NIGHT) in isolated,
        normal_cache=normal_cache,  # type: ignore[arg-type]
        require_rolling_range_layer=REQUIRE_ROLLING_RANGE_LAYER,
    )


def run_condition(
    engine: BacktestEngine,
    condition: Condition,
    groups: dict[tuple[date, Session], list[Any]],
    isolated: set[tuple[date, Session]],
    axis: list[str],
    schedule: list[date],
    base_events: dict[str, dict[str, object]],
    *,
    delay: int = 0,
    signal_clock: str = "08:45",
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for text in axis:
        target = date.fromisoformat(text)
        event = deepcopy(base_events[text])
        state = cast(str, event["status"])
        event.update(
            {
                "condition": condition,
                "base_event_status": state,
                "entry_delay_minutes": delay,
                "signal_clock": signal_clock,
            }
        )
        if not selectable(condition, state):
            event.update({"status": "skipped", "reason": event.get("reason", f"CONDITION_{state}")})
            audit[f"skipped_{event['reason']}"] += 1
            records.append(event)
            continue
        hour, minute = map(int, signal_clock.split(":"))
        signal = datetime(
            target.year,
            target.month,
            target.day,
            hour,
            minute,
            tzinfo=engine.classifier.session_open(target, Session.DAY).tzinfo,
        )
        exit_signal = datetime(target.year, target.month, target.day, 9, 45, tzinfo=signal.tzinfo)
        result = engine.run(
            groups.get((target, Session.DAY), []),
            NightTerminalRangeFollowthroughStrategy(
                f"r042_q002_{condition}", signal, side(condition, event), delay, exit_signal
            ),
            canonical_hash(
                {
                    "campaign": IDENTIFIER,
                    "condition": condition,
                    "event": event,
                    "delay": delay,
                    "signal": signal_clock,
                }
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R042-Q002 violated one trade_date / one position")
        audit["canceled_orders"] += result.canceled_orders
        if not result.trades:
            event.update(
                {"status": "eligible_order_unfilled", "reason": "ENGINE_NO_FILL_OR_FIXED_EXIT"}
            )
            audit["eligible_order_unfilled"] += 1
            records.append(event)
            continue
        trade = result.trades[0]
        event.update(
            {
                "status": "filled",
                "side": trade.side.value,
                "entry_ts_jst": trade.entry_ts.isoformat(),
                "exit_ts_jst": trade.exit_ts.isoformat(),
                "entry_signal_ts_jst": trade.entry_signal_ts.isoformat(),
                "exit_signal_ts_jst": trade.exit_signal_ts.isoformat()
                if trade.exit_signal_ts
                else None,
                "exit_reason": trade.exit_reason.value,
                "gross_pnl_jpy": trade.gross_pnl_jpy,
                "slippage_cost_jpy": trade.slippage_cost_jpy,
                "fees_jpy": trade.fees_jpy,
                "net_pnl_jpy": trade.net_pnl_jpy,
            }
        )
        trades.append(trade)
        audit["trades"] += 1
        records.append(event)
    return (
        tuple(
            replace(item, trade_id=f"trade-{index:06d}")
            for index, item in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
        ),
        records,
        dict(audit),
    )


def attach_price_audits(
    records: list[dict[str, object]], groups: dict[tuple[date, Session], list[Any]], multiplier: int
) -> None:
    for row in records:
        if row.get("base_event_status") not in {"terminal", "nonterminal"}:
            continue
        target = date.fromisoformat(cast(str, row["trade_date"]))
        bars = {bar.ts_jst: bar for bar in groups.get((target, Session.DAY), [])}
        tz = next(iter(bars)).tzinfo if bars else None
        if tz is None:
            row["raw_return_reason"] = "DAY_MISSING"
            continue
        entry, nine, exit_ = (
            datetime(target.year, target.month, target.day, h, m, tzinfo=tz)
            for h, m in ((8, 46), (9, 0), (9, 46))
        )
        if not all(point in bars for point in (entry, nine, exit_)):
            row["raw_return_reason"] = "FIXED_OPEN_MISSING"
            continue
        adjusted = (bars[exit_].open - bars[entry].open) * cast(int, row["rN_sign"]) * multiplier
        row.update({"raw_sign_adjusted_0846_0946_jpy": adjusted, "raw_return_reason": "OK"})
        if row.get("status") == "filled" and row.get("condition") == "A_terminal":
            trade_side = 1 if row["side"] == "long" else -1
            entry_fill = bars[entry].open + 5 * trade_side
            exit_fill = bars[exit_].open - 5 * trade_side
            first = (bars[nine].open - entry_fill) * trade_side * multiplier
            second = (exit_fill - bars[nine].open) * trade_side * multiplier
            row.update(
                {
                    "A_gross_0846_0900_jpy": first,
                    "A_gross_0900_0946_jpy": second,
                    "A_gross_decomposition_equals_trade_gross": first + second
                    == row["gross_pnl_jpy"],
                }
            )


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    point = (len(ordered) - 1) * q
    low, high = floor(point), ceil(point)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (point - low)
    )


def ols_terminal_beta(_: list[dict[str, object]]) -> float:
    """Q003 injects the fixed continuous-range OLS before this runner executes."""
    raise RuntimeError("continuous-range OLS was not configured")


def bootstrap(
    daily_by: dict[str, dict[str, int]],
    records: dict[str, list[dict[str, object]]],
    axis: list[str],
) -> dict[str, object]:
    arrays = {name: [daily_by[name][item] for item in axis] for name in CONDITIONS}
    filled = {
        name: {
            cast(str, row["trade_date"]): cast(int, row["net_pnl_jpy"])
            for row in records[name]
            if row.get("status") == "filled"
        }
        for name in ("A_terminal", "B_all", "C_nonterminal")
    }
    raw = {
        cast(str, row["trade_date"]): row
        for row in records["B_all"]
        if row.get("raw_return_reason") == "OK"
    }
    keys = (
        "A_daily_mean_net_jpy",
        "A_minus_B_conditional_expectancy_jpy",
        "A_minus_C_conditional_expectancy_jpy",
        "A_minus_D_daily_mean_net_jpy",
        "A_minus_E_daily_mean_net_jpy",
        "A_minus_F_daily_mean_net_jpy",
        "beta_terminal_continuous_range_adjusted_jpy",
    )
    samples: dict[str, list[float]] = {key: [] for key in keys}
    rng = Random(SEED)
    count = len(axis)
    for _ in range(10000):
        picks: list[int] = []
        while len(picks) < count:
            picks.extend(range((start := rng.randrange(count - 19)), start + 20))
        picks = picks[:count]
        samples["A_daily_mean_net_jpy"].append(fmean(arrays["A_terminal"][i] for i in picks))
        for control, label in (("D_buy", "D"), ("E_sell", "E"), ("F_reverse", "F")):
            samples[f"A_minus_{label}_daily_mean_net_jpy"].append(
                fmean(arrays["A_terminal"][i] - arrays[control][i] for i in picks)
            )
        for control, label in (("B_all", "B"), ("C_nonterminal", "C")):
            a = [filled["A_terminal"][axis[i]] for i in picks if axis[i] in filled["A_terminal"]]
            b = [filled[control][axis[i]] for i in picks if axis[i] in filled[control]]
            samples[f"A_minus_{label}_conditional_expectancy_jpy"].append(
                fmean(a) - fmean(b) if a and b else 0.0
            )
        if Q003_CONTINUOUS_RANGE_ADJUSTMENT:
            samples["beta_terminal_continuous_range_adjusted_jpy"].append(
                ols_terminal_beta([raw[axis[i]] for i in picks if axis[i] in raw])
            )
        else:
            cell: dict[tuple[str, str], list[int]] = defaultdict(list)
            for i in picks:
                row = raw.get(axis[i])
                if row is not None:
                    cell[(cast(str, row["range_layer"]), cast(str, row["base_event_status"]))].append(
                        cast(int, row["raw_sign_adjusted_0846_0946_jpy"])
                    )
            samples["beta_terminal_continuous_range_adjusted_jpy"].append(
                fmean(
                    fmean(cell[(layer, "terminal")]) - fmean(cell[(layer, "nonterminal")])
                    for layer in ("low", "high")
                )
                if all(
                    cell[(layer, state)]
                    for layer in ("low", "high")
                    for state in ("terminal", "nonterminal")
                )
                else 0.0
            )

    def conditional_estimate(left: str, right: str) -> float | None:
        if not filled[left] or not filled[right]:
            return None
        return fmean(filled[left].values()) - fmean(filled[right].values())

    estimates: dict[str, float | None] = {
        "A_daily_mean_net_jpy": fmean(arrays["A_terminal"]),
        "A_minus_B_conditional_expectancy_jpy": conditional_estimate("A_terminal", "B_all"),
        "A_minus_C_conditional_expectancy_jpy": conditional_estimate("A_terminal", "C_nonterminal"),
        "A_minus_D_daily_mean_net_jpy": fmean(
            a - b for a, b in zip(arrays["A_terminal"], arrays["D_buy"], strict=True)
        ),
        "A_minus_E_daily_mean_net_jpy": fmean(
            a - b for a, b in zip(arrays["A_terminal"], arrays["E_sell"], strict=True)
        ),
        "A_minus_F_daily_mean_net_jpy": fmean(
            a - b for a, b in zip(arrays["A_terminal"], arrays["F_reverse"], strict=True)
        ),
    }
    if Q003_CONTINUOUS_RANGE_ADJUSTMENT:
        estimates["beta_terminal_continuous_range_adjusted_jpy"] = ols_terminal_beta(
            list(raw.values())
        )
    else:
        cells: dict[tuple[str, str], list[int]] = defaultdict(list)
        for row in raw.values():
            cells[(cast(str, row["range_layer"]), cast(str, row["base_event_status"]))].append(
                cast(int, row["raw_sign_adjusted_0846_0946_jpy"])
            )
        estimates["beta_terminal_continuous_range_adjusted_jpy"] = (
            fmean(
                fmean(cells[(layer, "terminal")]) - fmean(cells[(layer, "nonterminal")])
                for layer in ("low", "high")
            )
            if all(
                cells[(layer, state)]
                for layer in ("low", "high")
                for state in ("terminal", "nonterminal")
            )
            else None
        )
    return {
        "method": "20 trade-date noncircular moving-block bootstrap, common indices, tail truncate, linear percentile; conditional samples recompute sum/count",
        "repetitions": 10000,
        "seed": SEED,
        **{
            key: {
                "estimate": estimates[key],
                "ci95_percentile_linear": [percentile(value, 0.025), percentile(value, 0.975)],
            }
            for key, value in samples.items()
        },
    }


def strata(records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for state in ("terminal", "nonterminal"):
        for direction in ("long", "short"):
            rows = [
                row
                for row in records
                if row.get("base_event_status") == state and row.get("A_direction") == direction
            ]
            filled = [row for row in rows if row.get("status") == "filled"]
            net = [cast(int, row["net_pnl_jpy"]) for row in filled]
            output[f"{state}_{direction}"] = {
                "event_count": len(rows),
                "trade_count": len(filled),
                "gross_pnl_jpy": sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in filled),
                "fees_jpy": sum(cast(int, row.get("fees_jpy", 0)) for row in filled),
                "net_pnl_jpy": sum(net),
                "expectancy_jpy": fmean(net) if net else None,
            }
    if REQUIRE_ROLLING_RANGE_LAYER:
        for layer in ("low", "high"):
            for state in ("terminal", "nonterminal"):
                rows = [
                    row
                    for row in records
                    if row.get("base_event_status") == state and row.get("range_layer") == layer
                ]
                filled = [row for row in rows if row.get("status") == "filled"]
                net = [cast(int, row["net_pnl_jpy"]) for row in filled]
                output[f"{layer}_{state}"] = {
                    "event_count": len(rows),
                    "trade_count": len(filled),
                    "gross_pnl_jpy": sum(cast(int, row.get("gross_pnl_jpy", 0)) for row in filled),
                    "fees_jpy": sum(cast(int, row.get("fees_jpy", 0)) for row in filled),
                    "net_pnl_jpy": sum(net),
                    "expectancy_jpy": fmean(net) if net else None,
                    "raw_sign_adjusted_return_mean_jpy": fmean(
                        cast(int, row["raw_sign_adjusted_0846_0946_jpy"])
                        for row in rows
                        if row.get("raw_return_reason") == "OK"
                    )
                    if any(row.get("raw_return_reason") == "OK" for row in rows)
                    else None,
                }
    return output


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.max_fill_delay_minutes,
        baseline.execution.allow_cross_session_pending_order,
    ) != (1, 30, 10, False):
        raise ValueError("active execution/cost contract differs from frozen R042-Q002 contract")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    names = [
        (IMPLEMENTATION_SCRIPT or Path(__file__)).relative_to(ROOT),
        *EXTRA_IMPLEMENTATION_FILES,
        Path("src/n225m_bt/research/r042.py"),
        Path("src/n225m_bt/strategies/night_terminal_range_followthrough.py"),
        Path("tests/test_r042_q001.py"),
    ]
    files, inputs = (
        {str(name): digest(ROOT / name) for name in names},
        manifest(data_config.gold_root),
    )
    with ZipFile(OUT / "implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(ROOT / name, name)
    plan = preregistration(source, inputs, files)
    for path, value in (
        ("input_manifest.json", inputs),
        ("preregistration.json", plan),
        (
            "effective_config.json",
            {
                "instrument": instrument.model_dump(mode="json"),
                "backtest": baseline.model_dump(mode="json"),
            },
        ),
        (
            "campaign_manifest.json",
            {
                "campaign_id": IDENTIFIER,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "preregistered_before_price_statistics_events_or_pnl",
                "plan_hash": canonical_hash(plan),
                "seed": SEED,
                "oos": "NOT_EVALUATED",
                "final_holdout": "NOT_ACCESSED",
            },
        ),
    ):
        write_json(OUT / path, value)
    commands = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r042_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [executable, "-m", "ruff", "check", *map(str, names)],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r042.py",
            "src/n225m_bt/strategies/night_terminal_range_followthrough.py",
        ],
    }
    validation: dict[str, object] = {
        "coverage": "institution regimes/night-day mapping/full normal night/ON-CN-HN-LN/terminal equality/up-down/missing-isolation-outside/prefix/fresh 08:45->08:46 and 08:59->09:00 fills/fixed exits/no cross-session/accounting/OOS-Holdout lock"
    }
    for key, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[key] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, object], validation[key])["returncode"] == 0 for key in commands)
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R042-Q002 synthetic/static gate failed before Development price access")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    groups = session_groups(view.bars)
    schedule = [item.trade_date for item in classifier.exchange_calendar.trading_days()]
    axis = [
        item.isoformat()
        for item in schedule
        if date(2021, 1, 1) <= item <= date(2025, 6, 30) and (item, Session.DAY) not in isolated
    ]
    if len(axis) != 1111:
        raise ValueError(f"unexpected fixed Development day axis {len(axis)}")
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": quarantine_audit,
            "fixed_target_trade_dates": axis,
            "fixed_target_count": len(axis),
            "schedule_snapshot_sha256": digest(ROOT / "config" / "sessions.yaml"),
            "calendar_snapshot_sha256": digest(ROOT / "config" / "local_calendar.yaml"),
            "physical_io": "Development normalized Parquet only; no OOS or Final Holdout selected",
        },
    )
    records_by: dict[str, list[dict[str, object]]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    all_day_bars = [
        bar for text in axis for bar in groups.get((date.fromisoformat(text), Session.DAY), [])
    ]
    cases: list[tuple[str, Condition, int, int, str]] = [
        (name, name, 1, 0, "08:45") for name in CONDITIONS
    ] + [
        (name, "A_terminal", ticks, delay, clock)
        for name, (ticks, delay, clock) in VARIANTS.items()
    ]
    normal_cache: dict[date, tuple[list[Any] | None, dict[str, object], str | None]] = {}
    base_events = {
        text: event_for(
            classifier,
            date.fromisoformat(text),
            schedule,
            groups,
            isolated,
            normal_cache,
        )
        for text in axis
    }
    for name, condition, ticks, delay, clock in cases:
        config = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, records, audit = run_condition(
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            condition,
            groups,
            isolated,
            axis,
            schedule,
            base_events,
            delay=delay,
            signal_clock=clock,
        )
        attach_price_audits(records, groups, instrument.instrument.contract_multiplier)
        daily = dict.fromkeys(axis, 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = OUT / name
        folder.mkdir()
        metrics = research_metrics(trades, all_day_bars)
        write_results(
            folder,
            trades,
            (),
            {
                "campaign_id": IDENTIFIER,
                "condition": name,
                "cost": f"{ticks} tick/side + 30JPY/side",
            },
        )
        write_json(folder / "events.json", records)
        pl.DataFrame(records).write_parquet(folder / "events.parquet")
        write_json(folder / "daily_net_pnl_aligned.json", daily)
        write_json(folder / "metrics_research.json", metrics)
        write_json(folder / "execution_audit.json", audit)
        records_by[name], trades_by[name], daily_by[name], results[name] = (
            records,
            trades,
            daily,
            {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit},
        )
    by_day = {
        name: {cast(str, row["trade_date"]): row for row in rows}
        for name, rows in records_by.items()
    }
    fields = ("side", "entry_ts_jst", "exit_ts_jst", "gross_pnl_jpy", "fees_jpy", "net_pnl_jpy")
    checks = {
        "terminal_A_B_path_equal": all(
            all(
                by_day["A_terminal"][day].get(key) == by_day["B_all"][day].get(key)
                for key in fields
            )
            for day in axis
            if by_day["A_terminal"][day].get("status") == "filled"
        ),
        "nonterminal_B_C_path_equal": all(
            all(
                by_day["B_all"][day].get(key) == by_day["C_nonterminal"][day].get(key)
                for key in fields
            )
            for day in axis
            if by_day["C_nonterminal"][day].get("status") == "filled"
        ),
        "A_F_opposite_side": all(
            by_day["A_terminal"][day].get("side") != by_day["F_reverse"][day].get("side")
            for day in axis
            if by_day["A_terminal"][day].get("status") == "filled"
        ),
        "A_variants_same_event_side": all(
            all(
                by_day["A_terminal"][day].get(key) == by_day[name][day].get(key)
                for key in (
                    "ON_points",
                    "CN_points",
                    "HN_points",
                    "LN_points",
                    "rN_points",
                    "RN_points",
                    "QM_RN_over_ON",
                    "range_layer",
                    "side",
                )
            )
            for name in VARIANTS
            for day in axis
        ),
        "fixed_fill_times": all(
            row.get("entry_ts_jst")
            == f"{row['trade_date']}T{'09:00' if name == 'A_tse' else ('08:47' if name == 'A_delay' else '08:46')}:00+09:00"
            and row.get("exit_ts_jst") == f"{row['trade_date']}T09:46:00+09:00"
            and row.get("exit_reason") == ExitReason.SIGNAL.value
            for name, rows in records_by.items()
            for row in rows
            if row.get("status") == "filled"
        ),
        "one_trade_per_day": all(
            len({trade.trade_date for trade in items}) == len(items) for items in trades_by.values()
        ),
        "gross_net_accounting": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for items in trades_by.values()
            for trade in items
        ),
        "no_double_slippage": all(
            trade.fees_jpy == 60 for items in trades_by.values() for trade in items
        ),
        "A_gross_decomposition": all(
            row.get("A_gross_decomposition_equals_trade_gross")
            for row in records_by["A_terminal"]
            if row.get("status") == "filled"
        ),
    }
    execution = {
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "checks": checks,
        "accounting": "Gross includes both execution slippage; Net=Gross-fees exactly once.",
    }
    write_json(OUT / "execution_accounting_audit.json", execution)
    boot, table = bootstrap(daily_by, records_by, axis), strata(records_by["B_all"])
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "strata.json", table)
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {
            "trade_dates": axis,
            "no_trade": "0 JPY",
            "series": {name: [daily_by[name][day] for day in axis] for name in daily_by},
        },
    )
    a_metrics = cast(dict[str, Any], results["A_terminal"])["metrics"]
    overall = cast(dict[str, Any], a_metrics)["overall"]
    years = {
        str(year): sum(daily_by["A_terminal"][day] for day in axis if day.startswith(str(year)))
        for year in range(2021, 2026)
    }
    months = {
        f"{year}-{month:02d}": sum(
            daily_by["A_terminal"][day] for day in axis if day.startswith(f"{year}-{month:02d}")
        )
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }
    info = {
        "B_trade_count_at_least_700": len(trades_by["B_all"]) >= 700,
        "A_trade_count_at_least_200": len(trades_by["A_terminal"]) >= 200,
        "C_trade_count_at_least_200": len(trades_by["C_nonterminal"]) >= 200,
        "A_long_at_least_70": sum(item.side.value == "long" for item in trades_by["A_terminal"])
        >= 70,
        "A_short_at_least_70": sum(item.side.value == "short" for item in trades_by["A_terminal"])
        >= 70,
        "four_terminal_state_direction_groups_at_least_60": all(
            cast(int, table[f"{state}_{direction}"]["trade_count"]) >= 60
            for state in ("terminal", "nonterminal")
            for direction in ("long", "short")
        ),
    }
    if REQUIRE_ROLLING_RANGE_LAYER:
        info["low_high_terminal_nonterminal_each_at_least_50"] = all(
            cast(int, table[f"{layer}_{state}"]["trade_count"]) >= 50
            for layer in ("low", "high")
            for state in ("terminal", "nonterminal")
        )

    def lower(key: str) -> bool:
        value = cast(dict[str, object], boot[key])
        ci = value["ci95_percentile_linear"]
        return value["estimate"] is not None and cast(list[float], ci)[0] > 0

    gates = info | {
        "A_net_positive": overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_one": overall["profit_factor"] is not None
        and overall["profit_factor"] > 1,
        "all_requested_ci_lowers_positive": all(
            lower(key)
            for key in (
                "A_daily_mean_net_jpy",
                "A_minus_B_conditional_expectancy_jpy",
                "A_minus_C_conditional_expectancy_jpy",
                "A_minus_D_daily_mean_net_jpy",
                "A_minus_E_daily_mean_net_jpy",
                "A_minus_F_daily_mean_net_jpy",
                "beta_terminal_continuous_range_adjusted_jpy",
            )
        ),
        "A2_A3_delay_tse_expectancy_positive": all(
            cast(dict[str, Any], results[name])["metrics"]["overall"]["expectancy_jpy"] is not None
            and cast(dict[str, Any], results[name])["metrics"]["overall"]["expectancy_jpy"] > 0
            for name in VARIANTS
        ),
        "at_least_three_positive_years_2021_2024": sum(
            value > 0 for year, value in years.items() if year != "2025"
        )
        >= 3,
        "positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "top10_winners_removed_net_positive": cast(dict[str, Any], a_metrics)["concentration"][
            "net_excluding_top10_jpy"
        ]
        > 0,
    }
    decision = (
        "BLOCKED"
        if execution["status"] != "PASS"
        else "INCONCLUSIVE"
        if not all(info.values())
        else "INVESTIGATE"
        if all(gates.values())
        else "REJECT"
    )
    write_json(
        OUT / "development_results.json",
        {
            "campaign_id": IDENTIFIER,
            "quality_status": "PASS_LIMITED",
            "decision": decision,
            "information_gate": info,
            "gates": gates,
            "conditions": results,
            "A_year_net_jpy": years,
            "A_month_net_jpy": months,
            "A_year_month_side_metrics": cast(dict[str, Any], a_metrics)["segments"],
            "strata": table,
            "scope": "Development only; 2025 Jan-Jun partial. No WFA/OOS/Final Holdout.",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "campaign_id": IDENTIFIER,
            "status": "development_complete",
            "decision": decision,
            "quality_status": "PASS_LIMITED",
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
