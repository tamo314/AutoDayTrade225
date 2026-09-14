"""Causal opening and normal-morning path-efficiency events for R046-Q001."""

from __future__ import annotations

from datetime import date, timedelta
from functools import cmp_to_key

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session


def _path(rows: list[Bar]) -> tuple[int, int, int] | None:
    """Return (p0, r, L) for exactly thirty scheduled eligible minute bars."""
    if len(rows) != 30 or any(
        not row.is_eligible or row.session is not Session.DAY for row in rows
    ):
        return None
    prices = [rows[0].open, *(row.close for row in rows)]
    path_length = sum(
        abs(right - left) for left, right in zip(prices[:-1], prices[1:], strict=True)
    )
    return prices[0], prices[-1] - prices[0], path_length


def _ordered(left: tuple[int, int], right: tuple[int, int]) -> int:
    """Order (r, L) exact rational efficiencies |r|/L without float rounding."""
    value = abs(left[0]) * right[1] - abs(right[0]) * left[1]
    return (value > 0) - (value < 0)


def _rank(paths: list[tuple[int, int]], percentile: int) -> tuple[int, int]:
    ordered = sorted(paths, key=cmp_to_key(_ordered))
    return ordered[(len(ordered) * percentile + 99) // 100 - 1]


def r046_event(
    classifier: CalendarClassifier,
    target: date,
    target_bars: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, bool]],
    *,
    target_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Make the common-E ledger using only the prior 60 scheduled TSE business days.

    ``history`` is newest first and must contain exactly those 60 dates. Invalid
    references are retained rather than replaced by older observations.
    """
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
    # Both 30-minute observation windows plus every bar needed to carry the two
    # fixed-time orders through their exits.  No missing price is substituted.
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
    path_rows = [
        [by_time[start + timedelta(minutes=offset + index)] for index in range(30)]
        for offset in (0, 60)
    ]
    if any(
        any(
            row.trade_date != target or row.session is not Session.DAY or not row.is_eligible
            for row in rows
        )
        for rows in path_rows
    ):
        event["reason"] = "COMMON_WINDOWS_OR_EXECUTION_PATH_INELIGIBLE"
        return event
    current = [_path(rows) for rows in path_rows]
    if any(item is None for item in current):
        event["reason"] = "COMMON_WINDOWS_INVALID"
        return event
    paths = [item for item in current if item is not None]
    if any(r == 0 or length == 0 for _, r, length in paths):
        event["reason"] = "TARGET_ZERO_R_OR_L"
        return event
    references: list[list[tuple[int, int]]] = [[], []]
    audits: list[dict[str, object]] = []
    for prior, bars, quarantined in history:
        audit: dict[str, object] = {"trade_date": prior.isoformat()}
        if not development_start <= prior <= development_end:
            audit["reason"] = "OUTSIDE_DEVELOPMENT"
        elif quarantined or bars is None:
            audit["reason"] = "QUARANTINED_OR_MISSING"
        else:
            previous = {bar.ts_jst: bar for bar in bars}
            candidate: list[tuple[int, int, int] | None] = []
            for offset in (0, 60):
                rows = [
                    previous.get(
                        classifier.session_open(prior, Session.DAY)
                        + timedelta(minutes=offset + index)
                    )
                    for index in range(30)
                ]
                if any(row is None for row in rows):
                    candidate.append(None)
                else:
                    valid = [row for row in rows if row is not None]
                    candidate.append(
                        _path(valid) if all(row.trade_date == prior for row in valid) else None
                    )
            if any(item is None or item[1] == 0 or item[2] == 0 for item in candidate):
                audit["reason"] = "WINDOW_MISSING_INELIGIBLE_OR_ZERO"
            else:
                for index, item in enumerate(candidate):
                    assert item is not None
                    references[index].append((item[1], item[2]))
                audit["reason"] = "VALID_BOTH_WINDOWS"
        audits.append(audit)
    event["reference_audit"] = audits
    event["open_valid_reference_count"], event["placebo_valid_reference_count"] = map(
        len, references
    )
    if any(len(items) < 50 for items in references):
        event["reason"] = "INSUFFICIENT_VALID_REFERENCES"
        return event
    event.update(
        {
            "W0_start_jst": start.isoformat(),
            "W0_end_jst": (start + timedelta(minutes=29)).isoformat(),
            "WP_start_jst": (start + timedelta(minutes=60)).isoformat(),
            "WP_end_jst": (start + timedelta(minutes=89)).isoformat(),
            "A_signal_jst": (start + timedelta(minutes=29)).isoformat(),
            "A_entry_jst": (start + timedelta(minutes=30)).isoformat(),
            "A_exit_jst": (start + timedelta(minutes=90)).isoformat(),
            "G_signal_jst": (start + timedelta(minutes=89)).isoformat(),
            "G_entry_jst": (start + timedelta(minutes=90)).isoformat(),
            "G_exit_jst": (start + timedelta(minutes=150)).isoformat(),
        }
    )
    for label, (p0, r, length), reference in zip(
        ("open", "placebo"), paths, references, strict=True
    ):
        record: dict[str, object] = {
            f"{label}_p0": p0,
            f"{label}_r": r,
            f"{label}_L": length,
            f"{label}_e": abs(r) / length,
            f"{label}_direction": "long" if r > 0 else "short",
        }
        for percentile in (70, 75, 80):
            q_r, q_l = _rank(reference, percentile)
            record[f"{label}_q{percentile}_r"] = q_r
            record[f"{label}_q{percentile}_L"] = q_l
            record[f"{label}_high_q{percentile}"] = abs(r) * q_l >= abs(q_r) * length
        event.update(record)
    event.update({"status": "E", "reason": "COMMON_CAUSAL_ELIGIBLE"})
    return event
