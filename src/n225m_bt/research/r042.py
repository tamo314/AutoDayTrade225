"""Causal full-night directional-terminal events for R042-Q002/Q003."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import ceil

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r022 import normal_session_end


def _normal_night(
    classifier: CalendarClassifier, trade_date: date, bars: list[Bar] | None
) -> tuple[list[Bar] | None, dict[str, object], str | None]:
    """Return only the complete scheduled normal night; no observed-end substitute."""
    try:
        opening = classifier.session_open(trade_date, Session.NIGHT)
        end = normal_session_end(classifier, trade_date, Session.NIGHT)
    except ValueError:
        return None, {}, "NO_SCHEDULED_NIGHT_MAPPING"
    metadata: dict[str, object] = {
        "N_scheduled_open_jst": opening.isoformat(),
        "N_normal_end_jst": end.isoformat(),
        "N_final_normal_bar_start_jst": (end - timedelta(minutes=1)).isoformat(),
        "N_expected_bar_count": int((end - opening).total_seconds() // 60),
    }
    if bars is None:
        return None, metadata, "REFERENCE_NIGHT_MISSING"
    by_time = {bar.ts_jst: bar for bar in bars}
    rows = [
        by_time.get(opening + timedelta(minutes=index))
        for index in range(int((end - opening).total_seconds() // 60))
    ]
    if any(row is None for row in rows):
        return None, metadata, "REFERENCE_FULL_NORMAL_NIGHT_MISSING"
    normal = [row for row in rows if row is not None]
    if any(
        not row.is_eligible or row.trade_date != trade_date or row.session is not Session.NIGHT
        for row in normal
    ):
        return None, metadata, "REFERENCE_NIGHT_INELIGIBLE_OR_SESSION_MISMATCH"
    return normal, metadata, None


def night_terminal_range_event(
    classifier: CalendarClassifier,
    trade_date: date,
    night_bars: list[Bar] | None,
    history: list[tuple[date, list[Bar] | None, bool]],
    *,
    day_quarantined: bool = False,
    night_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
    normal_cache: dict[date, tuple[list[Bar] | None, dict[str, object], str | None]] | None = None,
    require_rolling_range_layer: bool = True,
) -> dict[str, object]:
    """Classify an event using only the preceding complete night and its history.

    The 08:45 day bar is deliberately absent from all state calculations.  It
    merely hosts the later, fresh in-session engine signal in the runner.
    """
    day_open = datetime(trade_date.year, trade_date.month, trade_date.day, 8, 45, tzinfo=JST)
    event: dict[str, object] = {
        "trade_date": trade_date.isoformat(),
        "session": "day",
        "t_signal_bar_start_jst": day_open.isoformat(),
        "E_planned_entry_jst": (day_open + timedelta(minutes=1)).isoformat(),
        "EXIT_signal_bar_start_jst": (day_open + timedelta(minutes=60)).isoformat(),
        "X_planned_exit_jst": (day_open + timedelta(minutes=61)).isoformat(),
        "status": "skipped",
    }
    if not development_start <= trade_date <= development_end:
        event["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return event
    if day_quarantined:
        event["reason"] = "DAY_SESSION_QUARANTINED"
        return event
    if night_quarantined:
        event["reason"] = "REFERENCE_NIGHT_SESSION_QUARANTINED"
        return event

    def normal_for(
        ref_date: date, ref_bars: list[Bar] | None
    ) -> tuple[list[Bar] | None, dict[str, object], str | None]:
        if normal_cache is not None and ref_date in normal_cache:
            return normal_cache[ref_date]
        result = _normal_night(classifier, ref_date, ref_bars)
        if normal_cache is not None:
            normal_cache[ref_date] = result
        return result

    normal, metadata, reason = normal_for(trade_date, night_bars)
    event.update(metadata)
    if reason is not None:
        event["reason"] = reason
        return event
    assert normal is not None
    on, cn = normal[0].open, normal[-1].close
    hn, ln = max(row.high for row in normal), min(row.low for row in normal)
    r_n, r_n_range = cn - on, hn - ln
    event.update(
        {
            "ON_points": on,
            "CN_points": cn,
            "HN_points": hn,
            "LN_points": ln,
            "rN_points": r_n,
            "RN_points": r_n_range,
        }
    )
    if r_n_range <= 0:
        event["reason"] = "NONPOSITIVE_RN"
        return event
    if r_n == 0:
        event["reason"] = "ZERO_rN"
        return event
    if require_rolling_range_layer:
        if len(history) != 60:
            event["reason"] = "HISTORY_60_SCHEDULED_NIGHTS_SHORT"
            return event
        values: list[float] = []
        audit: list[dict[str, object]] = []
        for ref_day, ref_bars, quarantined in history:
            item: dict[str, object] = {"trade_date": ref_day.isoformat()}
            if not development_start <= ref_day <= development_end:
                item["reason"] = "OUTSIDE_DEVELOPMENT"
            elif quarantined:
                item["reason"] = "NIGHT_SESSION_QUARANTINED"
            else:
                rows, _, ref_reason = normal_for(ref_day, ref_bars)
                if ref_reason is not None:
                    item["reason"] = ref_reason
                else:
                    assert rows is not None
                    rn, on_ref = (
                        max(row.high for row in rows) - min(row.low for row in rows),
                        rows[0].open,
                    )
                    if rn <= 0 or on_ref == 0:
                        item["reason"] = "NONPOSITIVE_RN_OR_ZERO_ON"
                    else:
                        value = rn / on_ref
                        values.append(value)
                        item.update({"RN_over_ON": value, "reason": "VALID"})
            audit.append(item)
        event.update({"rolling_reference_audit": audit, "valid_reference_count": len(values)})
        if len(values) < 50:
            event["reason"] = "INSUFFICIENT_VALID_RN_OVER_ON_REFERENCES"
            return event
        qm = sorted(values)[ceil(len(values) * 0.5) - 1]
    terminal = 4 * (cn - ln) >= 3 * r_n_range if r_n > 0 else 4 * (hn - cn) >= 3 * r_n_range
    direction = "long" if r_n > 0 else "short"
    event.update(
        {
            "RN_over_ON": r_n_range / on,
            "rN_sign": 1 if r_n > 0 else -1,
            "rN_direction": direction,
            "terminal_expression": 4 * (cn - ln) - 3 * r_n_range
            if r_n > 0
            else 4 * (hn - cn) - 3 * r_n_range,
            "terminal": terminal,
            "A_direction": direction,
            "F_reverse_direction": "short" if direction == "long" else "long",
            "status": "terminal" if terminal else "nonterminal",
            "reason": "DIRECTIONAL_OUTER_QUARTILE_EQUALITY_INCLUSIVE"
            if terminal
            else "DIRECTIONAL_OUTER_QUARTILE_NOT_MET",
        }
    )
    if require_rolling_range_layer:
        event.update(
            {
                "QM_RN_over_ON": qm,
                "range_layer": "low" if r_n_range / on <= qm else "high",
                "range_layer_rule": "RN/ON<=QM low; target excluded; valid scheduled references only",
            }
        )
    return event
