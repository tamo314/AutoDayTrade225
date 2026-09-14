"""Fixed next-open / absolute-15-minute execution for R035-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class SameClockShockStrategy:
    """Enter after a completed five-minute shock and never extend its exit."""

    strategy_version = "r035-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str, delay: int = 0) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("invalid R035 direction or delay")
        self._strategy_id = strategy_id
        self.direction = direction
        self.entry_signal_time = signal_time + timedelta(minutes=delay)
        # signal at k; E is k+1 and the required exit fill is E+15=k+16.
        self.exit_signal_time = signal_time + timedelta(minutes=15)
        self._entry_sent = False
        self._exit_sent = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_sent and bar.ts_jst == self.entry_signal_time:
            self._entry_sent = True
            action = SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT
            return Signal(action, bar.ts_jst, reason="r035_q001_same_clock_shock_entry")
        if not self._exit_sent and bar.ts_jst == self.exit_signal_time:
            self._exit_sent = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r035_q001_fixed_15m_exit")
        return None
