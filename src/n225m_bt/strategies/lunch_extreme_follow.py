"""Fixed next-open / 30-minute execution path for R038-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class LunchExtremeFollowStrategy:
    """Enter after a fixed window close and exit at its absolute scheduled endpoint."""

    strategy_version = "r038-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str, delay: int = 0) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("invalid R038 direction or delay")
        self._strategy_id = strategy_id
        self.direction = direction
        self.entry_signal_time = signal_time + timedelta(minutes=delay)
        self.exit_signal_time = signal_time + timedelta(minutes=30)
        self._entry_sent = False
        self._exit_sent = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_sent and bar.ts_jst == self.entry_signal_time:
            self._entry_sent = True
            action = SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT
            return Signal(action, bar.ts_jst, reason="r038_q001_fixed_window_entry")
        if not self._exit_sent and bar.ts_jst == self.exit_signal_time:
            self._exit_sent = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r038_q001_fixed_30m_exit")
        return None
