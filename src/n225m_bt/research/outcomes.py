"""Scheduled-axis outcome accounting that does not coerce unknown PnL to zero."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class OutcomeState(StrEnum):
    KNOWN_NO_TRADE = "KNOWN_NO_TRADE"
    CANCELED_WITH_FEE = "CANCELED_WITH_FEE"
    REALIZED = "REALIZED"
    UNKNOWN_PNL = "UNKNOWN_PNL"
    OPEN_POSITION = "OPEN_POSITION"


@dataclass(frozen=True, slots=True)
class ScheduledOutcome:
    trade_date: date
    state: OutcomeState
    net_pnl_jpy: int | None


@dataclass(frozen=True, slots=True)
class AxisOutcomeSummary:
    axis_hash: str
    scheduled_days: int
    observed_days: int
    unknown_days: int
    partial_observed_net_jpy: int
    complete_net_jpy: int | None


def summarize_scheduled_axis(
    axis_hash: str, axis: tuple[date, ...], outcomes: tuple[ScheduledOutcome, ...]
) -> AxisOutcomeSummary:
    """Summarize a complete scheduled axis and fail closed on an omitted day."""
    if not axis_hash:
        raise ValueError("scheduled axis hash is required")
    if len(set(axis)) != len(axis):
        raise ValueError("scheduled axis has duplicate trade dates")
    by_day = {item.trade_date: item for item in outcomes}
    if len(by_day) != len(outcomes):
        raise ValueError("outcomes have duplicate trade dates")
    if set(by_day) != set(axis):
        raise ValueError("outcomes must cover exactly the frozen scheduled axis")
    partial = 0
    unknown = 0
    for item in outcomes:
        if item.state is OutcomeState.KNOWN_NO_TRADE and item.net_pnl_jpy != 0:
            raise ValueError("known no-trade must be exactly zero")
        if item.state in {OutcomeState.UNKNOWN_PNL, OutcomeState.OPEN_POSITION}:
            if item.net_pnl_jpy is not None:
                raise ValueError("unknown/open outcome must retain null PnL")
            unknown += 1
        elif item.net_pnl_jpy is None:
            raise ValueError("observed outcome requires net PnL")
        else:
            partial += item.net_pnl_jpy
    return AxisOutcomeSummary(
        axis_hash=axis_hash,
        scheduled_days=len(axis),
        observed_days=len(axis) - unknown,
        unknown_days=unknown,
        partial_observed_net_jpy=partial,
        complete_net_jpy=partial if unknown == 0 else None,
    )
