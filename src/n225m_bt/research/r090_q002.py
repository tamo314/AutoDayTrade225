"""PnL-free availability gate revision for the separately frozen TASK-R090-Q002."""

from __future__ import annotations

from typing import cast

from n225m_bt.research.r090_lunch_rejection import feasibility


def feasibility_q002(
    primary: list[dict[str, object]], placebo: list[dict[str, object]]
) -> dict[str, object]:
    """Apply Q001's gate with only the 2025H1 LR minimum revised from 8 to 6."""
    result = feasibility(primary, placebo)
    gate = dict(cast(dict[str, object], result["gate"]))
    gate.pop("lr_2025_h1_at_least_8")
    by_year = cast(dict[str, int], gate["lr_by_year"])
    gate["lr_2025_h1_at_least_6"] = by_year["2025"] >= 6
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "lr_la_pr_at_least_90_each",
            "lr_up_down_at_least_30_each",
            "lr_2022_2024_at_least_15_each",
            "lr_2025_h1_at_least_6",
            "each_band_lr_la_at_least_25",
            "unexplained_exclusions_equal_zero",
        )
    )
    return result | {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic. Q002 changes only the 2025H1 LR minimum from 8 to 6.",
        "gate": gate,
        "q002_gate_revision": {
            "changed_condition": "lr_2025_h1_minimum",
            "q001_minimum": 8,
            "q002_minimum": 6,
            "all_other_gate_conditions_unchanged": True,
        },
    }
