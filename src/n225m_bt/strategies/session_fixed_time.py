"""One fixed-time entry/exit strategy for preselected research events."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class SessionFixedTimeStrategy:
    """Issue a selected side at t+1 and exit at an absolute fixed timestamp."""

    strategy_version = "fixed-time-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("fixed-time direction must be long or short")
        self._strategy_id, self.signal_time, self.direction = strategy_id, signal_time, direction
        self.entry_time = signal_time + timedelta(minutes=1)
        self.exit_time = signal_time + timedelta(minutes=61)
        self.exit_signal_time = self.exit_time - timedelta(minutes=1)
        self._entry_decided = self._exit_decided = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.signal_time:
            self._entry_decided = True
            return Signal(SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT, bar.ts_jst, reason="r011_q001_close_mean_reversion")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r011_q001_fixed_60m_exit")
        return None
