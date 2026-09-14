"""Causal day-opening path-efficiency events for R037-Q001."""

from __future__ import annotations

from datetime import date, timedelta
from functools import cmp_to_key

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def _opening_path(
    classifier: CalendarClassifier, day: date, bars: list[Bar] | None
) -> tuple[int, int] | None:
    """Return D and V from exactly the first 30 scheduled day bars, or None."""
    if bars is None:
        return None
    start = classifier.session_open(day, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in bars}
    rows = [by_time.get(start + timedelta(minutes=index)) for index in range(30)]
    if any(row is None for row in rows):
        return None
    valid = [row for row in rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != day or row.session is not Session.DAY
        for row in valid
    ):
        return None
    prices = [valid[0].open, *(row.close for row in valid)]
    return prices[-1] - prices[0], sum(
        abs(right - left) for left, right in zip(prices[:-1], prices[1:], strict=True)
    )


def _rank(values: list[int], fraction: float) -> int:
    """Nearest-rank statistic; callers provide a nonempty list."""
    numerator, denominator = {0.25: (1, 4), 0.50: (1, 2), 0.75: (3, 4)}[fraction]
    return sorted(values)[(len(values) * numerator + denominator - 1) // denominator - 1]


def _efficiency_order(left: tuple[int, int], right: tuple[int, int]) -> int:
    """Order exact non-negative rational efficiencies |D| / V."""
    comparison = abs(left[0]) * right[1] - abs(right[0]) * left[1]
    return (comparison > 0) - (comparison < 0)


def opening_path_efficiency_event(
    classifier: CalendarClassifier,
    target: date,
    target_bars: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, bool]],
    *,
    target_quarantined: bool = False,
    target_tse_open: bool = True,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Classify a day using exactly 60 preceding *scheduled* day sessions.

    The supplied history must be newest first.  Invalid references are retained as
    invalid observations, never replaced by older sessions.
    """
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "session": "day",
        "status": "skipped",
    }
    if not development_start <= target <= development_end:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    if not target_tse_open:
        event["reason"] = "TSE_CASH_MARKET_CLOSED"
        return event
    if target_quarantined:
        event["reason"] = "TARGET_QUARANTINED"
        return event
    if len(history) != 60:
        event["reason"] = "HISTORY_SHORT"
        return event
    start = classifier.session_open(target, Session.DAY)
    event.update(
        {
            "S_scheduled_open_jst": start.isoformat(),
            "opening_end_jst": (start + timedelta(minutes=29)).isoformat(),
            "required_opening_bars": 30,
            "required_history_sessions": 60,
        }
    )
    target_path = _opening_path(classifier, target, target_bars)
    if target_path is None:
        event["reason"] = "TARGET_OPENING_MISSING_OR_INELIGIBLE"
        return event
    d, v = target_path
    event.update({"D_points": d, "V_points": v, "eta": abs(d) / v if v else None})
    references: list[tuple[int, int]] = []
    reference_audits: list[dict[str, object]] = []
    for previous, bars, quarantined in history:
        record: dict[str, object] = {"trade_date": previous.isoformat()}
        if not development_start <= previous <= development_end:
            record["reason"] = "OUTSIDE_DEVELOPMENT"
        elif quarantined:
            record["reason"] = "QUARANTINED"
        else:
            path = _opening_path(classifier, previous, bars)
            if path is None:
                record["reason"] = "MISSING_OR_INELIGIBLE"
            elif path[0] == 0 or path[1] == 0:
                record.update({"D_points": path[0], "V_points": path[1], "reason": "ZERO_D_OR_V"})
            else:
                record.update({"D_points": path[0], "V_points": path[1], "reason": "VALID"})
                references.append(path)
        reference_audits.append(record)
    event["history"] = reference_audits
    event["valid_reference_count"] = len(references)
    if len(references) < 50:
        event["reason"] = "INSUFFICIENT_VALID_REFERENCES"
        return event
    # Sort as exact rational |D|/V, so classification never depends on float rounding.
    ordered = sorted(references, key=cmp_to_key(_efficiency_order))
    q_d, q_v = ordered[(3 * len(ordered) + 3) // 4 - 1]
    amplitudes = [abs(item[0]) for item in references]
    q25, q50, q75 = (_rank(amplitudes, fraction) for fraction in (0.25, 0.50, 0.75))
    magnitude = abs(d)
    magnitude_bucket = (
        1 if magnitude <= q25 else 2 if magnitude <= q50 else 3 if magnitude <= q75 else 4
    )
    event.update(
        {
            "Q_eta_D_points": q_d,
            "Q_eta_V_points": q_v,
            "Q_eta": abs(q_d) / q_v,
            "Q_abs_D_25_points": q25,
            "Q_abs_D_50_points": q50,
            "Q_abs_D_75_points": q75,
            "magnitude_bucket": magnitude_bucket,
        }
    )
    if d == 0 or v == 0:
        event["reason"] = "TARGET_ZERO_D_OR_V"
        return event
    high = abs(d) * q_v > abs(q_d) * v
    event.update(
        {
            "high_efficiency": high,
            "comparison": "abs(D)*Q_eta_V > abs(Q_eta_D)*V (strict; ties are non-high)",
            "direction": "long" if d > 0 else "short",
            "signal_ts_jst": (start + timedelta(minutes=29)).isoformat(),
            "E_planned_entry_jst": (start + timedelta(minutes=30)).isoformat(),
            "EXIT_signal_bar_start_jst": (start + timedelta(minutes=89)).isoformat(),
            "X_planned_exit_jst": (start + timedelta(minutes=90)).isoformat(),
            "status": "high_efficiency" if high else "noneff",
            "reason": "VALID_OPENING_PATH",
        }
    )
    return event
