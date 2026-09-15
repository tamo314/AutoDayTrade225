"""Execute the preregistered Development-only R054-Q001 experiment."""

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
from sys import path as sys_path
from typing import Any, cast

import numpy as np
import polars as pl

sys_path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from numpy.typing import NDArray

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
from n225m_bt.research.r053 import R053QNotIdentifiableError, fwl_delta
from n225m_bt.research.r054 import BASE, R054Specification, r054_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r054-q001-20260915-tse-opening-range-failed-breakout-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
SEED = 20261002
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
BASE_CONDITIONS = ("A", "D", "B", "A_continue", "A_buy", "A_sell")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [
        {"path": str(p.resolve().relative_to(ROOT)), "sha256": digest(p)}
        for p in partition_paths(gold_root, "development")
    ]
    return {
        "status": "frozen_before_r054_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; OOS and Final Holdout are not selected or read.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {
        key
        for key, rows in groups.items()
        if any("TICK_GRID_VIOLATION" in r.quality_flags for r in rows)
    }
    included = [r for key, rows in groups.items() if key not in isolated for r in rows]
    listed = [{"trade_date": d.isoformat(), "session": s.value} for d, s in sorted(isolated)]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in r.quality_flags for r in included
        ),
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
        k: {"actual": audit[k], "expected": v} for k, v in expected.items() if audit[k] != v
    }
    audit.update(expected_match=not mismatch, mismatches=mismatch)
    if mismatch:
        raise ValueError(f"R004 quarantine mismatch: {mismatch}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def preregistration(
    source: dict[str, object], inputs: dict[str, object], implementation: dict[str, str]
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R054-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "prior_immutable_attempts": "…-01 stopped before Development price access because its own Ruff gate detected an import-order violation. …-02 completed event/execution but passed the post-R004 view (including retained night bars of day-isolated dates) to overall metrics, so its 1,120-date metrics axis did not equal the preregistered 1,111 trade_date axis. …-03 changes only that metrics input and adds the full fixed regression ledger; event, execution, cost, seed and decision predicates are unchanged.",
        "duplicate_review": {
            "R001_R053": "R034 alone shares the daily 30-minute opening H/L and first strict close breakout in bars 31--90, but fixes classification at b+15 and exits 60 minutes after its signal. R054 fixes classification at b+5, enters next scheduled open, exits after 30 minutes, and preregisters 20/40-minute range, 3/10-minute confirmation and 15/45-minute holding sensitivities. No registered study has all four of opening range, search window, failure confirmation and holding specifications.",
            "conclusion": "No duplicate; execute no alternative specification.",
        },
        "hypothesis": "A first strict close breakout of the first 30 planned TSE minutes that is back inside or on its opening-range boundary five planned minutes later has greater subsequent 30-minute return in the direction opposite the breakout than accepted breakouts and same-event continuation/fixed-side controls.",
        "event_rule": "For each target TSE day, H=max(high) and L=min(low) over mS..mS+29. Search mS+30..mS+89 only and use the first close>H (upper) or close<L (lower); equality never breaks out. At b+5, upper is failed iff close<=H and lower is failed iff close>=L; equality is failed, otherwise the strict outside state is accepted. Signal is known only after that close; entry is the next scheduled open; exit is the open 30 planned minutes after entry. Missing, isolated, H<=L, no breakout, or any common path/exit crossing the TSE segment is excluded. Each sensitivity independently rebuilds the event causally.",
        "conditions": {
            "A": "failed fade",
            "D": "accepted fade",
            "B": "all breakout fade",
            "A_continue": "same failed event, breakout direction",
            "A_buy": "same failed event fixed long",
            "A_sell": "same failed event fixed short",
        },
        "costs": {
            "base": "1 tick plus JPY30 each side",
            "A2_A3": "2/3 ticks plus JPY30 each side",
            "delay": "entry one scheduled bar later, original absolute exit unchanged",
        },
        "sensitivity_only": {
            "opening_minutes": [20, 40],
            "confirmation_minutes": [3, 10],
            "holding_minutes": [15, 45],
        },
        "ols": "all direction-valid breakout events; y is 0-tick pre-cost 30-minute gross adjusted opposite breakout; Q=failed; nuisance is intercept, breakout excess bps, range width bps, scheduled minutes mS-to-breakout, upper indicator, and calendar-year fixed effects. R053-Q002 nuisance-only Moore-Penrose FWL; Q residual sum of squares must exceed 1e-12 in full and every bootstrap replicate without dropping, redrawing, or result-dependent columns.",
        "bootstrap": {
            "block_trade_dates": 20,
            "repetitions": 10000,
            "seed": SEED,
            "noncircular": True,
            "tail_truncate": True,
            "common_index": True,
            "percentile": "linear",
        },
        "information_gate": "E>=850; breakout>=500; A>=150; D>=150; A buy and sell each>=50; A in 20m and 40m sensitivities each>=100.",
        "decision": "If information gate fails INCONCLUSIVE; otherwise any fixed economic gate failure REJECT; all pass is Development-only INVESTIGATE. No WFA, OOS, Final Holdout, direction reversal or rescue exploration.",
        "inputs": inputs,
        "r004_quarantine": "45 sessions/27,345 bars excluded; selected Development only",
        "source": source,
        "implementation": implementation,
        "implementation_hash": canonical_hash(implementation),
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def planned_days(calendar: ExchangeCalendar) -> list[date]:
    return [
        r.trade_date
        for r in calendar.trading_days()
        if date(2021, 1, 1) <= r.trade_date <= date(2025, 6, 30)
    ]


def selected(condition: str, status: str) -> bool:
    return (
        (condition == "A" and status == "failed")
        or (condition == "D" and status == "accepted")
        or (condition == "B" and status in {"failed", "accepted"})
        or (condition in {"A_continue", "A_buy", "A_sell"} and status == "failed")
    )


def direction(condition: str, event: dict[str, object]) -> str:
    breakout = cast(str, event["breakout_direction"])
    if condition == "A_continue":
        return breakout
    if condition == "A_buy":
        return "long"
    if condition == "A_sell":
        return "short"
    return "short" if breakout == "long" else "long"


def run_condition(
    data: ResearchData,
    engine: BacktestEngine,
    condition: str,
    groups: dict[tuple[date, Session], list[Bar]],
    quarantined: set[tuple[date, Session]],
    days: list[date],
    specification: R054Specification = BASE,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for target in days:
        key = (target, Session.DAY)
        event = r054_event(
            engine.classifier,
            target,
            groups.get(key),
            specification=specification,
            target_quarantined=key in quarantined,
        )
        event.update(condition=condition, base_event_status=event["status"])
        if not selected(condition, cast(str, event["status"])):
            event.update(status="skipped", reason=event.get("reason", "EVENT_FILTER"))
            audit[f"skip_{event['reason']}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["confirmation_ts_jst"]))
        result = engine.run(
            groups[key],
            OpeningRangeCompressionBreakoutStrategy(
                f"r054_{condition}",
                signal,
                direction(condition, event),
                delay,
                specification.holding_minutes,
            ),
            canonical_hash(
                {"condition": condition, "event": event, "delay": delay, "data": data.data_version}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("more than one R054 trade/day")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update(
                status="filled",
                side=trade.side.value,
                entry_ts_jst=trade.entry_ts.isoformat(),
                exit_ts_jst=trade.exit_ts.isoformat(),
                entry_delay_minutes=int((trade.entry_ts - signal).total_seconds() // 60 - 1),
                exit_reason=trade.exit_reason.value,
                gross_pnl_jpy=trade.gross_pnl_jpy,
                slippage_cost_jpy=trade.slippage_cost_jpy,
                fees_jpy=trade.fees_jpy,
                net_pnl_jpy=trade.net_pnl_jpy,
            )
            trades.append(trade)
            audit["trades"] += 1
        else:
            event.update(status="eligible_order_unfilled")
            audit["eligible_order_unfilled"] += 1
        events.append(event)
    return (
        tuple(
            replace(t, trade_id=f"trade-{n:06d}")
            for n, t in enumerate(sorted(trades, key=lambda x: x.entry_ts), 1)
        ),
        events,
        dict(sorted(audit.items())),
    )


def daily(trades: tuple[Trade, ...], days: list[date]) -> dict[str, int]:
    result = dict.fromkeys((d.isoformat() for d in days), 0)
    for trade in trades:
        result[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return result


def write_condition(
    folder: Path,
    name: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    audit: dict[str, int],
    data: ResearchData,
    ticks: int,
    daily_values: dict[str, int],
) -> dict[str, object]:
    folder.mkdir(parents=True, exist_ok=True)
    write_results(
        folder,
        trades,
        (),
        {
            "experiment_id": folder.name,
            "campaign_id": IDENTIFIER,
            "condition": name,
            "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30},
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    metrics = research_metrics(trades, data.bars)
    write_json(folder / "research_metrics.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {
            "trade_date": sorted(daily_values),
            "net_pnl_jpy": [daily_values[k] for k in sorted(daily_values)],
        }
    ).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def percentile(values: list[float], q: float) -> float:
    rows = sorted(values)
    position = (len(rows) - 1) * q
    lo, hi = floor(position), ceil(position)
    return rows[lo] if lo == hi else rows[lo] + (rows[hi] - rows[lo]) * (position - lo)


def regression_rows(
    events: list[dict[str, object]], trades: tuple[Trade, ...]
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], list[str]]:
    by_day = {t.trade_date.isoformat(): t for t in trades}
    output: list[tuple[float, float, list[float], str]] = []
    for e in events:
        if e.get("base_event_status") not in {"failed", "accepted"}:
            continue
        trade = by_day.get(cast(str, e["trade_date"]))
        if trade is None:
            raise ValueError("R054 breakout event not filled for regression")
        sign = -1.0 if e["breakout_direction"] == "long" else 1.0
        gross0 = sign * (trade.exit_reference_price - trade.entry_reference_price) * 100
        output.append(
            (
                1.0 if e["base_event_status"] == "failed" else 0.0,
                gross0,
                [
                    cast(float, e["breakout_excess_bps"]),
                    cast(float, e["opening_range_bps"]),
                    float(cast(int, e["breakout_elapsed_scheduled_minutes"])),
                    1.0 if e["breakout_direction"] == "long" else 0.0,
                ],
                cast(str, e["trade_date"]),
            )
        )
    years = sorted({int(day[:4]) for *_, day in output})
    q = np.array([r[0] for r in output], dtype=np.float64)
    y = np.array([r[1] for r in output], dtype=np.float64)
    nuisance = np.array(
        [[1.0, *r[2], *[1.0 if int(r[3][:4]) == year else 0.0 for year in years]] for r in output],
        dtype=np.float64,
    )
    return q, y, nuisance, [r[3] for r in output]


def bootstrap(
    daily_by: dict[str, dict[str, int]],
    events_b: list[dict[str, object]],
    trades_b: tuple[Trade, ...],
) -> dict[str, object]:
    keys = sorted(daily_by["A"])
    count, block = len(keys), 20
    q, y, nuisance, row_days = regression_rows(events_b, trades_b)
    delta, ss = fwl_delta(q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12)
    write_json(
        OUT / "regression_ledger.json",
        {
            "trade_dates": row_days,
            "q_failed": q.tolist(),
            "y_opposite_breakout_0tick_gross_jpy": y.tolist(),
            "nuisance_columns": [
                "intercept",
                "breakout_excess_bps",
                "opening_range_bps",
                "breakout_elapsed_scheduled_minutes",
                "upper_breakout",
                *[f"year_{year}" for year in sorted({int(day[:4]) for day in row_days})],
            ],
            "nuisance": nuisance.tolist(),
            "fwl": {
                "delta": delta,
                "q_residual_ss": ss,
                "pinv_rcond": 1e-12,
                "residual_ss_tolerance": 1e-12,
            },
        },
    )
    series = {"A_daily_mean_net_jpy": [float(daily_by["A"][k]) for k in keys]}
    for c in ("D", "B", "A_continue", "A_buy", "A_sell"):
        series[f"A_minus_{c}_daily_mean_net_jpy"] = [
            float(daily_by["A"][k] - daily_by[c][k]) for k in keys
        ]
    row_indices_by_day: dict[str, list[int]] = {}
    for i, day in enumerate(row_days):
        row_indices_by_day.setdefault(day, []).append(i)
    samples: dict[str, list[float]] = {k: [] for k in [*series, "delta_fwl_jpy"]}
    common_indices = np.empty((10000, count), dtype=np.uint16)
    rng = Random(SEED)
    for replicate in range(10000):
        selected_indices: list[int] = []
        while len(selected_indices) < count:
            begin = rng.randrange(count - block + 1)
            selected_indices.extend(range(begin, begin + block))
        selected_indices = selected_indices[:count]
        common_indices[replicate] = selected_indices
        for name, values in series.items():
            samples[name].append(fmean(values[index] for index in selected_indices))
        event_indices = [
            i for index in selected_indices for i in row_indices_by_day.get(keys[index], [])
        ]
        try:
            d, _ = fwl_delta(
                q[event_indices],
                y[event_indices],
                nuisance[event_indices],
                pinv_rcond=1e-12,
                residual_ss_tolerance=1e-12,
            )
        except R053QNotIdentifiableError as exc:
            raise ValueError(f"BLOCKED: bootstrap replicate {replicate} {exc}") from exc
        samples["delta_fwl_jpy"].append(d)
    np.save(OUT / "bootstrap_common_day_indices.npy", common_indices)
    result: dict[str, object] = {
        "method": "20 trade_date noncircular moving-block bootstrap with replacement, tail truncate",
        "repetitions": 10000,
        "seed": SEED,
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "common_index": True,
        "index_file": "bootstrap_common_day_indices.npy",
        "full_sample_q_residual_ss": ss,
    }
    for name, values in series.items():
        result[name] = {
            "estimate": fmean(values),
            "ci95_percentile_linear": [
                percentile(samples[name], 0.025),
                percentile(samples[name], 0.975),
            ],
        }
    result["delta_fwl_jpy"] = {
        "estimate": delta,
        "ci95_percentile_linear": [
            percentile(samples["delta_fwl_jpy"], 0.025),
            percentile(samples["delta_fwl_jpy"], 0.975),
        ],
    }
    return result


def main() -> None:
    if OUT.exists():
        raise ValueError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.allow_cross_session_pending_order,
    ) != (1, 30, False):
        raise ValueError("execution contract differs from preregistration")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    files = {
        str(p): digest(ROOT / p)
        for p in (
            Path(__file__).relative_to(ROOT),
            Path("src/n225m_bt/research/r054.py"),
            Path("src/n225m_bt/research/r053.py"),
            Path("src/n225m_bt/strategies/opening_range_compression_breakout.py"),
            Path("tests/test_r054_q001.py"),
        )
    }
    source, inputs = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        input_manifest(data_config.gold_root),
    )
    plan = preregistration(source, inputs, files)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "preregistered_before_price_access",
            "seed": SEED,
            "source": source,
            "plan_hash": canonical_hash(plan),
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    checks = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r053_q001.py",
            "tests/test_r054_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r054.py",
            "src/n225m_bt/strategies/opening_range_compression_breakout.py",
            "tests/test_r054_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r054.py",
            "src/n225m_bt/strategies/opening_range_compression_breakout.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
    }
    validation: dict[str, Any] = {
        name: {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
        for name, cmd in checks.items()
        for done in [
            run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                env=os.environ | {"PYTHONPATH": str(ROOT / "src")},
            )
        ]
    }
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, object], x)["returncode"] == 0 for x in validation.values())
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("BLOCKED: pre-execution validation failed")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, quarantined = quarantine(development)
    scheduled = planned_days(calendar)
    days = [d for d in scheduled if (d, Session.DAY) not in quarantined]
    if len(scheduled) != 1131 or len(days) != 1111:
        raise ValueError("unexpected fixed Development day axis")
    groups = session_groups(view.bars)
    metric_view = ResearchData(
        [bar for bar in view.bars if bar.trade_date in set(days)],
        view.data_version,
        view.quality,
    )
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "quarantine": quarantine_audit,
            "fixed_target_day_count": len(days),
            "fixed_target_trade_dates": [d.isoformat() for d in days],
            "physical_io": "Development normalized Parquet only",
        },
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    for condition in BASE_CONDITIONS:
        trades, events, audit = run_condition(view, engine, condition, groups, quarantined, days)
        values = daily(trades, days)
        metrics = write_condition(
            OUT / condition, condition, trades, events, audit, metric_view, 1, values
        )
        trades_by[condition], events_by[condition], daily_by[condition] = trades, events, values
        results[condition] = {"trade_count": len(trades), "metrics": metrics, "audit": audit}
    variants: list[tuple[str, R054Specification, int, int]] = [
        ("A2", BASE, 2, 0),
        ("A3", BASE, 3, 0),
        ("A_delay", BASE, 1, 1),
        ("A_or20", R054Specification(opening_minutes=20), 1, 0),
        ("A_or40", R054Specification(opening_minutes=40), 1, 0),
        ("A_confirm3", R054Specification(confirmation_minutes=3), 1, 0),
        ("A_confirm10", R054Specification(confirmation_minutes=10), 1, 0),
        ("A_hold15", R054Specification(holding_minutes=15), 1, 0),
        ("A_hold45", R054Specification(holding_minutes=45), 1, 0),
    ]
    for name, spec, ticks, delay in variants:
        config = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, events, audit = run_condition(
            view,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            "A",
            groups,
            quarantined,
            days,
            spec,
            delay,
        )
        metrics = write_condition(
            OUT / name, name, trades, events, audit, metric_view, ticks, daily(trades, days)
        )
        trades_by[name], events_by[name] = trades, events
        results[name] = {"trade_count": len(trades), "metrics": metrics, "audit": audit}

    def same_event(
        left: list[dict[str, object]], right: list[dict[str, object]], fields: tuple[str, ...]
    ) -> bool:
        return all(
            all(a.get(k) == b.get(k) for k in fields) for a, b in zip(left, right, strict=True)
        )

    audit = {
        "A_D_exclusive": all(
            not (
                a.get("base_event_status") == "failed" and d.get("base_event_status") == "accepted"
            )
            for a, d in zip(events_by["A"], events_by["D"], strict=True)
        ),
        "B_union_A_D": all(
            (b.get("base_event_status") in {"failed", "accepted"})
            == (a.get("base_event_status") == "failed" or d.get("base_event_status") == "accepted")
            for a, b, d in zip(events_by["A"], events_by["B"], events_by["D"], strict=True)
        ),
        "same_A_control_event_entry_exit": all(
            same_event(
                events_by["A"],
                events_by[n],
                ("breakout_ts_jst", "confirmation_ts_jst", "entry_ts_jst", "exit_ts_jst"),
            )
            for n in ("A_continue", "A_buy", "A_sell")
        ),
        "A_continue_opposite_side": all(
            a.get("side") != c.get("side")
            for a, c in zip(events_by["A"], events_by["A_continue"], strict=True)
            if a.get("status") == "filled"
        ),
        "variants_independently_rebuilt": all(
            e[0].get("opening_minutes") in {20, 30, 40}
            for n in ("A_or20", "A_or40", "A_confirm3", "A_confirm10", "A_hold15", "A_hold45")
            for e in [events_by[n]]
        ),
        "accounting": all(
            t.net_pnl_jpy == t.gross_pnl_jpy - t.fees_jpy for ts in trades_by.values() for t in ts
        ),
        "all_signal_exits": all(
            t.exit_reason.value == "signal" for ts in trades_by.values() for t in ts
        ),
        "delay_nonextension": all(
            t.exit_ts == next(x for x in trades_by["A"] if x.trade_date == t.trade_date).exit_ts
            for t in trades_by["A_delay"]
        ),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "checks": audit,
            "all_pass": all(audit.values()),
            "note": "Gross is slippage-inclusive; Net=Gross-fees; no slippage double deduction.",
        },
    )
    if not all(audit.values()):
        raise ValueError("BLOCKED: execution/accounting audit failed")
    boot = bootstrap(daily_by, events_by["B"], trades_by["B"])
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "all_candidate_event_ledger.json", events_by["B"])
    write_json(OUT / "development_results.json", results)
    base_metrics = cast(dict[str, object], results["A"])["metrics"]
    overall = cast(dict[str, object], cast(dict[str, object], base_metrics)["overall"])
    segments = cast(dict[str, object], cast(dict[str, object], base_metrics)["segments"])
    conc = cast(dict[str, object], cast(dict[str, object], base_metrics)["concentration"])
    side = cast(dict[str, dict[str, object]], segments["side"])
    yearly = cast(dict[str, dict[str, object]], segments["year"])
    sufficient = (
        len(days) >= 800
        and len(trades_by["B"]) >= 500
        and len(trades_by["A"]) >= 150
        and len(trades_by["D"]) >= 150
        and all(cast(int, side.get(x, {}).get("trade_count", 0)) >= 50 for x in ("long", "short"))
        and len(trades_by["A_or20"]) >= 100
        and len(trades_by["A_or40"]) >= 100
    )

    def ci_lower(name: str) -> bool:
        item = cast(dict[str, object], boot[name])
        return cast(list[float], item["ci95_percentile_linear"])[0] > 0

    def variant_positive(name: str) -> bool:
        item = cast(dict[str, object], results[name])
        metrics = cast(dict[str, object], item["metrics"])
        item_overall = cast(dict[str, object], metrics["overall"])
        return (
            cast(int, item_overall["net_pnl_jpy"]) > 0
            and item_overall["profit_factor"] is not None
            and cast(float, item_overall["profit_factor"]) > 1
        )

    positive_specs = all(
        variant_positive(n)
        for n in (
            "A2",
            "A3",
            "A_delay",
            "A_or20",
            "A_or40",
            "A_confirm3",
            "A_confirm10",
            "A_hold15",
            "A_hold45",
        )
    )
    gates = {
        "A_net_positive": cast(int, overall["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": overall["profit_factor"] is not None
        and cast(float, overall["profit_factor"]) > 1,
        "A_mean_A_minus_D_A_minus_B_delta_ci_lower_positive": all(
            ci_lower(n)
            for n in (
                "A_daily_mean_net_jpy",
                "A_minus_D_daily_mean_net_jpy",
                "A_minus_B_daily_mean_net_jpy",
                "delta_fwl_jpy",
            )
        ),
        "same_event_controls_ci_lower_positive": all(
            ci_lower(f"A_minus_{n}_daily_mean_net_jpy") for n in ("A_continue", "A_buy", "A_sell")
        ),
        "cost_delay_and_all_sensitivities_positive": positive_specs,
        "three_positive_years_2021_2024": sum(
            cast(int, yearly.get(str(y), {}).get("net_pnl_jpy", 0)) > 0 for y in range(2021, 2025)
        )
        >= 3,
        "positive_months_at_least_27": cast(float, conc["positive_month_fraction"]) >= 0.5,
        "top10_excluded_net_positive": cast(int, conc["net_excluding_top10_jpy"]) > 0,
    }
    decision = (
        "INCONCLUSIVE" if not sufficient else "INVESTIGATE" if all(gates.values()) else "REJECT"
    )
    write_json(
        OUT / "decision.json",
        {
            "status": decision,
            "information_sufficient": sufficient,
            "information": {
                "E": len(days),
                "breakout": len(trades_by["B"]),
                "A": len(trades_by["A"]),
                "D": len(trades_by["D"]),
                "A_side": side,
                "A_or20": len(trades_by["A_or20"]),
                "A_or40": len(trades_by["A_or40"]),
            },
            "fixed_gates": gates,
            "bootstrap": boot,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "complete",
            "decision": decision,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
