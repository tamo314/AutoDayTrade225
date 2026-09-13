"""Fixed local-shock reversal/control orders for R007-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext

Condition = Literal["A_local_shock_reversal", "B_always_long", "C_always_short"]


class LocalShockReversalStrategy:
    """Submit one predetermined order on an already causal event timestamp."""

    strategy_version = "r007-q001-v1"

    def __init__(
        self, strategy_id: str, signal_time: datetime, condition: Condition, q_points: int
    ) -> None:
        self._strategy_id = strategy_id
        self.signal_time = signal_time
        self.entry_time = signal_time + timedelta(minutes=1)
        self.exit_time = self.entry_time + timedelta(minutes=15)
        self.exit_signal_time = self.exit_time - timedelta(minutes=1)
        self.condition = condition
        self.q_points = q_points
        self._entry_decided = False
        self._exit_decided = False
        self.event: dict[str, object] = {
            "strategy_status": "awaiting_event_signal",
            "condition": condition,
            "q_t_points": q_points,
            "t_signal_jst": signal_time.isoformat(),
            "E_planned_entry_jst": self.entry_time.isoformat(),
            "X_planned_exit_jst": self.exit_time.isoformat(),
            "EXIT_signal_bar_start_jst": self.exit_signal_time.isoformat(),
        }

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def _action(self) -> SignalAction:
        if self.condition == "A_local_shock_reversal":
            return SignalAction.ENTER_SHORT if self.q_points > 0 else SignalAction.ENTER_LONG
        return (
            SignalAction.ENTER_LONG
            if self.condition == "B_always_long"
            else SignalAction.ENTER_SHORT
        )

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.signal_time:
            self._entry_decided = True
            action = self._action()
            self.event.update(
                {
                    "strategy_status": "entry_order_issued",
                    "side": "long" if action is SignalAction.ENTER_LONG else "short",
                }
            )
            return Signal(action, bar.ts_jst, reason="r007_q001_local_shock")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if not ctx.has_position:
                self.event["exit_status"] = "no_position_at_scheduled_exit_signal"
                return None
            self.event["exit_status"] = "exit_order_issued"
            return Signal(SignalAction.EXIT, bar.ts_jst, reason="r007_q001_fixed_15m_exit")
        return None

    def finalize(self, bars: list[Bar]) -> dict[str, object]:
        eligible = {bar.ts_jst for bar in bars if bar.is_eligible}
        if not self._entry_decided:
            self.event.update({"strategy_status": "entry_signal_missing_or_ineligible"})
        elif self.entry_time not in eligible:
            self.event["entry_fill_risk"] = "SCHEDULED_ENTRY_BAR_MISSING_OR_INELIGIBLE"
        if not self._exit_decided:
            self.event["exit_status"] = "EXIT_SIGNAL_BAR_MISSING_OR_INELIGIBLE"
        elif self.exit_time not in eligible:
            self.event["exit_fill_risk"] = "SCHEDULED_EXIT_BAR_MISSING_OR_INELIGIBLE"
        return dict(self.event)
