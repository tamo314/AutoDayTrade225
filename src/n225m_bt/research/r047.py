"""Causal opening and normal-morning range-compression breakout events for R047."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def _range(rows: list[Bar]) -> tuple[int, int, int, float] | None:
    """Return p0/high/low/normalized range for exactly 30 eligible day bars."""
    if len(rows) != 30 or any(not row.is_eligible or row.session is not Session.DAY for row in rows):
        return None
    p0 = rows[0].open
    if p0 == 0:
        return None
    high, low = max(row.high for row in rows), min(row.low for row in rows)
    return p0, high, low, (high - low) / p0


def _rank(values: list[float], percentile: int) -> float:
    """Nearest-rank percentile."""
    return sorted(values)[(len(values) * percentile + 99) // 100 - 1]


def _breakout(
    rows: dict[datetime, Bar], start: datetime, high: int, low: int
) -> tuple[Bar, str] | None:
    """The first strict close breakout in exactly the 15 scheduled search minutes."""
    for index in range(15):
        row = rows[start + timedelta(minutes=index)]
        if row.close > high:
            return row, "long"
        if row.close < low:
            return row, "short"
    return None


def r047_event(
    classifier: CalendarClassifier,
    target: date,
    target_bars: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, bool]],
    *,
    target_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Create a common-E record using only the immediately prior 60 scheduled TSE days."""
    event: dict[str, object] = {"trade_date": target.isoformat(), "status": "skipped"}
    if not development_start <= target <= development_end:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if target_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    if len(history) != 60:
        event["reason"] = "HISTORY_NOT_EXACTLY_60_TSE_DAYS"
        return event
    if target_bars is None:
        event["reason"] = "DAY_SESSION_MISSING"
        return event
    start = classifier.session_open(target, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in target_bars}
    required = [start + timedelta(minutes=index) for index in range(151)]
    if any(stamp not in by_time for stamp in required):
        event["reason"] = "COMMON_WINDOWS_OR_EXECUTION_PATH_MISSING"
        return event
    if any(
        by_time[stamp].trade_date != target
        or by_time[stamp].session is not Session.DAY
        or not by_time[stamp].is_eligible
        for stamp in required
    ):
        event["reason"] = "COMMON_WINDOWS_OR_EXECUTION_PATH_INELIGIBLE"
        return event
    current = {
        "open": _range([by_time[start + timedelta(minutes=index)] for index in range(30)]),
        "placebo": _range([by_time[start + timedelta(minutes=index)] for index in range(60, 90)]),
    }
    if any(value is None for value in current.values()):
        event["reason"] = "TARGET_WINDOW_INVALID"
        return event
    references: dict[str, list[float]] = {"open": [], "placebo": []}
    audits: list[dict[str, object]] = []
    for prior, bars, quarantined in history:
        audit: dict[str, object] = {"trade_date": prior.isoformat()}
        if not development_start <= prior <= development_end:
            audit["reason"] = "OUTSIDE_DEVELOPMENT"
        elif quarantined or bars is None:
            audit["reason"] = "QUARANTINED_OR_MISSING"
        else:
            previous = {bar.ts_jst: bar for bar in bars}
            valid = True
            for name, offset in (("open", 0), ("placebo", 60)):
                rows = [
                    previous.get(classifier.session_open(prior, Session.DAY) + timedelta(minutes=offset + i))
                    for i in range(30)
                ]
                if any(row is None for row in rows):
                    valid = False
                    continue
                checked = [row for row in rows if row is not None]
                value = _range(checked) if all(row.trade_date == prior for row in checked) else None
                if value is None:
                    valid = False
                else:
                    references[name].append(value[3])
            audit["reason"] = "VALID_BOTH_WINDOWS" if valid else "WINDOW_MISSING_OR_INELIGIBLE"
        audits.append(audit)
    event["reference_audit"] = audits
    event["open_valid_reference_count"] = len(references["open"])
    event["placebo_valid_reference_count"] = len(references["placebo"])
    if any(len(values) < 50 for values in references.values()):
        event["reason"] = "INSUFFICIENT_VALID_REFERENCES"
        return event
    event.update(
        {
            "W0_start_jst": start.isoformat(),
            "W0_end_jst": (start + timedelta(minutes=29)).isoformat(),
            "WP_start_jst": (start + timedelta(minutes=60)).isoformat(),
            "WP_end_jst": (start + timedelta(minutes=89)).isoformat(),
            "A_exit_jst": (start + timedelta(minutes=90)).isoformat(),
            "G_exit_jst": (start + timedelta(minutes=150)).isoformat(),
        }
    )
    for name, offset in (("open", 0), ("placebo", 60)):
        value = current[name]
        assert value is not None
        p0, high, low, normalized = value
        event.update(
            {
                f"{name}_p0": p0,
                f"{name}_high": high,
                f"{name}_low": low,
                f"{name}_x": normalized,
                **{f"{name}_q{q}_x": _rank(references[name], q) for q in (20, 25, 30)},
            }
        )
        for q in (20, 25, 30):
            event[f"{name}_compressed_q{q}"] = normalized <= cast(float, event[f"{name}_q{q}_x"])
        search = _breakout(by_time, start + timedelta(minutes=30 + offset), high, low)
        if search is not None:
            row, direction = search
            prefix = "A" if name == "open" else "G"
            event.update(
                {
                    f"{name}_direction": direction,
                    f"{name}_breakout_close": row.close,
                    f"{name}_breakout_excess_points": row.close - high if direction == "long" else low - row.close,
                    f"{prefix}_signal_jst": row.ts_jst.isoformat(),
                    f"{prefix}_entry_jst": (row.ts_jst + timedelta(minutes=1)).isoformat(),
                    f"{name}_signal_minute": int((row.ts_jst - (start + timedelta(minutes=30 + offset))).total_seconds() // 60),
                }
            )
            event[f"{name}_breakout_status"] = "FIRST_STRICT_CLOSE_BREAKOUT"
        else:
            event[f"{name}_breakout_status"] = "NO_CLOSE_BREAKOUT"
    event.update({"status": "E", "reason": "COMMON_CAUSAL_ELIGIBLE"})
    return event
