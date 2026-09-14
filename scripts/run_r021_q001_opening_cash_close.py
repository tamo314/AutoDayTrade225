"""Execute the preregistered Development-only R021-Q001 experiment."""

from __future__ import annotations

import json
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
from urllib.request import Request, urlopen
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
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r021 import opening_to_cash_close_event, tse_cash_close
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.opening_cash_close import OpeningCashCloseStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r021-q001-20260914-opening-cash-close-followthrough-05"
OUT = ROOT / "results" / "research" / IDENTIFIER
R020 = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
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
Condition = Literal[
    "A_opening_follow", "B_always_long", "C_short", "D_preclose_follow", "F_reverse"
]
CONDITIONS: tuple[Condition, ...] = (
    "A_opening_follow",
    "B_always_long",
    "C_short",
    "D_preclose_follow",
    "F_reverse",
)
URLS = {
    "jpx_tse_trading_hours.pdf": "https://www.jpx.co.jp/english/equities/trading/domestic/tvdivq0000006blj-att/tradinghours_eg.pdf",
    "jpx_trading_strengthening.html": "https://www.jpx.co.jp/english/equities/trading/strengthening/",
}


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_evidence() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    folder = reserve_directory(OUT, "institutional_evidence")
    r020_manifest_path = R020 / "institutional_evidence" / "evidence_manifest.json"
    r020_holidays = R020 / "institutional_evidence" / "cabinet_office_public_holidays.csv"
    if not r020_manifest_path.exists() or not r020_holidays.exists():
        raise ValueError("R020 formal institutional evidence is unavailable")
    r020_manifest = json.loads(r020_manifest_path.read_text(encoding="utf-8"))
    holidays_bytes = r020_holidays.read_bytes()
    (folder / "r020_cabinet_office_public_holidays.csv").write_bytes(holidays_bytes)
    records: list[dict[str, object]] = [
        {
            "name": "r020_cabinet_office_public_holidays.csv",
            "source_artifact": str(r020_holidays.relative_to(ROOT)),
            "sha256": sha256_file(folder / "r020_cabinet_office_public_holidays.csv"),
            "r020_records_hash": r020_manifest["records_hash"],
        }
    ]
    for name, url in URLS.items():
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; N225M-research/0.1; evidence archival)"
                },
            )
            with urlopen(request, timeout=30) as response:
                payload = response.read()
        except Exception as exc:
            raise RuntimeError(
                f"institutional/literature evidence download failed for {url}: {exc}"
            ) from exc
        (folder / name).write_bytes(payload)
        records.append(
            {"name": name, "url": url, "sha256": sha256_file(folder / name), "bytes": len(payload)}
        )
    literature = {
        "source": "user-supplied task prompt",
        "citation": "Gao et al., Market Intraday Momentum",
        "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866",
        "role": "motivation only; not a direct replication and not a signal or data input",
        "retrieval_note": "Direct archival request returned HTTP 403 in stopped R021 -01/-02 before preregistration. No substitute source was used and no empirical rule parameter was taken from it.",
    }
    write_json(folder / "literature_provenance.json", literature)
    records.append(
        {
            "name": "literature_provenance.json",
            "sha256": sha256_file(folder / "literature_provenance.json"),
            "role": "user-supplied citation provenance",
        }
    )
    cash_calendar = TSECashMarketCalendar.from_cabinet_office_csv(holidays_bytes.decode("cp932"))
    if not (
        cash_calendar.source_start <= date(2021, 1, 1)
        and cash_calendar.source_end >= date(2025, 6, 30)
    ):
        raise ValueError("R020 official TSE holiday evidence does not cover Development")
    open_days = []
    cursor = date(2021, 1, 1)
    while cursor <= date(2025, 6, 30):
        if cash_calendar.is_open(cursor):
            open_days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    derived = {
        "calendar_name": "TSE_cash_market_business_days_R021",
        "range": ["2021-01-01", "2025-06-30"],
        "tse_open_trade_dates": open_days,
        "count": len(open_days),
        "cash_close_rule": "15:00 through 2024-11-01; 15:30 from 2024-11-05; fixed from JPX sources, never inferred from OSE or futures bars",
        "preclosing_note": "From 2024-11-05 the 15:25-15:30 TSE cash pre-closing period is recorded; R021 trades futures only and makes no claim about cash continuous execution, cash closing prices, or C-open reaction.",
    }
    write_json(folder / "tse_cash_calendar.json", derived)
    evidence: dict[str, object] = {
        "status": "frozen_before_r021_price_statistics_events_or_pnl",
        "r020_formal_completion": {
            "campaign": str(R020.relative_to(ROOT)),
            "completed_sha256": sha256_file(R020 / "COMPLETED.json"),
            "institutional_evidence_manifest_sha256": sha256_file(r020_manifest_path),
            "records_hash": r020_manifest["records_hash"],
        },
        "records": records,
        "records_hash": canonical_hash(records),
        "derived_calendar_sha256": sha256_file(folder / "tse_cash_calendar.json"),
        "derived_calendar_hash": canonical_hash(derived),
        "asserted_institutional_interpretation": "TSE official open days are independent of OSE observed sessions. Official JPX materials establish C=15:00 through 2024-11-01 and C=15:30 from 2024-11-05. The R021 exit is a futures order at C's start, not a reproduction of cash-market execution or cash close.",
    }
    write_json(folder / "evidence_manifest.json", evidence)
    return cash_calendar, evidence


def input_manifest(gold_root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)}
        for path in partition_paths(gold_root, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "Only selected normalized Development Parquet partitions; no raw, OOS, Final Holdout, external price, or volume inputs.",
        "trade_date_range": ["2021-01-01", "2025-06-30"],
        "files": files,
        "files_hash": canonical_hash(files),
    }


def implementation_snapshot() -> dict[str, str]:
    names = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r021.py"),
        Path("src/n225m_bt/strategies/opening_cash_close.py"),
        Path("tests/test_r021_q001.py"),
    ]
    return {str(name): sha256_file(ROOT / name) for name in names}


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


def axis_from_r012() -> list[str]:
    if not R012_AXIS.exists():
        raise ValueError("R012 fixed 1,111-day axis artifact is missing")
    values = json.loads(R012_AXIS.read_text(encoding="utf-8"))["trade_dates"]
    if (
        not isinstance(values, list)
        or len(values) != EXPECTED_AXIS_COUNT
        or len(set(values)) != len(values)
    ):
        raise ValueError("R012 daily axis is not the fixed unique 1,111 trade_date universe")
    return values


def percentile(values: list[float], q: float) -> float:
    ordered, position = sorted(values), (len(values) - 1) * q
    lower, upper = floor(position), ceil(position)
    return (
        ordered[lower]
        if lower == upper
        else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    )


def bootstrap(values: dict[str, list[int]]) -> dict[str, object]:
    count, block = len(values["A_opening_follow"]), 20
    if count != EXPECTED_AXIS_COUNT or any(len(series) != count for series in values.values()):
        raise ValueError("R021 daily series are not the aligned 1,111 axis")
    a, b, c, d, f = (values[name] for name in CONDITIONS)
    series = {
        "A": a,
        "A_minus_B": [x - y for x, y in zip(a, b, strict=True)],
        "A_minus_C_short": [x - y for x, y in zip(a, c, strict=True)],
        "A_minus_D": [x - y for x, y in zip(a, d, strict=True)],
        "A_minus_F_reverse": [x - y for x, y in zip(a, f, strict=True)],
    }
    samples: dict[str, list[float]] = {name: [] for name in series}
    rng = Random(20260913)
    for _ in range(10_000):
        indexes: list[int] = []
        while len(indexes) < count:
            start = rng.randrange(count - block + 1)
            indexes.extend(range(start, start + block))
        indexes = indexes[:count]
        for name, value in series.items():
            samples[name].append(fmean(value[index] for index in indexes))
    labels = {
        "A": "A_daily_mean_net_jpy",
        "A_minus_B": "A_minus_B_daily_mean_net_jpy",
        "A_minus_C_short": "A_minus_C_short_daily_mean_net_jpy",
        "A_minus_D": "A_minus_D_daily_mean_net_jpy",
        "A_minus_F_reverse": "A_minus_F_reverse_daily_mean_net_jpy",
    }
    return {
        "method": "moving_block_bootstrap_with_replacement_no_wrap_then_tail_truncate",
        "target_trade_dates": count,
        "block_length_trade_dates": block,
        "repetitions": 10_000,
        "seed": 20260913,
        "common_indices_all_conditions": True,
        "percentile_implementation": "linear interpolation at (n-1)*q",
        **{
            labels[name]: {
                "estimate": fmean(value),
                "ci95_percentile_linear": [
                    percentile(samples[name], 0.025),
                    percentile(samples[name], 0.975),
                ],
            }
            for name, value in series.items()
        },
    }


def direction_for(condition: Condition, event: dict[str, object]) -> str:
    if condition == "B_always_long":
        return "long"
    if condition == "C_short":
        return "short"
    return cast(
        str,
        event[
            {
                "A_opening_follow": "A_direction",
                "D_preclose_follow": "D_direction",
                "F_reverse": "F_reverse_direction",
            }[condition]
        ],
    )


def run_condition(
    data: ResearchData,
    cash_calendar: TSECashMarketCalendar,
    engine: BacktestEngine,
    condition: Condition,
    targets: list[str],
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    groups, events, trades, audit = session_groups(data.bars), [], [], Counter[str]()
    for text_day in targets:
        trade_day = date.fromisoformat(text_day)
        event = opening_to_cash_close_event(
            trade_day, groups.get((trade_day, Session.DAY), []), cash_calendar
        )
        event.update({"condition": condition, "pre_event_status": event["status"]})
        audit[f"event_{event['status']}"] += 1
        if event["status"] != "eligible":
            audit[f"no_trade_{event.get('reason', 'UNKNOWN')}"] += 1
            events.append(event)
            continue
        signal, exit_time = (
            datetime.fromisoformat(cast(str, event["t_signal_bar_start_jst"])),
            datetime.fromisoformat(cast(str, event["X_planned_exit_jst"])),
        )
        result = engine.run(
            groups[(trade_day, Session.DAY)],
            OpeningCashCloseStrategy(
                f"r021_q001_{condition}", signal, exit_time, direction_for(condition, event)
            ),
            canonical_hash(
                {"condition": condition, "event": event, "data_version": data.data_version}
            ),
        )
        if len(result.trades) > 1:
            raise AssertionError("R021 produced more than one day trade")
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


def write_condition(
    folder: Path,
    condition: str,
    trades: tuple[Trade, ...],
    events: list[dict[str, object]],
    metrics: dict[str, object],
    audit: dict[str, int],
    data: ResearchData,
    ticks: int,
    daily: dict[str, int],
) -> None:
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
    write_json(folder / "metrics_research.json", metrics)
    write_json(folder / "execution_audit.json", audit)
    pl.DataFrame(events).write_parquet(folder / "events.parquet")
    pl.DataFrame(
        {"trade_date": sorted(daily), "net_pnl_jpy": [daily[item] for item in sorted(daily)]}
    ).write_parquet(folder / "daily_net_pnl.parquet")


def path_audit(
    events: list[dict[str, object]], trades: tuple[Trade, ...], max_delay: int
) -> dict[str, object]:
    eligible, filled = (
        [item for item in events if item.get("pre_event_status") == "eligible"],
        [item for item in events if item.get("status") == "filled"],
    )
    checks = {
        "every_eligible_event_filled": len(eligible) == len(filled),
        "one_trade_per_filled_event": len(filled) == len(trades),
        "no_canceled_or_unfilled_eligible_order": not any(
            item.get("status") == "eligible_order_unfilled" for item in events
        ),
        "entry_within_max_delay": all(
            0 <= cast(int, item["entry_delay_minutes"]) <= max_delay for item in filled
        ),
        "fixed_signal_exit_within_max_delay": all(
            item["exit_reason"] == "signal"
            and 0 <= cast(int, item["exit_delay_minutes"]) <= max_delay
            for item in filled
        ),
        "net_equals_gross_minus_fees": all(
            item.net_pnl_jpy == item.gross_pnl_jpy - item.fees_jpy for item in trades
        ),
        "no_force_flat_or_end_of_data": all(
            item.exit_reason.value not in {"force_flat", "end_of_data"} for item in trades
        ),
    }
    return {
        "checks": checks,
        "all_pass": all(checks.values()),
        "accounting": "slippage attribution is informational and is never additionally deducted",
    }


def preregistration(
    source: dict[str, object],
    inputs: dict[str, object],
    evidence: dict[str, object],
    implementation: dict[str, str],
) -> dict[str, object]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R021-Q001",
        "status": "frozen_before_r021_price_statistics_events_or_pnl",
        "prior_stopped_attempts": {
            "ids": [
                "r021-q001-20260914-opening-cash-close-followthrough-01",
                "r021-q001-20260914-opening-cash-close-followthrough-02",
                "r021-q001-20260914-opening-cash-close-followthrough-03",
                "r021-q001-20260914-opening-cash-close-followthrough-04",
            ],
            "reason": "-01/-02: SSRN evidence URL returned HTTP 403 before preregistration or any Development price/statistics/event/order/fill/trade/PnL/bootstrap access. -03: completed the unchanged rule but its pre-execution test did not explicitly assert delayed entry's fixed C exit. -04: added only that synthetic assertion but the local runner was interrupted after condition ledgers and before bootstrap/decision artefacts; -05 repeats unchanged code/specification to a terminal result.",
            "price_statistics_events_orders_fills_trades_pnl_bootstrap": "NOT_CREATED",
        },
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; known-Development additional exploration, not independent confirmation, unused validation, or research-wide multiplicity-corrected validation.",
        "novelty_correspondence": {
            "R001": "initial-move immediate trading, not a 09:00 direction held only into the final cash-close 30 minutes",
            "R005": "futures session-end preceding-60-minute direction",
            "R012/R013/R015": "prior or night/session-spanning direction inputs",
            "conclusion": "No R001-R020 registration or implementation applies the 09:00-09:29 futures direction to a fixed futures position from C-30 to C against the specified five contemporaneous controls.",
        },
        "hypothesis": "Following the 09:00-09:30 Nikkei 225 futures change while holding the final 30 minutes before TSE cash close has positive post-cost expectation and outperforms always-long, always-short, the immediately preceding 30-minute direction follow, and opening-direction reversal. Order splitting, hedging demand and participant behavior are unobserved explanations, not identified mechanisms. Gao et al. Market Intraday Momentum is motivation only: its US ETF/previous-close initial move differs from this Japanese futures/same-day 09:00 open rule.",
        "institution_and_calendar": evidence,
        "fixed_rule": {
            "day_only": "Official TSE-open dates only; TSE holidays skip all conditions even if OSE observes a futures day session.",
            "windows": "O=close_09:29-open_09:00 and M=close_(E-1)-open_(E-30), each requiring exactly 30 contiguous eligible day-session bars. O/M nonzero is the common pre-event. Intermediate prices are neither signal inputs nor completeness filters. No thresholds, standardization, weekday/year/side/range filters, volume, external price, night move or session gap.",
            "clock": "C=15:00 through 2024-11-01, C=15:30 from 2024-11-05; E=C-30, signal E-1, X=C. Existing schedule verifies E at/before new-entry cutoff and X at/before force-flat. 15:25-15:30 cash pre-closing after the new regime is recorded but is not modeled as a futures/cash execution claim.",
            "conditions": {
                "A": "sign(O)",
                "B": "always long",
                "C_short": "always short",
                "D": "sign(M)",
                "F_reverse": "-sign(O)",
                "A2": "A rerun by unchanged engine with 2 ticks/side",
            },
            "orders": "After E-1 close, earliest entry E open; after X-1 close, earliest exit X open. Delays do not alter X. No entry at/after X; preserve unchanged engine max-delay, conflict, force-flat, cutoff and one-position contracts.",
            "prohibited": [
                "stop",
                "target",
                "re-entry",
                "early exit",
                "direction/time/window/threshold rescue",
                "additional costs or delays",
                "WFA",
                "OOS",
                "Final Holdout",
            ],
        },
        "inputs": {
            "physical": inputs,
            "r004_fixed_quarantine": "Must reproduce hash 2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa, 45 sessions/27,345 bars isolated, 2,216 sessions/1,326,086 bars retained.",
            "fixed_daily_axis": {
                "source": str(R012_AXIS.relative_to(ROOT)),
                "sha256": sha256_file(R012_AXIS),
                "count": EXPECTED_AXIS_COUNT,
                "rule": "R006/R012/R015 1,111 day axis; day-isolated dates excluded, TSE-closed/window-missing/zero/cancel/no-trade are zero.",
            },
            "quality_ceiling": "PASS_LIMITED; inherited continuous-series, real-contract/roll/adjustment, provider timestamp and post-quality whole-session isolation limits.",
        },
        "costs": {
            "baseline": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30},
            "A2": {"slippage_ticks_per_side": 2, "fee_jpy_per_side": 30},
            "accounting": "Gross is fill-to-fill and slippage-inclusive; Net=Gross-fees; do not deduct slippage attribution twice.",
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
                "A/B/C_short/D/F_reverse >=200 trades",
                "A long/short >=50",
                "each O-sign x M-sign eligible group >=50 pre-events",
            ],
            "pass_requires_all": [
                "A Net>0",
                "A PF>1",
                "A and A-B/A-C_short/A-D/A-F_reverse all 95% lower bounds >0",
                "A2 expectancy>0",
                "A positive months>=27/54",
                "A Net excluding top10 winners>0",
            ],
            "decision": "BLOCKED on input/institution/synthetic/execution/accounting failure; INCONCLUSIVE on information failure; otherwise REJECT if any primary condition fails. All passing is Development primary/PASS_LIMITED only and INVESTIGATE, never automatic CANDIDATE.",
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
        raise ValueError("active execution/cost contract differs from frozen R021 specification")
    cash_calendar, evidence = freeze_evidence()
    source, implementation, inputs = (
        snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        implementation_snapshot(),
        input_manifest(data_config.gold_root),
    )
    with ZipFile(OUT / "r021_implementation_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        for name in implementation:
            archive.write(ROOT / name, name)
    plan = preregistration(source, inputs, evidence, implementation)
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
            "status": "preregistered_before_r021_price_statistics_events_or_pnl",
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
            "tests/test_r021_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r021.py",
            "src/n225m_bt/strategies/opening_cash_close.py",
            "tests/test_r021_q001.py",
            str(Path(__file__).relative_to(ROOT)),
        ],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r021.py",
            "src/n225m_bt/strategies/opening_cash_close.py",
        ],
    }
    validation: dict[str, object] = {
        "coverage": [
            "TSE/OSE calendar distinction; official 15:00/15:30 C and schedule cutoff/force-flat alignment",
            "09:00 origin, both 30-bar windows, O/M signs/zeros/missing, intermediate-price/prefix invariance",
            "same M/different O and same O/different M direction separation",
            "next-bar entry, fixed C exit, delayed non-extension, cutoff-held exit, one position and accounting",
            "Final Holdout input rejection",
        ]
    }
    for name, command in commands.items():
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validation[name] = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    validation["status"] = (
        "PASS"
        if all(cast(dict[str, int], validation[name])["returncode"] == 0 for name in commands)
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("R021 synthetic/static validation failed before Development price access")
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    timing = []
    for example in (date(2024, 11, 1), date(2024, 11, 5)):
        close, entry, ose_close = (
            tse_cash_close(example),
            tse_cash_close(example) - timedelta(minutes=30),
            classifier.session_close(example, Session.DAY),
        )
        timing.append(
            {
                "trade_date_example": example.isoformat(),
                "C": close.isoformat(),
                "E": entry.isoformat(),
                "X": close.isoformat(),
                "OSE_session_close": ose_close.isoformat(),
                "new_entry_cutoff": (
                    ose_close
                    - timedelta(minutes=baseline.risk.new_entry_cutoff_minutes_before_session_close)
                ).isoformat(),
                "force_flat": (
                    ose_close
                    - timedelta(minutes=baseline.risk.force_flat_minutes_before_session_close)
                ).isoformat(),
                "E_within_cutoff": entry
                <= ose_close
                - timedelta(minutes=baseline.risk.new_entry_cutoff_minutes_before_session_close),
                "X_within_force_flat": close
                <= ose_close
                - timedelta(minutes=baseline.risk.force_flat_minutes_before_session_close),
            }
        )
    if not all(row["E_within_cutoff"] and row["X_within_force_flat"] for row in timing):
        raise ValueError("R021 planned times violate existing engine cutoff/force-flat contract")
    development = load_split(data_config.gold_root, "development")
    parent_groups = session_groups(development.bars)
    view, quarantine_audit, isolated = quarantine(development)
    targets = axis_from_r012()
    actual = sorted(
        day.isoformat()
        for day, session in parent_groups
        if session is Session.DAY and (day, Session.DAY) not in isolated
    )
    if targets != actual:
        raise ValueError("R012 fixed 1,111-day axis does not reproduce R004 day-session universe")
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "development_input": development.quality,
            "quarantine": quarantine_audit,
            "fixed_target_trade_dates": targets,
            "target_count": len(targets),
            "schedule_timing_checks": timing,
            "physical_io": "Development selected normalized Parquet only; OOS and Final Holdout never selected",
            "logical_price_access": "trade_date 2021-01-01..2025-06-30 only",
            "institutional_gate": evidence,
        },
    )
    results: dict[str, dict[str, object]] = {}
    events_by: dict[str, list[dict[str, object]]] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    for condition in CONDITIONS:
        trades, events, audit = run_condition(
            view,
            cash_calendar,
            BacktestEngine(instrument.instrument.to_spec(), baseline, classifier),
            condition,
            targets,
        )
        daily = dict.fromkeys(targets, 0)
        for trade in trades:
            daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        metrics = research_metrics(trades, view.bars)
        write_condition(
            reserve_directory(OUT, condition),
            condition,
            trades,
            events,
            metrics,
            audit,
            view,
            1,
            daily,
        )
        results[condition], events_by[condition], trades_by[condition], daily_by[condition] = (
            {"trade_count": len(trades), "metrics": metrics, "execution_audit": audit},
            events,
            trades,
            daily,
        )
    stress = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_ticks": 2})}
    )
    stress_trades, stress_events, stress_audit = run_condition(
        view,
        cash_calendar,
        BacktestEngine(instrument.instrument.to_spec(), stress, classifier),
        "A_opening_follow",
        targets,
    )
    stress_daily = dict.fromkeys(targets, 0)
    for trade in stress_trades:
        stress_daily[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    stress_metrics = research_metrics(stress_trades, view.bars)
    write_condition(
        reserve_directory(OUT, "A_opening_follow_2tick"),
        "A_opening_follow_2tick",
        stress_trades,
        stress_events,
        stress_metrics,
        stress_audit,
        view,
        2,
        stress_daily,
    )
    results["A_opening_follow_2tick"] = {
        "trade_count": len(stress_trades),
        "metrics": stress_metrics,
        "execution_audit": stress_audit,
    }
    audits = {
        condition: path_audit(
            events_by[condition], trades_by[condition], baseline.execution.max_fill_delay_minutes
        )
        for condition in CONDITIONS
    } | {
        "A_opening_follow_2tick": path_audit(
            stress_events, stress_trades, baseline.execution.max_fill_delay_minutes
        )
    }
    shared_fields = (
        "trade_date",
        "pre_event_status",
        "reason",
        "O_points",
        "M_points",
        "E_planned_entry_jst",
        "X_planned_exit_jst",
    )
    shared = all(
        len(events_by[condition]) == len(events_by["A_opening_follow"])
        and all(
            tuple(row.get(field) for field in shared_fields)
            == tuple(events_by[condition][index].get(field) for field in shared_fields)
            for index, row in enumerate(events_by["A_opening_follow"])
        )
        for condition in CONDITIONS[1:]
    )
    diagnostics: dict[str, object] = {"O_sign_x_M_sign": {}}
    a_events, d_events = events_by["A_opening_follow"], events_by["D_preclose_follow"]
    for o_sign in ("positive", "negative"):
        for m_sign in ("positive", "negative"):
            selected = [
                row
                for row in a_events
                if row.get("pre_event_status") == "eligible"
                and (cast(int, row.get("O_points", 0)) > 0) == (o_sign == "positive")
                and (cast(int, row.get("M_points", 0)) > 0) == (m_sign == "positive")
            ]
            filled, net = (
                [row for row in selected if row.get("status") == "filled"],
                [
                    cast(int, row["net_pnl_jpy"])
                    for row in selected
                    if row.get("status") == "filled"
                ],
            )
            cast(dict[str, object], diagnostics["O_sign_x_M_sign"])[f"O_{o_sign}_M_{m_sign}"] = {
                "pre_event_count": len(selected),
                "trade_count": len(filled),
                "entry_delay_minutes_total": sum(
                    cast(int, row["entry_delay_minutes"]) for row in filled
                ),
                "exit_delay_minutes_total": sum(
                    cast(int, row["exit_delay_minutes"]) for row in filled
                ),
                "fees_jpy": sum(cast(int, row["fees_jpy"]) for row in filled),
                "net_pnl_jpy": sum(net),
                "expectancy_jpy": fmean(net) if net else None,
            }
    same, opposite = [], []
    for a, d in zip(a_events, d_events, strict=True):
        if a.get("pre_event_status") == "eligible":
            (
                same
                if (cast(int, a["O_points"]) > 0) == (cast(int, a["M_points"]) > 0)
                else opposite
            ).append((a, d))
    diagnostics["A_D_same_sign_O_M"] = {
        "count": len(same),
        "side_path_net_identical": all(
            a.get("side") == d.get("side") and a.get("net_pnl_jpy") == d.get("net_pnl_jpy")
            for a, d in same
        ),
        "A_minus_D_net_jpy": sum(
            cast(int, a.get("net_pnl_jpy", 0)) - cast(int, d.get("net_pnl_jpy", 0)) for a, d in same
        ),
    }
    diagnostics["A_D_opposite_sign_O_M"] = {
        "count": len(opposite),
        "A_minus_D_net_jpy": sum(
            cast(int, a.get("net_pnl_jpy", 0)) - cast(int, d.get("net_pnl_jpy", 0))
            for a, d in opposite
        ),
    }
    post = {
        "status": "PASS"
        if shared
        and all(cast(bool, audit["all_pass"]) for audit in audits.values())
        and cast(
            bool,
            cast(dict[str, object], diagnostics["A_D_same_sign_O_M"])["side_path_net_identical"],
        )
        and cast(
            int, cast(dict[str, object], diagnostics["A_D_same_sign_O_M"])["A_minus_D_net_jpy"]
        )
        == 0
        else "BLOCKED",
        "conditions": audits,
        "all_conditions_identical_pre_event": shared,
        "O_M_same_sign_A_equals_D": diagnostics["A_D_same_sign_O_M"],
        "O_M_opposite_sign_A_D_difference": diagnostics["A_D_opposite_sign_O_M"],
        "policy": "No successful-fill intersection is used; all axis no-trades are retained as zero.",
    }
    write_json(OUT / "post_execution_validation.json", post)
    write_json(OUT / "o_m_direction_diagnostics.json", diagnostics)
    values = {condition: [daily_by[condition][day] for day in targets] for condition in CONDITIONS}
    boot = bootstrap(values)
    months = {
        f"{year:04d}-{month:02d}": 0
        for year in range(2021, 2026)
        for month in range(1, 13)
        if (year, month) <= (2025, 6)
    }
    for day, amount in daily_by["A_opening_follow"].items():
        months[day[:7]] += amount
    a_metrics, a_overall, stress_overall = (
        cast(dict[str, Any], results["A_opening_follow"]["metrics"]),
        cast(
            dict[str, Any], cast(dict[str, Any], results["A_opening_follow"]["metrics"])["overall"]
        ),
        cast(dict[str, Any], stress_metrics["overall"]),
    )

    def lower(key: str) -> bool:
        return (
            cast(list[float], cast(dict[str, object], boot[key])["ci95_percentile_linear"])[0] > 0
        )

    group_gate = all(
        cast(int, item["pre_event_count"]) >= 50
        for item in cast(dict[str, dict[str, object]], diagnostics["O_sign_x_M_sign"]).values()
    )
    long_count = sum(trade.side.value == "long" for trade in trades_by["A_opening_follow"])
    gates = {
        "A_trade_count_at_least_200": len(trades_by["A_opening_follow"]) >= 200,
        "B_trade_count_at_least_200": len(trades_by["B_always_long"]) >= 200,
        "C_short_trade_count_at_least_200": len(trades_by["C_short"]) >= 200,
        "D_trade_count_at_least_200": len(trades_by["D_preclose_follow"]) >= 200,
        "F_reverse_trade_count_at_least_200": len(trades_by["F_reverse"]) >= 200,
        "A_long_at_least_50": long_count >= 50,
        "A_short_at_least_50": len(trades_by["A_opening_follow"]) - long_count >= 50,
        "each_O_sign_x_M_sign_pre_event_at_least_50": group_gate,
        "A_net_positive": a_overall["net_pnl_jpy"] > 0,
        "A_profit_factor_above_1": a_overall["profit_factor"] is not None
        and a_overall["profit_factor"] > 1,
        "A_bootstrap_lower_above_0": lower("A_daily_mean_net_jpy"),
        "A_minus_B_bootstrap_lower_above_0": lower("A_minus_B_daily_mean_net_jpy"),
        "A_minus_C_short_bootstrap_lower_above_0": lower("A_minus_C_short_daily_mean_net_jpy"),
        "A_minus_D_bootstrap_lower_above_0": lower("A_minus_D_daily_mean_net_jpy"),
        "A_minus_F_reverse_bootstrap_lower_above_0": lower("A_minus_F_reverse_daily_mean_net_jpy"),
        "A_2tick_expectancy_positive": stress_overall["expectancy_jpy"] is not None
        and stress_overall["expectancy_jpy"] > 0,
        "A_positive_months_at_least_27_of_54": sum(value > 0 for value in months.values()) >= 27,
        "A_net_excluding_top10_positive": cast(dict[str, Any], a_metrics["concentration"])[
            "net_excluding_top10_jpy"
        ]
        > 0,
    }
    info = [
        key
        for key in gates
        if "trade_count" in key
        or key
        in {
            "A_long_at_least_50",
            "A_short_at_least_50",
            "each_O_sign_x_M_sign_pre_event_at_least_50",
        }
    ]
    decision = (
        "BLOCKED"
        if post["status"] != "PASS"
        else "INCONCLUSIVE"
        if not all(gates[key] for key in info)
        else "DEVELOPMENT_PRIMARY_CONDITIONS_PASSED_PASS_LIMITED"
        if all(gates.values())
        else "REJECT"
    )
    write_json(
        OUT / "daily_net_pnl_aligned.json",
        {
            "trade_dates": targets,
            "no_trade": "0",
            "day_isolated": "excluded",
            "TSE_closed_or_missing_or_zero_or_unfilled": "0",
            **values,
        },
    )
    write_json(OUT / "bootstrap.json", boot)
    write_json(
        OUT / "development_results.json",
        {
            "campaign_id": IDENTIFIER,
            "quality_status": "PASS_LIMITED",
            "decision": decision,
            "gates": gates,
            "A_direction_counts": {
                "long": long_count,
                "short": len(trades_by["A_opening_follow"]) - long_count,
            },
            "positive_months_of_54": sum(value > 0 for value in months.values()),
            "monthly_A_net_jpy": months,
            "A_required_segments": a_metrics["segments"],
            "conditions": results,
            "accounting": "Gross fill-to-fill slippage-inclusive; Net=Gross-fees; no double deduction",
            "scope": "Development only; day only; 2025 Jan-Jun; no WFA/OOS/Final Holdout",
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
