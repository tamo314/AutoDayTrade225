"""Fixed next-open execution for R064-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class R064TrendPullbackContinuationStrategy:
    strategy_version = "r064-q001-v1"

    def __init__(
        self, strategy_id: str, signal: datetime, direction: str, exit_: datetime, delay: int = 0
    ) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("invalid R064-Q001 strategy parameters")
        self._strategy_id, self._direction = strategy_id, direction
        self._entry_signal, self._exit_signal = (
            signal + timedelta(minutes=delay),
            exit_ - timedelta(minutes=1),
        )
        self._entered = False
        self._exited = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entered and bar.ts_jst == self._entry_signal:
            self._entered = True
            return Signal(
                SignalAction.ENTER_LONG if self._direction == "long" else SignalAction.ENTER_SHORT,
                bar.ts_jst,
                reason="r064_q001_entry",
            )
        if not self._exited and bar.ts_jst == self._exit_signal:
            self._exited = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r064_q001_fixed_exit")
        return None
