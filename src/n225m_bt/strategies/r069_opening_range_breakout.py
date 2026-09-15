"""Live causal strategy adapter for frozen R069-Q001."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext


class R069OpeningRangeBreakoutStrategy:
    """Freeze an opening range, then enter only the first strict close breakout."""

    strategy_version = "r069-q001-v1"

    def __init__(
        self,
        strategy_id: str,
        *,
        opening_minutes: int = 30,
        search_end: time = time(11, 0),
        entry_delay_minutes: int = 0,
        reverse_direction: bool = False,
    ) -> None:
        if opening_minutes not in {20, 30, 45}:
            raise ValueError("unregistered R069 opening window")
        if search_end not in {time(10, 30), time(11, 0), time(11, 30)}:
            raise ValueError("unregistered R069 search end")
        if entry_delay_minutes not in {0, 1}:
            raise ValueError("unregistered R069 entry delay")
        self._strategy_id = strategy_id
        self._opening_minutes = opening_minutes
        self._search_end = search_end
        self._entry_delay = entry_delay_minutes
        self._reverse = reverse_direction
        self._expected: datetime | None = None
        self._range: list[Bar] = []
        self._high: int | None = None
        self._low: int | None = None
        self._pending_direction: str | None = None
        self._delay_signal: datetime | None = None
        self._entered = False
        self._exited = False
        self._invalid = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def _invalidate_if_gap(self, bar: Bar) -> bool:
        if bar.is_missing_prev_expected:
            self._invalid = True
        return self._invalid

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if self._invalidate_if_gap(bar):
            return None
        clock = bar.ts_jst.time()
        opening = time(9, 0)
        if self._expected is None and clock == opening:
            self._expected = bar.ts_jst
        if self._expected is not None and len(self._range) < self._opening_minutes:
            if bar.ts_jst != self._expected:
                self._invalid = True
                return None
            self._range.append(bar)
            self._expected += timedelta(minutes=1)
            if len(self._range) == self._opening_minutes:
                self._high = max(item.high for item in self._range)
                self._low = min(item.low for item in self._range)
            return None
        if self._high is not None and self._low is not None and not self._entered:
            if self._pending_direction is not None:
                if bar.ts_jst != self._delay_signal:
                    self._invalid = True
                    return None
                self._entered = True
                return Signal(
                    SignalAction.ENTER_LONG
                    if self._pending_direction == "long"
                    else SignalAction.ENTER_SHORT,
                    bar.ts_jst,
                    reason="r069_delayed_first_strict_breakout",
                )
            if (
                clock <= self._search_end
                and bar.ts_jst > self._range[-1].ts_jst
                and (bar.close > self._high or bar.close < self._low)
            ):
                direction = "long" if bar.close > self._high else "short"
                if self._reverse:
                    direction = "short" if direction == "long" else "long"
                if self._entry_delay == 0:
                    self._entered = True
                    return Signal(
                        SignalAction.ENTER_LONG if direction == "long" else SignalAction.ENTER_SHORT,
                        bar.ts_jst,
                        reason="r069_first_strict_breakout",
                    )
                self._pending_direction = direction
                self._delay_signal = bar.ts_jst + timedelta(minutes=1)
        if ctx.has_position and not self._exited and clock == time(14, 29):
            self._exited = True
            return Signal(SignalAction.EXIT, bar.ts_jst, reason="r069_fixed_1430_exit")
        return None
