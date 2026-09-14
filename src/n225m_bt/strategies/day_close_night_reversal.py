"""Fixed day-close signal / scheduled night-entry execution for R039-Q001."""

from __future__ import annotations

from datetime import datetime

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class DayCloseNightReversalStrategy:
    strategy_version = "r039-q001-v1"

    def __init__(
        self,
        strategy_id: str,
        entry_signal_time: datetime,
        exit_signal_time: datetime,
        direction: str,
    ) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("invalid R039 direction")
        self._strategy_id = strategy_id
        self.direction = direction
        self.entry_signal_time = entry_signal_time
        self.exit_signal_time = exit_signal_time
        self._entry_sent = False
        self._exit_sent = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_sent and bar.ts_jst == self.entry_signal_time:
            self._entry_sent = True
            action = SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT
            return Signal(action, bar.ts_jst, reason="r039_q001_scheduled_night_entry")
        if not self._exit_sent and bar.ts_jst == self.exit_signal_time:
            self._exit_sent = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r039_q001_fixed_30m_exit")
        return None
