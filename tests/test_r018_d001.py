from __future__ import annotations

import math

from n225m_bt.research.r018 import (
    P2_SUFFIX,
    augmented_replicate,
    p2_daily,
    p2_linear,
    simultaneous_q,
    upper_bound_decision,
)


def test_p2_preserves_no_trade_days_varied_costs_and_long_short_accounting() -> None:
    # Day one represents a long result, day two a short result, and day three no trade.
    p0 = [1_000, -2_000, 0]
    fees = [60, 120, 0]
    slippage = [1_000, 2_000, 0]
    p1 = [value - fee - slip for value, fee, slip in zip(p0, fees, slippage, strict=True)]
    p2 = [p2_daily(a, b, c, d) for a, b, c, d in zip(p0, p1, fees, slippage, strict=True)]
    assert p2 == [-1_060, -6_120, 0]
    assert all(math.isclose(value, p2_linear(a, b)) for value, a, b in zip(p2, p0, p1, strict=True))


def test_augmented_replicate_keeps_correspondence_and_linear_means() -> None:
    names = tuple(
        f"R0{rule}_{suffix}"
        for rule in range(11, 16)
        for suffix in ("P0_A", "P1_A", "P0_A_minus_B", "P0_A_minus_C")
    )
    row = {name: float(index) for index, name in enumerate(names)}
    augmented = augmented_replicate(row, names)
    assert len(augmented) == 25
    assert augmented["R011_P2_A"] == p2_linear(row["R011_P0_A"], row["R011_P1_A"])
    assert set(augmented) == set(names) | {f"R0{rule}_{P2_SUFFIX}" for rule in range(11, 16)}


def test_25_series_max_deviation_cannot_reduce_q_and_upper_bound_is_strict() -> None:
    means = {"a": 0.0, "b": 0.0}
    replicates = [{"a": 0.0, "b": 1.0}, {"a": 0.0, "b": 2.0}, {"a": 0.0, "b": 3.0}]
    q2, _ = simultaneous_q(means, replicates)
    means3 = means | {"c": 0.0}
    replicates3 = [row | {"c": 4.0} for row in replicates]
    q3, _ = simultaneous_q(means3, replicates3)
    assert q3 >= q2
    assert upper_bound_decision(-2.0, -0.001) == "ECONOMIC_BREAK_EVEN_EXCLUDED"
    assert upper_bound_decision(-2.0, 0.0) == "INSUFFICIENT_PRECISION_TO_EXCLUDE_ECONOMIC_BREAK_EVEN"
    assert upper_bound_decision(0.001, 2.0) == "REQUIRES_EXISTING_RESULT_CONSISTENCY_REVIEW"
