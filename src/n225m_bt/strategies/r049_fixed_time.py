"""Scheduled next-open entry and fixed absolute exit for R049-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class R049FixedTimeStrategy:
    strategy_version = "r049-q001-v1"

    def __init__(
        self,
        strategy_id: str,
        entry_time: datetime,
        exit_time: datetime,
        direction: str,
        delay: int = 0,
    ) -> None:
        if direction not in {"long", "short"} or delay < 0:
            raise ValueError("invalid R049 direction or delay")
        self._strategy_id, self._direction = strategy_id, direction
        self._entry_signal = entry_time - timedelta(minutes=1) + timedelta(minutes=delay)
        self._exit_signal = exit_time - timedelta(minutes=1)
        self._entered = self._exited = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entered and bar.ts_jst == self._entry_signal:
            self._entered = True
            return Signal(
                SignalAction.ENTER_LONG if self._direction == "long" else SignalAction.ENTER_SHORT,
                bar.ts_jst,
                reason="r049_entry",
            )
        if not self._exited and bar.ts_jst == self._exit_signal:
            self._exited = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r049_fixed_exit")
        return None
