"""Fixed 09:01--09:16 execution path for R028-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class CashOpenDiscontinuityStrategy:
    """Issue entry after 09:00 and fixed exit after the 09:15 bar closes."""

    strategy_version = "r028-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("R028 direction must be long or short")
        self._strategy_id, self.signal_time, self.direction = strategy_id, signal_time, direction
        self.exit_time = signal_time + timedelta(minutes=16)
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
                reason="r028_q001_cash_open_discontinuity_entry",
            )
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r028_q001_fixed_15m_exit")
        return None
