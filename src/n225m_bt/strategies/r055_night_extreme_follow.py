"""Known-night-close order with a fixed TSE-opening exit for R055-Q001."""
from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class R055NightExtremeFollowStrategy:
    strategy_version = "r055-q001-v1"
    def __init__(
        self, strategy_id: str, night_signal: datetime, entry: datetime, exit_: datetime,
        direction: str, delay: int = 0,
    ) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("invalid R055 parameters")
        self._strategy_id, self._direction = strategy_id, direction
        self._entry_signal = night_signal if delay == 0 else entry
        self._exit_signal = exit_ - timedelta(minutes=1)
        self._sent_entry = self._sent_exit = False
    @property
    def strategy_id(self) -> str: return self._strategy_id
    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._sent_entry and bar.ts_jst == self._entry_signal:
            self._sent_entry = True
            return Signal(SignalAction.ENTER_LONG if self._direction == "long" else SignalAction.ENTER_SHORT, bar.ts_jst, reason="r055_night_extreme_follow")
        if not self._sent_exit and bar.ts_jst == self._exit_signal:
            self._sent_exit = True
            return Signal(SignalAction.EXIT, bar.ts_jst, reason="r055_fixed_exit") if ctx.has_position else None
        return None
