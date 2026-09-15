"""Fixed causal signals for the R085 day-to-night reopen gap ledger."""

from __future__ import annotations

from datetime import datetime

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class R085FixedSignalStrategy:
    """Submit once at the precomputed night entry and exit signals."""

    strategy_version = "r085-q001-v1"

    def __init__(
        self, strategy_id: str, entry_signal: datetime, exit_signal: datetime, direction: str
    ) -> None:
        if direction not in {"long", "short"} or exit_signal <= entry_signal:
            raise ValueError("invalid R085 fixed signal contract")
        self._strategy_id = strategy_id
        self._entry_signal = entry_signal
        self._exit_signal = exit_signal
        self._direction = direction
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
                reason="r085_fixed_causal_night_reopen_entry",
            )
        if not self._exited and ctx.has_position and bar.ts_jst == self._exit_signal:
            self._exited = True
            return Signal(SignalAction.EXIT, bar.ts_jst, reason="r085_fixed_exit")
        return None
