from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from n225m_bt.research.r016 import (
    GateError,
    _extension_dates,
    _p0,
    _trade_daily,
    moving_block_indices,
    simultaneous_intervals,
)


def test_moving_blocks_are_non_circular_tail_truncated_and_reproducible() -> None:
    first = moving_block_indices(7, 4, 3, 20260913)
    assert first == moving_block_indices(7, 4, 3, 20260913)
    assert all(len(row) == 7 and all(0 <= index < 7 for index in row) for row in first)
    assert any(row[-1] == 6 for row in first)


def test_simultaneous_interval_uses_one_common_index_for_every_series() -> None:
    indices = [[0, 1, 2, 3], [3, 2, 1, 0], [0, 0, 0, 0]]
    result = simultaneous_intervals({"a": [10, 20, 30, 40], "b": [-10, -20, -30, -40]}, indices)
    assert result["means"] == {"a": 25.0, "b": -25.0}
    assert result["replicated_means"]["a"] == [25.0, 25.0, 10.0]
    assert result["replicated_means"]["b"] == [-25.0, -25.0, -10.0]
    assert result["intervals"]["a"]["lower"] == 11.500000000000002
    assert result["intervals"]["b"]["upper"] == -11.500000000000002


def test_synthetic_ledger_separates_reference_pnl_slippage_fees_and_daily_storage() -> None:
    ledger = pl.DataFrame(
        {
            "trade_date": [date(2025, 1, 6), date(2025, 1, 6)],
            "side": ["long", "short"],
            "entry_reference_price": [100, 110],
            "exit_reference_price": [110, 90],
            "qty": [1, 2],
            "slippage_cost_jpy": [1000, 2000],
            "fees_jpy": [60, 120],
            "net_pnl_jpy": [-60, 1880],
            "gross_pnl_jpy": [0, 2000],
        }
    )
    reconciled = _p0(ledger, 100)
    assert reconciled.get_column("P0_saved").to_list() == [1000, 4000]
    assert reconciled.get_column("P0_reference").to_list() == [1000, 4000]
    assert _trade_daily(reconciled, "P0_saved") == {date(2025, 1, 6): 5000}
    assert _trade_daily(reconciled, "net_pnl_jpy") == {date(2025, 1, 6): 1820}


def test_only_saved_day_session_quarantine_dates_can_be_zero_extended() -> None:
    axis = [date(2021, 1, 1) + timedelta(days=index) for index in range(1120)]
    missing = axis[100:109]
    reduced = [day for day in axis if day not in missing]
    quarantine = {
        "quarantined_session_list": [{"trade_date": str(day), "session": "day"} for day in missing]
    }
    assert _extension_dates(axis, reduced, quarantine) == missing
    with pytest.raises(GateError, match="not proven"):
        _extension_dates(axis, reduced, {"quarantined_session_list": []})
