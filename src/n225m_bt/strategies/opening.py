"""Session-opening hypotheses. Decisions depend only on bars already observed."""

from datetime import datetime, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.research.config import Family
from n225m_bt.strategies.base import StrategyContext


class OpeningStrategy:
    strategy_version = "1"

    def __init__(
        self,
        family: Family,
        lookback_minutes: int,
        holding_minutes: int,
        session_open: datetime,
        entry_delay: int = 0,
        exit_delay: int = 0,
    ) -> None:
        if lookback_minutes < 2 or holding_minutes < 2 or min(entry_delay, exit_delay) < 0:
            raise ValueError("invalid opening strategy duration")
        self.strategy_id = family
        self.lookback = lookback_minutes
        self.holding = holding_minutes
        self.open_time = session_open
        self.entry_delay, self.exit_delay = entry_delay, exit_delay
        self.count = 0
        self.first_open: int | None = None
        self.direction = 0
        self.invalid_opening = False
        self.entry_sent = False
        self.exit_sent = False
        self.entry_observed: datetime | None = None
        self.opening_high: int | None = None
        self.opening_low: int | None = None
        self.breakout_offset: int | None = None

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        offset = int((bar.ts_jst - self.open_time).total_seconds() // 60)
        if offset < self.lookback:
            if offset != self.count or (self.count == 0 and not bar.is_session_open):
                self.invalid_opening = True
            if self.first_open is None:
                self.first_open = bar.open
            self.opening_high = (
                max(self.opening_high, bar.high) if self.opening_high is not None else bar.high
            )
            self.opening_low = (
                min(self.opening_low, bar.low) if self.opening_low is not None else bar.low
            )
            self.count += 1
            if offset == self.lookback - 1 and not self.invalid_opening:
                change = bar.close - self.first_open
                if self.strategy_id != "opening_breakout":
                    self.direction = (change > 0) - (change < 0)
                if self.strategy_id == "opening_reversal":
                    self.direction *= -1
        elif self.count != self.lookback:
            self.invalid_opening = True
        elif (
            self.strategy_id == "opening_breakout"
            and self.breakout_offset is None
            and offset < 120
            and not self.invalid_opening
        ):
            assert self.opening_high is not None and self.opening_low is not None
            self.direction = int(bar.close > self.opening_high) - int(bar.close < self.opening_low)
            if self.direction:
                self.breakout_offset = offset

        if ctx.has_position:
            if self.entry_observed is None:
                self.entry_observed = bar.ts_jst
            exit_signal_time = self.entry_observed + timedelta(
                minutes=self.holding - 1 + self.exit_delay
            )
            if bar.ts_jst >= exit_signal_time and not self.exit_sent:
                self.exit_sent = True
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="timed_exit")
            return None
        signal_offset = self.lookback - 1 + self.entry_delay
        if self.strategy_id == "opening_breakout":
            signal_offset = (
                -1 if self.breakout_offset is None else self.breakout_offset + self.entry_delay
            )
        if (
            offset == signal_offset
            and not self.invalid_opening
            and self.direction
            and not self.entry_sent
        ):
            self.entry_sent = True
            action = SignalAction.ENTER_LONG if self.direction > 0 else SignalAction.ENTER_SHORT
            return Signal(action, bar.ts_jst, reason=self.strategy_id)
        return None
