"""Synthetic R1 regression tests; no market-data files are read."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from n225m_bt.config import JST
from n225m_bt.domain import Side
from n225m_bt.research.causality import (
    DecisionSnapshot,
    FutureInformationLeakError,
    assert_prefix_invariant,
)
from n225m_bt.research.conditions import (
    ConditionDefinition,
    ConditionSpecificationError,
    GateOperator,
    GateRequirement,
    SetRelation,
    assert_gate_satisfiable,
    validate_condition_matrix,
)
from n225m_bt.research.execution_ledger import (
    ExecutionStatus,
    freeze_selection,
    record_execution,
)
from n225m_bt.research.governance import (
    AccessDeniedError,
    AccessRequest,
    MarketSplit,
    ResearchStage,
    StudyIdentity,
    authorize_market_access,
)
from n225m_bt.research.outcomes import (
    OutcomeState,
    ScheduledOutcome,
    summarize_scheduled_axis,
)
from n225m_bt.research.reconciliation import (
    R032_DISPLAYED_CONFIRMATION_METRICS,
    R032_STATED_DIFFERENCE,
    ReconciliationStatus,
    assess_comparison,
)

IDENTITY = StudyIdentity("opening_family", "R1-SYNTH", "v1", "run-001", "RG-20260915-01")


def snapshot(*, analysis: str = "matched", order: str = "order-a") -> DecisionSnapshot:
    moment = datetime(2025, 6, 30, 9, 15, tzinfo=JST)
    return DecisionSnapshot(
        "r046-main", moment, {"references": 60}, {"eligible": True}, {"side": "long"},
        {"id": order}, {"paired": analysis}, {"state": "unobserved"},
    )


def test_m01_shared_path_opposite_side_negative_gate_is_rejected() -> None:
    base = dict(
        path_id="same-event-path", event_set_id="B", weighting_id="uniform",
        metric_id="gross_pre_fee_mean_jpy", slippage_ticks_per_side=0,
        fee_jpy_per_side=0, operator=GateOperator.LESS_THAN, threshold=0,
    )
    requirements = (
        GateRequirement("long-negative", side=Side.LONG, **base),
        GateRequirement("short-negative", side=Side.SHORT, **base),
    )
    with pytest.raises(ConditionSpecificationError, match="unsatisfiable"):
        assert_gate_satisfiable(requirements)


def test_condition_matrix_rejects_implicit_parent_and_preserves_explicit_side_quantile() -> None:
    with pytest.raises(ConditionSpecificationError, match="requires an explicit parent"):
        validate_condition_matrix((ConditionDefinition("A", Side.LONG, 75, SetRelation.SUBSET, None, True),))
    validate_condition_matrix(
        (
            ConditionDefinition("A", Side.LONG, 90, SetRelation.ROOT, None, True),
            ConditionDefinition("A_fade", Side.SHORT, None, SetRelation.EQUAL, "A", True),
        )
    )


def test_m02_to_m04_future_analysis_mutation_preserves_decision_prefix() -> None:
    before = (snapshot(),)
    after = (snapshot(analysis="exit-missing"),)
    assert_prefix_invariant(before, after, before[0].decision_at)
    with pytest.raises(FutureInformationLeakError, match="order"):
        assert_prefix_invariant(before, (snapshot(order="removed"),), before[0].decision_at)


def test_stage_guard_keeps_holdout_locked_and_oos_fail_closed() -> None:
    with pytest.raises(AccessDeniedError, match="Final Holdout"):
        authorize_market_access(
            AccessRequest(IDENTITY, ResearchStage.S6_OOS, MarketSplit.FINAL_HOLDOUT, "spec-hash")
        )
    with pytest.raises(AccessDeniedError, match="requires S6"):
        authorize_market_access(
            AccessRequest(IDENTITY, ResearchStage.S3_DEVELOPMENT, MarketSplit.OOS, "spec-hash")
        )
    authorize_market_access(
        AccessRequest(
            IDENTITY, ResearchStage.S6_OOS, MarketSplit.OOS, "spec-hash", "access-plan", True
        )
    )


def test_scheduled_axis_keeps_unknown_pnl_null() -> None:
    axis = (date(2025, 6, 27), date(2025, 6, 30))
    summary = summarize_scheduled_axis(
        "axis-hash",
        axis,
        (
            ScheduledOutcome(axis[0], OutcomeState.KNOWN_NO_TRADE, 0),
            ScheduledOutcome(axis[1], OutcomeState.OPEN_POSITION, None),
        ),
    )
    assert summary.partial_observed_net_jpy == 0
    assert summary.complete_net_jpy is None and summary.unknown_days == 1
    with pytest.raises(ValueError, match="must cover exactly"):
        summarize_scheduled_axis("axis-hash", axis, (ScheduledOutcome(axis[0], OutcomeState.REALIZED, 1),))


def test_selection_record_is_immutable_when_execution_is_later_filled() -> None:
    event = {
        "status": "E_EXEC",
        "trade_date": "2025-06-30",
        "selection_status": "A",
        "execution_status": "scheduled",
        "planned_entry_jst": "2025-06-30T09:31:00+09:00",
    }
    selection = freeze_selection("R1-SYNTH", event)
    execution = record_execution(selection, ExecutionStatus.FILLED, "next_eligible_open")
    assert selection.selection_status == execution.selection.selection_status == "A"
    overwritten = {**event, "selection_status": "filled"}
    with pytest.raises(ValueError, match="must not be overwritten"):
        freeze_selection("R1-SYNTH", overwritten)


def test_r032_displayed_per_trade_means_are_not_the_daily_bootstrap_estimand() -> None:
    finding = assess_comparison(R032_DISPLAYED_CONFIRMATION_METRICS, R032_STATED_DIFFERENCE)
    assert finding.status is ReconciliationStatus.RECONCILIATION_REQUIRED
    assert set(finding.reasons) >= {"unit differs", "population differs", "aggregation differs"}
