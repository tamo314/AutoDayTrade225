"""Causal 08:45--08:59 futures-opening event selection for R023-Q001."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r006 import night_reference_time
from n225m_bt.research.r020 import TSECashMarketCalendar


def cash_open_reversal_event(
    classifier: CalendarClassifier,
    trade_date: date,
    day_bars: list[Bar] | None,
    night_bars: list[Bar] | None,
    cash_calendar: TSECashMarketCalendar,
    *,
    day_quarantined: bool = False,
    night_quarantined: bool = False,
) -> dict[str, object]:
    """Select one R023 event using only information known at 08:59 JST.

    The night close timestamp is derived from the versioned schedule, never from
    the last observed night row.  No prior night is substituted when this exact
    reference is unavailable.
    """
    opening = datetime(trade_date.year, trade_date.month, trade_date.day, 8, 45, tzinfo=JST)
    signal = opening + timedelta(minutes=14)
    entry, exit_signal, exit_time = signal + timedelta(minutes=1), signal + timedelta(minutes=30), signal + timedelta(minutes=31)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "O_bar_start_jst": opening.isoformat(),
        "P_window_end_jst": signal.isoformat(),
        "t_signal_bar_start_jst": signal.isoformat(),
        "E_planned_entry_jst": entry.isoformat(),
        "EXIT_signal_bar_start_jst": exit_signal.isoformat(),
        "X_planned_exit_jst": exit_time.isoformat(),
        "required_day_window_bars": 15,
        "status": "skipped",
    }
    if not cash_calendar.is_open(trade_date):
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if day_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    if night_quarantined:
        event["reason"] = "REFERENCE_NIGHT_QUARANTINED"
        return event
    if day_bars is None:
        event["reason"] = "DAY_SESSION_MISSING"
        return event
    by_day_time = {bar.ts_jst: bar for bar in day_bars}
    expected = [opening + timedelta(minutes=offset) for offset in range(15)]
    window = [by_day_time.get(stamp) for stamp in expected]
    if any(bar is None for bar in window):
        event["reason"] = "WINDOW_0845_TO_0859_MISSING"
        return event
    concrete = [bar for bar in window if bar is not None]
    if any(
        not bar.is_eligible or bar.trade_date != trade_date or bar.session is not Session.DAY
        for bar in concrete
    ):
        event["reason"] = "DAY_WINDOW_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    try:
        night_close_start, night_close_known, schedule_version, reference_basis = (
            night_reference_time(classifier, trade_date)
        )
    except ValueError as exc:
        event["reason"] = str(exc)
        return event
    event.update(
        {
            "N_normal_bar_start_jst": night_close_start.isoformat(),
            "N_close_known_jst": night_close_known.isoformat(),
            "night_schedule_version": schedule_version,
            "N_selection_basis": reference_basis,
        }
    )
    if night_bars is None:
        event["reason"] = "REFERENCE_NIGHT_MISSING_OR_OUTSIDE_DEVELOPMENT"
        return event
    reference = {bar.ts_jst: bar for bar in night_bars}.get(night_close_start)
    if reference is None:
        event["reason"] = "REFERENCE_NORMAL_NIGHT_BAR_MISSING"
        return event
    if (
        not reference.is_eligible
        or reference.trade_date != trade_date
        or reference.session is not Session.NIGHT
        or reference.calendar_date != night_close_start.date()
    ):
        event["reason"] = "REFERENCE_NORMAL_NIGHT_BAR_INELIGIBLE_OR_MISMATCH"
        return event
    opening_price, close_price, night_close = concrete[0].open, concrete[-1].close, reference.close
    p, gap = close_price - opening_price, opening_price - night_close
    event.update(
        {
            "N_points": night_close,
            "O_points": opening_price,
            "C_points": close_price,
            "P_points": p,
            "G_points": gap,
        }
    )
    if p == 0:
        event["reason"] = "ZERO_P"
        return event
    if gap == 0:
        event["reason"] = "ZERO_G"
        return event
    p_follow = "long" if p > 0 else "short"
    gap_follow = "long" if gap > 0 else "short"
    event.update(
        {
            "status": "eligible",
            "reason": "TSE_OPEN_P_AND_G_NONZERO",
            "P_sign": 1 if p > 0 else -1,
            "G_sign": 1 if gap > 0 else -1,
            "A_direction": "short" if p_follow == "long" else "long",
            "D_direction": p_follow,
            "F_gap_direction": "short" if gap_follow == "long" else "long",
        }
    )
    return event
