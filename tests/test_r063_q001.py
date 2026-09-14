from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from n225m_bt.research.r063 import (
    MIN_REFERENCES,
    R063QNotIdentifiableError,
    fwl_delta,
    rolling_u_ledger,
)


def source(day: int, *, future_status: str = "anything") -> dict[str, object]:
    u: dict[str, list[dict[str, object]]] = {}
    for length, count in ((3, 40), (5, 24), (10, 12)):
        u[str(length)] = [
            {"position": position, "length": length, "u_member": True, "m": float(day + position)}
            for position in range(count)
        ]
    # This intentionally represents information U must never inspect.
    return {"trade_date": f"2024-01-{day + 1:03d}", "u": u, "future_status": future_status}


def test_u_threshold_starts_at_exactly_100_and_is_prefix_invariant() -> None:
    rows = rolling_u_ledger([source(index) for index in range(MIN_REFERENCES + 2)])
    early = rows[99]["u"]["5"][0]
    first = rows[100]["u"]["5"][0]
    assert early["rolling_valid"] is False
    assert first["rolling_valid"] is True
    assert first["rolling_valid_count"] == 100
    assert first["q90"] == 89.0
    assert all(day < rows[100]["trade_date"] for day in first["rolling_reference_trade_dates"])
    extended = rolling_u_ledger(
        [
            *[source(index) for index in range(MIN_REFERENCES + 2)],
            source(102, future_status="response changed"),
        ]
    )
    assert rows == extended[: len(rows)]


def test_position_histories_never_cross_fill() -> None:
    rows = rolling_u_ledger([source(index) for index in range(101)])
    position = rows[100]["u"]["5"][7]
    assert position["rolling_valid_count"] == 100
    assert position["q70"] == 76.0


def test_fixed_fwl_identification_contract() -> None:
    nuisance = np.asarray([[1.0, -1.0], [1.0, 0.0], [1.0, 1.0], [1.0, 2.0]])
    q = np.asarray([0.0, 1.0, 0.0, 1.0])
    y = np.asarray([2.0, 6.0, 3.0, 10.0])
    delta, ss = fwl_delta(q, y, nuisance)
    assert delta == pytest.approx(
        np.linalg.lstsq(np.column_stack((nuisance, q)), y, rcond=None)[0][-1]
    )
    assert ss > 1e-12
    with pytest.raises(R063QNotIdentifiableError):
        fwl_delta(q, y, np.column_stack((nuisance, q)))


def test_no_future_date_or_holdout_is_embedded_in_u_fixture() -> None:
    assert date(2025, 6, 30) < date(2026, 1, 1)
