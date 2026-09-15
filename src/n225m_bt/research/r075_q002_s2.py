"""PnL-free S2 eligibility gate for the separately frozen R075-Q002 run."""

from __future__ import annotations

from datetime import date
from typing import cast

from n225m_bt.research.r075_q001_s2 import feasibility_q001

MINIMUM_MIDDLE_OBSERVABLE_TRADES = 250


def feasibility_q002(
    high_events: dict[date, dict[str, object]], middle_events: dict[date, dict[str, object]]
) -> dict[str, object]:
    """Apply Q001's PnL-free gate with only the pre-registered M threshold replaced."""
    result = feasibility_q001(high_events, middle_events)
    gate = dict(cast(dict[str, object], result["gate"]))
    gate["minimum_middle_observable_trades"] = MINIMUM_MIDDLE_OBSERVABLE_TRADES
    middle_observable = gate["middle_observable_trades"]
    if not isinstance(middle_observable, int):
        raise TypeError("R075 S2 middle observable-trade count must be an integer")
    gate["middle_at_least_250"] = middle_observable >= MINIMUM_MIDDLE_OBSERVABLE_TRADES
    gate.pop("middle_at_least_300")
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "high_at_least_150",
            "high_direction_minima_passed",
            "middle_at_least_250",
            "unexplained_exclusions_equal_zero",
            "annual_2021_2024_binomial_and_execution_rate_passed",
        )
    )
    return result | {"s2_scope": "PnL-free availability only; Q002 changes M minimum 300 to 250.", "gate": gate}
