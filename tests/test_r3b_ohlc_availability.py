"""Synthetic S2 checks; the test never opens a market-data artifact."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar, TradingDay
from n225m_bt.config import JST, load_project_config
from n225m_bt.research.r3b_ohlc_availability import S2_COLUMNS, diagnose_frame


def _days() -> list[date]:
    return [date(2021, 1, 1) + timedelta(days=index) for index in range(101)]


def _frame(*, alter_outcome_prices: bool) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for target in _days():
        start = datetime.combine(target, datetime.min.time(), JST).replace(hour=8, minute=45)
        for offset in range(106):
            close = 100 + min(offset, 59) // 3
            if 60 <= offset <= 74:
                close = 110
            if alter_outcome_prices and offset in {75, 105}:
                close = 999_999
            rows.append(
                {
                    "ts_jst": start + timedelta(minutes=offset),
                    "trade_date": target,
                    "calendar_date": target,
                    "session": "day",
                    "schedule_version": "synthetic",
                    "open": 100,
                    "high": max(100, close),
                    "low": min(100, close),
                    "close": close,
                    "is_eligible": True,
                    "quality_flags": [],
                    "instrument": "N225M",
                    "series_type": "center_continuous",
                    "source": "225labo",
                }
            )
    return pl.DataFrame(rows).select(S2_COLUMNS)


def test_s2_selection_is_invariant_to_later_entry_and_exit_prices() -> None:
    _, sessions, _, _ = load_project_config(Path("config"))
    days = _days()
    calendar = ExchangeCalendar(
        [TradingDay(item, None, None, None, False, "synthetic") for item in days]
    )
    classifier = CalendarClassifier(sessions, calendar)
    baseline = diagnose_frame(_frame(alter_outcome_prices=False), classifier, days)
    mutated = diagnose_frame(_frame(alter_outcome_prices=True), classifier, days)
    assert baseline["scheduled_axis"] == mutated["scheduled_axis"]
    assert baseline["decision_time"] == mutated["decision_time"]
    assert baseline["post_decision_observability"] == mutated["post_decision_observability"]
    decision = baseline["decision_time"]
    assert isinstance(decision, dict)
    assert decision["main_candidate_counts"] == {"A": 1, "C": 0}
