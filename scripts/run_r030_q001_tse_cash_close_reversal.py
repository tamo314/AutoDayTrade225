"""Execute the preregistered Development-only R030-Q001 experiment."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from random import Random
from shutil import copyfile
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
from n225m_bt.research.metrics import ledger_metrics, research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r030 import tse_cash_close_reversal_event
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.cash_close_reversal import CashCloseReversalStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r030-q001-20260914-tse-cash-close-reversal-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
R021 = ROOT / "results" / "research" / "r021-q001-20260914-opening-cash-close-followthrough-05"
R012_AXIS = (
    ROOT
    / "results"
    / "research"
    / "r012-q001-20260914-night-direction-followthrough-01"
    / "daily_net_pnl_aligned.json"
)
EXPECTED_AXIS_COUNT = 1_111
EXPECTED_PARENT_VERSION = "f6c267da7fb87e59ef01e18195f3f4263a8fdb72da85c6618c76f4ced22a7db0"
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
Condition = Literal["A_reversal", "B_buy", "C_sell", "D_follow", "G_placebo"]
CONDITIONS: tuple[Condition, ...] = ("A_reversal", "B_buy", "C_sell", "D_follow", "G_placebo")


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)}
        for path in partition_paths(gold_root, "development")
    ]
    return {
        "status": "frozen_before_r030_price_statistics_events_or_pnl",
        "scope": "Selected normalized Development Parquet partitions only; no raw, OOS, Final Holdout, cash/external price, volume, basis, order-flow, or participant input.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def freeze_tse_evidence() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    """Reuse frozen official evidence, without deriving a calendar from OSE bars."""
    folder = reserve_directory(OUT, "institutional_evidence")
    source_files = [
        R020 / "institutional_evidence" / "cabinet_office_public_holidays.csv",
        R020 / "institutional_evidence" / "evidence_manifest.json",
        R021 / "institutional_evidence" / "jpx_tse_trading_hours.pdf",
        R021 / "institutional_evidence" / "jpx_trading_strengthening.html",
        R021 / "institutional_evidence" / "evidence_manifest.json",
    ]
    if any(not item.exists() for item in source_files):
        raise ValueError("R020/R021 frozen official institutional evidence is unavailable")
    records: list[dict[str, str]] = []
    for item in source_files:
        target = folder / f"source_{item.parent.parent.name}_{item.name}"
        copyfile(item, target)
        records.append(
            {
                "source_artifact": str(item.relative_to(ROOT)),
                "name": target.name,
                "sha256": sha256_file(target),
            }
        )
    holiday = folder / f"source_{R020.name}_cabinet_office_public_holidays.csv"
    calendar = TSECashMarketCalendar.from_cabinet_office_csv(holiday.read_text(encoding="cp932"))
    if not calendar.source_start <= date(2021, 1, 1) or not calendar.source_end >= date(
        2025, 6, 30
    ):
        raise ValueError("frozen TSE holiday evidence does not cover Development")
    evidence: dict[str, object] = {
        "status": "frozen_before_r030_price_statistics_events_or_pnl",
        "records": records,
        "records_hash": canonical_hash(records),
        "schedule": "Official TSE cash close T=15:00 through 2024-11-01 and T=15:30 from 2024-11-05, as frozen in src/n225m_bt/research/r021.py and supported by the copied JPX evidence. It is never inferred from futures observations.",
        "interpretation_limit": "TSE business days are independent of observed OSE sessions. The experiment trades futures only and does not identify cash-close auction prices, basis, volume, flow, arbitrage, or participant behavior.",
    }
    write_json(folder / "evidence_manifest.json", evidence)
    return calendar, evidence


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {
        key
        for key, rows in groups.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [row for key, rows in groups.items() if key not in isolated for row in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(isolated),
        "quarantined_sessions_by_type": dict(
            sorted(Counter(session.value for _, session in isolated).items())
        ),
        "included_bars": len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_tick_grid_violations": sum(
            "TICK_GRID_VIOLATION" in row.quality_flags for row in included
        ),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_session_list": listed,
        "quarantined_session_list_hash": canonical_hash(listed),
    }
    expected = {
        "parent_data_version": EXPECTED_PARENT_VERSION,
        "quarantined_sessions": 45,
        "quarantined_bars": 27345,
        "included_bars": 1326086,
        "included_sessions": 2216,
        "quarantined_sessions_by_type": {"day": 20, "night": 25},
        "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH,
    }
    mismatches = {
        key: {"actual": audit.get(key), "expected": value}
        for key, value in expected.items()
        if audit.get(key) != value
    }
    if audit["included_tick_grid_violations"]:
        mismatches["included_tick_grid_violations"] = {
            "actual": audit["included_tick_grid_violations"],
            "expected": 0,
        }
    audit["expected_match"], audit["mismatches"] = not mismatches, mismatches
    if mismatches:
        raise ValueError(f"R004 quarantine reproduction mismatch: {mismatches}")
    return (
        ResearchData(
            included,
            canonical_hash(
                {"parent": data.data_version, "rule": audit["rule"], "sessions": listed}
            ),
            data.quality | {"quarantine": audit},
        ),
        audit,
        isolated,
    )


def axis() -> list[str]:
    if not R012_AXIS.exists():
        raise ValueError("R012 fixed 1,111-day axis artifact is missing")
    values = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if (
        not isinstance(values, list)
        or len(values) != EXPECTED_AXIS_COUNT
        or len(set(values)) != len(values)
    ):
        raise ValueError("R012 daily axis is not the fixed unique 1,111 trade_date universe")
    return cast(list[str], values)


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    lower, upper = floor(position), ceil(position)
    return (
        ordered[lower]
        if lower == upper
        else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    )


def bootstrap(values: dict[str, list[int]]) -> dict[str, object]:
    count, block = len(values["A_reversal"]), 20
    if count != EXPECTED_AXIS_COUNT or any(len(item) != count for item in values.values()):
        raise ValueError("R030 daily series are not aligned to the fixed 1,111-day axis")
    a, b, c, d, g = (values[name] for name in CONDITIONS)
    series = {
        "A_daily_mean_net_jpy": a,
        "A_minus_B_buy_daily_mean_net_jpy": [x - y for x, y in zip(a, b, strict=True)],
        "A_minus_C_sell_daily_mean_net_jpy": [x - y for x, y in zip(a, c, strict=True)],
        "A_minus_D_follow_daily_mean_net_jpy": [x - y for x, y in zip(a, d, strict=True)],
        "A_minus_G_placebo_daily_mean_net_jpy": [x - y for x, y in zip(a, g, strict=True)],
    }
    samples: dict[str, list[float]] = {name: [] for name in series}
    rng = Random(20260913)
    for _ in range(10_000):
        indexes: list[int] = []
        while len(indexes) < count:
            start = rng.randrange(count - block + 1)
            indexes.extend(range(start, start + block))
        for name, item in series.items():
            samples[name].append(fmean(item[index] for index in indexes[:count]))
    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10_000,
        "seed": 20260913,
        "common_indices_all_conditions": True,
        "percentile_implementation": "linear interpolation at (n-1)*q",
        **{
            name: {
                "estimate": fmean(item),
                "ci95_percentile_linear": [
                    percentile(samples[name], 0.025),
                    percentile(samples[name], 0.975),
                ],
            }
            for name, item in series.items()
        },
    }


def direction(condition: Condition, event: dict[str, object]) -> str:
    if condition == "A_reversal":
        return cast(str, event["A_direction"])
    if condition == "B_buy":
        return "long"
    if condition == "C_sell":
        return "short"
    if condition == "D_follow":
        return cast(str, event["D_direction"])
    return cast(str, event["G_direction"])


def run_condition(
    data: ResearchData,
    calendar: TSECashMarketCalendar,
    engine: BacktestEngine,
    condition: Condition,
    quarantined: set[tuple[date, Session]],
    targets: list[str],
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    for text_day in targets:
        trade_day = date.fromisoformat(text_day)
        event = tse_cash_close_reversal_event(
            trade_day,
            groups.get((trade_day, Session.DAY)),
            calendar,
            day_quarantined=(trade_day, Session.DAY) in quarantined,
        )
        event["condition"] = condition
        event["pre_event_status"] = (
            event["placebo_status"] if condition == "G_placebo" else event["status"]
        )
        audit[f"event_{event['pre_event_status']}"] += 1
        if event["pre_event_status"] != "eligible":
            event["reason_for_condition"] = event.get(
                "placebo_reason" if condition == "G_placebo" else "reason", "UNKNOWN"
            )
            audit[f"no_trade_{event['reason_for_condition']}"] += 1
            events.append(event)
            continue
        signal_key, exit_key = (
            ("G_signal_bar_start_jst", "G_planned_exit_jst")
            if condition == "G_placebo"
            else ("t_signal_bar_start_jst", "X_planned_exit_jst")
        )
        signal, exit_time = (
            datetime.fromisoformat(cast(str, event[signal_key])),
            datetime.fromisoformat(cast(str, event[exit_key])),
        )
        result = engine.run(
            groups[(trade_day, Session.DAY)],
            CashCloseReversalStrategy(
                f"r030_q001_{condition}", signal, exit_time, direction(condition, event)
            ),
            canonical_hash(
                {"condition": condition, "event": event, "data_version": data.data_version}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R030 produced more than one day trade")
        audit["canceled_orders"] += result.canceled_orders
        if result.trades:
            trade = result.trades[0]
            event.update(
                {
                    "status": "filled",
                    "side": trade.side.value,
                    "entry_ts_jst": trade.entry_ts.isoformat(),
                    "exit_ts_jst": trade.exit_ts.isoformat(),
                    "entry_delay_minutes": int(
                        (trade.entry_ts - signal - timedelta(minutes=1)).total_seconds() // 60
                    ),
                    "exit_delay_minutes": int((trade.exit_ts - exit_time).total_seconds() // 60),
                    "exit_reason": trade.exit_reason.value,
                    "gross_pnl_jpy": trade.gross_pnl_jpy,
                    "fees_jpy": trade.fees_jpy,
                    "slippage_cost_jpy": trade.slippage_cost_jpy,
                    "net_pnl_jpy": trade.net_pnl_jpy,
                }
            )
            trades.append(trade)
            audit["trades"] += 1
            audit[f"exit_{trade.exit_reason.value}"] += 1
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


def daily(trades: tuple[Trade, ...], targets: list[str]) -> dict[str, int]:
    result = dict.fromkeys(targets, 0)
    for trade in trades:
        result[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return result


def write_condition(
    folder: Path,
    condition: str,
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
            "condition": condition,
            "status": "complete",
            "data_version": data.data_version,
            "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": 30},
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    metrics = research_metrics(trades, data.bars)
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {
            "trade_date": sorted(daily_values),
            "net_pnl_jpy": [daily_values[key] for key in sorted(daily_values)],
        }
    ).write_parquet(folder / "daily_net_pnl.parquet")
    return metrics


def segment_metrics(
    trades: tuple[Trade, ...], events: list[dict[str, object]], key: str
) -> dict[str, object]:
    by_day = {
        cast(str, item["trade_date"]): item for item in events if item.get("status") == "filled"
    }
    groups: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        groups[cast(str, by_day[trade.trade_date.isoformat()][key])].append(trade)
    return {name: ledger_metrics(items) for name, items in sorted(groups.items())}


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    OUT.mkdir(parents=True, exist_ok=False)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    contract = (
        baseline.execution.slippage_ticks,
        baseline.fees.jpy_per_side_per_contract,
        baseline.execution.max_fill_delay_minutes,
        baseline.execution.allow_cross_session_pending_order,
        baseline.risk.new_entry_cutoff_minutes_before_session_close,
        baseline.risk.force_flat_minutes_before_session_close,
    )
    if contract != (1, 30, 10, False, 15, 5):
        raise ValueError("active execution/cost contract differs from frozen R030 specification")
    names = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r030.py"),
        Path("src/n225m_bt/strategies/cash_close_reversal.py"),
        Path("tests/test_r030_q001.py"),
        Path("src/n225m_bt/research/r021.py"),
    ]
    implementation = {str(path): sha256_file(ROOT / path) for path in names}
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    inputs = input_manifest(data_config.gold_root)
    calendar, evidence = freeze_tse_evidence()
    with ZipFile(OUT / "r030_implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in implementation:
            archive.write(ROOT / name, name)
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R030-Q001",
        "status": "frozen_before_r030_price_statistics_events_or_pnl",
        "prior_stopped_attempt": {
            "id": "r030-q001-20260914-tse-cash-close-reversal-01",
            "reason": "A direction-selection dictionary eagerly evaluated G_placebo-only G_direction while A was executing, raising KeyError after Development load and in-memory event construction. No condition ledger, PnL, bootstrap, or decision was persisted.",
            "change_in_02": "Replace eager dictionary evaluation with condition branches only; hypothesis, windows, directions, clocks, costs, data boundary, validation criteria, and seed are unchanged.",
        },
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation, unused validation, or multiplicity-corrected validation.",
        "duplicate_review": {
            "R007": "general session-local one-minute shock reversal, not a schedule-relative T-5..T-1 cash-close window, T..T+10 holding, and scheduled placebo.",
            "R021": "uses 09:00--09:29 direction to follow futures from C-30 to C; its D preclose-follow control neither reverses P=T-5..T-1 nor holds after T.",
            "R001-R029": "No registered implementation uses official TSE close T, reverses the immediately preceding five-minute futures direction from T through T+10, and compares it with same-event buy/sell/follow controls plus the fixed C-35..C-31 placebo.",
            "conclusion": "No equivalent registered specification exists; no alternative specification is created.",
        },
        "hypothesis": "On official TSE business days, reversing the Nikkei 225 futures direction P=close_(T-1)-open_(T-5) from T through T+10 has positive post-cost expectancy and exceeds same-event always-long, always-short, P-following, and the fixed C-35..C-31 five-minute reversal placebo. This examines a possible temporary close-related price-pressure correction but does not identify cash prices, closing auctions, basis, order flow, volume, arbitrage, or participant behavior.",
        "institution_and_calendar": evidence,
        "fixed_rule": {
            "T": "Use version-controlled official TSE cash close T=15:00 through 2024-11-01 and T=15:30 from 2024-11-05, never inferred from observations.",
            "main": "Require five contiguous eligible day bars T-5..T-1. P=close_(T-1)-open_(T-5); P=0 skips A/B/C/D. A=-sign(P), B=always long, C=always short, D=sign(P). Signal after T-1 close, earliest entry T open; signal EXIT after T+9 close, earliest exit T+10 open.",
            "placebo": "Independently require five contiguous eligible day bars T-35..T-31. P0=close_(T-31)-open_(T-35); P0=0 skips G only. G=-sign(P0), signal after T-31 close, earliest entry T-30 open; signal EXIT after T-21 close, earliest exit T-20 open.",
            "execution": "One contract, maximum one position and one trade/day per condition. No Stop/Target, re-entry, intraday update, early exit, or exit extension after delayed entry. Preserve unchanged engine pending-order, delay, cutoff, force-flat and accounting contracts.",
            "prohibited": [
                "window/direction/placebo/holding rescue",
                "additional delay or cost tests except specified A2 and A 0-tick diagnostic",
                "WFA",
                "OOS",
                "Final Holdout",
            ],
        },
        "inputs": {
            "physical": inputs,
            "r004_fixed_quarantine": "45 sessions/27,345 bars isolated; 2,216 sessions/1,326,086 bars retained; required list hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa.",
            "fixed_daily_axis": {
                "source": str(R012_AXIS.relative_to(ROOT)),
                "sha256": sha256_file(R012_AXIS),
                "count": EXPECTED_AXIS_COUNT,
            },
            "quality_ceiling": "PASS_LIMITED",
        },
        "costs": {
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "A_0tick": "Gross diagnostic only; never used for decision.",
            "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; do not deduct slippage twice.",
        },
        "evaluation": {
            "bootstrap": {
                "block_length_trade_dates": 20,
                "repetitions": 10_000,
                "seed": 20260913,
                "common_indices_all_conditions": True,
                "sampling": "non-circular moving blocks with replacement and tail truncation",
                "ci": "linear percentile",
            },
            "information_gate": [
                "A/B/C/D >=700 trades",
                "G_placebo >=700 trades",
                "A long/short >=250",
                "A T=15:00 and T=15:30 >=100 each",
            ],
            "pass_requires_all_after_information": [
                "A Net>0",
                "A PF>1",
                "A and A-B/A-C/A-D/A-G 95% CI lower bounds>0",
                "A2 expectancy>0",
                "A Net>0 in both T regimes",
                "A positive months>=27/54",
                "A Net excluding top10 winners>0",
            ],
            "decision": "BLOCKED for input/institution/synthetic/execution/accounting failure; INCONCLUSIVE for information insufficiency; otherwise REJECT if a required condition fails; all passing remains Development-only INVESTIGATE.",
        },
        "identifiers_before_run": {
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "implementation_files": implementation,
            "implementation_files_hash": canonical_hash(implementation),
            "input_manifest_hash": canonical_hash(inputs),
        },
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }
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
            "status": "preregistered_before_r030_price_statistics_events_or_pnl",
            "source": source,
            "plan_hash": canonical_hash(plan),
            "seed": 20260913,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    commands = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r030_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r030.py",
            "src/n225m_bt/strategies/cash_close_reversal.py",
            "tests/test_r030_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r030.py",
            "src/n225m_bt/strategies/cash_close_reversal.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
    }
    validation: dict[str, Any] = {
        "coverage": [
            "TSE 15:00/15:30 schedule, holidays, JST/trade_date, P/P0 signs/zero/missing/quarantine/period and main/placebo independence",
            "P side prefix invariance against T-and-later OHLC changes",
            "next-bar entry, fixed exit, delayed-entry non-extension, cutoff/force-flat precedence, one position, Net=Gross-fees, Holdout rejection",
        ]
    }
    for name, command in commands.items():
        completed = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    validation["status"] = (
        "PASS" if all(validation[name]["returncode"] == 0 for name in commands) else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R030 validation failed before Development price access")
    targets = axis()
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, quarantined = quarantine(development)
    expected_days = {
        bar.trade_date.isoformat() for bar in development.bars if bar.session is Session.DAY
    } - {item.isoformat() for item, session in quarantined if session is Session.DAY}
    if set(targets) != expected_days:
        raise ValueError(
            "R030 fixed 1,111-day axis does not equal Development day universe excluding isolated days"
        )
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": quarantine_audit,
            "fixed_target_trade_dates": targets,
            "physical_io": "Development selected normalized Parquet only; OOS/Final Holdout never selected",
            "logical_price_access": "trade_date 2021-01-01..2025-06-30 only",
        },
    )
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    engine = BacktestEngine(instrument.instrument.to_spec(), baseline, classifier)
    trades_by: dict[str, tuple[Trade, ...]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    metrics_by: dict[str, dict[str, object]] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(
            view, calendar, engine, condition, quarantined, targets
        )
        daily_values = daily(trades, targets)
        metrics_by[condition] = write_condition(
            OUT / condition, condition, trades, events, audit, view, 1, daily_values
        )
        trades_by[condition], events_by[condition], daily_by[condition] = (
            trades,
            events,
            daily_values,
        )
    a2_config = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})}
    )
    a2_trades, a2_events, a2_audit = run_condition(
        view,
        calendar,
        BacktestEngine(instrument.instrument.to_spec(), a2_config, classifier),
        "A_reversal",
        quarantined,
        targets,
    )
    a2_metrics = write_condition(
        OUT / "A_reversal_2tick",
        "A_reversal_2tick",
        a2_trades,
        a2_events,
        a2_audit,
        view,
        2,
        daily(a2_trades, targets),
    )
    a0_config = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 0})}
    )
    a0_trades, a0_events, a0_audit = run_condition(
        view,
        calendar,
        BacktestEngine(instrument.instrument.to_spec(), a0_config, classifier),
        "A_reversal",
        quarantined,
        targets,
    )
    a0_metrics = write_condition(
        OUT / "diagnostics" / "A_reversal_0tick",
        "A_reversal_0tick_diagnostic",
        a0_trades,
        a0_events,
        a0_audit,
        view,
        0,
        daily(a0_trades, targets),
    )
    common = ("A_reversal", "B_buy", "C_sell", "D_follow")
    main_event_days = {
        name: {
            cast(str, item["trade_date"])
            for item in events_by[name]
            if item.get("status") == "filled"
        }
        for name in common
    }
    same_main_events = len({frozenset(item) for item in main_event_days.values()}) == 1
    a_a2_fields = (
        "trade_date",
        "P_points",
        "P_direction",
        "A_direction",
        "T_cash_close_jst",
        "E_planned_entry_jst",
        "X_planned_exit_jst",
    )
    a_a2_equal = len(a2_events) == len(events_by["A_reversal"]) and all(
        tuple(item.get(key) for key in a_a2_fields)
        == tuple(events_by["A_reversal"][index].get(key) for key in a_a2_fields)
        for index, item in enumerate(a2_events)
    )
    all_trades = [*trades_by.values(), a2_trades, a0_trades]
    path_ok = all(
        item.net_pnl_jpy == item.gross_pnl_jpy - item.fees_jpy
        and item.exit_reason.value == "signal"
        and item.entry_signal_ts is not None
        and item.exit_signal_ts is not None
        and item.entry_ts >= item.entry_signal_ts + timedelta(minutes=1)
        and item.exit_ts >= item.exit_signal_ts + timedelta(minutes=1)
        for ledger in all_trades
        for item in ledger
    )
    post = {
        "status": "PASS" if same_main_events and a_a2_equal and path_ok else "BLOCKED",
        "A_B_C_D_identical_main_pre_event_set": same_main_events,
        "A_A2_identical_pre_event_direction_schedule": a_a2_equal,
        "all_paths_next_eligible_entry_fixed_exit_signal_and_accounting": path_ok,
        "policy": "Any execution or accounting failure blocks; no successful-fill intersection is selected.",
    }
    write_json(OUT / "post_execution_validation.json", post)
    values: dict[str, list[int]] = {
        name: [daily_by[name][day] for day in targets] for name in CONDITIONS
    }
    boot = bootstrap(values)
    months = {
        f"{year:04d}-{month:02d}": 0
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }
    for day, value in daily_by["A_reversal"].items():
        months[day[:7]] += value
    a_overall = cast(dict[str, Any], metrics_by["A_reversal"]["overall"])
    a2_overall = cast(dict[str, Any], a2_metrics["overall"])
    a_regimes = segment_metrics(
        trades_by["A_reversal"], events_by["A_reversal"], "cash_close_regime"
    )
    p_sign = {
        name: segment_metrics(trades_by[name], events_by[name], "P_direction") for name in common
    }
    p0_sign = segment_metrics(trades_by["G_placebo"], events_by["G_placebo"], "P0_direction")
    long_count = sum(item.side.value == "long" for item in trades_by["A_reversal"])

    def lower(name: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
        )

    gates: dict[str, bool] = {
        "A_trade_count_at_least_700": len(trades_by["A_reversal"]) >= 700,
        "B_buy_trade_count_at_least_700": len(trades_by["B_buy"]) >= 700,
        "C_sell_trade_count_at_least_700": len(trades_by["C_sell"]) >= 700,
        "D_follow_trade_count_at_least_700": len(trades_by["D_follow"]) >= 700,
        "G_placebo_trade_count_at_least_700": len(trades_by["G_placebo"]) >= 700,
        "A_long_at_least_250": long_count >= 250,
        "A_short_at_least_250": len(trades_by["A_reversal"]) - long_count >= 250,
        "A_T_1500_at_least_100": cast(dict[str, Any], a_regimes.get("T_1500", {})).get(
            "trade_count", 0
        )
        >= 100,
        "A_T_1530_at_least_100": cast(dict[str, Any], a_regimes.get("T_1530", {})).get(
            "trade_count", 0
        )
        >= 100,
        "A_net_positive": a_overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": a_overall["profit_factor"] is not None
        and a_overall["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"),
        "A_minus_B_buy_bootstrap_lower_above_0": lower("A_minus_B_buy_daily_mean_net_jpy"),
        "A_minus_C_sell_bootstrap_lower_above_0": lower("A_minus_C_sell_daily_mean_net_jpy"),
        "A_minus_D_follow_bootstrap_lower_above_0": lower("A_minus_D_follow_daily_mean_net_jpy"),
        "A_minus_G_placebo_bootstrap_lower_above_0": lower("A_minus_G_placebo_daily_mean_net_jpy"),
        "A_2tick_expectancy_positive": a2_overall["expectancy_jpy"] is not None
        and a2_overall["expectancy_jpy"] > 0,
        "A_T_1500_net_positive": cast(dict[str, Any], a_regimes.get("T_1500", {})).get(
            "net_pnl_jpy", 0
        )
        > 0,
        "A_T_1530_net_positive": cast(dict[str, Any], a_regimes.get("T_1530", {})).get(
            "net_pnl_jpy", 0
        )
        > 0,
        "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "A_net_excluding_top10_positive": cast(
            dict[str, Any], metrics_by["A_reversal"]["concentration"]
        )["net_excluding_top10_jpy"]
        > 0,
    }
    information = [
        key
        for key in gates
        if "trade_count" in key
        or "at_least_250" in key
        or ("T_15" in key and "at_least_100" in key)
    ]
    decision = (
        "BLOCKED"
        if post["status"] != "PASS"
        else "INCONCLUSIVE"
        if not all(gates[key] for key in information)
        else "INVESTIGATE"
        if all(gates.values())
        else "REJECT"
    )
    aligned: dict[str, object] = {
        "trade_dates": targets,
        "no_trade": "0",
        "day_session_quarantined": "excluded",
        "TSE_closed_missing_zero_cancel_unfilled": "0",
    }
    aligned.update(values)
    write_json(OUT / "daily_net_pnl_aligned.json", aligned)
    write_json(OUT / "bootstrap.json", boot)
    write_json(
        OUT / "A_0tick_gross_diagnostic.json",
        {
            "purpose": "diagnostic only; excluded from gates and decision",
            "trade_count": len(a0_trades),
            "gross_pnl_jpy": cast(dict[str, Any], a0_metrics["overall"])["gross_pnl_jpy"],
            "gross_expectancy_jpy": cast(dict[str, Any], a0_metrics["overall"])["expectancy_jpy"],
        },
    )
    write_json(
        OUT / "development_results.json",
        {
            "campaign_id": IDENTIFIER,
            "quality_status": "PASS_LIMITED",
            "decision": decision,
            "gates": gates,
            "A_direction_counts": {
                "long": long_count,
                "short": len(trades_by["A_reversal"]) - long_count,
            },
            "positive_months_of_54": sum(value > 0 for value in months.values()),
            "monthly_A_net_jpy": months,
            "conditions": {
                name: {"trade_count": len(trades_by[name]), "metrics": metrics_by[name]}
                for name in CONDITIONS
            }
            | {
                "A_reversal_2tick": {"trade_count": len(a2_trades), "metrics": a2_metrics},
                "A_reversal_0tick_diagnostic": {
                    "trade_count": len(a0_trades),
                    "metrics": a0_metrics,
                },
            },
            "P_sign_metrics": p_sign,
            "P0_sign_metrics_G_placebo": p0_sign,
            "A_cash_close_regime_metrics": a_regimes,
            "A_year_month_side_metrics": {
                "year": cast(dict[str, object], metrics_by["A_reversal"]["segments"])["year"],
                "month": cast(dict[str, object], metrics_by["A_reversal"]["segments"])["month"],
                "side": cast(dict[str, object], metrics_by["A_reversal"]["segments"])["side"],
            },
            "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction",
            "scope": "Development only; 2025 Jan-Jun partial; WFA/OOS/Final Holdout not run",
        },
    )
    (OUT / "summary.md").write_text(
        f"# R030-Q001\n\nDevelopment-only fixed TSE cash-close reversal experiment. Decision: **{decision}**.\n\nSee preregistration, institutional evidence, ledgers, daily series, bootstrap, and validation artifacts. OOS and Final Holdout were not accessed.\n",
        encoding="utf-8",
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
