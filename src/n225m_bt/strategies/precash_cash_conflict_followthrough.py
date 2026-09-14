"""Fixed 09:05--10:05 execution path for R043-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class PrecashCashConflictFollowthroughStrategy:
    strategy_version = "r043-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str, delay: int = 0) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("R043 requires a long/short direction and zero/one-bar delay")
        self._strategy_id = strategy_id
        self.signal_time = signal_time
        self.direction = direction
        self.entry_signal_time = signal_time + timedelta(minutes=delay)
        self.entry_time = self.entry_signal_time + timedelta(minutes=1)
        self.exit_time = signal_time + timedelta(minutes=61)
        self.exit_signal_time = self.exit_time - timedelta(minutes=1)
        self._entry_decided = False
        self._exit_decided = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.entry_signal_time:
            self._entry_decided = True
            return Signal(
                SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT,
                bar.ts_jst,
                reason="r043_q001_precash_cash_conflict_entry",
            )
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r043_q001_fixed_1005_exit")
        return None
