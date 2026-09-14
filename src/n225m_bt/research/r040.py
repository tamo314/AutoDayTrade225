"""Causal prior-day range compression / accepted-breakout events for R040-Q001."""

from __future__ import annotations

from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r022 import normal_session_end


def _normal_rows(
    classifier: CalendarClassifier, day: date, bars: list[Bar] | None
) -> list[Bar] | None:
    """Return only the scheduled continuous normal day bars; never infer an end."""
    if bars is None:
        return None
    start, end = (
        classifier.session_open(day, Session.DAY),
        normal_session_end(classifier, day, Session.DAY),
    )
    rows_by_time = {row.ts_jst: row for row in bars}
    rows = [
        rows_by_time.get(start + timedelta(minutes=index))
        for index in range((end - start).seconds // 60)
    ]
    if any(row is None for row in rows):
        return None
    valid = [row for row in rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != day or row.session is not Session.DAY
        for row in valid
    ):
        return None
    return valid


def _range(
    classifier: CalendarClassifier, day: date, bars: list[Bar] | None
) -> tuple[int, int, int] | None:
    rows = _normal_rows(classifier, day, bars)
    if rows is None:
        return None
    high, low = max(row.high for row in rows), min(row.low for row in rows)
    return high, low, high - low


def prior_day_compression_acceptance_event(
    classifier: CalendarClassifier,
    target: date,
    target_bars: list[Bar] | None,
    prior: tuple[date, list[Bar] | None, bool] | None,
    history: list[tuple[date, list[Bar] | None, bool]],
    *,
    target_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Classify d using p and exactly p's preceding 60 scheduled day sessions.

    The caller supplies p and history from the schedule, newest first.  Missing or
    invalid planned references remain in the ledger and are never backfilled.
    """
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "session": "day",
        "status": "skipped",
    }
    if not development_start <= target <= development_end:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    if target_quarantined:
        event["reason"] = "TARGET_DAY_SESSION_QUARANTINED"
        return event
    if prior is None:
        event["reason"] = "NO_IMMEDIATE_PRIOR_SCHEDULED_DAY"
        return event
    prior_day, prior_bars, prior_quarantined = prior
    event["p_trade_date"] = prior_day.isoformat()
    if not development_start <= prior_day <= development_end:
        event["reason"] = "PRIOR_DAY_OUTSIDE_DEVELOPMENT"
        return event
    if prior_quarantined:
        event["reason"] = "PRIOR_DAY_SESSION_QUARANTINED"
        return event
    prior_range = _range(classifier, prior_day, prior_bars)
    if prior_range is None:
        event["reason"] = "PRIOR_DAY_FULL_NORMAL_SESSION_MISSING_OR_INELIGIBLE"
        return event
    pdh, pdl, width = prior_range
    event.update({"PDH_points": pdh, "PDL_points": pdl, "W_points": width})
    if width <= 0:
        event["reason"] = "PRIOR_DAY_ZERO_RANGE"
        return event
    if len(history) != 60:
        event["reason"] = "HISTORY_SHORT"
        return event
    references: list[int] = []
    audit: list[dict[str, object]] = []
    for day, bars, quarantined in history:
        item: dict[str, object] = {"trade_date": day.isoformat()}
        if not development_start <= day <= development_end:
            item["reason"] = "OUTSIDE_DEVELOPMENT"
        elif quarantined:
            item["reason"] = "QUARANTINED"
        else:
            value = _range(classifier, day, bars)
            if value is None:
                item["reason"] = "FULL_NORMAL_SESSION_MISSING_OR_INELIGIBLE"
            elif value[2] <= 0:
                item.update({"W_points": value[2], "reason": "ZERO_RANGE"})
            else:
                item.update({"W_points": value[2], "reason": "VALID"})
                references.append(value[2])
        audit.append(item)
    event.update({"history": audit, "valid_reference_count": len(references)})
    if len(references) < 50:
        event["reason"] = "INSUFFICIENT_VALID_REFERENCES"
        return event
    threshold = sorted(references)[(len(references) + 3) // 4 - 1]
    compressed = width < threshold
    event.update(
        {
            "QW_points": threshold,
            "compression": compressed,
            "compression_rule": "W < QW (strict; ties noncompressed)",
        }
    )
    start = classifier.session_open(target, Session.DAY)
    event.update(
        {
            "S_scheduled_open_jst": start.isoformat(),
            "search_start_jst": start.isoformat(),
            "search_end_jst": (start + timedelta(minutes=119)).isoformat(),
            "confirmation_delay_bars": 5,
        }
    )
    target_map = {row.ts_jst: row for row in target_bars or []}
    first = target_map.get(start)
    if first is None:
        event["reason"] = "TARGET_FIRST_OPEN_MISSING"
        return event
    if not first.is_eligible or first.trade_date != target or first.session is not Session.DAY:
        event["reason"] = "TARGET_FIRST_OPEN_INELIGIBLE_OR_SESSION_MISMATCH"
        return event
    event["target_first_open_points"] = first.open
    if not pdl <= first.open <= pdh:
        event["reason"] = "TARGET_OPEN_OUTSIDE_PRIOR_RANGE_GAP"
        return event
    for offset in range(120):
        breakout = target_map.get(start + timedelta(minutes=offset))
        if breakout is None:
            event["reason"] = "BREAKOUT_SEARCH_MISSING"
            return event
        if (
            not breakout.is_eligible
            or breakout.trade_date != target
            or breakout.session is not Session.DAY
        ):
            event["reason"] = "BREAKOUT_SEARCH_INELIGIBLE_OR_SESSION_MISMATCH"
            return event
        if not (breakout.close > pdh or breakout.close < pdl):
            continue
        direction = "long" if breakout.close > pdh else "short"
        confirmation = [
            target_map.get(breakout.ts_jst + timedelta(minutes=index)) for index in range(6)
        ]
        if any(row is None for row in confirmation):
            event.update(
                {
                    "reason": "CONFIRMATION_WINDOW_MISSING",
                    "breakout_ts_jst": breakout.ts_jst.isoformat(),
                }
            )
            return event
        confirmation_rows = [row for row in confirmation if row is not None]
        if any(
            not row.is_eligible or row.trade_date != target or row.session is not Session.DAY
            for row in confirmation_rows
        ):
            event.update(
                {
                    "reason": "CONFIRMATION_WINDOW_INELIGIBLE_OR_SESSION_MISMATCH",
                    "breakout_ts_jst": breakout.ts_jst.isoformat(),
                }
            )
            return event
        k = confirmation_rows[-1]
        accepted = k.close > pdh if direction == "long" else k.close < pdl
        event.update(
            {
                "status": "compressed_accepted"
                if compressed and accepted
                else "noncompressed_accepted"
                if accepted
                else "unaccepted",
                "reason": "FIRST_STRICT_CLOSE_BREAK_FIXED_5M_ACCEPTANCE"
                if accepted
                else "FIRST_STRICT_CLOSE_BREAK_NOT_ACCEPTED",
                "breakout_direction": direction,
                "breakout_ts_jst": breakout.ts_jst.isoformat(),
                "breakout_close": breakout.close,
                "k_ts_jst": k.ts_jst.isoformat(),
                "k_close": k.close,
                "E_planned_entry_jst": (k.ts_jst + timedelta(minutes=1)).isoformat(),
                "EXIT_signal_bar_start_jst": (k.ts_jst + timedelta(minutes=60)).isoformat(),
                "X_planned_exit_jst": (k.ts_jst + timedelta(minutes=61)).isoformat(),
            }
        )
        return event
    event["reason"] = "NO_STRICT_CLOSE_BREAK_IN_FIRST_120_BARS"
    return event
