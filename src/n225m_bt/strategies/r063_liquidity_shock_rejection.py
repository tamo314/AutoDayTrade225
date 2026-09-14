"""Scheduled next-open execution for the fixed R063-Q001 event ledger."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class R063LiquidityShockRejectionStrategy:
    """One signal-confirmed entry and one absolute, never-extended exit."""

    strategy_version = "r063-q001-v1"

    def __init__(
        self,
        strategy_id: str,
        signal_time: datetime,
        exit_time: datetime,
        direction: str,
        delay: int = 0,
    ) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("invalid R063 direction or delay")
        self._strategy_id = strategy_id
        self._direction = direction
        self._entry_signal = signal_time + timedelta(minutes=delay)
        self._exit_signal = exit_time - timedelta(minutes=1)
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
                reason="r063_q001_entry",
            )
        if not self._exited and bar.ts_jst == self._exit_signal:
            self._exited = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r063_q001_fixed_exit")
        return None
