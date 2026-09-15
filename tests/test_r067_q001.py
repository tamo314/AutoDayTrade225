from __future__ import annotations

import numpy as np
import pytest

from n225m_bt.research.r067 import (
    R067QNotIdentifiableError,
    fwl_delta,
    nearest_rank,
    rolling_u_ledger,
)


def source(index: int) -> dict[str, object]:
    return {
        "trade_date": f"2024-01-{index + 1:03d}",
        "u": {"u_member": True, "a": float(index + 1), "e": float(index + 1) / 200},
        "future_tse_execution": "must_not_be_read",
    }


def test_prior_only_dual_nearest_rank_and_prefix() -> None:
    ledger = rolling_u_ledger([source(index) for index in range(102)])
    assert ledger[99]["u"]["rolling_valid"] is False
    first = ledger[100]["u"]
    assert first["rolling_valid_count"] == 100
    assert (first["q50"], first["q70"], first["e_q50"]) == (50.0, 70.0, 0.25)
    assert all(day < ledger[100]["trade_date"] for day in first["rolling_reference_trade_dates"])
    assert ledger == rolling_u_ledger([*[source(index) for index in range(102)], source(102)])[:102]
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 75) == 3.0


def test_q002_uses_exact_prior_240_scheduled_nights_and_160_valid() -> None:
    ledger = rolling_u_ledger([source(index) for index in range(242)], lookback=240, min_references=160)
    assert ledger[159]["u"]["rolling_valid"] is False
    first = ledger[160]["u"]
    assert first["rolling_scheduled_count"] == 160
    assert first["rolling_valid_count"] == 160
    assert first["rolling_valid"] is True
    assert all(day < ledger[160]["trade_date"] for day in first["rolling_reference_trade_dates"])
    assert ledger == rolling_u_ledger(
        [*[source(index) for index in range(242)], source(242)], lookback=240, min_references=160
    )[:242]


def test_fwl_identification_contract() -> None:
    nuisance = np.asarray([[1.0, -1.0], [1.0, 0.0], [1.0, 1.0], [1.0, 2.0]])
    q, y = np.asarray([0.0, 1.0, 0.0, 1.0]), np.asarray([2.0, 6.0, 3.0, 10.0])
    assert fwl_delta(q, y, nuisance)[1] > 1e-12
    with pytest.raises(R067QNotIdentifiableError):
        fwl_delta(q, y, np.column_stack((nuisance, q)))
