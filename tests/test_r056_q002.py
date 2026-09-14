from __future__ import annotations

import numpy as np
import pytest

from n225m_bt.research.r053 import R053QNotIdentifiableError, fwl_delta
from n225m_bt.research.r056_q002 import fixed_design_score_bootstrap


def test_fixed_design_score_bootstrap_preserves_point_and_seed() -> None:
    q = np.asarray([0.0, 0.0, 1.0, 1.0])
    y = np.asarray([1.0, 2.0, 4.0, 7.0])
    nuisance = np.ones((4, 1))
    days = [f"2024-01-0{index}" for index in range(1, 5)]
    # A moving-block resample can lose Q variation completely.
    with pytest.raises(R053QNotIdentifiableError):
        fwl_delta(
            q[[0, 1, 0, 1]],
            y[[0, 1, 0, 1]],
            nuisance[[0, 1, 0, 1]],
            pinv_rcond=1e-12,
            residual_ss_tolerance=1e-12,
        )
    point, _, errors, audit = fixed_design_score_bootstrap(
        q, y, nuisance, days, days, seed=71, block_length=2, repetitions=10_000
    )
    repeat, _, repeated_errors, repeated_audit = fixed_design_score_bootstrap(
        q, y, nuisance, days, days, seed=71, block_length=2, repetitions=10_000
    )
    expected, _ = fwl_delta(q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12)
    assert point == expected == repeat
    assert np.array_equal(errors, repeated_errors)
    assert len(errors) == 10_000
    assert audit["fixed_axis_trade_dates"] == 4
    assert audit == repeated_audit
