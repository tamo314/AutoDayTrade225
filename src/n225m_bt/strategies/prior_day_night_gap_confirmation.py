"""Fixed sixth-night-bar to +60 minute execution for R032-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class PriorDayNightGapConfirmationStrategy:
    strategy_version = "r032-q001-v1"

    def __init__(self, strategy_id: str, signal_time: datetime, direction: str) -> None:
        if direction not in {"long", "short"}:
            raise ValueError("R032 direction must be long or short")
        self._strategy_id, self.signal_time, self.direction = strategy_id, signal_time, direction
        self.exit_time = signal_time + timedelta(minutes=61)
        self.exit_signal_time = self.exit_time - timedelta(minutes=1)
        self._entry_decided = self._exit_decided = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.signal_time:
            self._entry_decided = True
            return Signal(SignalAction.ENTER_LONG if self.direction == "long" else SignalAction.ENTER_SHORT, bar.ts_jst, reason="r032_q001_prior_day_night_gap_confirmation_entry")
        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if ctx.has_position:
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="r032_q001_fixed_night_exit")
        return None
