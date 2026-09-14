"""Fixed entry and absolute exit for R033-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class OpeningRangeCompressionBreakoutStrategy:
    strategy_version = "r033-q001-v1"

    def __init__(
        self,
        strategy_id: str,
        signal_time: datetime,
        direction: str,
        entry_delay: int = 0,
        exit_after_minutes: int = 60,
    ) -> None:
        if direction not in {"long", "short"} or entry_delay < 0 or exit_after_minutes <= 0:
            raise ValueError("invalid R033 direction or delay")
        self._strategy_id, self.direction = strategy_id, direction
        self.entry_signal_time = signal_time + timedelta(minutes=entry_delay)
        self.exit_signal_time = signal_time + timedelta(minutes=exit_after_minutes)
        self._entry = self._exit = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry and bar.ts_jst == self.entry_signal_time:
            self._entry = True
            return Signal(SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT, bar.ts_jst, reason="r033_q001_entry")
        if not self._exit and bar.ts_jst == self.exit_signal_time:
            self._exit = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r033_q001_fixed_exit")
        return None
