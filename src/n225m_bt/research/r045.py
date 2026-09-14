"""Scheduled TSE-cash-lunch displacement events for R045-Q001."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar


@dataclass(frozen=True)
class TSECashLunchSchedule:
    """Versioned TSE cash-market front-close/reopen schedule.

    The present Development interval has one published lunch regime.  Event
    selection receives these boundaries from this schedule, never from OSE
    observations or an inferred fixed-width futures gap.
    """

    effective_start: date
    effective_end: date
    front_close: time
    afternoon_open: time
    source: str

    def boundaries(self, trade_day: date) -> tuple[datetime, datetime]:
        if not self.effective_start <= trade_day <= self.effective_end:
            raise ValueError("TSE lunch schedule does not cover trade_date")
        return (
            datetime.combine(trade_day, self.front_close, JST),
            datetime.combine(trade_day, self.afternoon_open, JST),
        )


TSE_LUNCH_SCHEDULE = TSECashLunchSchedule(
    effective_start=date(2021, 1, 1),
    effective_end=date(2025, 6, 30),
    front_close=time(11, 30),
    afternoon_open=time(12, 30),
    source="JPX/TSE cash-equity trading-hours evidence frozen from R020; versioned schedule record R045-LUNCH-1",
)


def tse_lunch_placebo_event(
    trade_day: date,
    day_bars: list[Bar] | None,
    cash_calendar: TSECashMarketCalendar,
    *,
    day_quarantined: bool = False,
    schedule: TSECashLunchSchedule = TSE_LUNCH_SCHEDULE,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Return the common, causal eligibility ledger for all R045 conditions."""
    if not development_start <= trade_day <= development_end:
        return {
            "trade_date": trade_day.isoformat(),
            "session": "day",
            "schedule_id": "R045-LUNCH-1",
            "status": "skipped",
            "reason": "OUTSIDE_DEVELOPMENT",
        }
    t_s, t_r = schedule.boundaries(trade_day)
    delta = t_r - t_s
    t_b = t_s - timedelta(minutes=30)
    lunch_rows = [t_s + timedelta(minutes=index) for index in range(delta.seconds // 60)]
    placebo_rows = [t_b - delta + timedelta(minutes=index) for index in range(delta.seconds // 60)]
    post_lunch = [t_r + timedelta(minutes=index) for index in range(16)]
    post_placebo = [t_b + timedelta(minutes=index) for index in range(16)]
    event: dict[str, object] = {
        "trade_date": trade_day.isoformat(),
        "session": "day",
        "schedule_id": "R045-LUNCH-1",
        "tS_jst": t_s.isoformat(),
        "tR_jst": t_r.isoformat(),
        "delta_minutes": delta.seconds // 60,
        "tB_jst": t_b.isoformat(),
        "L_window_start_jst": t_s.isoformat(),
        "L_window_end_jst": lunch_rows[-1].isoformat(),
        "P_window_start_jst": placebo_rows[0].isoformat(),
        "P_window_end_jst": placebo_rows[-1].isoformat(),
        "A_signal_bar_start_jst": lunch_rows[-1].isoformat(),
        "A_planned_entry_jst": t_r.isoformat(),
        "A_exit_signal_bar_start_jst": post_lunch[-2].isoformat(),
        "A_planned_exit_jst": post_lunch[-1].isoformat(),
        "G_signal_bar_start_jst": placebo_rows[-1].isoformat(),
        "G_planned_entry_jst": t_b.isoformat(),
        "G_exit_signal_bar_start_jst": post_placebo[-2].isoformat(),
        "G_planned_exit_jst": post_placebo[-1].isoformat(),
        "required_lunch_window_bars": len(lunch_rows),
        "required_placebo_window_bars": len(placebo_rows),
        "required_post_path_bars": 16,
        "status": "skipped",
    }
    if not cash_calendar.is_open(trade_day):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if day_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    if day_bars is None:
        event["reason"] = "DAY_SESSION_MISSING"
        return event
    by_time = {bar.ts_jst: bar for bar in day_bars}
    required = [*lunch_rows, *placebo_rows, *post_lunch, *post_placebo]
    rows = [by_time.get(stamp) for stamp in required]
    if any(row is None for row in rows):
        event["reason"] = "COMMON_WINDOWS_OR_FIXED_PATH_MISSING"
        return event
    concrete = [row for row in rows if row is not None]
    if any(not row.is_eligible or row.trade_date != trade_day or row.session is not Session.DAY for row in concrete):
        event["reason"] = "COMMON_WINDOWS_OR_FIXED_PATH_INELIGIBLE_OR_DAY_SESSION_MISMATCH"
        return event
    lunch = [by_time[stamp] for stamp in lunch_rows]
    placebo = [by_time[stamp] for stamp in placebo_rows]
    r_l = lunch[-1].close - lunch[0].open
    r_p = placebo[-1].close - placebo[0].open
    event.update(
        open_L_points=lunch[0].open,
        close_L_points=lunch[-1].close,
        rL_points=r_l,
        open_P_points=placebo[0].open,
        close_P_points=placebo[-1].close,
        rP_points=r_p,
    )
    if r_l == 0:
        event["reason"] = "ZERO_rL"
        return event
    if r_p == 0:
        event["reason"] = "ZERO_rP"
        return event
    event.update(
        status="eligible",
        reason="COMMON_E_NONZERO_CONTIGUOUS",
        rL_sign=1 if r_l > 0 else -1,
        rP_sign=1 if r_p > 0 else -1,
        lunch_direction="long" if r_l > 0 else "short",
        placebo_direction="long" if r_p > 0 else "short",
    )
    return event
