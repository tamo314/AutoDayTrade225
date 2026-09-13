"""Schedule-derived, causal reference selection for R006-Q001."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.session_rules import regime_for_trade_date
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session


def session_groups(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    grouped: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.trade_date, bar.session)].append(bar)
    return {key: sorted(value, key=lambda item: item.ts_jst) for key, value in grouped.items()}


def night_reference_time(
    classifier: CalendarClassifier, trade_date: date
) -> tuple[datetime, datetime, str, str]:
    """Return C's bar start/known time using the versioned schedule only.

    A regime with explicit ``regular_end_next_day`` has a separately-modelled
    closing auction.  Its final normal one-minute bar starts one minute before
    regular end.  Older regimes have no finer frozen auction metadata, so the
    scheduled session close is the normal-bar endpoint; it is never inferred
    from the final observed row.
    """
    day = classifier.exchange_calendar.get(trade_date)
    if day is None or day.night_calendar_start_date is None:
        raise ValueError("NO_SCHEDULED_NIGHT_MAPPING")
    regime = regime_for_trade_date(classifier.sessions, day.night_calendar_start_date)
    regular_end = regime.night.regular_end_next_day
    if regular_end is None:
        end = classifier.session_close(trade_date, Session.NIGHT)
        basis = "scheduled_session_close_no_finer_auction_metadata"
    else:
        end = datetime.combine(trade_date, regular_end, JST)
        basis = "regular_end_before_separate_closing_auction"
    return end - timedelta(minutes=1), end, regime.id, basis


def reference_event(
    classifier: CalendarClassifier,
    trade_date: date,
    day_bars: list[Bar],
    night_bars: list[Bar] | None,
    *,
    reference_night_quarantined: bool = False,
) -> dict[str, object]:
    """Create one immutable event record without looking at future day bars."""
    S = classifier.session_open(trade_date, Session.DAY)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "S_day_open_bar_start_jst": S.isoformat(),
        "E_planned_entry_jst": (S + timedelta(minutes=1)).isoformat(),
        "EXIT_signal_bar_start_jst": (S + timedelta(minutes=60)).isoformat(),
        "EXIT_planned_fill_jst": (S + timedelta(minutes=61)).isoformat(),
        "status": "skipped",
    }
    try:
        C_start, C_known, schedule_version, reference_basis = night_reference_time(
            classifier, trade_date
        )
    except ValueError as exc:
        event["reason"] = str(exc)
        return event
    event.update(
        {
            "night_schedule_version": schedule_version,
            "C_normal_bar_start_jst": C_start.isoformat(),
            "C_close_known_jst": C_known.isoformat(),
            "C_selection_basis": reference_basis,
        }
    )
    by_day_timestamp = {bar.ts_jst: bar for bar in day_bars}
    signal_bar = by_day_timestamp.get(S)
    if signal_bar is None:
        event["reason"] = "DAY_OPEN_BAR_MISSING"
        return event
    if not signal_bar.is_eligible:
        event["reason"] = "DAY_OPEN_BAR_INELIGIBLE"
        return event
    if reference_night_quarantined:
        event["reason"] = "REFERENCE_NIGHT_QUARANTINED"
        return event
    if night_bars is None:
        event["reason"] = "REFERENCE_NIGHT_MISSING"
        return event
    by_night_timestamp = {bar.ts_jst: bar for bar in night_bars}
    reference_bar = by_night_timestamp.get(C_start)
    if reference_bar is None:
        event["reason"] = "REFERENCE_NORMAL_BAR_MISSING"
        return event
    if not reference_bar.is_eligible:
        event["reason"] = "REFERENCE_NORMAL_BAR_INELIGIBLE"
        return event
    if reference_bar.calendar_date != C_start.date():
        event["reason"] = "REFERENCE_NORMAL_BAR_CALENDAR_DATE_MISMATCH"
        return event
    gap = signal_bar.open - reference_bar.close
    event.update(
        {
            "O_day_open": signal_bar.open,
            "C_night_normal_close": reference_bar.close,
            "G_points": gap,
        }
    )
    if gap == 0:
        event["reason"] = "ZERO_GAP"
        return event
    event.update({"status": "eligible", "reason": "G_NONZERO"})
    return event


def condition_side(
    condition: Literal["A_gap_reversal", "B_always_long", "C_always_short"], gap: int
) -> str | None:
    if gap == 0:
        return None
    if condition == "A_gap_reversal":
        return "short" if gap > 0 else "long"
    return "long" if condition == "B_always_long" else "short"
