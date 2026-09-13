"""R004-Q001 failed-opening-break reversal signals.

The strategy emits signals only.  The established engine continues to own next-open
fills, slippage, protective exits, forced flattening, and ledger economics.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction
from math import ceil, floor
from typing import Literal

from n225m_bt.domain import Bar, Side, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext

Condition = Literal["A_return_confirmation", "B_immediate_control"]


class FailedBreakoutReversalStrategy:
    """One first-break event and at most one reverse entry per session."""

    strategy_version = "1.0.0"

    def __init__(
        self,
        *,
        strategy_id: str,
        session_open: datetime,
        entry_cutoff: datetime,
        condition: Condition,
        tick_size: int = 5,
        holding_minutes: int = 60,
    ) -> None:
        self.strategy_id = strategy_id
        self._open = session_open
        self._entry_cutoff = entry_cutoff
        self._condition = condition
        self._tick = tick_size
        self._holding = holding_minutes
        self._opening: list[Bar] = []
        self._opening_invalid = False
        self._upper: int | None = None
        self._lower: int | None = None
        self._break: Bar | None = None
        self._break_direction: Side | None = None
        self._peak_high: int | None = None
        self._trough_low: int | None = None
        self._entry_sent = False
        self._entry_observed: datetime | None = None
        self._exit_sent = False
        self._return_invalid = False
        self._next_return_step = 1
        self._event: dict[str, object] = {
            "condition": condition,
            "session_open_jst": session_open.isoformat(),
            "status": "pending",
        }

    @property
    def event(self) -> dict[str, object]:
        return dict(self._event)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        offset = int((bar.ts_jst - self._open).total_seconds() // 60)
        if 0 <= offset < 30:
            if offset != len(self._opening):
                self._opening_invalid = True
            self._opening.append(bar)
            if offset == 29:
                self._freeze_opening()
            return None
        if self._upper is None or self._lower is None:
            if offset >= 30:
                self._event.update({"status": "no_event", "reason": "OPENING_INVALID"})
            return None

        if ctx.has_position:
            return self._timed_exit(bar)
        if self._entry_sent:
            return None
        if self._break is None:
            if 30 <= offset < 120:
                direction = self._break_side(bar)
                if direction is not None:
                    self._record_break(bar, direction)
                    if self._condition == "B_immediate_control":
                        return self._entry_signal(bar, bar.high, bar.low)
            return None
        if self._condition == "A_return_confirmation":
            return self._await_return(bar)
        return None

    def finalize(self, all_bars: list[Bar]) -> dict[str, object]:
        """Close no-trade states using the complete current session, never future sessions."""
        if self._upper is None or self._lower is None:
            self._event.setdefault("reason", "OPENING_INVALID")
            self._event["status"] = "no_event"
            return self.event
        if self._break is None:
            self._event.update({"status": "no_event", "reason": "NO_FIRST_BREAK"})
            return self.event
        if self._condition == "A_return_confirmation" and not self._entry_sent:
            expected = {self._break.ts_jst + timedelta(minutes=step) for step in range(1, 6)}
            observed = {bar.ts_jst: bar for bar in all_bars}
            missing = sorted(
                stamp.isoformat()
                for stamp in expected
                if stamp not in observed or not observed[stamp].is_eligible
            )
            if missing:
                self._event.update(
                    {
                        "status": "no_order",
                        "reason": "RETURN_WINDOW_MISSING",
                        "missing_timestamps": missing,
                    }
                )
            elif self._return_invalid:
                self._event.update({"status": "no_order", "reason": "RETURN_WINDOW_INVALID"})
            else:
                self._event.update({"status": "no_order", "reason": "NO_RETURN_WITHIN_5M"})
        return self.event

    def _freeze_opening(self) -> None:
        if self._opening_invalid or len(self._opening) != 30:
            self._event.update({"status": "no_event", "reason": "OPENING_INVALID"})
            return
        self._upper = max(bar.high for bar in self._opening)
        self._lower = min(bar.low for bar in self._opening)
        self._event.update(
            {"U": self._upper, "L": self._lower, "M": str(Fraction(self._upper + self._lower, 2))}
        )

    def _break_side(self, bar: Bar) -> Side | None:
        assert self._upper is not None and self._lower is not None
        if bar.close >= self._upper + self._tick:
            return Side.SHORT
        if bar.close <= self._lower - self._tick:
            return Side.LONG
        return None

    def _record_break(self, bar: Bar, side: Side) -> None:
        self._break = bar
        self._break_direction = side
        self._peak_high, self._trough_low = bar.high, bar.low
        self._event.update(
            {
                "event_id": f"{bar.trade_date.isoformat()}-{bar.session.value}-{bar.ts_jst.isoformat()}-{side.value}",
                "break_ts_jst": bar.ts_jst.isoformat(),
                "break_close": bar.close,
                "break_direction": "upper" if side is Side.SHORT else "lower",
                "side": side.value,
                "status": "break_observed",
            }
        )

    def _await_return(self, bar: Bar) -> Signal | None:
        assert self._break is not None and self._upper is not None and self._lower is not None
        delta = int((bar.ts_jst - self._break.ts_jst).total_seconds() // 60)
        if delta < 1:
            return None
        if delta > 5:
            return None
        if delta != self._next_return_step:
            self._return_invalid = True
            return None
        self._next_return_step += 1
        assert self._peak_high is not None and self._trough_low is not None
        self._peak_high = max(self._peak_high, bar.high)
        self._trough_low = min(self._trough_low, bar.low)
        if not (self._lower <= bar.close <= self._upper):
            return None
        self._event.update({"return_ts_jst": bar.ts_jst.isoformat(), "return_close": bar.close})
        return self._entry_signal(bar, self._peak_high, self._trough_low)

    def _entry_signal(self, bar: Bar, high_known: int, low_known: int) -> Signal | None:
        assert (
            self._break_direction is not None
            and self._upper is not None
            and self._lower is not None
        )
        side = self._break_direction
        midpoint = Fraction(self._upper + self._lower, 2)
        target = (
            ceil(midpoint / self._tick) * self._tick
            if side is Side.SHORT
            else floor(midpoint / self._tick) * self._tick
        )
        stop = high_known + self._tick if side is Side.SHORT else low_known - self._tick
        valid = target < bar.close < stop if side is Side.SHORT else stop < bar.close < target
        if not valid:
            self._event.update(
                {
                    "status": "no_order",
                    "reason": "RETURN_CLOSE_OUTSIDE_PROTECTIVE_INTERVAL",
                    "target_price": target,
                    "stop_price": stop,
                }
            )
            self._entry_sent = True
            return None
        if bar.ts_jst >= self._entry_cutoff:
            self._event.update(
                {
                    "status": "no_order",
                    "reason": "ENTRY_CUTOFF",
                    "target_price": target,
                    "stop_price": stop,
                }
            )
            self._entry_sent = True
            return None
        self._entry_sent = True
        self._event.update(
            {
                "status": "order_issued",
                "order_signal_ts_jst": bar.ts_jst.isoformat(),
                "target_price": target,
                "stop_price": stop,
            }
        )
        action = SignalAction.ENTER_LONG if side is Side.LONG else SignalAction.ENTER_SHORT
        return Signal(
            action, bar.ts_jst, stop_price=stop, target_price=target, reason=self.strategy_id
        )

    def _timed_exit(self, bar: Bar) -> Signal | None:
        if self._entry_observed is None:
            self._entry_observed = bar.ts_jst
        if not self._exit_sent and bar.ts_jst >= self._entry_observed + timedelta(
            minutes=self._holding - 1
        ):
            self._exit_sent = True
            return Signal(SignalAction.EXIT, bar.ts_jst, reason="timed_exit_60m")
        return None
