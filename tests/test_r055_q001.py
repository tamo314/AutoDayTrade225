from datetime import date, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r055 import normal_night_end, r055_event
from n225m_bt.strategies.r055_night_extreme_follow import R055NightExtremeFollowStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def bars(c: CalendarClassifier, day: date, night_close: int = 10100) -> tuple[list[Bar], list[Bar]]:
    n0 = c.session_open(day, Session.NIGHT)
    cn, _, _, _ = normal_night_end(c, day)
    n = [
        Bar(n0 + timedelta(minutes=i), day, (n0 + timedelta(minutes=i)).date(), Session.NIGHT, "test", 10000, 10100, 9900, night_close)
        for i in range(int((cn - n0).total_seconds() // 60) + 1)
    ]
    ds = c.session_open(day, Session.DAY)
    d = [
        Bar(ds + timedelta(minutes=i), day, day, Session.DAY, "test", 10000 + i, 10000 + i, 10000 + i, 10000 + i)
        for i in range(46)
    ]
    return n, d


def history(c: CalendarClassifier, day: date) -> list[tuple[date, list[Bar], bool]]:
    cal = c.exchange_calendar
    result = []
    current = day
    for _ in range(120):
        record = cal.get(current)
        assert record is not None and record.previous_trade_date is not None
        current = record.previous_trade_date
        n, _ = bars(c, current)
        result.append((current, n, False))
    return result


def test_full_scheduled_night_causal_rank_equality_and_prefix() -> None:
    c, day = classifier(), date(2024, 11, 5)
    n, d = bars(c, day)
    event = r055_event(c, day, n, d, history(c, day))
    assert event["status"] == "E"
    assert event["x"] == event["q90"]
    assert event["planned_entry_jst"].endswith("08:45:00+09:00")
    assert event["planned_exit30_jst"].endswith("09:15:00+09:00")
    assert event == r055_event(c, day, n, d, history(c, day))


def test_history_zero_holdout_missing_and_isolation_are_not_substituted() -> None:
    c, day = classifier(), date(2024, 11, 5)
    n, d = bars(c, day)
    assert r055_event(c, day, n, d, history(c, day)[:-1])["reason"] == "HISTORY_NOT_EXACTLY_120_SCHEDULED_TRADE_DATES"
    zero_n, _ = bars(c, day, 10000)
    zero = r055_event(c, day, zero_n, d, history(c, day))
    assert zero["status"] == "E" and zero["direction_eligible"] is False
    assert r055_event(c, day, n[:-1], d, history(c, day))["reason"] == "TARGET_NIGHT_INVALID"
    assert r055_event(c, day, n, d, history(c, day), night_quarantined=True)["reason"] == "TARGET_SESSION_QUARANTINED"
    assert r055_event(c, date(2025, 7, 1), n, d, history(c, day))["reason"] == "OUTSIDE_DEVELOPMENT"
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]


def test_final_night_signal_fills_tse_open_and_delay_does_not_extend_exit() -> None:
    c, day = classifier(), date(2024, 11, 5)
    n, d = bars(c, day)
    _, _, _, config = load_project_config(Path("config"))
    runtime = config.model_copy(update={"execution": config.execution.model_copy(update={"allow_cross_session_pending_order": True, "max_fill_delay_minutes": 20160}), "risk": config.risk.model_copy(update={"new_entry_cutoff_minutes_before_session_close": 0})})
    cn, _, _, _ = normal_night_end(c, day)
    entry = c.session_open(day, Session.DAY)
    engine = BacktestEngine(load_project_config(Path("config"))[0].instrument.to_spec(), runtime, c)
    trade = engine.run(n + d, R055NightExtremeFollowStrategy("r055", cn, entry, entry + timedelta(minutes=30), "long")).trades[0]
    delayed = engine.run(n + d, R055NightExtremeFollowStrategy("r055d", cn, entry, entry + timedelta(minutes=30), "long", 1)).trades[0]
    assert trade.entry_ts == entry and trade.exit_ts == entry + timedelta(minutes=30)
    assert delayed.entry_ts == entry + timedelta(minutes=1) and delayed.exit_ts == trade.exit_ts
    assert trade.exit_reason is ExitReason.SIGNAL
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy


def test_monday_night_endpoint_uses_assigned_friday_not_trade_date_calendar_day() -> None:
    c = classifier()
    final, known, _, _ = normal_night_end(c, date(2024, 11, 11))
    assert final.isoformat() == "2024-11-09T05:54:00+09:00"
    assert known.isoformat() == "2024-11-09T05:55:00+09:00"
