"""Fixed 09:30--10:30 execution path for R041-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class NightConflictOpenFollowthroughStrategy:
    strategy_version = "r041-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str, delay: int = 0) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("R041 requires long/short and a zero/one-bar delay")
        self._strategy_id = strategy_id
        self.signal_time = signal_time
        self.direction = direction
        self.delay = delay
        self.exit_time = signal_time + timedelta(minutes=61)
        self.exit_signal_time = self.exit_time - timedelta(minutes=1)
        self._entry_decided = False
        self._exit_decided = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        entry_signal = self.signal_time + timedelta(minutes=self.delay)
        if not self._entry_decided and bar.ts_jst == entry_signal:
            self._entry_decided = True
            action = SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT
            return Signal(action, bar.ts_jst, reason="r041_q001_opening_follow_entry")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r041_q001_fixed_1030_exit")
        return None
