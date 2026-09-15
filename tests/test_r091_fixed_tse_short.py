from __future__ import annotations

from datetime import date, datetime, timedelta

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r091_fixed_tse_short import feasibility, scheduled_event, tse_cash_close


def _bars(day: date) -> list[Bar]:
    close = tse_cash_close(day)
    stamps = [datetime(day.year, day.month, day.day, 8, 59, tzinfo=JST), datetime(day.year, day.month, day.day, 9, tzinfo=JST), close - timedelta(minutes=1), close]
    return [Bar(stamp, day, day, Session.DAY, "synthetic", 100, 100, 100, 100) for stamp in stamps]


def test_old_new_cash_close_and_schedule_not_observed_last_bar() -> None:
    old, new = date(2024, 11, 1), date(2024, 11, 5)
    assert tse_cash_close(old).isoformat().endswith("15:00:00+09:00")
    assert tse_cash_close(new).isoformat().endswith("15:30:00+09:00")
    event = scheduled_event(new, [*_bars(new), Bar(datetime(2024, 11, 5, 15, 44, tzinfo=JST), new, new, Session.DAY, "synthetic", 1, 1, 1, 1)], r004_day_quarantined=False)
    assert event["exit_fill_planned_jst"] == "2024-11-05T15:30:00+09:00"
    assert event["completion_status"] == "COMPLETE"


def test_exit_missing_never_cancels_precommitted_entry_order() -> None:
    day = date(2024, 11, 5)
    bars = _bars(day)
    event = scheduled_event(day, bars[:-1], r004_day_quarantined=False)
    assert event["entry_eligible"] is True
    assert event["order_submitted"] is True
    assert event["completion_status"] == "UNRESOLVED_EXIT_FILL"


def test_r004_and_pnl_free_gate_are_explicit() -> None:
    day = date(2024, 11, 5)
    isolated = scheduled_event(day, _bars(day), r004_day_quarantined=True)
    assert isolated["completion_status"] == "NO_ORDER_R004_DAY_SESSION_QUARANTINED"
    events = [scheduled_event(day, _bars(day), r004_day_quarantined=False) for _ in range(900)]
    result = feasibility(events)
    assert result["scope"].startswith("PnL-free")
    assert result["gate"]["a_short_complete_trades_at_least_900"] is True
