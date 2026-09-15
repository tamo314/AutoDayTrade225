
import numpy as np
import pytest

from n225m_bt.research.r066 import (
    R066QNotIdentifiableError,
    fwl_delta,
    nearest_rank,
    rolling_u_ledger,
)


def test_prior_only_nearest_rank_and_prefix() -> None:
    rows = [
        {"trade_date": f"2024-01-{i + 1:03d}", "u": {"u_member": True, "r": float(i)}}
        for i in range(102)
    ]
    ledger = rolling_u_ledger(rows)
    assert ledger[99]["u"]["rolling_valid"] is False
    assert ledger[100]["u"]["rolling_valid_count"] == 100
    assert ledger[100]["u"]["q25"] == 24.0
    assert all(
        x < ledger[100]["trade_date"] for x in ledger[100]["u"]["rolling_reference_trade_dates"]
    )
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 25) == 1.0


def test_fwl_identification() -> None:
    nuisance = np.asarray([[1.0, -1.0], [1.0, 0.0], [1.0, 1.0], [1.0, 2.0]])
    q, y = np.asarray([0.0, 1.0, 0.0, 1.0]), np.asarray([2.0, 6.0, 3.0, 10.0])
    assert fwl_delta(q, y, nuisance)[1] > 1e-12
    with pytest.raises(R066QNotIdentifiableError):
        fwl_delta(q, y, np.column_stack((nuisance, q)))
