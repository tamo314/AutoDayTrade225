"""Fixed scheduled entry/exit strategy used only by R045-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class TSELunchPlaceboReversalStrategy:
    strategy_version = "r045-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, exit_time: datetime, direction: str, delay: int = 0) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("R045 requires long/short and zero/one-minute delay")
        if exit_time != signal_time + timedelta(minutes=16):
            raise ValueError("R045 requires the fixed fifteen-minute boundary exit")
        self._strategy_id = strategy_id
        self.direction = direction
        self.entry_signal_time = signal_time + timedelta(minutes=delay)
        self.exit_signal_time = exit_time - timedelta(minutes=1)
        self._entered = False
        self._exited = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entered and bar.ts_jst == self.entry_signal_time:
            self._entered = True
            action = SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT
            return Signal(action, bar.ts_jst, reason="r045_q001_scheduled_entry")
        if not self._exited and bar.ts_jst == self.exit_signal_time:
            self._exited = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r045_q001_fixed_15m_exit")
        return None
