"""Execute TASK-R098-Q001 once, using immutable R097 daily-net inputs only."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean
from subprocess import run
from typing import Any

import numpy as np
from run_r074_q001_low_night_range_opening_breakout import quarantine

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.r074_low_night_range_opening_breakout import (
    DEVELOPMENT_START,
    _night_range,
    nearest_rank,
    previous_calendar_eligible_nights,
)
from n225m_bt.research.r098_state_meta_selection import (
    BLOCK_LENGTH,
    BOOTSTRAP_REPETITIONS,
    BOOTSTRAP_SEED,
    CALIBRATION_DAYS,
    MAX_SCHEDULED_LOOKBACK_DAYS,
    STATE_LOOKBACK_OBSERVATIONS,
    STATES,
    StateSelectionRow,
    mbb_indices,
    percentile_ci,
    routed_daily_net,
    select_state_paths,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r098-q001-state-conditioned-cash-meta-selection-20260916-02"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = Path("docs/strategy/103_r098_q001_state_conditioned_cash_meta_selection.md")
TECHNICAL_REPAIR = Path("docs/strategy/104_r098_q001_state_meta_selection_technical_repair.md")
R097_FREEZE = (
    ROOT
    / "results"
    / "research"
    / "task-r097-q001-tse-cash-meta-selection-20260916-03"
    / "constituent_freeze_before_evaluation_pnl.json"
)

# This registry is deliberately byte-for-byte checked against R097 before any
# immutable daily-Net ledger is parsed.  It is not a new eligibility screen.
CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "strategy_id": "R078-A",
        "family_id": "cash_first_hour_extreme",
        "run": "r078-q001-20260915-cash-first-hour-extreme-fade-01",
        "daily": "extreme_fade_daily_axis.json",
        "trades": "extreme_fade_trades.json",
    },
    {
        "strategy_id": "R079-A",
        "family_id": "cash_first_hour_extreme",
        "run": "r079-q001-20260915-cash-first-hour-extreme-continuation-01",
        "daily": "extreme_continuation_daily_axis.json",
        "trades": "extreme_continuation_trades.json",
    },
    {
        "strategy_id": "R080-A",
        "family_id": "cash_first_hour_all",
        "run": "r080-q001-20260915-cash-first-hour-all-continuation-01",
        "daily": "continuation_daily_axis.json",
        "trades": "continuation_trades.json",
    },
    {
        "strategy_id": "R081-A",
        "family_id": "cash_lunch_direction",
        "run": "r081-q001-20260915-tse-lunch-continuation-01",
        "daily": "continuation_daily_axis.json",
        "trades": "continuation_trades.json",
    },
    {
        "strategy_id": "R084-A",
        "family_id": "cash_morning_compression",
        "run": "r084-q001-20260915-morning-compression-lunch-breakout-01",
        "daily": "compressed_breakout_daily_axis.json",
        "trades": "compressed_breakout_trades.json",
    },
    {
        "strategy_id": "R086-A",
        "family_id": "cash_intraday_shock",
        "run": "r086-q001-20260915-intraday-same-clock-shock-fade-01",
        "daily": "shock_fade_daily_axis.json",
        "trades": "shock_fade_trades.json",
    },
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def copy_snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for path in files:
        shutil.copy2(ROOT / path, destination / path.name)


def audit_bundle(candidate: dict[str, str]) -> dict[str, Any]:
    """Read only audit metadata and byte hashes before parsing PnL ledgers."""
    directory = ROOT / "results" / "research" / candidate["run"]
    required = [
        "COMPLETED.json",
        "pre_execution_validation.json",
        "execution_accounting_audit.json",
        candidate["daily"],
        candidate["trades"],
    ]
    exists = all((directory / name).is_file() for name in required)
    validation = read_json(directory / "pre_execution_validation.json") if exists else {}
    accounting = read_json(directory / "execution_accounting_audit.json") if exists else {}
    completed = read_json(directory / "COMPLETED.json") if exists else {}
    audit_values = list(accounting.values()) if isinstance(accounting, dict) else []
    eligible = bool(
        exists
        and validation.get("status") == "PASS"
        and bool(completed.get("run_id"))
        and audit_values
        and all(value is True for value in audit_values)
    )
    return {
        **candidate,
        "required_files_present": exists,
        "validation_status": validation.get("status"),
        "completed_marker_present": bool(completed.get("run_id")),
        "execution_accounting_all_checks_true": bool(audit_values)
        and all(value is True for value in audit_values),
        "daily_sha256": digest(directory / candidate["daily"]) if exists else None,
        "trades_sha256": digest(directory / candidate["trades"]) if exists else None,
        "eligible": eligible,
    }


def r097_hash_match(frozen: list[dict[str, Any]]) -> bool:
    if not R097_FREEZE.is_file():
        return False
    prior = read_json(R097_FREEZE)
    expected = {item["strategy_id"]: item for item in prior.get("included", [])}
    if set(expected) != {item["strategy_id"] for item in frozen}:
        return False
    fields = ("strategy_id", "family_id", "run", "daily", "trades", "daily_sha256", "trades_sha256")
    return all(
        all(item[field] == expected[item["strategy_id"]][field] for field in fields)
        for item in frozen
    )


def load_paths(
    frozen: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[int]], dict[str, dict[str, dict[str, Any]]]]:
    """Load immutable paths only after the R097-equivalence freeze has passed."""
    axes: dict[str, list[str]] = {}
    daily: dict[str, list[int]] = {}
    trades: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate in frozen:
        directory = ROOT / "results" / "research" / str(candidate["run"])
        raw_daily = read_json(directory / str(candidate["daily"]))
        raw_trades = read_json(directory / str(candidate["trades"]))
        if not isinstance(raw_daily, dict) or not isinstance(raw_trades, list):
            raise ValueError(f"{candidate['strategy_id']} immutable ledger has an invalid format")
        strategy_id = str(candidate["strategy_id"])
        axes[strategy_id] = list(raw_daily)
        daily[strategy_id] = [int(value) for value in raw_daily.values()]
        indexed = {str(trade["trade_date"]): trade for trade in raw_trades}
        if len(indexed) != len(raw_trades):
            raise ValueError(f"{strategy_id} has duplicate completed trades on a trade_date")
        for trade_date, trade in indexed.items():
            if int(trade["net_pnl_jpy"]) != raw_daily[trade_date]:
                raise ValueError(f"{strategy_id} immutable trade ledger does not reconcile")
            if int(trade["qty"]) != 1:
                raise ValueError(f"{strategy_id} violates one-contract eligibility")
        trades[strategy_id] = indexed
    axis = axes[sorted(axes)[0]]
    if len(axis) != len(set(axis)) or any(values != axis for values in axes.values()):
        raise ValueError("constituent scheduled TSE daily axes are not identical")
    if axis[0] < "2021-01-01" or axis[-1] != "2025-06-30":
        raise ValueError("constituent Development boundary does not match frozen requirement")
    return axis, daily, trades


def state_event(
    classifier: CalendarClassifier,
    target: date,
    bars_by_day: dict[date, list[Bar]],
    isolated: set[tuple[date, Session]],
) -> dict[str, Any]:
    """Build a complete R075 state ledger without inspecting DAY information."""
    event: dict[str, Any] = {
        "trade_date": target.isoformat(),
        "state_available_at_jst": f"{target.isoformat()}T08:45:00+09:00",
        "night_information_cutoff_jst": f"{target.isoformat()}T05:29:00+09:00",
        "history_count_required": 20,
        "max_calendar_lookback_days": 60,
        "quantile_method": "nearest-rank ceil(20*p/100)-1",
    }
    references = previous_calendar_eligible_nights(classifier, target)
    if references is None:
        event["state"] = None
        event["reason"] = "INSUFFICIENT_20_CALENDAR_ELIGIBLE_NIGHT_HISTORY_WITHIN_60_DAYS"
        return event
    event["reference_trade_dates_p1_to_p20"] = [item.isoformat() for item in references]
    if any(reference < DEVELOPMENT_START for reference in references):
        event["state"] = None
        event["reason"] = "REFERENCE_OUTSIDE_DEVELOPMENT"
        return event
    reference_ranges: list[int] = []
    for index, reference in enumerate(references, start=1):
        value, reason, _ = _night_range(
            classifier, reference, bars_by_day.get(reference, []), isolated=isolated
        )
        if value is None:
            event["state"] = None
            event["reason"] = f"REFERENCE_{reason}"
            event["reference_index"] = index
            return event
        reference_ranges.append(value)
    current, reason, _ = _night_range(
        classifier, target, bars_by_day.get(target, []), isolated=isolated
    )
    if current is None:
        event["state"] = None
        event["reason"] = f"CURRENT_{reason}"
        return event
    quantiles = {
        percentile: nearest_rank(reference_ranges, percentile)
        for percentile in (20, 25, 33, 67, 75, 80)
    }
    event.update(
        reference_ranges_points_p1_to_p20=reference_ranges,
        current_night_range_points=current,
        **{f"q{percentile}_points": value for percentile, value in quantiles.items()},
    )
    if quantiles[25] >= quantiles[75]:
        event["state"] = None
        event["reason"] = "STATE_DEGENERATE_Q25_GTE_Q75"
    elif current <= quantiles[25]:
        event["state"] = "L"
        event["reason"] = "STATE_AVAILABLE"
    elif current < quantiles[75]:
        event["state"] = "M"
        event["reason"] = "STATE_AVAILABLE"
    else:
        event["state"] = "H"
        event["reason"] = "STATE_AVAILABLE"
    return event


def states_from_events(events: list[dict[str, Any]], low: int, high: int) -> list[str | None]:
    """Apply a preregistered state threshold pair without accessing daily PnL."""
    states: list[str | None] = []
    for event in events:
        if "current_night_range_points" not in event:
            states.append(None)
            continue
        current = int(event["current_night_range_points"])
        low_q = int(event[f"q{low}_points"])
        high_q = int(event[f"q{high}_points"])
        if low_q >= high_q:
            states.append(None)
        elif current <= low_q:
            states.append("L")
        elif current < high_q:
            states.append("M")
        else:
            states.append("H")
    return states


def pf(values: list[int]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return None if losses == 0 else gains / losses


def rows_to_ledger(axis: list[str], rows: list[StateSelectionRow]) -> list[dict[str, Any]]:
    return [
        {
            "trade_date": axis[row.index],
            "state": row.state,
            "matching_prior_trade_dates": [axis[index] for index in row.matching_prior_indices],
            "selected_strategy_id": row.selected_strategy_id,
            "rank_two_strategy_id": row.rank_two_strategy_id,
            "static_strategy_id": row.static_strategy_id,
            "unconditional_strategy_id": row.unconditional_strategy_id,
            "state_scores": row.state_scores,
            "unconditional_scores": row.unconditional_scores,
        }
        for row in rows
    ]


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_task_r098_q001_state_conditioned_cash_meta_selection.py')

    if OUT.exists():
        raise FileExistsError(f"immutable run output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_task_r098_q001_state_conditioned_cash_meta_selection.py"),
        Path("scripts/run_task_r097_q001_tse_cash_meta_selection.py"),
        Path("scripts/run_r074_q001_low_night_range_opening_breakout.py"),
        Path("src/n225m_bt/research/r098_state_meta_selection.py"),
        Path("src/n225m_bt/research/r097_meta_selection.py"),
        Path("src/n225m_bt/research/r074_low_night_range_opening_breakout.py"),
        Path("tests/test_r098_state_meta_selection.py"),
        Path("tests/test_r097_meta_selection.py"),
        Path("tests/test_r075_q001.py"),
        TECHNICAL_REPAIR,
    ]
    config_files = [
        Path(f"config/{name}")
        for name in (
            "backtest.yaml",
            "data.yaml",
            "instrument.yaml",
            "sessions.yaml",
            "local_calendar.yaml",
            "research.yaml",
        )
    ]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    preregistration = {
        "task_id": "TASK-R098-Q001",
        "study_id": "R098-Q001",
        "family_id": "cash_meta_selection_night_range_state",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_EVALUATION_PNL",
        "preregistration_document": str(PREREG),
        "preregistration_document_sha256": digest(ROOT / PREREG),
        "prior_information_seen": True,
        "duplicate_review": "R075 state definition is reused; R097 unconditioned selection is a fixed comparator; no R001-R097 strategy rule is changed.",
        "constituents": [candidate["strategy_id"] for candidate in CANDIDATES],
        "state": "R075 complete official NIGHT high-low through 05:29, prior 20 calendar-eligible nights within 60 calendar days, current excluded, q25/q75 L/M/H, R004/incomplete/degenerate unavailable, available by 08:45.",
        "selection": "first 252 scheduled dates calibration; thereafter every known-state date is evaluable. Latest 60 matching-state observations in at most prior 360 scheduled dates, no-event daily Net=0; if fewer than 60 A/Q cash, otherwise strictly positive score only and frozen strategy-id tie-break.",
        "technical_repair_document": str(TECHNICAL_REPAIR),
        "technical_repair_document_sha256": digest(ROOT / TECHNICAL_REPAIR),
        "comparators": "U unconditioned strict-prior 120 scheduled-date R097 selection on common evaluable dates; Q same-state positive rank two; S state-specific positive calibration static; N cash.",
        "bootstrap": {
            "seed": BOOTSTRAP_SEED,
            "block_length_trade_dates": BLOCK_LENGTH,
            "repetitions": BOOTSTRAP_REPETITIONS,
            "method": "non-wrapping MBB; tail truncation; linear percentile; common indices",
        },
        "primary_gate": "A Net>0; PF>1; A daily Net CI lower>0; paired A-U, A-Q and A-S CI lower>0.",
        "sensitivities_after_primary_pass_only": [
            "same_state_40",
            "same_state_80",
            "q20_q80",
            "q33_q67",
            "base_path_cost_2tick",
            "base_path_fee_x2",
            "cost_3tick_diagnostic",
            "leave_one_family_out",
            "top10_winners_removed",
        ],
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(
        OUT / "run_manifest.json",
        {
            "run_id": RUN_ID,
            "preregistration_hash": canonical_hash(preregistration),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "S0_S1_FROZEN",
        },
    )
    copy_snapshot(OUT / "source_snapshot", source_files)
    copy_snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / PREREG, OUT / "documentation_snapshot" / PREREG.name)
    shutil.copy2(ROOT / TECHNICAL_REPAIR, OUT / "documentation_snapshot" / TECHNICAL_REPAIR.name)

    command_env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_r098_state_meta_selection.py",
            "tests/test_r097_meta_selection.py",
            "tests/test_r075_q001.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r098_state_meta_selection.py",
            str(source_files[0]),
        ],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        completed = run(
            command, cwd=ROOT, capture_output=True, text=True, check=False, env=command_env
        )
        validation[name] = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    validation["status"] = (
        "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(
            OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}
        )
        raise ValueError("pre-execution validation failed")

    frozen = [audit_bundle(candidate) for candidate in CANDIDATES]
    freeze = {
        "task_id": "TASK-R098-Q001",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_EVALUATION_PNL",
        "economic_performance_used_for_adoption": False,
        "r097_frozen_constituent_hashes_match": r097_hash_match(frozen),
        "included": frozen,
    }
    write_json(OUT / "constituent_freeze_before_evaluation_pnl.json", freeze)
    duplicate_review = {
        "reviewed_range": "R001-R097",
        "R075_relationship": "reuses the already frozen official-night range state only; no R075 trading rule is rerun or selected",
        "R097_relationship": "same frozen six audited cash-only ledgers; U is the specified R097-type unconditioned selection comparator",
        "conclusion": "POST_HOC_META_HYPOTHESIS_NOT_INDEPENDENT; DECISION_CEILING_INVESTIGATE",
        "new_constituents": False,
        "removed_constituents": False,
        "constituent_order": [candidate["strategy_id"] for candidate in CANDIDATES],
    }
    write_json(OUT / "duplicate_review_before_evaluation_pnl.json", duplicate_review)
    if (
        not all(item["eligible"] for item in frozen)
        or not freeze["r097_frozen_constituent_hashes_match"]
    ):
        write_json(
            OUT / "decision.json",
            {"status": "INCONCLUSIVE", "reason": "FROZEN_CONSTITUENT_OR_HASH_GATE_FAILED"},
        )
        write_json(
            OUT / "COMPLETED.json",
            {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"},
        )
        return

    _, sessions, _, _ = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars_by_day: dict[date, list[Bar]] = defaultdict(list)
    for bar in view.bars:
        if bar.session is Session.NIGHT:
            bars_by_day[bar.trade_date].append(bar)

    state_axis = [
        row.trade_date
        for row in calendar.trading_days()
        if date(2021, 1, 1) <= row.trade_date <= date(2025, 6, 30)
    ]
    events = [state_event(classifier, target, bars_by_day, isolated) for target in state_axis]
    state_reason_counts = Counter(str(event["reason"]) for event in events)
    known_unavailable = all(
        event["state"] in STATES
        or str(event["reason"]).startswith(
            ("INSUFFICIENT_", "REFERENCE_", "CURRENT_", "STATE_DEGENERATE_")
        )
        for event in events
    )
    strict_prior_state = all(
        all(
            reference < event["trade_date"]
            for reference in event.get("reference_trade_dates_p1_to_p20", [])
        )
        for event in events
    )
    state_ledger = {
        "state_definition": "R075 official NIGHT R=max(high)-min(low), session start through 05:29; current excluded from exactly 20 preceding calendar-eligible nights within 60 days; L/M/H q25/q75.",
        "availability": "state is determined from night information ending 05:29 and is available by 08:45 before any constituent event.",
        "quarantine": quarantine_audit,
        "events": events,
        "reason_counts": dict(sorted(state_reason_counts.items())),
        "strict_prior_references": strict_prior_state,
        "all_unavailable_reasons_explained": known_unavailable,
    }
    write_json(OUT / "state_ledger_before_evaluation_pnl.json", state_ledger)
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "constituent freeze then state-only availability then conditional immutable-ledger evaluation",
            "split": "development",
            "trade_date_filter": ["2021-01-01", "2025-06-30"],
            "physical_partitions": development.quality["partitions"],
            "data_version": development.data_version,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )

    axis, daily, trades = load_paths(frozen)
    if axis != [target.isoformat() for target in state_axis]:
        raise ValueError("R075 state and immutable constituent axes differ")
    states = states_from_events(events, 25, 75)
    rows, static_choices, unavailable = select_state_paths(daily, states)
    a, q, s, u = routed_daily_net(rows, daily)
    selection_ledger = rows_to_ledger(axis, rows)
    write_json(OUT / "selection_ledger.json", selection_ledger)
    write_json(
        OUT / "selection_unavailable_reasons.json",
        {axis[index]: reason for index, reason in unavailable.items()},
    )
    score_not_ready = {
        axis[row.index]: "INSUFFICIENT_MATCHING_STATE_OBSERVATIONS"
        for row in rows
        if not row.state_scores
    }
    write_json(OUT / "selection_score_not_ready_reasons.json", score_not_ready)
    selected_counts = Counter(
        row.selected_strategy_id for row in rows if row.selected_strategy_id is not None
    )
    selected_by_state = Counter(row.state for row in rows if row.selected_strategy_id is not None)
    completed_a_trades = [
        {
            "trade_date": axis[row.index],
            "state": row.state,
            "strategy_id": row.selected_strategy_id,
            **trades[row.selected_strategy_id][axis[row.index]],
        }
        for row in rows
        if row.selected_strategy_id is not None
        and axis[row.index] in trades[row.selected_strategy_id]
    ]
    trade_years = Counter(item["trade_date"][:4] for item in completed_a_trades)
    evaluation_state_counts = Counter(row.state for row in rows)
    strict_prior_scores = all(
        all(
            index < row.index and row.index - index <= MAX_SCHEDULED_LOOKBACK_DAYS
            for index in row.matching_prior_indices
        )
        and (len(row.matching_prior_indices) == STATE_LOOKBACK_OBSERVATIONS or not row.state_scores)
        for row in rows
    )
    unresolved = any(
        daily[strategy_id][row.index] != 0 and axis[row.index] not in trades[strategy_id]
        for row in rows
        for strategy_id in daily
    )
    pre_pnl_gate = {
        "six_constituents_and_hashes_match_r097": len(frozen) == 6
        and freeze["r097_frozen_constituent_hashes_match"],
        "common_daily_axis": True,
        "state_strict_prior": strict_prior_state and strict_prior_scores,
        "state_unavailable_reasons_fully_explained": known_unavailable
        and all(reason == "STATE_UNAVAILABLE" for reason in unavailable.values()),
        "evaluation_days_at_least_650": len(rows) >= 650,
        "each_l_m_h_evaluation_days_at_least_150": all(
            evaluation_state_counts[state] >= 150 for state in STATES
        ),
        "a_non_cash_selections_at_least_150": sum(
            row.selected_strategy_id is not None for row in rows
        )
        >= 150,
        "completed_a_trades_at_least_100": len(completed_a_trades) >= 100,
        "at_least_two_strategies_selected_at_least_20": sum(
            count >= 20 for count in selected_counts.values()
        )
        >= 2,
        "each_state_a_non_cash_selections_at_least_30": all(
            selected_by_state[state] >= 30 for state in STATES
        ),
        "2022_2024_each_completed_a_trades_at_least_20": all(
            trade_years[str(year)] >= 20 for year in range(2022, 2025)
        ),
        "2025_h1_completed_a_trades_at_least_10": trade_years["2025"] >= 10,
        "no_future_reference": strict_prior_state and strict_prior_scores,
        "no_same_day_availability_selection": True,
        "no_unexplained_exclusion": known_unavailable,
        "no_unresolved_filled_position": not unresolved,
        "passed": False,
        "counts": {
            "scheduled_axis": len(axis),
            "calibration_days": CALIBRATION_DAYS,
            "evaluation_days": len(rows),
            "evaluation_days_by_state": dict(sorted(evaluation_state_counts.items())),
            "a_non_cash_selections": sum(row.selected_strategy_id is not None for row in rows),
            "a_non_cash_selections_by_state": dict(sorted(selected_by_state.items())),
            "selected_counts": dict(sorted(selected_counts.items())),
            "completed_a_trades": len(completed_a_trades),
            "completed_a_trades_by_year": dict(sorted(trade_years.items())),
            "static_choices": static_choices,
            "state_reason_counts": dict(sorted(state_reason_counts.items())),
            "selection_unavailable_reason_counts": dict(
                sorted(Counter(unavailable.values()).items())
            ),
            "selection_score_not_ready_reason_counts": dict(
                sorted(Counter(score_not_ready.values()).items())
            ),
        },
    }
    pre_pnl_gate["passed"] = all(
        value is True for key, value in pre_pnl_gate.items() if key not in {"passed", "counts"}
    )
    write_json(OUT / "pre_pnl_gate.json", pre_pnl_gate)
    if not pre_pnl_gate["passed"]:
        write_json(
            OUT / "decision.json",
            {
                "status": "INCONCLUSIVE",
                "reason": "PRE_PNL_GATE_FAILED",
                "pre_pnl_gate": pre_pnl_gate,
                "oos": "NOT_ACCESSED",
                "walk_forward": "NOT_ACCESSED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
        write_json(
            OUT / "COMPLETED.json",
            {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"},
        )
        return

    write_json(OUT / "selected_A_trades.json", completed_a_trades)
    n = [0] * len(a)
    index = mbb_indices(len(a))
    np.save(OUT / "bootstrap_common_indices.npy", index)
    arrays = [np.asarray(path, dtype=float) for path in (a, u, q, s, n)]
    labels = (("A_minus_U", 0, 1), ("A_minus_Q", 0, 2), ("A_minus_S", 0, 3))
    bootstrap: dict[str, Any] = {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile; common index",
        "seed": BOOTSTRAP_SEED,
        "repetitions": BOOTSTRAP_REPETITIONS,
        "block_length_trade_dates": BLOCK_LENGTH,
        "A_daily_net_jpy": {
            "estimate": fmean(a),
            "ci95_percentile_linear": percentile_ci(arrays[0][index].mean(axis=1)),
        },
    }
    for label, left, right in labels:
        bootstrap[f"{label}_paired_daily_net_jpy"] = {
            "estimate": fmean(
                [x - y for x, y in zip((a, a, a)[left], (u, q, s)[right - 1], strict=True)]
            ),
            "ci95_percentile_linear": percentile_ci(
                (arrays[left][index] - arrays[right][index]).mean(axis=1)
            ),
        }
    write_json(OUT / "bootstrap.json", bootstrap)
    primary = {
        "net_pnl_jpy": sum(a),
        "profit_factor": pf(a),
        "a_daily_ci_lower": bootstrap["A_daily_net_jpy"]["ci95_percentile_linear"][0],
        "a_minus_u_ci_lower": bootstrap["A_minus_U_paired_daily_net_jpy"]["ci95_percentile_linear"][
            0
        ],
        "a_minus_q_ci_lower": bootstrap["A_minus_Q_paired_daily_net_jpy"]["ci95_percentile_linear"][
            0
        ],
        "a_minus_s_ci_lower": bootstrap["A_minus_S_paired_daily_net_jpy"]["ci95_percentile_linear"][
            0
        ],
        "net_by_state": {
            state: sum(value for row, value in zip(rows, a, strict=True) if row.state == state)
            for state in STATES
        },
    }
    primary_and = {
        "A_net_positive": primary["net_pnl_jpy"] > 0,
        "A_pf_gt_one": primary["profit_factor"] is not None and primary["profit_factor"] > 1,
        "A_daily_ci_lower_positive": primary["a_daily_ci_lower"] > 0,
        "A_minus_U_ci_lower_positive": primary["a_minus_u_ci_lower"] > 0,
        "A_minus_Q_ci_lower_positive": primary["a_minus_q_ci_lower"] > 0,
        "A_minus_S_ci_lower_positive": primary["a_minus_s_ci_lower"] > 0,
    }
    write_json(
        OUT / "primary_results.json",
        {"primary": primary, "primary_and": primary_and, "N_daily_net_jpy": n},
    )
    if not all(primary_and.values()):
        write_json(
            OUT / "decision.json",
            {
                "status": "REJECT",
                "reason": "PRIMARY_AND_FAILED",
                "decision_ceiling": "INVESTIGATE",
                "primary_and": primary_and,
                "oos": "NOT_ACCESSED",
                "walk_forward": "NOT_ACCESSED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
        write_json(
            OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "REJECT"}
        )
        return

    selected_trade_dates = {item["trade_date"] for item in completed_a_trades}
    sensitivity: dict[str, int] = {}
    for observation_count in (40, 80):
        alternate_rows, _, _ = select_state_paths(
            daily, states, state_observations=observation_count
        )
        sensitivity[f"same_state_observations_{observation_count}"] = sum(
            routed_daily_net(alternate_rows, daily)[0]
        )
    for low, high in ((20, 80), (33, 67)):
        alternate_rows, _, _ = select_state_paths(daily, states_from_events(events, low, high))
        sensitivity[f"state_q{low}_q{high}"] = sum(routed_daily_net(alternate_rows, daily)[0])
    sensitivity["base_path_cost_2tick"] = sum(
        value - (1000 if axis[row.index] in selected_trade_dates else 0)
        for row, value in zip(rows, a, strict=True)
    )
    sensitivity["base_path_fee_x2"] = sum(
        value - (60 if axis[row.index] in selected_trade_dates else 0)
        for row, value in zip(rows, a, strict=True)
    )
    sensitivity["cost_3tick_diagnostic"] = sum(
        value - (2000 if axis[row.index] in selected_trade_dates else 0)
        for row, value in zip(rows, a, strict=True)
    )
    for family in sorted({str(item["family_id"]) for item in frozen}):
        remaining = {
            item["strategy_id"]: daily[item["strategy_id"]]
            for item in frozen
            if item["family_id"] != family
        }
        family_rows, _, _ = select_state_paths(remaining, states)
        sensitivity[f"leave_out_{family}"] = sum(routed_daily_net(family_rows, remaining)[0])
    winners = sorted(
        (int(item["net_pnl_jpy"]) for item in completed_a_trades if int(item["net_pnl_jpy"]) > 0),
        reverse=True,
    )[:10]
    sensitivity["top10_winners_removed"] = sum(a) - sum(winners)
    by_year: dict[str, int] = defaultdict(int)
    for row, value in zip(rows, a, strict=True):
        by_year[axis[row.index][:4]] += value
    robustness = {
        "all_fixed_non_diagnostic_net_positive": all(
            value > 0 for key, value in sensitivity.items() if key != "cost_3tick_diagnostic"
        ),
        "all_l_m_h_net_positive": all(primary["net_by_state"][state] > 0 for state in STATES),
        "two_of_2022_2024_positive": sum(by_year[str(year)] > 0 for year in range(2022, 2025)) >= 2,
        "2025_h1_positive": by_year["2025"] > 0,
        "all_family_leave_out_positive": all(
            value > 0 for key, value in sensitivity.items() if key.startswith("leave_out_")
        ),
        "top10_winners_removed_positive": sensitivity["top10_winners_removed"] > 0,
    }
    write_json(
        OUT / "sensitivity_results.json",
        {
            "net_pnl_jpy": sensitivity,
            "A_net_by_year": dict(sorted(by_year.items())),
            "robustness": robustness,
        },
    )
    write_json(
        OUT / "decision.json",
        {
            "status": "INVESTIGATE",
            "reason": "DEVELOPMENT_REUSE_DECISION_CEILING"
            if all(robustness.values())
            else "ROBUSTNESS_FAILED_AFTER_PRIMARY_PASS",
            "decision_ceiling": "INVESTIGATE",
            "primary_and": primary_and,
            "robustness": robustness,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INVESTIGATE"}
    )


if __name__ == "__main__":
    main()
