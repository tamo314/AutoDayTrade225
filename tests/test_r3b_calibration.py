"""R3-B calibration tests use generated arrays only; no market artifact route exists."""

from __future__ import annotations

import numpy as np
import pytest

from n225m_bt.domain import Side
from n225m_bt.research.conditions import (
    ConditionSpecificationError,
    GateOperator,
    GateRequirement,
    assert_gate_satisfiable,
    shared_path_gross_witness,
)
from n225m_bt.research.r3b_calibration import (
    CalibrationPlan,
    evaluate_gate,
    synthetic_axis_outcomes,
)


def test_r031_impossible_and_is_rejected_and_shared_path_witness_is_coherent() -> None:
    requirements = tuple(
        GateRequirement(
            requirement_id=f"{side}-negative",
            path_id="shared",
            event_set_id="same-events",
            weighting_id="equal",
            side=side,
            metric_id="gross_pre_fee_mean_jpy",
            slippage_ticks_per_side=0,
            fee_jpy_per_side=0,
            operator=GateOperator.LESS_THAN,
            threshold=0,
        )
        for side in (Side.LONG, Side.SHORT)
    )
    with pytest.raises(ConditionSpecificationError, match="unsatisfiable"):
        assert_gate_satisfiable(requirements)
    witness = shared_path_gross_witness((5, 10, -5), 100)
    assert witness["long_gross_pre_fee_mean_jpy"] == -witness["short_gross_pre_fee_mean_jpy"]


def test_gross_zero_and_net_zero_are_distinct_under_the_same_cost_model() -> None:
    plan = CalibrationPlan(axis_length=160, repetitions=2, bootstrap_repetitions=20, block_length=10)
    gross_a, gross_c, _, _ = synthetic_axis_outcomes("gross_zero", plan, np.random.default_rng(1))
    net_a, net_c, _, _ = synthetic_axis_outcomes("net_zero", plan, np.random.default_rng(1))
    assert gross_a[gross_a != 0].mean() < 0
    assert gross_c[gross_c != 0].mean() < 0
    assert abs(net_a[net_a != 0].mean()) < plan.cost_jpy_round_trip
    assert abs(net_c[net_c != 0].mean()) < plan.cost_jpy_round_trip


def test_common_axis_gate_requires_information_and_both_primary_conditions() -> None:
    plan = CalibrationPlan(axis_length=100, repetitions=2, bootstrap_repetitions=20, block_length=10)
    result = evaluate_gate(
        np.full(100, 100.0),
        np.zeros(100),
        a_trade_count=69,
        c_trade_count=70,
        plan=plan,
        rng=np.random.default_rng(3),
    )
    assert result.identified is False and result.passed is False
