"""Fixed entry/exit path for R020-Q001 lunch-window events."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class LunchReversalStrategy:
    """Signal at 12:29 and exit at the absolute scheduled 13:30."""

    strategy_version = "r020-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("R020 direction must be long or short")
        self._strategy_id = strategy_id
        self.signal_time = signal_time
        self.direction = direction
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
            action = SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT
            return Signal(action, bar.ts_jst, reason="r020_q001_lunch_reversal_entry")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r020_q001_fixed_60m_exit")
        return None
