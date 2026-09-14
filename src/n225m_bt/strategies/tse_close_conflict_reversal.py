"""Fixed scheduled post-boundary execution path for R044-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class TSECloseConflictReversalStrategy:
    strategy_version = "r044-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, exit_time: datetime, direction: str, delay: int = 0) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("R044 requires long/short and zero/one-bar delay")
        if exit_time != signal_time + timedelta(minutes=11):
            raise ValueError("R044 requires a fixed ten-minute post-boundary exit")
        self._strategy_id, self.signal_time, self.exit_time, self.direction = strategy_id, signal_time, exit_time, direction
        self.entry_signal_time, self.exit_signal_time = signal_time + timedelta(minutes=delay), exit_time - timedelta(minutes=1)
        self._entry_decided = self._exit_decided = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.entry_signal_time:
            self._entry_decided = True
            return Signal(SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT, bar.ts_jst, reason="r044_q001_entry")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r044_q001_fixed_exit")
        return None
