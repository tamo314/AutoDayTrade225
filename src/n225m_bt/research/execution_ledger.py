"""Immutable boundary between an R1 selection record and its later execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class ExecutionStatus(StrEnum):
    """Observed order state; it must never replace a selection state."""

    SCHEDULED = "scheduled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SelectionRecord:
    """A frozen decision-time record before any fill or outcome is known."""

    study_id: str
    trade_date: date
    selection_status: str
    execution_status_at_decision: ExecutionStatus
    planned_entry_jst: str | None


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """A later execution observation linked to, not merged into, selection."""

    selection: SelectionRecord
    execution_status: ExecutionStatus
    reason: str


def freeze_selection(study_id: str, event: Mapping[str, object]) -> SelectionRecord:
    """Validate and freeze an adapter's E_exec decision without reading prices."""
    if not study_id:
        raise ValueError("study_id is required")
    if event.get("status") != "E_EXEC":
        raise ValueError("only E_EXEC events may enter the execution ledger")
    raw_selection = event.get("selection_status")
    raw_execution = event.get("execution_status")
    if not isinstance(raw_selection, str) or not raw_selection:
        raise ValueError("E_EXEC event requires an explicit selection_status")
    if raw_selection in {item.value for item in ExecutionStatus}:
        raise ValueError("selection_status must not be overwritten by an execution status")
    if not isinstance(raw_execution, str):
        raise ValueError("E_EXEC event requires an explicit execution_status")
    try:
        at_decision = ExecutionStatus(raw_execution)
    except ValueError as exc:
        raise ValueError("unknown execution_status") from exc
    planned_entry = event.get("planned_entry_jst")
    if planned_entry is not None and not isinstance(planned_entry, str):
        raise ValueError("planned_entry_jst must be string or null")
    if raw_selection == "not_selected" and at_decision is ExecutionStatus.SCHEDULED:
        raise ValueError("not-selected event cannot be scheduled")
    if at_decision is ExecutionStatus.SCHEDULED and planned_entry is None:
        raise ValueError("scheduled event requires planned_entry_jst")
    raw_date = event.get("trade_date")
    if not isinstance(raw_date, str):
        raise ValueError("E_EXEC event requires trade_date")
    try:
        trade_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError("trade_date must be ISO date") from exc
    return SelectionRecord(study_id, trade_date, raw_selection, at_decision, planned_entry)


def record_execution(
    selection: SelectionRecord, execution_status: ExecutionStatus, reason: str
) -> ExecutionRecord:
    """Attach an execution outcome while preserving the original selection."""
    if not reason:
        raise ValueError("execution reason is required")
    if selection.selection_status == "not_selected" and execution_status is ExecutionStatus.FILLED:
        raise ValueError("not-selected event cannot be filled")
    return ExecutionRecord(selection, execution_status, reason)
