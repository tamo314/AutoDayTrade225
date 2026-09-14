from __future__ import annotations

from datetime import date, datetime, time, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r057 import nearest_rank, r057_event, tse_normal_close


def rows(day: date, close: int = 100, gap: int = 5) -> list[Bar]:
    result = []
    start = datetime.combine(day, time(9), JST)
    for i in range(70):
        o = close + gap if i == 0 else close + gap + i
        result.append(
            Bar(
                start + timedelta(minutes=i),
                day,
                day,
                Session.DAY,
                "synthetic",
                o,
                o + 2,
                o - 1,
                o + 1,
            )
        )
    final = datetime.combine(day, tse_normal_close(day), JST) - timedelta(minutes=1)
    result.append(
        Bar(final, day, day, Session.DAY, "synthetic", close, close + 1, close - 1, close)
    )
    return result


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset(), date(2020, 1, 1), date(2025, 12, 31))


def test_schedule_rank_equality_and_next_open_entry() -> None:
    day = date(2024, 11, 5)
    prior = date(2024, 11, 1)
    history = [
        (
            day - timedelta(days=i + 1),
            day - timedelta(days=i + 2),
            rows(day - timedelta(days=i + 1)),
            rows(day - timedelta(days=i + 2)),
            False,
        )
        for i in range(120)
    ]
    event = r057_event(day, rows(day), prior, rows(prior), history, calendar())
    assert tse_normal_close(date(2024, 11, 1)) == time(15)
    assert tse_normal_close(day) == time(15, 30)
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 75) == 3.0
    assert event["status"] == "E"
    assert event["planned_times"]["15"]["30"]["entry"].endswith("09:15:00+09:00")
    assert event["planned_times"]["15"]["30"]["exit"].endswith("09:45:00+09:00")


def test_scope_history_and_prefix_locks() -> None:
    day, prior = date(2024, 11, 5), date(2024, 11, 1)
    history = [
        (
            day - timedelta(days=i + 1),
            day - timedelta(days=i + 2),
            rows(day - timedelta(days=i + 1)),
            rows(day - timedelta(days=i + 2)),
            False,
        )
        for i in range(120)
    ]
    base = r057_event(day, rows(day), prior, rows(prior), history, calendar())
    changed = rows(day)
    changed[55] = Bar(
        changed[55].ts_jst, day, day, Session.DAY, "synthetic", 999, 999, 999, 999
    )
    assert r057_event(day, changed, prior, rows(prior), history, calendar())["x"] == base["x"]
    assert (
        r057_event(date(2025, 7, 1), rows(day), prior, rows(prior), history, calendar())["reason"]
        == "OUTSIDE_DEVELOPMENT"
    )
    assert (
        r057_event(day, rows(day), prior, rows(prior), history[:-1], calendar())["reason"]
        == "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
    )
