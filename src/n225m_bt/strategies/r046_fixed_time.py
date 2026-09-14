"""Fixed entry and absolute exit orders for R046-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class R046FixedTimeStrategy:
    strategy_version = "r046-q001-v1"

    def __init__(
        self, strategy_id: str, signal_time: datetime, direction: str, delay: int = 0
    ) -> None:
        if direction not in {"long", "short"} or delay < 0:
            raise ValueError("invalid R046 direction or delay")
        self._strategy_id, self._direction = strategy_id, direction
        self._entry_time, self._exit_time = (
            signal_time + timedelta(minutes=delay),
            signal_time + timedelta(minutes=60),
        )
        self._entered = self._exited = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entered and bar.ts_jst == self._entry_time:
            self._entered = True
            return Signal(
                SignalAction.ENTER_LONG if self._direction == "long" else SignalAction.ENTER_SHORT,
                bar.ts_jst,
                reason="r046_entry",
            )
        if not self._exited and bar.ts_jst == self._exit_time:
            self._exited = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r046_fixed_exit")
        return None
