"""Fresh day-session execution path for R042-Q002."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class NightTerminalRangeFollowthroughStrategy:
    strategy_version = "r042-q002-v1"

    def __init__(
        self,
        strategy_id: str,
        signal_time: datetime,
        direction: str,
        delay: int = 0,
        exit_signal_time: datetime | None = None,
    ) -> None:
        if direction not in {"long", "short"} or delay not in {0, 1}:
            raise ValueError("R042-Q002 requires long/short and zero/one-bar delay")
        self._strategy_id, self.signal_time, self.direction, self.delay = (
            strategy_id,
            signal_time,
            direction,
            delay,
        )
        self.exit_signal_time = exit_signal_time or signal_time + timedelta(minutes=60)
        self._entry_sent = False
        self._exit_sent = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_sent and bar.ts_jst == self.signal_time + timedelta(minutes=self.delay):
            self._entry_sent = True
            return Signal(
                SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT,
                bar.ts_jst,
                reason="r042_q002_fresh_day_signal",
            )
        if not self._exit_sent and bar.ts_jst == self.exit_signal_time:
            self._exit_sent = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r042_q002_fixed_0946_exit")
        return None
