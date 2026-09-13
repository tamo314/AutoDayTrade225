"""Fixed R014-Q001 entry/exit execution path."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class RangeMidpointStrategy:
    """Enter after t and request the absolute planned X exit."""

    strategy_version = "r014-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("R014 direction must be long or short")
        self._strategy_id, self.signal_time, self.direction = strategy_id, signal_time, direction
        self.exit_signal_time = signal_time + timedelta(minutes=60)
        self._entry_decided = self._exit_decided = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.signal_time:
            self._entry_decided = True
            action = (
                SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT
            )
            return Signal(action, bar.ts_jst, reason="r014_q001_range_midpoint")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r014_q001_fixed_60m_exit")
        return None
