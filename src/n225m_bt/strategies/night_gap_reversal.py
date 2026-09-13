"""Fixed day-open gap-reversal signals for R006-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext

Condition = Literal["A_gap_reversal", "B_always_long", "C_always_short"]


class NightGapReversalStrategy:
    """One scheduled day-session trade whose direction is fixed before entry.

    ``signal_time`` is the day opening bar.  The opening bar's OHLC is known
    only after it closes, so the engine can first fill the pending entry at
    ``signal_time + 1 minute``.  The scheduled exit is deliberately anchored
    to that planned entry time, rather than a delayed actual fill.
    """

    strategy_version = "r006-q001-v1"

    def __init__(
        self,
        strategy_id: str,
        signal_time: datetime,
        condition: Condition,
        gap_points: int,
    ) -> None:
        self._strategy_id = strategy_id
        self.signal_time = signal_time
        self.entry_time = signal_time + timedelta(minutes=1)
        self.exit_time = self.entry_time + timedelta(minutes=60)
        self.exit_signal_time = self.exit_time - timedelta(minutes=1)
        self.condition = condition
        self.gap_points = gap_points
        self._entry_decided = False
        self._exit_decided = False
        self.event: dict[str, object] = {
            "status": "awaiting_open_signal",
            "condition": condition,
            "G_points": gap_points,
            "signal_time_jst": signal_time.isoformat(),
            "scheduled_entry_time_jst": self.entry_time.isoformat(),
            "scheduled_exit_time_jst": self.exit_time.isoformat(),
            "exit_signal_time_jst": self.exit_signal_time.isoformat(),
        }

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def _action(self) -> SignalAction | None:
        if self.gap_points == 0:
            return None
        if self.condition == "A_gap_reversal":
            return SignalAction.ENTER_SHORT if self.gap_points > 0 else SignalAction.ENTER_LONG
        if self.condition == "B_always_long":
            return SignalAction.ENTER_LONG
        return SignalAction.ENTER_SHORT

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.signal_time:
            self._entry_decided = True
            action = self._action()
            if action is None:
                self.event.update({"status": "skipped", "reason": "ZERO_GAP"})
                return None
            side = "long" if action is SignalAction.ENTER_LONG else "short"
            self.event.update({"status": "entry_order_issued", "side": side})
            return Signal(action, bar.ts_jst, reason="r006_q001_fixed_gap_condition")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if not ctx.has_position:
                self.event.update({"exit_status": "no_position_at_scheduled_exit_signal"})
                return None
            self.event.update({"exit_status": "exit_order_issued", "exit_reason": "SCHEDULED_EXIT"})
            return Signal(SignalAction.EXIT, bar.ts_jst, reason="r006_q001_scheduled_exit")
        return None

    def finalize(self, bars: list[Bar]) -> dict[str, object]:
        eligible = {bar.ts_jst for bar in bars if bar.is_eligible}
        if not self._entry_decided:
            self.event.update(
                {"status": "skipped", "reason": "DAY_OPEN_SIGNAL_BAR_MISSING_OR_INELIGIBLE"}
            )
        elif self.event.get("status") == "entry_order_issued" and self.entry_time not in eligible:
            self.event.update({"entry_fill_risk": "SCHEDULED_ENTRY_BAR_MISSING_OR_INELIGIBLE"})
        if not self._exit_decided:
            self.event.update({"exit_status": "EXIT_SIGNAL_BAR_MISSING_OR_INELIGIBLE"})
        elif self.exit_time not in eligible:
            self.event.update({"exit_fill_risk": "SCHEDULED_EXIT_BAR_MISSING_OR_INELIGIBLE"})
        return dict(self.event)
