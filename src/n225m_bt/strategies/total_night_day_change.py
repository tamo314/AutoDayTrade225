"""Fixed R015-Q001 day-session order path."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class TotalNightDayChangeStrategy:
    """Signal at t=S+29 and issue the fixed exit at X-1=S+89."""

    strategy_version = "r015-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("R015 direction must be long or short")
        self._strategy_id = strategy_id
        self.signal_time = signal_time
        self.direction = direction
        self.entry_time = signal_time + timedelta(minutes=1)
        self.exit_time = signal_time + timedelta(minutes=61)
        self.exit_signal_time = self.exit_time - timedelta(minutes=1)
        self._entry_decided = False
        self._exit_decided = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.signal_time:
            self._entry_decided = True
            return Signal(
                SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT,
                bar.ts_jst,
                reason="r015_q001_total_change",
            )
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r015_q001_fixed_60m_exit")
        return None
