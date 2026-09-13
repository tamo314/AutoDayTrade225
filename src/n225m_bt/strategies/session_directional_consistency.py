"""Predetermined R009-Q001 fixed-time momentum and controls."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext

Condition = Literal[
    "A_directional_consistency", "B_always_long", "C_always_short", "D_direction_only"
]


class SessionDirectionalConsistencyStrategy:
    """Issue one preselected entry and an absolute S+180 exit."""

    strategy_version = "r009-q001-v1"

    def __init__(
        self, strategy_id: str, signal_time: datetime, condition: Condition, direction: str
    ) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("R009 direction must be long or short")
        self._strategy_id, self.signal_time, self.condition, self.direction = (
            strategy_id,
            signal_time,
            condition,
            direction,
        )
        self.entry_time = signal_time + timedelta(minutes=1)
        self.exit_time = signal_time + timedelta(minutes=61)
        self.exit_signal_time = self.exit_time - timedelta(minutes=1)
        self._entry_decided = self._exit_decided = False
        self.event: dict[str, object] = {
            "strategy_status": "awaiting_event_signal",
            "condition": condition,
            "event_direction": direction,
            "t_signal_jst": signal_time.isoformat(),
            "E_planned_entry_jst": self.entry_time.isoformat(),
            "X_planned_exit_jst": self.exit_time.isoformat(),
            "EXIT_signal_bar_start_jst": self.exit_signal_time.isoformat(),
        }

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def _action(self) -> SignalAction:
        if self.condition == "B_always_long":
            return SignalAction.ENTER_LONG
        if self.condition == "C_always_short":
            return SignalAction.ENTER_SHORT
        return SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.signal_time:
            self._entry_decided = True
            action = self._action()
            self.event.update({"strategy_status": "entry_order_issued", "side": "long" if action is SignalAction.ENTER_LONG else "short"})
            return Signal(action, bar.ts_jst, reason="r009_q001_directional_consistency")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if not ctx.has_position:
                self.event["exit_status"] = "no_position_at_scheduled_exit_signal"
                return None
            self.event["exit_status"] = "exit_order_issued"
            return Signal(SignalAction.EXIT, bar.ts_jst, reason="r009_q001_fixed_60m_exit")
        return None

    def finalize(self, bars: list[Bar]) -> dict[str, object]:
        eligible = {bar.ts_jst for bar in bars if bar.is_eligible}
        if not self._entry_decided:
            self.event["strategy_status"] = "entry_signal_missing_or_ineligible"
        elif self.entry_time not in eligible:
            self.event["entry_fill_risk"] = "SCHEDULED_ENTRY_BAR_MISSING_OR_INELIGIBLE"
        if not self._exit_decided:
            self.event["exit_status"] = "EXIT_SIGNAL_BAR_MISSING_OR_INELIGIBLE"
        elif self.exit_time not in eligible:
            self.event["exit_fill_risk"] = "SCHEDULED_EXIT_BAR_MISSING_OR_INELIGIBLE"
        return dict(self.event)
