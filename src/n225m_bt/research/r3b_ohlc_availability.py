"""Development-only, non-PnL S2 diagnostics for the 225Labo OHLC scope.

This module intentionally has no execution-engine, trade, return, or metric
dependency.  It reads only the fixed Development partitions and the columns
declared below, produces aggregate availability counts, and keeps post-signal
entry/exit observability separate from ``E_exec``.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import polars as pl

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r064 import candidate_rows, r064_exec_event

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
S2_COLUMNS = (
    "ts_jst",
    "trade_date",
    "calendar_date",
    "session",
    "schedule_version",
    "open",
    "high",
    "low",
    "close",
    "is_eligible",
    "quality_flags",
    "instrument",
    "series_type",
    "source",
)


class OhlcAvailabilityError(ValueError):
    """The limited S2 input does not satisfy its fixed contract."""


def development_paths(gold_root: Path) -> list[Path]:
    """Return only calendar partitions that can contain the allowed dates.

    The day-only study has no preceding-session warm-up, so December 2020 and
    every OOS/holdout partition are intentionally absent from the scan list.
    """
    paths: list[Path] = []
    for year in range(DEVELOPMENT_START.year, DEVELOPMENT_END.year + 1):
        last_month = DEVELOPMENT_END.month if year == DEVELOPMENT_END.year else 12
        for month in range(1, last_month + 1):
            path = gold_root / f"year={year}" / f"month={month:02d}" / "bars.parquet"
            if path.is_file():
                paths.append(path)
    if not paths:
        raise OhlcAvailabilityError(f"no Development Parquet partitions under {gold_root}")
    return paths


def _scheduled_days(calendar: ExchangeCalendar) -> list[date]:
    days = [
        item.trade_date
        for item in calendar.trading_days()
        if DEVELOPMENT_START <= item.trade_date <= DEVELOPMENT_END
    ]
    if not days:
        raise OhlcAvailabilityError("calendar has no scheduled Development trade dates")
    return days


def _bars(frame: pl.DataFrame) -> dict[date, list[Bar]]:
    if set(S2_COLUMNS) != set(frame.columns):
        raise OhlcAvailabilityError("S2 frame columns differ from the declared read contract")
    if frame.is_empty():
        raise OhlcAvailabilityError("S2 scan returned no Development rows")
    for column, required in (("instrument", "N225M"), ("series_type", "center_continuous"), ("source", "225labo")):
        if frame[column].unique().to_list() != [required]:
            raise OhlcAvailabilityError(f"S2 requires {column}={required}")
    if frame["ts_jst"].dtype != pl.Datetime("us", "Asia/Tokyo"):
        raise OhlcAvailabilityError("S2 requires timezone-aware Asia/Tokyo timestamps")
    duplicates = frame.filter(pl.col("ts_jst").is_duplicated())
    if not duplicates.is_empty():
        conflicts = duplicates.group_by("ts_jst").agg(pl.struct(S2_COLUMNS).n_unique().alias("n"))
        if conflicts.filter(pl.col("n") > 1).height:
            raise OhlcAvailabilityError("conflicting duplicate timestamp in S2 input")
        frame = frame.unique(subset=["ts_jst"], keep="first", maintain_order=True)
    grouped: dict[date, list[Bar]] = defaultdict(list)
    for row in frame.sort("ts_jst").iter_rows(named=True):
        stamp = cast(Any, row["ts_jst"])
        calendar_date = cast(date, row["calendar_date"])
        if stamp.date() != calendar_date:
            raise OhlcAvailabilityError("ts_jst and calendar_date disagree")
        target = cast(date, row["trade_date"])
        if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
            raise OhlcAvailabilityError("S2 frame contains a protected trade_date")
        if row["session"] != Session.DAY.value:
            continue
        grouped[target].append(
            Bar(
                ts_jst=stamp,
                trade_date=target,
                calendar_date=calendar_date,
                session=Session.DAY,
                schedule_version=cast(str, row["schedule_version"]),
                open=cast(int, row["open"]),
                high=cast(int, row["high"]),
                low=cast(int, row["low"]),
                close=cast(int, row["close"]),
                is_eligible=cast(bool, row["is_eligible"]),
                quality_flags=tuple(cast(list[str], row["quality_flags"])),
            )
        )
    return grouped


def diagnose_frame(
    frame: pl.DataFrame, classifier: CalendarClassifier, scheduled_days: list[date]
) -> dict[str, object]:
    """Count only decision-time candidates; never calculate an outcome."""
    fingerprint_buffer = BytesIO()
    frame.sort("ts_jst").write_ipc(fingerprint_buffer, compression="uncompressed")
    bars_by_day = _bars(frame)
    candidates = candidate_rows(classifier, scheduled_days, bars_by_day, set())
    by_day = {date.fromisoformat(cast(str, item["trade_date"])): item for item in candidates}
    selection_reasons: Counter[str] = Counter()
    cells: Counter[str] = Counter()
    post_decision: Counter[str] = Counter()
    e_exec = 0
    for target in scheduled_days:
        event = r064_exec_event(
            classifier,
            target,
            bars_by_day.get(target),
            set(),
            by_day[target],
            observation_window=60,
            extreme_quantile=75,
            pullback_low=0.20,
            pullback_high=0.50,
        )
        reason = str(event.get("reason", "UNCLASSIFIED"))
        selection_reasons[reason] += 1
        if event.get("status") != "E_EXEC":
            continue
        e_exec += 1
        cell = str(event["cell"])
        cells[cell] += 1
        if cell not in {"A", "C"}:
            continue
        start = classifier.session_open(target, Session.DAY)
        # These checks happen after the order decision and are diagnostic only.
        # Their prices are never read into a return, fill, or PnL calculation.
        by_time = {bar.ts_jst: bar for bar in bars_by_day.get(target, [])}
        entry = by_time.get(start + timedelta(minutes=75))
        exit_ = by_time.get(start + timedelta(minutes=105))
        entry_ok = bool(entry is not None and entry.is_eligible)
        exit_ok = bool(exit_ is not None and exit_.is_eligible)
        post_decision["entry_observable" if entry_ok else "entry_unobservable"] += 1
        post_decision["exit_observable" if exit_ok else "exit_unobservable"] += 1
        post_decision["entry_and_exit_observable" if entry_ok and exit_ok else "incomplete_outcome_path"] += 1
    return {
        "status": "COMPLETE_NON_PNL_AVAILABILITY_ONLY",
        "study": "R3B-R064-MR-01",
        "market_output_prohibited": ["return", "gross_pnl", "net_pnl", "win_rate", "performance_ranking"],
        "scheduled_axis": {
            "trade_date_start": DEVELOPMENT_START.isoformat(),
            "trade_date_end": DEVELOPMENT_END.isoformat(),
            "source": "version_controlled_exchange_calendar",
            "scheduled_trade_dates": len(scheduled_days),
        },
        "input_projection_sha256": sha256(fingerprint_buffer.getvalue()).hexdigest(),
        "decision_time": {
            "feature": "first 60 scheduled day-session 1m bars plus next 15 response bars",
            "U": "previous 120 scheduled day dates; at least 100 valid first-60-bar observations; no backfill",
            "E_exec": "U, first 75 scheduled day bars, and their causal eligibility only",
            "e_exec_classified_dates": e_exec,
            "selection_reason_counts": dict(sorted(selection_reasons.items())),
            "cell_counts": dict(sorted(cells.items())),
            "main_candidate_counts": {"A": cells["A"], "C": cells["C"]},
        },
        "post_decision_observability": {
            "purpose": "separate later entry/exit availability diagnostic; does not alter E_exec",
            "counts": dict(sorted(post_decision.items())),
        },
    }


def write_s2_diagnostic(output: Path, config_dir: Path) -> Path:
    """Write an exclusive aggregate artifact after the permitted Development scan."""
    if output.exists():
        raise OhlcAvailabilityError(f"output already exists: {output}")
    _, sessions, data_config, _ = load_project_config(config_dir)
    calendar_path = config_dir / "local_calendar.yaml"
    calendar = ExchangeCalendar.from_path(calendar_path)
    classifier = CalendarClassifier(sessions, calendar)
    paths = development_paths(data_config.gold_root)
    frame = (
        pl.scan_parquet(paths, hive_partitioning=False)
        .filter(pl.col("trade_date").is_between(DEVELOPMENT_START, DEVELOPMENT_END))
        .select(S2_COLUMNS)
        .collect()
    )
    diagnostic = diagnose_frame(frame, classifier, _scheduled_days(calendar))
    output.mkdir(parents=True)
    tracked_files = [
        Path(__file__),
        config_dir / "data.yaml",
        config_dir / "instrument.yaml",
        config_dir / "backtest.yaml",
        config_dir / "sessions.yaml",
        calendar_path,
        Path("docs/strategy/20_225labo_only_scope_and_s2.md"),
    ]
    file_hashes = {
        str(path): sha256(path.read_bytes()).hexdigest() for path in tracked_files
    }
    (output / "run_manifest.json").write_text(
        json.dumps(
            {
                "audit_id": output.name,
                "study": "R3B-R064-MR-01",
                "stage": "S2",
                "protocol_revision": "RG-20260915-01",
                "preregistration": "docs/strategy/20_225labo_only_scope_and_s2.md",
                "implementation_hashes": file_hashes,
                "implementation_hash": sha256(
                    json.dumps(file_hashes, sort_keys=True).encode()
                ).hexdigest(),
                "random_seed": None,
                "market_result_generation": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "access_ledger.json").write_text(
        json.dumps(
            {
                "stage": "S2",
                "split": "development",
                "trade_date_filter": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
                "physical_partitions": [str(path) for path in paths],
                "columns_read": list(S2_COLUMNS),
                "excluded": ["volume", "OOS", "Final Holdout", "returns", "PnL", "trade ledgers"],
                "whole_period_cache_used": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "availability.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "COMPLETED.json").write_text(
        json.dumps({"status": diagnostic["status"], "stage": "S2"}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return output
