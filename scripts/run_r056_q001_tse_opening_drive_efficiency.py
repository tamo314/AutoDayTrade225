"""Execute the preregistered Development-only R056-Q001 experiment."""

# mypy: disable-error-code="arg-type, redundant-cast, type-arg"

from __future__ import annotations

import os
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, floor, log
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
from n225m_bt.research.r056 import (
    LOOKBACK,
    SCHEDULE_ID,
    r056_event,
)
from n225m_bt.research.runner import snapshot_source, write_json
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r056-q001-20260915-tse-opening-drive-efficiency-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
R045 = ROOT / "results" / "research" / "r045-q001-20260914-tse-lunch-placebo-reversal-03"
SEED, BLOCK = 20261003, 20
PARENT_HASH = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
BASE_CONDITIONS = ("A", "D", "B", "A_fade", "A_buy", "A_sell")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold_root, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "scope": "Selected normalized Development Parquet only; raw, OOS and Final Holdout are prohibited.",
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
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in groups.items() if key not in isolated for bar in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_bars": len(included),
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
        raise ValueError(f"R004 fixed isolation mismatch: {mismatch}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R045 / "institutional_evidence" / "cabinet_office_public_holidays.csv"
    if not source.exists():
        raise FileNotFoundError("R056 frozen TSE holiday evidence unavailable")
    destination = OUT / "institutional_evidence"
    destination.mkdir()
    copied = destination / source.name
    copied.write_bytes(source.read_bytes())
    return TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932")), {
        "schedule_id": SCHEDULE_ID,
        "holiday_csv_sha256": digest(copied),
    }


def preregistration(
    source: dict[str, object], inputs: dict[str, object], implementation: dict[str, str]
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R056-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "prior_immutable_attempts": "…-01 stopped before price statistics, event construction, orders or PnL because it incorrectly required the TSE-cash-only candidate calendar to equal the fixed 1,111 OSE trade_date PnL axis. …-02 correctly retained non-TSE OSE axis days as zero-PnL/TSE-closed rows, and completed event/order/cost gates, but stopped before FWL estimation because its event execution ledger omitted the already-known entry/exit reference prices required for 0-tick gross y. …-03 changes only that ledger serialization; the hypothesis, price definition, thresholds, execution, costs, seed and decision rules are unchanged.",
        "duplicate_review": {
            "R001_R055": "R046 shares a 30-minute path-efficiency concept but uses OSE day 08:45, an efficiency-only 60-day classification, 60-minute holding and a normal-morning placebo. R054 uses a 30-minute opening range and failed breakouts. No R001--R055 registration combines the TSE 09:00 30-minute displacement and efficiency thresholds, 120 scheduled TSE-day history, next-open entry, 30-minute holding and specified FWL regression.",
            "conclusion": "No identical existing observation window, direction efficiency, thresholds, entry and holding specification; execute this preregistered experiment only.",
        },
        "hypothesis": "Large, directionally efficient TSE opening drives continue for the following 30 planned minutes in their drive direction, and outperform equally large low-efficiency drives and same-event fade/fixed-side controls.",
        "event_rule": "For W in {20,30,40}, o is the first planned TSE open, c0..c(W-1) are planned closes, x=abs(c_last-o)/o, v=abs(c0-o)+sum(abs(c_i-c_(i-1))), e=abs(c_last-o)/v, s=sign(c_last-o). v=0 or s=0 stays in common E where otherwise eligible but is excluded from directional conditions. Exact prior 120 scheduled TSE cash days only (no target, no older replenishment); with >=100 valid values nearest-rank qx70/qx75/qx80 and qe40/qe50/qe60 are computed. Equality belongs above. Signal after last close, entry at next planned open, fixed exit at entry+15/30/45 planned minutes. Common E requires first 40 planned bars, all rolling thresholds, and all entry/exit paths in the TSE segment. Each window sensitivity reconstructs the causal event from its own W.",
        "conditions": {
            "A": "x>=qx75 and e>=qe50, side=s",
            "D": "x>=qx75 and e<qe50, side=s",
            "B": "x>=qx75, side=s",
            "A_fade": "A same event, -s",
            "A_buy": "A same event, long",
            "A_sell": "A same event, short",
        },
        "costs": {
            "base": "one tick plus JPY30 per side",
            "A2_A3": "two/three ticks plus JPY30 per side",
            "A_delay": "entry one planned bar later; original absolute exit is not extended",
        },
        "sensitivity_only": {
            "x_threshold": ["q70", "q80"],
            "e_threshold": ["q40", "q60"],
            "observation_minutes": [20, 40],
            "holding_minutes": [15, 45],
        },
        "ols": "All direction-valid B events: y is s-adjusted 0-tick/pre-cost 30-minute gross; Q=1[e>=qe50]; nuisance Z is intercept, ln(x/qx75), first-30m high-low range bps, first-5m s-adjusted return bps, upward indicator and calendar-year fixed effects. R053-Q002 nuisance-only Moore-Penrose FWL uses pinv_rcond=1e-12 and Q residual SS tolerance=1e-12. Nonidentification in full sample or any frozen replicate is BLOCKED without dropping/redrawing/changing regressors.",
        "bootstrap": {
            "block_trade_dates": BLOCK,
            "repetitions": 10000,
            "seed": SEED,
            "noncircular": True,
            "tail_truncate": True,
            "common_index": True,
            "percentile": "linear",
        },
        "information_gate": "E>=850; B>=200; A>=90; D>=90; A buy and sell each>=25; qx80 and qe60 A sensitivities each>=35.",
        "decision": "Information failure is INCONCLUSIVE. Otherwise any required economics failure is REJECT; all pass remains Development-only INVESTIGATE. No WFA, OOS, Final Holdout, direction reversal or rescue search.",
        "inputs": inputs,
        "source": source,
        "implementation": implementation,
        "implementation_hash": canonical_hash(implementation),
        "r004_quarantine": "fixed 45 sessions / 27,345 bars; selected Development only",
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def scheduled_days(calendar: TSECashMarketCalendar) -> list[date]:
    begin, end = date(2021, 1, 1), date(2025, 6, 30)
    days: list[date] = []
    while begin <= end:
        if calendar.is_open(begin):
            days.append(begin)
        begin = begin.fromordinal(begin.toordinal() + 1)
    return days


def event_series(
    days: list[date],
    raw_groups: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    calendar: TSECashMarketCalendar,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index, target in enumerate(days):
        history = [
            (day, raw_groups.get((day, Session.DAY)), (day, Session.DAY) in isolated)
            for day in days[max(0, index - LOOKBACK) : index]
        ]
        events.append(
            r056_event(
                target,
                raw_groups.get((target, Session.DAY)),
                history,
                calendar,
                quarantined=(target, Session.DAY) in isolated,
            )
        )
    return events


def select(
    event: dict[str, object], condition: str, *, window: int = 30, xq: int = 75, eq: int = 50
) -> bool:
    if event.get("status") != "E" or not event.get("direction_eligible"):
        return False
    observation = cast(
        dict[str, object], cast(dict[int, dict[str, object]], event["opening_observations"])[window]
    )
    thresholds = cast(
        dict[str, float], cast(dict[str, dict[str, float]], event["thresholds"])[str(window)]
    )
    high_x, high_e = (
        float(observation["x"]) >= thresholds[f"qx{xq}"],
        float(observation["e"]) >= thresholds[f"qe{eq}"],
    )
    if condition == "B":
        return high_x
    if condition == "D":
        return high_x and not high_e
    return high_x and high_e


def side(event: dict[str, object], condition: str, window: int) -> str:
    sign = int(
        cast(
            int,
            cast(
                dict[str, object],
                cast(dict[int, dict[str, object]], event["opening_observations"])[window],
            )["sign"],
        )
    )
    if condition == "A_fade":
        sign = -sign
    if condition == "A_buy":
        sign = 1
    if condition == "A_sell":
        sign = -1
    return "long" if sign > 0 else "short"


def run_condition(
    events: list[dict[str, object]],
    groups: dict[tuple[date, Session], list[Bar]],
    engine: BacktestEngine,
    condition: str,
    *,
    ticks: int = 1,
    delay: int = 0,
    window: int = 30,
    xq: int = 75,
    eq: int = 50,
    holding: int = 30,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records: list[dict[str, object]] = []
    trades: list[Trade] = []
    audit: Counter[str] = Counter()
    for original in events:
        row, target = dict(original), date.fromisoformat(cast(str, original["trade_date"]))
        allowed = select(row, condition, window=window, xq=xq, eq=eq)
        row.update(
            condition=condition,
            condition_eligible=allowed,
            observation_minutes=window,
            x_quantile=xq,
            e_quantile=eq,
            requested_slippage_ticks=ticks,
            requested_delay_minutes=delay,
            requested_holding_minutes=holding,
        )
        if not allowed:
            row.update(status="skipped", condition_reason="DIRECTION_ZERO_OR_THRESHOLD_NOT_MET")
            records.append(row)
            continue
        times = cast(
            dict[str, str], cast(dict[str, dict[str, str]], row["planned_times"])[str(window)]
        )
        entry, exit_time = (
            datetime.fromisoformat(times["entry"]),
            datetime.fromisoformat(times[f"exit{holding}"]),
        )
        direction = side(row, condition, window)
        row.update(
            side=direction,
            planned_entry_jst=entry.isoformat(),
            planned_exit_jst=exit_time.isoformat(),
        )
        result = engine.run(
            groups.get((target, Session.DAY), []),
            R049FixedTimeStrategy(f"r056_{condition}", entry, exit_time, direction, delay),
            canonical_hash(
                {
                    "condition": condition,
                    "ticks": ticks,
                    "delay": delay,
                    "window": window,
                    "xq": xq,
                    "eq": eq,
                    "holding": holding,
                }
            ),
        )
        audit["canceled_orders"] += result.canceled_orders
        if len(result.trades) > 1:
            raise AssertionError("R056 maximum one position violated")
        if not result.trades:
            row.update(status="eligible_order_unfilled", condition_reason="ENGINE_NO_FILL_OR_EXIT")
            audit["eligible_order_unfilled"] += 1
            records.append(row)
            continue
        trade = result.trades[0]
        row.update(
            status="filled",
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            entry_signal_ts_jst=trade.entry_signal_ts.isoformat(),
            exit_reason=trade.exit_reason.value,
            gross_pnl_jpy=trade.gross_pnl_jpy,
            fees_jpy=trade.fees_jpy,
            slippage_cost_jpy=trade.slippage_cost_jpy,
            net_pnl_jpy=trade.net_pnl_jpy,
            entry_reference_price=trade.entry_reference_price,
            exit_reference_price=trade.exit_reference_price,
            entry_delay_minutes=round((trade.entry_ts - entry).total_seconds() / 60),
            exit_delay_minutes=round((trade.exit_ts - exit_time).total_seconds() / 60),
        )
        trades.append(trade)
        records.append(row)
    return (
        tuple(
            replace(trade, trade_id=f"trade-{index:06d}")
            for index, trade in enumerate(sorted(trades, key=lambda item: item.entry_ts), 1)
        ),
        records,
        dict(audit),
    )


def daily(trades: tuple[Trade, ...], axis: list[date]) -> dict[str, int]:
    values = dict.fromkeys((day.isoformat() for day in axis), 0)
    for trade in trades:
        values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def write_condition(
    name: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    audit: dict[str, int],
    view: ResearchData,
    ticks: int,
    values: dict[str, int],
) -> dict[str, object]:
    folder = OUT / name
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
    metrics = research_metrics(trades, view.bars)
    write_json(folder / "research_metrics.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {"trade_date": sorted(values), "net_pnl_jpy": [values[key] for key in sorted(values)]}
    ).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    lower, upper = floor(position), ceil(position)
    return (
        ordered[lower]
        if lower == upper
        else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    )


def regression(
    rows: list[dict[str, object]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    output: list[tuple[float, float, list[float], str]] = []
    for row in rows:
        if row.get("status") != "filled":
            continue
        observation = cast(
            dict[str, object], cast(dict[int, dict[str, object]], row["opening_observations"])[30]
        )
        threshold = cast(
            dict[str, float], cast(dict[str, dict[str, float]], row["thresholds"])["30"]
        )
        x, qx = float(observation["x"]), float(threshold["qx75"])
        if x <= 0 or qx <= 0:
            raise ValueError("R056 preregistered ln(x/qx75) is undefined")
        sign = int(cast(int, observation["sign"]))
        output.append(
            (
                float(float(observation["e"]) >= float(threshold["qe50"])),
                sign
                * (float(row["exit_reference_price"]) - float(row["entry_reference_price"]))
                * 100,
                [
                    log(x / qx),
                    float(row["high_low_range_bps"]),
                    float(row["first5_adjusted_return_bps"]),
                    float(row["upward_indicator"]),
                ],
                cast(str, row["trade_date"]),
            )
        )
    years = sorted({int(day[:4]) for *_, day in output})
    q = np.asarray([value[0] for value in output], dtype=float)
    y = np.asarray([value[1] for value in output], dtype=float)
    nuisance = np.asarray(
        [
            [1.0, *value[2], *[1.0 if int(value[3][:4]) == year else 0.0 for year in years[1:]]]
            for value in output
        ],
        dtype=float,
    )
    return q, y, nuisance, [value[3] for value in output]


def bootstrap(
    daily_by: dict[str, dict[str, int]], b_records: list[dict[str, object]]
) -> dict[str, object]:
    axis = sorted(daily_by["A"])
    count = len(axis)
    q, y, nuisance, regression_days = regression(b_records)
    delta, residual_ss = fwl_delta(q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12)
    row_indices: dict[str, list[int]] = {}
    for index, day in enumerate(regression_days):
        row_indices.setdefault(day, []).append(index)
    series = {"A_mean_net_jpy": [float(daily_by["A"][day]) for day in axis]}
    for name in ("D", "B", "A_fade", "A_buy", "A_sell"):
        series[f"A_minus_{name}_mean_jpy"] = [
            float(daily_by["A"][day] - daily_by[name][day]) for day in axis
        ]
    samples: dict[str, list[float]] = {key: [] for key in [*series, "delta_fwl_jpy"]}
    indices_all = np.empty((10000, count), dtype=np.uint16)
    rng = Random(SEED)
    for replicate in range(10000):
        indices: list[int] = []
        while len(indices) < count:
            start = rng.randrange(count - BLOCK + 1)
            indices.extend(range(start, start + BLOCK))
        indices = indices[:count]
        indices_all[replicate] = indices
        for name, values in series.items():
            samples[name].append(fmean(values[index] for index in indices))
        event_indices = [
            event_index for index in indices for event_index in row_indices.get(axis[index], [])
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
            write_json(
                OUT / "technical_fwl_gate.json",
                {
                    "status": "BLOCKED",
                    "stage": "bootstrap_replicate",
                    "replicate_number_zero_based": replicate,
                    "error": str(exc),
                    "sampled_axis_indices_zero_based": indices,
                },
            )
            raise ValueError(
                f"BLOCKED: FWL identification failed in bootstrap replicate {replicate}"
            ) from exc
        samples["delta_fwl_jpy"].append(d)
    np.save(OUT / "bootstrap_common_day_indices.npy", indices_all)
    write_json(
        OUT / "regression_ledger.json",
        {
            "trade_dates": regression_days,
            "Q_high_efficiency": q.tolist(),
            "y_s_adjusted_0tick_gross_jpy": y.tolist(),
            "nuisance_columns": [
                "intercept",
                "ln_x_over_qx75",
                "opening_high_low_range_bps",
                "first5_s_adjusted_return_bps",
                "upward_indicator",
                *[
                    f"calendar_year_{year}"
                    for year in sorted({int(day[:4]) for day in regression_days})[1:]
                ],
            ],
            "nuisance": nuisance.tolist(),
            "fwl": {
                "delta": delta,
                "q_residual_ss": residual_ss,
                "pinv_rcond": 1e-12,
                "residual_ss_tolerance": 1e-12,
            },
        },
    )
    write_json(
        OUT / "technical_fwl_gate.json",
        {
            "status": "PASS",
            "stage": "all_10000_bootstrap_replicates",
            "observed_Q_residual_sum_of_squares": residual_ss,
            "pinv_rcond": 1e-12,
            "residual_ss_tolerance": 1e-12,
            "seed": SEED,
            "repetitions": 10000,
            "block_length_trade_dates": BLOCK,
        },
    )
    result: dict[str, object] = {
        "method": "20 trade_date noncircular moving-block bootstrap; common indices; tail truncation; linear percentile; R053-Q002 nuisance-only FWL",
        "repetitions": 10000,
        "seed": SEED,
        "block_length_trade_dates": BLOCK,
        "target_trade_dates": count,
        "common_index": True,
        "index_file": "bootstrap_common_day_indices.npy",
    }
    for name, values in samples.items():
        point = delta if name == "delta_fwl_jpy" else fmean(series[name])
        result[name] = {
            "estimate": point,
            "ci95_percentile_linear": [percentile(values, 0.025), percentile(values, 0.975)],
        }
    return result


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r056_q001_tse_opening_drive_efficiency.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    if (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.allow_cross_session_pending_order,
    ) != (1, 30, False):
        raise ValueError("execution contract differs from preregistration")
    files = {
        str(path): digest(ROOT / path)
        for path in (
            Path(__file__).relative_to(ROOT),
            Path("src/n225m_bt/research/r056.py"),
            Path("src/n225m_bt/research/r053.py"),
            Path("src/n225m_bt/strategies/r049_fixed_time.py"),
            Path("tests/test_r056_q001.py"),
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
            "tests/test_r056_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r056.py",
            str(Path(__file__).relative_to(ROOT)),
            "tests/test_r056_q001.py",
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r056.py",
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
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, object], value)["returncode"] == 0 for value in validation.values())
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("BLOCKED: pre-execution validation failed")
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    calendar, calendar_audit = cash_calendar()
    tse_scheduled = scheduled_days(calendar)
    ose_scheduled = [
        row.trade_date
        for row in ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml").trading_days()
        if date(2021, 1, 1) <= row.trade_date <= date(2025, 6, 30)
    ]
    axis = [day for day in ose_scheduled if (day, Session.DAY) not in isolated]
    if len(ose_scheduled) != 1131 or len(axis) != 1111:
        raise ValueError("unexpected fixed TSE/Development axis")
    raw_groups, groups = session_groups(development.bars), session_groups(view.bars)
    metric_view = ResearchData(
        [bar for bar in view.bars if bar.trade_date in set(axis)], view.data_version, view.quality
    )
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "quarantine": quarantine_audit,
            "calendar": calendar_audit,
            "fixed_target_day_count": len(axis),
            "fixed_target_trade_dates": [day.isoformat() for day in axis],
            "scheduled_tse_cash_day_count": len(tse_scheduled),
            "physical_io": "Development normalized Parquet only",
        },
    )
    base = event_series(tse_scheduled, raw_groups, isolated, calendar)
    base_by_day = {cast(str, event["trade_date"]): event for event in base}
    events = [
        base_by_day.get(
            day.isoformat(),
            {
                "trade_date": day.isoformat(),
                "status": "skipped",
                "reason": "TSE_CASH_MARKET_CLOSED",
                "schedule_id": SCHEDULE_ID,
            },
        )
        for day in axis
    ]
    engine = BacktestEngine(
        instrument.instrument.to_spec(),
        baseline,
        CalendarClassifier(
            sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
        ),
    )
    trades_by: dict[str, tuple[Trade, ...]] = {}
    records_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    results: dict[str, object] = {}

    def execute(name: str, condition: str = "A", **kwargs: int) -> None:
        ticks = kwargs.get("ticks", 1)
        config = baseline.model_copy(
            update={"execution": baseline.execution.model_copy(update={"slippage_ticks": ticks})}
        )
        trades, records, audit = run_condition(
            events,
            groups,
            BacktestEngine(instrument.instrument.to_spec(), config, engine.classifier),
            condition,
            **kwargs,
        )
        values = daily(trades, axis)
        metrics = write_condition(name, trades, records, audit, metric_view, ticks, values)
        trades_by[name], records_by[name], daily_by[name], results[name] = (
            trades,
            records,
            values,
            {"trade_count": len(trades), "metrics": metrics, "audit": audit},
        )

    for condition in BASE_CONDITIONS:
        execute(condition, condition)
    for name, kwargs in {
        "A2": {"ticks": 2},
        "A3": {"ticks": 3},
        "A_delay": {"delay": 1},
        "A_xq70": {"xq": 70},
        "A_xq80": {"xq": 80},
        "A_eq40": {"eq": 40},
        "A_eq60": {"eq": 60},
        "A_w20": {"window": 20},
        "A_w40": {"window": 40},
        "A_h15": {"holding": 15},
        "A_h45": {"holding": 45},
    }.items():
        execute(name, **kwargs)

    def same_controls(name: str) -> bool:
        return all(
            a.get("condition_eligible") == b.get("condition_eligible")
            and (
                not a.get("condition_eligible")
                or all(
                    a.get(key) == b.get(key)
                    for key in ("trade_date", "planned_entry_jst", "planned_exit_jst")
                )
            )
            for a, b in zip(records_by["A"], records_by[name], strict=True)
        )

    audit = {
        "A_D_exclusive": all(
            not (a.get("condition_eligible") and d.get("condition_eligible"))
            for a, d in zip(records_by["A"], records_by["D"], strict=True)
        ),
        "B_union_A_D": all(
            bool(b.get("condition_eligible"))
            == (bool(a.get("condition_eligible")) or bool(d.get("condition_eligible")))
            for a, b, d in zip(records_by["A"], records_by["B"], records_by["D"], strict=True)
        ),
        "A_controls_event_entry_exit_match": all(
            same_controls(name) for name in ("A_fade", "A_buy", "A_sell")
        ),
        "A_fade_opposite_side": all(
            a.get("side") != fade.get("side")
            for a, fade in zip(records_by["A"], records_by["A_fade"], strict=True)
            if a.get("status") == "filled"
        ),
        "sensitivity_reconstructed": all(
            records_by[name][0].get("observation_minutes") == window
            for name, window in (("A_w20", 20), ("A_w40", 40))
        ),
        "accounting": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
        "all_signal_exits": all(
            trade.exit_reason.value == "signal" for trades in trades_by.values() for trade in trades
        ),
        "delay_nonextension": all(
            trade.exit_ts
            == next(
                base_trade.exit_ts
                for base_trade in trades_by["A"]
                if base_trade.trade_date == trade.trade_date
            )
            for trade in trades_by["A_delay"]
        ),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "checks": audit,
            "all_pass": all(audit.values()),
            "note": "Gross includes one-sided slippage mechanics and Net=Gross-fees; slippage is not deducted twice.",
        },
    )
    if not all(audit.values()):
        raise ValueError("BLOCKED: execution/accounting audit failed")
    write_json(OUT / "all_candidate_event_ledger.json", events)
    write_json(OUT / "development_results.json", results)
    try:
        boot = bootstrap(daily_by, records_by["B"])
    except ValueError as exc:
        if not str(exc).startswith("BLOCKED:"):
            raise
        write_json(
            OUT / "decision.json",
            {
                "status": "BLOCKED",
                "reason": str(exc),
                "oos": "NOT_EVALUATED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
        write_json(
            OUT / "COMPLETED.json",
            {
                "experiment_id": IDENTIFIER,
                "status": "complete",
                "decision": "BLOCKED",
                "oos": "NOT_EVALUATED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
        return
    write_json(OUT / "bootstrap.json", boot)
    metrics_a = cast(dict[str, object], cast(dict[str, object], results["A"])["metrics"])
    overall, segments, concentration = (
        cast(dict[str, object], metrics_a["overall"]),
        cast(dict[str, object], metrics_a["segments"]),
        cast(dict[str, object], metrics_a["concentration"]),
    )
    sides, years = (
        cast(dict[str, dict[str, object]], segments["side"]),
        cast(dict[str, dict[str, object]], segments["year"]),
    )
    sufficient = (
        len(axis) >= 800
        and len(trades_by["B"]) >= 200
        and len(trades_by["A"]) >= 90
        and len(trades_by["D"]) >= 90
        and all(
            cast(int, sides.get(side, {}).get("trade_count", 0)) >= 25 for side in ("long", "short")
        )
        and len(trades_by["A_xq80"]) >= 35
        and len(trades_by["A_eq60"]) >= 35
    )

    def ci(name: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
        )

    def positive(name: str) -> bool:
        item = cast(dict[str, object], cast(dict[str, object], results[name])["metrics"])
        summary = cast(dict[str, object], item["overall"])
        return (
            cast(int, summary["net_pnl_jpy"]) > 0
            and summary["profit_factor"] is not None
            and cast(float, summary["profit_factor"]) > 1
        )

    gates = {
        "A_net_positive": cast(int, overall["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": overall["profit_factor"] is not None
        and cast(float, overall["profit_factor"]) > 1,
        "A_mean_A_minus_D_A_minus_B_delta_ci_lower_positive": all(
            ci(name)
            for name in (
                "A_mean_net_jpy",
                "A_minus_D_mean_jpy",
                "A_minus_B_mean_jpy",
                "delta_fwl_jpy",
            )
        ),
        "same_event_controls_ci_lower_positive": all(
            ci(f"A_minus_{name}_mean_jpy") for name in ("A_fade", "A_buy", "A_sell")
        ),
        "all_cost_delay_threshold_window_holding_sensitivities_positive": all(
            positive(name)
            for name in (
                "A2",
                "A3",
                "A_delay",
                "A_xq70",
                "A_xq80",
                "A_eq40",
                "A_eq60",
                "A_w20",
                "A_w40",
                "A_h15",
                "A_h45",
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
                "E": len(axis),
                "B": len(trades_by["B"]),
                "A": len(trades_by["A"]),
                "D": len(trades_by["D"]),
                "A_side": sides,
                "A_xq80": len(trades_by["A_xq80"]),
                "A_eq60": len(trades_by["A_eq60"]),
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
