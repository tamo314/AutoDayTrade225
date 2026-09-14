"""Execute the preregistered Development-only R060-Q001 experiment."""

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
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r053 import R053QNotIdentifiableError, fwl_delta
from n225m_bt.research.r060 import BASE, R060Specification, r060_event
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r060-q001-20260915-prior-tse-range-opening-failed-auction-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
SEED, BLOCK = 20261005, 20
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
CONDITIONS = ("A", "D", "B", "A_continue", "A_buy", "A_sell")


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def input_manifest(gold: Path) -> dict[str, object]:
    files = [
        {"path": str(item.resolve().relative_to(ROOT)), "sha256": digest(item)}
        for item in partition_paths(gold, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "physical_scope": "Normalized Development Parquet only; OOS and Final Holdout are never selected.",
        "files": files,
        "files_hash": canonical_hash(files),
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, bars in grouped.items()
        if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in bars)
    }
    included = [bar for key, bars in grouped.items() if key not in isolated for bar in bars]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in bar.quality_flags for bar in included
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
        key: {"actual": audit[key], "expected": value}
        for key, value in expected.items()
        if audit[key] != value
    }
    audit.update(expected_match=not mismatch, mismatches=mismatch)
    if mismatch:
        raise ValueError(f"BLOCKED: R004 fixed quarantine mismatch: {mismatch}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def frozen_cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = (
        ROOT
        / "results"
        / "research"
        / "r025-q001-20260914-prior-tse-day-range-acceptance-03"
        / "institutional_evidence"
        / "r020_cabinet_office_public_holidays.csv"
    )
    if not source.exists():
        raise FileNotFoundError("frozen TSE holiday evidence required by R060 is unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copied = destination / source.name
    copied.write_bytes(source.read_bytes())
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932"))
    if calendar.source_start > date(2021, 1, 1) or calendar.source_end < date(2025, 6, 30):
        raise ValueError("frozen TSE holiday evidence does not cover Development")
    evidence: dict[str, object] = {
        "source": str(source.relative_to(ROOT)),
        "sha256": digest(copied),
        "rule": "Immediate preceding TSE business date from frozen official holiday evidence; no bar-derived fallback.",
    }
    write_json(destination / "evidence_manifest.json", evidence)
    return calendar, evidence


def preregistration(
    source: dict[str, object],
    inputs: dict[str, object],
    files: dict[str, str],
    evidence: dict[str, object],
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R060-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; repeated exploratory Development use, not independent replication.",
        "duplicate_review": "R001-R059 reviewed before price statistics. R036 uses prior normal-day H/L but first strict close break in 60 minutes, b+5 state, and 60-minute hold. R054 uses current opening range. No registered study has prior TSE H/L, inside open, high/low one-way first-30 breach, c29 +/-1 tick classification, and next-open 30-minute hold together.",
        "hypothesis": "A one-way 1-tick breach of the immediate prior TSE normal-session range during the first 30 planned day bars that has returned inside by one tick at c29 has larger subsequent 30-minute post-cost return opposite the breach than outside-staying events and same-event controls.",
        "fixed_event": "Prior scheduled normal session supplies H/L/p. Target first open must be inside [L,H]. Over N=30 planned bars, high>=H+tick only is upper s=+1 and low<=L-tick only is lower s=-1; both/no breach are retained in E ledger but excluded from directional events. At c(N-1), A is inside by one tick and D remains outside by one tick; neutral band excluded. Signal after c(N-1), entry next planned open, exit entry+30 planned minutes open.",
        "conditions": {
            "A": "returned fade -s",
            "D": "outside fade -s",
            "B": "A union D fade -s",
            "A_continue": "A +s",
            "A_buy": "A fixed long",
            "A_sell": "A fixed short",
        },
        "costs": "Base 1 tick plus JPY30 per side; A2/A3 use 2/3 ticks; A_delay delays entry one planned bar and retains original absolute exit.",
        "only_sensitivities": {
            "breach_ticks": [2],
            "reentry_ticks": [2],
            "observation_minutes": [20, 40],
            "holding_minutes": [15, 45],
        },
        "ols": "B events only: y=-s adjusted 0-tick pre-cost 30m gross; Q=1[A]; nuisance intercept, breach depth ticks, first30 high-low bps, first30 s-adjusted return bps, s-adjusted (o-p)/p gap bps, prior normal-session s-adjusted return bps, prior range bps, upper indicator, calendar-year FE. R053-Q002 nuisance-only Moore-Penrose FWL rcond/tolerance=1e-12; any full/bootstrap Q nonidentification is BLOCKED without redraw, discard, or changed regressors.",
        "bootstrap": {
            "seed": SEED,
            "repetitions": 10000,
            "block_trade_dates": BLOCK,
            "noncircular": True,
            "tail_truncate": True,
            "common_index": True,
            "percentile": "linear",
        },
        "information_gate": "E>=800; B>=160; A/D>=60; A upper/lower>=20; breach2 A>=35; observation20/40 A>=40.",
        "decision": "Information failure is INCONCLUSIVE; after it, any fixed economic-gate failure is REJECT; all pass remains Development-only INVESTIGATE. WFA, OOS, Final Holdout, direction changes and rescue exploration prohibited.",
        "inputs": inputs,
        "institutional_evidence": evidence,
        "implementation": files,
        "implementation_hash": canonical_hash(files),
        "source": source,
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def planned_days(calendar: ExchangeCalendar) -> list[date]:
    return [
        item.trade_date
        for item in calendar.trading_days()
        if date(2021, 1, 1) <= item.trade_date <= date(2025, 6, 30)
    ]


def selected(condition: str, state: str) -> bool:
    return (
        (condition == "A" and state == "A")
        or (condition == "D" and state == "D")
        or (condition == "B" and state in {"A", "D"})
        or (condition in {"A_continue", "A_buy", "A_sell"} and state == "A")
    )


def side(condition: str, event: dict[str, object]) -> str:
    if condition == "A_buy":
        return "long"
    if condition == "A_sell":
        return "short"
    s = cast(int, event["s"])
    if condition == "A_continue":
        return "long" if s == 1 else "short"
    return "short" if s == 1 else "long"


def run_condition(
    data: ResearchData,
    cash: TSECashMarketCalendar,
    engine: BacktestEngine,
    condition: str,
    groups: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    days: list[date],
    specification: R060Specification = BASE,
    delay: int = 0,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    events: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for target in days:
        key = (target, Session.DAY)
        prior = __import__(
            "n225m_bt.research.r025", fromlist=["previous_tse_open_date"]
        ).previous_tse_open_date(cash, target)
        prior_key = (prior, Session.DAY) if prior is not None else None
        event = r060_event(
            engine.classifier,
            cash,
            target,
            groups.get(key),
            groups.get(prior_key) if prior_key else None,
            specification=specification,
            tick_size=engine.spec.tick_size,
            target_quarantined=key in isolated,
            prior_quarantined=prior_key in isolated if prior_key else False,
        )
        state = cast(str, event["status"])
        event.update(condition=condition, base_event_status=state)
        if not selected(condition, state):
            event.update(status="skipped", reason=event.get("reason", "EVENT_FILTER"))
            audit[f"skip_{event['reason']}"] += 1
            events.append(event)
            continue
        signal = datetime.fromisoformat(cast(str, event["entry_signal_bar_start_jst"]))
        result = engine.run(
            groups[key],
            OpeningRangeCompressionBreakoutStrategy(
                f"r060_{condition}",
                signal,
                side(condition, event),
                delay,
                specification.holding_minutes,
            ),
            canonical_hash(
                {"condition": condition, "event": event, "delay": delay, "data": data.data_version}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R060 maximum one position/trade violated")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update(
                status="filled",
                side=trade.side.value,
                entry_ts_jst=trade.entry_ts.isoformat(),
                exit_ts_jst=trade.exit_ts.isoformat(),
                exit_reason=trade.exit_reason.value,
                entry_delay_minutes=int((trade.entry_ts - signal).total_seconds() // 60 - 1),
                gross_pnl_jpy=trade.gross_pnl_jpy,
                slippage_cost_jpy=trade.slippage_cost_jpy,
                fees_jpy=trade.fees_jpy,
                net_pnl_jpy=trade.net_pnl_jpy,
            )
            trades.append(trade)
            audit["trades"] += 1
        else:
            event["status"] = "eligible_order_unfilled"
            audit["eligible_order_unfilled"] += 1
        events.append(event)
    return (
        tuple(
            replace(item, trade_id=f"trade-{number:06d}")
            for number, item in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
        ),
        events,
        dict(sorted(audit.items())),
    )


def daily(trades: tuple[Trade, ...], days: list[date]) -> dict[str, int]:
    output = dict.fromkeys((day.isoformat() for day in days), 0)
    for trade in trades:
        output[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return output


def write_condition(
    folder: Path,
    name: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    audit: dict[str, int],
    metric_data: ResearchData,
    ticks: int,
    values: dict[str, int],
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
    metrics = research_metrics(trades, metric_data.bars)
    write_json(folder / "research_metrics.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {"trade_date": sorted(values), "net_pnl_jpy": [values[k] for k in sorted(values)]}
    ).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    place = (len(ordered) - 1) * q
    low, high = floor(place), ceil(place)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (place - low)
    )


def regression_rows(
    events: list[dict[str, object]], trades: tuple[Trade, ...], multiplier: int
) -> tuple[
    np.ndarray[Any, np.dtype[np.float64]],
    np.ndarray[Any, np.dtype[np.float64]],
    np.ndarray[Any, np.dtype[np.float64]],
    list[str],
]:
    by_day = {trade.trade_date.isoformat(): trade for trade in trades}
    rows: list[tuple[float, float, list[float], str]] = []
    for event in events:
        if event.get("base_event_status") not in {"A", "D"}:
            continue
        trade = by_day.get(cast(str, event["trade_date"]))
        if trade is None:
            raise ValueError("BLOCKED: B event was not filled for fixed regression")
        s = cast(int, event["s"])
        rows.append(
            (
                1.0 if event["base_event_status"] == "A" else 0.0,
                float(-s * (trade.exit_reference_price - trade.entry_reference_price) * multiplier),
                [
                    float(cast(int, event["breach_depth_ticks"])),
                    cast(float, event["first_observation_range_bps"]),
                    cast(float, event["first30_s_adjusted_return_bps"]),
                    cast(float, event["s_adjusted_gap_bps"]),
                    cast(float, event["prior_tse_s_adjusted_return_bps"]),
                    cast(float, event["prior_range_bps"]),
                    1.0 if s == 1 else 0.0,
                ],
                cast(str, event["trade_date"]),
            )
        )
    years = sorted({int(day[:4]) for *_, day in rows})
    q = np.array([row[0] for row in rows], dtype=np.float64)
    y = np.array([row[1] for row in rows], dtype=np.float64)
    nuisance = np.array(
        [
            [1.0, *row[2], *[1.0 if int(row[3][:4]) == year else 0.0 for year in years]]
            for row in rows
        ],
        dtype=np.float64,
    )
    return q, y, nuisance, [row[3] for row in rows]


def bootstrap(
    daily_by: dict[str, dict[str, int]],
    b_events: list[dict[str, object]],
    b_trades: tuple[Trade, ...],
    multiplier: int,
) -> dict[str, object]:
    keys = sorted(daily_by["A"])
    q, y, nuisance, row_days = regression_rows(b_events, b_trades, multiplier)
    delta, ss = fwl_delta(q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12)
    write_json(
        OUT / "regression_ledger.json",
        {
            "trade_dates": row_days,
            "q_returned": q.tolist(),
            "y_opposite_breakout_0tick_gross_jpy": y.tolist(),
            "nuisance_columns": [
                "intercept",
                "breach_depth_ticks",
                "first30_high_low_bps",
                "first30_s_adjusted_return_bps",
                "s_adjusted_gap_bps",
                "prior_tse_s_adjusted_return_bps",
                "prior_range_bps",
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
    series = {"A_daily_mean_net_jpy": [float(daily_by["A"][key]) for key in keys]}
    for condition in ("D", "B", "A_continue", "A_buy", "A_sell"):
        series[f"A_minus_{condition}_daily_mean_net_jpy"] = [
            float(daily_by["A"][key] - daily_by[condition][key]) for key in keys
        ]
    rows_by_day: dict[str, list[int]] = {}
    for index, day in enumerate(row_days):
        rows_by_day.setdefault(day, []).append(index)
    samples: dict[str, list[float]] = {name: [] for name in (*series, "delta_fwl_jpy")}
    indices = np.empty((10000, len(keys)), dtype=np.uint16)
    rng = Random(SEED)
    for replicate in range(10000):
        picked: list[int] = []
        while len(picked) < len(keys):
            begin = rng.randrange(len(keys) - BLOCK + 1)
            picked.extend(range(begin, begin + BLOCK))
        picked = picked[: len(keys)]
        indices[replicate] = picked
        for name, values in series.items():
            samples[name].append(fmean(values[index] for index in picked))
        event_indices = [
            row for day_index in picked for row in rows_by_day.get(keys[day_index], [])
        ]
        try:
            samples["delta_fwl_jpy"].append(
                fwl_delta(
                    q[event_indices],
                    y[event_indices],
                    nuisance[event_indices],
                    pinv_rcond=1e-12,
                    residual_ss_tolerance=1e-12,
                )[0]
            )
        except R053QNotIdentifiableError as error:
            write_json(
                OUT / "technical_fwl_gate.json",
                {"status": "BLOCKED", "replicate": replicate, "error": str(error)},
            )
            raise ValueError(
                f"BLOCKED: FWL identification failed in bootstrap replicate {replicate}"
            ) from error
    np.save(OUT / "bootstrap_common_day_indices.npy", indices)
    output: dict[str, object] = {
        "method": "20 trade_date noncircular moving-block bootstrap with replacement, tail truncate",
        "seed": SEED,
        "repetitions": 10000,
        "block_length_trade_dates": BLOCK,
        "target_trade_dates": len(keys),
        "common_index": True,
        "index_file": "bootstrap_common_day_indices.npy",
        "full_sample_q_residual_ss": ss,
    }
    for name, values in series.items():
        output[name] = {
            "estimate": fmean(values),
            "ci95_percentile_linear": [
                percentile(samples[name], 0.025),
                percentile(samples[name], 0.975),
            ],
        }
    output["delta_fwl_jpy"] = {
        "estimate": delta,
        "ci95_percentile_linear": [
            percentile(samples["delta_fwl_jpy"], 0.025),
            percentile(samples["delta_fwl_jpy"], 0.975),
        ],
    }
    return output


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable R060 output already exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.allow_cross_session_pending_order,
    ) != (1, 30, False):
        raise ValueError("BLOCKED: execution/cost contract differs from R060 preregistration")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    files = {
        str(path): digest(ROOT / path)
        for path in (
            Path(__file__).relative_to(ROOT),
            Path("src/n225m_bt/research/r060.py"),
            Path("src/n225m_bt/research/r053.py"),
            Path("src/n225m_bt/research/r020.py"),
            Path("src/n225m_bt/research/r022.py"),
            Path("src/n225m_bt/research/r025.py"),
            Path("src/n225m_bt/strategies/opening_range_compression_breakout.py"),
            Path("tests/test_r060_q001.py"),
        )
    }
    source, inputs, cash = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        input_manifest(data_config.gold_root),
        frozen_cash_calendar(),
    )
    cash_calendar, evidence = cash
    plan = preregistration(source, inputs, files, evidence)
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "effective_config.json",
        {
            "instrument": instrument.model_dump(mode="json"),
            "backtest": baseline.model_dump(mode="json"),
        },
    )
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
            "tests/test_r060_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r060.py",
            "tests/test_r060_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r060.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
    }
    validation: dict[str, Any] = {
        name: {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
        for name, command in checks.items()
        for done in [
            run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                env=os.environ | {"PYTHONPATH": str(ROOT / "src")},
            )
        ]
    }
    validation["coverage"] = (
        "trade/calendar mapping, prior normal H/L/p, inside open, tick breach/reentry/neutral/both paths, common E, signal/fill/absolute exit/delay, costs, prefix, sensitivity reconstruction, and holdout lock."
    )
    validation["status"] = (
        "PASS"
        if all(
            cast(int, item["returncode"]) == 0
            for item in validation.values()
            if isinstance(item, dict) and "returncode" in item
        )
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("BLOCKED: R060 pre-execution validation failed")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    scheduled = planned_days(calendar)
    days = [day for day in scheduled if (day, Session.DAY) not in isolated]
    if len(scheduled) != 1131 or len(days) != 1111:
        raise ValueError("BLOCKED: fixed 1,111 trade_date PnL axis mismatch")
    groups = session_groups(view.bars)
    metric_view = ResearchData(
        [bar for bar in view.bars if bar.trade_date in set(days)], view.data_version, view.quality
    )
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "quarantine": quarantine_audit,
            "fixed_target_day_count": len(days),
            "fixed_target_trade_dates": [day.isoformat() for day in days],
            "physical_io": "Development normalized Parquet only; OOS and Final Holdout unselected.",
        },
    )
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    for condition in CONDITIONS:
        trades, events, audit = run_condition(
            view, cash_calendar, engine, condition, groups, isolated, days
        )
        values = daily(trades, days)
        metrics = write_condition(
            OUT / condition, condition, trades, events, audit, metric_view, 1, values
        )
        trades_by[condition], events_by[condition], daily_by[condition], results[condition] = (
            trades,
            events,
            values,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )
    variants = [
        ("A2", BASE, 2, 0),
        ("A3", BASE, 3, 0),
        ("A_delay", BASE, 1, 1),
        ("A_breach2", R060Specification(breach_ticks=2), 1, 0),
        ("A_reentry2", R060Specification(reentry_ticks=2), 1, 0),
        ("A_obs20", R060Specification(observation_minutes=20), 1, 0),
        ("A_obs40", R060Specification(observation_minutes=40), 1, 0),
        ("A_hold15", R060Specification(holding_minutes=15), 1, 0),
        ("A_hold45", R060Specification(holding_minutes=45), 1, 0),
    ]
    for name, spec, ticks, delay in variants:
        config = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, events, audit = run_condition(
            view,
            cash_calendar,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            "A",
            groups,
            isolated,
            days,
            spec,
            delay,
        )
        values = daily(trades, days)
        metrics = write_condition(
            OUT / name, name, trades, events, audit, metric_view, ticks, values
        )
        trades_by[name], events_by[name], daily_by[name], results[name] = (
            trades,
            events,
            values,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )

    def same(left: dict[str, object], right: dict[str, object], fields: tuple[str, ...]) -> bool:
        return all(left.get(field) == right.get(field) for field in fields)

    audits = {
        "A_D_exclusive": all(
            not (a.get("base_event_status") == "A" and d.get("base_event_status") == "D")
            for a, d in zip(events_by["A"], events_by["D"], strict=True)
        ),
        "B_union_A_D": all(
            (b.get("base_event_status") in {"A", "D"})
            == (a.get("base_event_status") == "A" or d.get("base_event_status") == "D")
            for a, b, d in zip(events_by["A"], events_by["B"], events_by["D"], strict=True)
        ),
        "same_A_controls_event_entry_exit": all(
            same(
                events_by["A"][index],
                events_by[name][index],
                ("base_event_status", "entry_signal_bar_start_jst", "entry_ts_jst", "exit_ts_jst"),
            )
            for name in ("A_continue", "A_buy", "A_sell")
            for index in range(len(days))
        ),
        "A_continue_opposite_side": all(
            a.get("side") != c.get("side")
            for a, c in zip(events_by["A"], events_by["A_continue"], strict=True)
            if a.get("status") == "filled"
        ),
        "sensitivity_independently_rebuilt": all(
            events_by[name][0].get("observation_minutes") in {20, 30, 40}
            for name in ("A_breach2", "A_reentry2", "A_obs20", "A_obs40", "A_hold15", "A_hold45")
        ),
        "fixed_signal_exit": all(
            trade.exit_reason.value == "signal" for items in trades_by.values() for trade in items
        ),
        "delay_nonextension": all(
            trade.exit_ts
            == next(item for item in trades_by["A"] if item.trade_date == trade.trade_date).exit_ts
            for trade in trades_by["A_delay"]
        ),
        "accounting_no_double_slippage": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for items in trades_by.values()
            for trade in items
        ),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "checks": audits,
            "all_pass": all(audits.values()),
            "note": "Gross is slippage-inclusive; Net=Gross-fees.",
        },
    )
    if not all(audits.values()):
        raise ValueError("BLOCKED: R060 execution/accounting audit failed")
    boot = bootstrap(
        daily_by, events_by["B"], trades_by["B"], instrument.instrument.contract_multiplier
    )
    write_json(OUT / "bootstrap.json", boot)
    write_json(OUT / "all_candidate_event_ledger.json", events_by["B"])
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {"trade_dates": sorted(daily_by["A"]), "series": daily_by},
    )
    write_json(OUT / "development_results.json", results)
    metrics = cast(dict[str, object], cast(dict[str, object], results["A"])["metrics"])
    overall = cast(dict[str, object], metrics["overall"])
    segments = cast(dict[str, object], metrics["segments"])
    concentration = cast(dict[str, object], metrics["concentration"])
    sides, years = (
        cast(dict[str, dict[str, object]], segments["side"]),
        cast(dict[str, dict[str, object]], segments["year"]),
    )
    common_e = sum(bool(event.get("common_path_last_bar_start_jst")) for event in events_by["B"])

    def positive_variant(name: str) -> bool:
        item = cast(dict[str, object], cast(dict[str, object], results[name])["metrics"])["overall"]
        row = cast(dict[str, object], item)
        return (
            cast(int, row["net_pnl_jpy"]) > 0
            and row["profit_factor"] is not None
            and cast(float, row["profit_factor"]) > 1
        )

    def ci_lower(name: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
        )

    sufficient = (
        common_e >= 800
        and len(trades_by["B"]) >= 160
        and len(trades_by["A"]) >= 60
        and len(trades_by["D"]) >= 60
        and all(
            cast(int, sides.get(value, {}).get("trade_count", 0)) >= 20
            for value in ("long", "short")
        )
        and len(trades_by["A_breach2"]) >= 35
        and len(trades_by["A_obs20"]) >= 40
        and len(trades_by["A_obs40"]) >= 40
    )
    gates = {
        "A_net_positive": cast(int, overall["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": overall["profit_factor"] is not None
        and cast(float, overall["profit_factor"]) > 1,
        "A_mean_A_minus_D_A_minus_B_delta_ci_lower_positive": all(
            ci_lower(name)
            for name in (
                "A_daily_mean_net_jpy",
                "A_minus_D_daily_mean_net_jpy",
                "A_minus_B_daily_mean_net_jpy",
                "delta_fwl_jpy",
            )
        ),
        "same_event_controls_ci_lower_positive": all(
            ci_lower(f"A_minus_{name}_daily_mean_net_jpy")
            for name in ("A_continue", "A_buy", "A_sell")
        ),
        "all_cost_delay_and_sensitivity_net_pf_positive": all(
            positive_variant(name)
            for name in (
                "A2",
                "A3",
                "A_delay",
                "A_breach2",
                "A_reentry2",
                "A_obs20",
                "A_obs40",
                "A_hold15",
                "A_hold45",
            )
        ),
        "three_positive_years_2021_2024": sum(
            cast(int, years.get(str(year), {}).get("net_pnl_jpy", 0)) > 0
            for year in range(2021, 2025)
        )
        >= 3,
        "positive_months_at_least_27": cast(float, concentration["positive_month_fraction"]) >= 0.5,
        "top10_excluded_net_positive": cast(int, concentration["net_excluding_top10_jpy"]) > 0,
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
                "E": common_e,
                "B": len(trades_by["B"]),
                "A": len(trades_by["A"]),
                "D": len(trades_by["D"]),
                "A_side": sides,
                "A_breach2": len(trades_by["A_breach2"]),
                "A_obs20": len(trades_by["A_obs20"]),
                "A_obs40": len(trades_by["A_obs40"]),
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
