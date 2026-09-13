"""R003 opening-range compression and matched-control signal generation."""

from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.research.features import HistorySnapshot, compression_state
from n225m_bt.strategies.base import StrategyContext


class CompressionBreakoutStrategy:
    """Signal-only strategy; fills and costs remain entirely in BacktestEngine."""

    strategy_version = "1.0.0"

    def __init__(
        self,
        *,
        strategy_id: str,
        session_open: datetime,
        history: HistorySnapshot,
        threshold: Fraction,
        holding_minutes: int,
        baseline_sessions: int = 20,
        filtered: bool = True,
        entry_delay_minutes: int = 0,
        exit_delay_minutes: int = 0,
    ) -> None:
        if holding_minutes < 2 or min(entry_delay_minutes, exit_delay_minutes) < 0:
            raise ValueError("invalid R003 duration")
        self.strategy_id = strategy_id
        self._open = session_open
        self._history = history
        self._threshold = threshold
        self._holding = holding_minutes
        self._baseline_sessions = baseline_sessions
        self._filtered = filtered
        self._entry_delay = entry_delay_minutes
        self._exit_delay = exit_delay_minutes
        self._opening: list[Bar] = []
        self._opening_invalid = False
        self._upper: int | None = None
        self._lower: int | None = None
        self._armed = False
        self._disabled_reason: str | None = None
        self._breakout_direction = 0
        self._breakout_offset: int | None = None
        self._entry_sent = False
        self._entry_observed: datetime | None = None
        self._exit_sent = False

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    @property
    def opening_range(self) -> int | None:
        return None if self._upper is None or self._lower is None else self._upper - self._lower

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        offset = int((bar.ts_jst - self._open).total_seconds() // 60)
        if 0 <= offset < 30:
            if offset != len(self._opening) or not bar.is_eligible:
                self._opening_invalid = True
            self._opening.append(bar)
            if offset == 29:
                self._freeze_opening()
        elif len(self._opening) != 30:
            self._opening_invalid = True
            self._disabled_reason = "OPENING_INVALID"

        if ctx.has_position:
            if self._entry_observed is None:
                # Engine applies a pending entry at this bar's open before on_bar.
                self._entry_observed = bar.ts_jst
            if (
                not self._exit_sent
                and bar.ts_jst >= self._entry_observed + timedelta(
                    minutes=self._holding - 1 + self._exit_delay
                )
            ):
                self._exit_sent = True
                return Signal(SignalAction.EXIT, bar.ts_jst, reason="timed_exit")
            return None

        if not self._armed or self._entry_sent:
            return None
        if self._breakout_direction == 0 and 30 <= offset < 120:
            assert self._upper is not None and self._lower is not None
            self._breakout_direction = int(bar.close > self._upper) - int(bar.close < self._lower)
            if self._breakout_direction:
                self._breakout_offset = offset
        if (
            self._breakout_direction
            and self._breakout_offset is not None
            and offset == self._breakout_offset + self._entry_delay
        ):
            self._entry_sent = True
            action = (
                SignalAction.ENTER_LONG
                if self._breakout_direction > 0
                else SignalAction.ENTER_SHORT
            )
            return Signal(action, bar.ts_jst, reason=self.strategy_id)
        return None

    def _freeze_opening(self) -> None:
        if self._opening_invalid or len(self._opening) != 30:
            self._disabled_reason = "OPENING_INVALID"
            return
        self._upper = max(bar.high for bar in self._opening)
        self._lower = min(bar.low for bar in self._opening)
        state = compression_state(
            self._upper - self._lower,
            self._history.ranges,
            self._threshold,
            baseline_sessions=self._baseline_sessions,
        )
        if state is None:
            self._disabled_reason = (
                "HISTORY_SHORT"
                if self._history.status == "insufficient_count"
                else "HISTORY_INVALID"
                if self._history.status == "invalid_member"
                else "ZERO_BASELINE"
                if self._history.status == "zero_median"
                else "ZERO_RANGE"
            )
            return
        if self._filtered and not state.allowed:
            self._disabled_reason = "NOT_COMPRESSED"
            return
        self._armed = True
