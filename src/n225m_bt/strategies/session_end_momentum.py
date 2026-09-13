"""Fixed scheduled-session-end directional-continuation strategy for R005-Q001."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from n225m_bt.domain import Bar, Signal, SignalAction
from n225m_bt.strategies.base import StrategyContext

Condition = Literal["A_session_end", "B_time_control"]


class SessionEndMomentumStrategy:
    """One fixed scheduled entry and fixed scheduled exit, using known session times only.

    The engine calls ``on_bar`` after the supplied bar closes.  Thus the entry
    decision is made after E-1 closes and can first fill at E's open; the exit
    decision is made after F-1 closes and can first fill at F's open.
    """

    strategy_version = "r005-q001-v1"

    def __init__(
        self,
        strategy_id: str,
        entry_time: datetime,
        scheduled_exit_time: datetime,
    ) -> None:
        self._strategy_id = strategy_id
        self.entry_time = entry_time
        self.scheduled_exit_time = scheduled_exit_time
        self.entry_signal_time = entry_time - timedelta(minutes=1)
        self.exit_signal_time = scheduled_exit_time - timedelta(minutes=1)
        self.event: dict[str, object] = {
            "status": "awaiting_entry_signal",
            "entry_time_jst": entry_time.isoformat(),
            "scheduled_exit_time_jst": scheduled_exit_time.isoformat(),
            "entry_signal_time_jst": self.entry_signal_time.isoformat(),
            "exit_signal_time_jst": self.exit_signal_time.isoformat(),
        }
        self._entry_decided = False
        self._exit_decided = False

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None:
        if not self._entry_decided and bar.ts_jst == self.entry_signal_time:
            self._entry_decided = True
            window_start = self.entry_time - timedelta(minutes=60)
            expected = [window_start + timedelta(minutes=index) for index in range(60)]
            by_timestamp = {item.ts_jst: item for item in ctx.history}
            missing = [stamp for stamp in expected if stamp not in by_timestamp]
            if missing:
                self.event.update(
                    {
                        "status": "skipped",
                        "reason": "LOOKBACK_WINDOW_NOT_60_CONSECUTIVE_ELIGIBLE_BARS",
                        "missing_timestamps_jst": [stamp.isoformat() for stamp in missing],
                    }
                )
                return None
            first = by_timestamp[window_start]
            last = by_timestamp[self.entry_signal_time]
            direction = last.close - first.open
            self.event.update(
                {
                    "lookback_start_jst": window_start.isoformat(),
                    "lookback_end_jst": self.entry_signal_time.isoformat(),
                    "lookback_open": first.open,
                    "lookback_close": last.close,
                    "direction_points": direction,
                }
            )
            if direction == 0:
                self.event.update({"status": "skipped", "reason": "ZERO_DIRECTION"})
                return None
            action = SignalAction.ENTER_LONG if direction > 0 else SignalAction.ENTER_SHORT
            self.event.update(
                {
                    "status": "entry_order_issued",
                    "reason": "DIRECTION_CONTINUATION",
                    "side": "long" if direction > 0 else "short",
                }
            )
            return Signal(action, bar.ts_jst, reason="r005_q001_direction_continuation")

        if not self._exit_decided and bar.ts_jst == self.exit_signal_time:
            self._exit_decided = True
            if not ctx.has_position:
                self.event.update({"exit_status": "no_position_at_scheduled_exit_signal"})
                return None
            self.event.update({"exit_status": "exit_order_issued", "exit_reason": "SCHEDULED_EXIT"})
            return Signal(SignalAction.EXIT, bar.ts_jst, reason="r005_q001_scheduled_exit")
        return None

    def finalize(self, bars: list[Bar]) -> dict[str, object]:
        observed = {bar.ts_jst for bar in bars if bar.is_eligible}
        if not self._entry_decided:
            self.event.update(
                {
                    "status": "skipped",
                    "reason": "ENTRY_SIGNAL_BAR_MISSING_OR_INELIGIBLE",
                }
            )
        elif self.event.get("status") == "entry_order_issued" and self.entry_time not in observed:
            self.event.update({"entry_fill_risk": "SCHEDULED_ENTRY_BAR_MISSING_OR_INELIGIBLE"})
        if not self._exit_decided:
            self.event.update(
                {
                    "exit_status": (
                        "EXIT_SIGNAL_CALLBACK_NOT_REACHED"
                        if self.exit_signal_time in observed
                        else "EXIT_SIGNAL_BAR_MISSING_OR_INELIGIBLE"
                    )
                }
            )
        elif self.scheduled_exit_time not in observed:
            self.event.update({"exit_fill_risk": "SCHEDULED_EXIT_BAR_MISSING_OR_INELIGIBLE"})
        return dict(self.event)
