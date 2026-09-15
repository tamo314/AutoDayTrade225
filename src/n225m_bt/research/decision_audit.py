"""Synthetic-only binding for the R1 decision-time audit path.

This module is deliberately not a backtest runner.  It accepts already
constructed, in-memory synthetic decision events, freezes selections, and
records injected execution observations separately.  In particular it never
imports the production data loader or :class:`BacktestEngine`.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from n225m_bt.research.execution_ledger import (
    ExecutionRecord,
    ExecutionStatus,
    SelectionRecord,
    freeze_selection,
    record_execution,
)


class ProcessingProfile(StrEnum):
    """Profiles are explicit so R065 cannot block unrelated R1 work."""

    R1_SYNTHETIC_DECISION = "r1_synthetic_decision"
    R065_SHARED_ENGINE = "r065_shared_engine"


class SyntheticAccessDeniedError(PermissionError):
    """An input attempted to enter the synthetic path through a market route."""


class UnknownProcessingProfileError(ValueError):
    """Fail closed instead of silently selecting an execution profile."""


ADAPTER_BINDINGS: dict[str, dict[str, object]] = {
    "R046": {"callable": "r046_exec_event", "u": "prior W0", "mock": False},
    "R049": {"callable": "r049_exec_candidate", "u": "anchor-local prior observations", "mock": False},
    "R060": {"callable": "r060_exec_event", "u": "prior day range", "mock": False},
    "R061": {"callable": "r061_exec_event", "u": "prior compression windows", "mock": False},
    "R062": {"callable": "r062_exec_event", "u": "prior night observations", "mock": False},
    "R063": {"callable": "r063_exec_event", "u": "position-specific rolling U", "mock": False},
    "R064": {"callable": "r064_exec_event", "u": "window-specific rolling U", "mock": False},
}


def assert_synthetic_input(source_kind: str, source_name: str) -> None:
    """Reject data-like and protected-split routes before any read occurs."""
    forbidden = ("raw", "silver", "gold", "parquet", "out_of_sample", "final_holdout", "legacy")
    normalized = f"{source_kind}/{source_name}".casefold()
    if source_kind != "AUDIT_SYNTHETIC" or any(token in normalized for token in forbidden):
        raise SyntheticAccessDeniedError("synthetic decision audit rejects non-synthetic input route")


def resolve_profile(profile: str) -> dict[str, object]:
    """Return profile-scoped capability without a fallback profile."""
    if profile == ProcessingProfile.R1_SYNTHETIC_DECISION:
        return {
            "scope": "R1",
            "status": "SUPPORTED_SYNTHETIC_DECISION_ONLY",
            "reason": "does not invoke a market loader or execution engine",
            "blocked_dependency": None,
        }
    if profile == ProcessingProfile.R065_SHARED_ENGINE:
        return {
            "scope": "R065",
            "status": "BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED",
            "reason": "shared engine lacks calendar-open and unresolved-position capabilities",
            "blocked_dependency": "separate_r065_executor",
        }
    raise UnknownProcessingProfileError(f"unknown processing profile: {profile}")


def freeze_events(study_id: str, events: Iterable[Mapping[str, object]]) -> tuple[SelectionRecord, ...]:
    """Freeze each E_exec decision; duplicate selected order intents are rejected."""
    selections: list[SelectionRecord] = []
    scheduled_entries: set[tuple[str, str]] = set()
    for event in events:
        selection = freeze_selection(study_id, event)
        if selection.execution_status_at_decision is ExecutionStatus.SCHEDULED:
            key = (selection.trade_date.isoformat(), selection.planned_entry_jst or "")
            if key in scheduled_entries:
                raise ValueError("duplicate scheduled order intent")
            scheduled_entries.add(key)
        selections.append(selection)
    return tuple(selections)


def bind_adapter_event(study_id: str, adapter_id: str, event: Mapping[str, object]) -> SelectionRecord:
    """Bind an actual additive adapter result to the immutable selection ledger.

    This small seam intentionally accepts only an adapter listed in the seven
    R1 bindings and an ``E_EXEC`` result; it cannot turn a legacy ``E`` record
    into an execution decision or invent an order from a skipped event.
    """
    if adapter_id not in ADAPTER_BINDINGS:
        raise ValueError("unregistered decision adapter")
    return freeze_events(study_id, (event,))[0]


def bind_adapter_events(
    study_id: str, events_by_adapter: Mapping[str, Mapping[str, object]]
) -> dict[str, SelectionRecord]:
    """Freeze one concrete E_exec result per registered adapter.

    Adapter identity is retained in the returned mapping; no common-E/legacy
    event may be silently substituted for an execution-time decision.
    """
    if set(events_by_adapter) != set(ADAPTER_BINDINGS):
        raise ValueError("binding requires exactly the seven registered decision adapters")
    return {
        adapter_id: bind_adapter_event(study_id, adapter_id, event)
        for adapter_id, event in events_by_adapter.items()
    }


def inject_execution(
    selections: Iterable[SelectionRecord],
    observations: Iterable[tuple[int, ExecutionStatus, str]],
) -> tuple[ExecutionRecord, ...]:
    """Append explicitly injected test observations without changing selections."""
    frozen = tuple(selections)
    records: list[ExecutionRecord] = []
    for index, status, reason in observations:
        if index < 0 or index >= len(frozen):
            raise ValueError("execution observation references an unknown selection")
        records.append(record_execution(frozen[index], status, f"INJECTED_TEST_EVENT:{reason}"))
    return tuple(records)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_synthetic_decision_audit(
    output: Path,
    *,
    audit_id: str,
    source_files: Iterable[Path],
    fixture_files: Iterable[Path],
    commit: str,
) -> Path:
    """Write an exclusive, no-market-data audit skeleton for measured tests.

    The caller supplies only code and synthetic fixture paths.  The output is
    intentionally marked ``NOT_RUN`` until the associated pytest node IDs are
    executed; writing an audit directory is not evidence of a pipeline pass.
    """
    assert_synthetic_input("AUDIT_SYNTHETIC", "in_memory_fixture")
    output.mkdir(parents=True, exist_ok=False)
    source = {str(path): _sha256(path) for path in source_files}
    fixtures = {str(path): _sha256(path) for path in fixture_files}
    manifest = {
        "audit_id": audit_id,
        "scope": "R1 synthetic decision pipeline only",
        "input_type": "AUDIT_SYNTHETIC",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "commit": commit,
        "environment": {"python": sys.version, "platform": platform.platform()},
        "source_sha256": source,
        "fixture_sha256": fixtures,
        "market_data_access": False,
    }
    files: dict[str, object] = {
        "audit_manifest": manifest,
        "scope_and_dependencies": {
            "R1": {"scope": "common synthetic decision binding", "status": "NOT_RUN"},
            "R2": {"status": "NOT_RUN", "reason": "data meaning is out of scope"},
            "R3-A": {"status": "NOT_RUN", "reason": "R032 ledger audit is out of scope"},
            "R065": resolve_profile(ProcessingProfile.R065_SHARED_ENGINE),
        },
        "runner_binding_matrix": {"bindings": ADAPTER_BINDINGS, "status": "NOT_RUN"},
        "access_ledger": {
            "allowed": ["AUDIT_SYNTHETIC in-memory fixture", "code/config hashes"],
            "denied_before_read": ["raw", "silver", "gold", "OOS", "Final Holdout", "legacy run"],
            "physical_market_data_read": False,
            "status": "NOT_RUN",
        },
        "spec_audit": {"status": "NOT_RUN", "reason": "use research audit-spec for static audit"},
        "condition_resolution": {"status": "NOT_RUN"},
        "gate_witnesses": {"status": "NOT_RUN"},
        "causality_audit": {"status": "NOT_RUN", "required_ids": [f"RI-{n:02d}" for n in range(3, 10)]},
        "regression_impact": {"status": "NOT_RUN", "legacy_contracts_mutated": False},
        "test_results": {"status": "NOT_RUN", "reason": "run the dedicated synthetic pytest nodes"},
    }
    for name, value in files.items():
        (output / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
    (output / "summary.md").write_text(
        "# R1 synthetic decision audit\n\n"
        "Status: NOT_RUN. This directory is an exclusive audit shell, not a PASS. "
        "It has not opened market data or invoked BacktestEngine.\n",
        encoding="utf-8",
    )
    return output
