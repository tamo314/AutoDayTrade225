"""Fixed 09:00--09:30 execution path for R023-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class CashOpenReversalStrategy:
    strategy_version = "r023-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("R023 direction must be long or short")
        self._strategy_id, self.signal_time, self.direction = strategy_id, signal_time, direction
        self.entry_time = signal_time + timedelta(minutes=1)
        self.exit_time = signal_time + timedelta(minutes=31)
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
                reason="r023_q001_cash_open_reversal_entry",
            )
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r023_q001_fixed_0930_exit")
        return None
