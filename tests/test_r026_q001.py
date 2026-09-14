from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r026 import prior_night_rejection_event
from n225m_bt.strategies.prior_night_rejection import PriorNightRejectionStrategy


def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )


def cash() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(
        frozenset({date(2024, 11, 4)}), date(2021, 1, 1), date(2025, 12, 31)
    )


def bar(
    ts: datetime,
    day: date,
    session: Session,
    o: int,
    c: int,
    h: int | None = None,
    low: int | None = None,
) -> Bar:
    return Bar(ts, day, ts.date(), session, "synthetic", o, h or max(o, c), low or min(o, c), c)


def night(day: date, hi: int = 140, lo: int = 100, *, missing: bool = False) -> list[Bar]:
    rules = classifier()
    start, end = (
        rules.session_open(day, Session.NIGHT),
        normal_session_end(rules, day, Session.NIGHT),
    )
    rows = [
        bar(start + timedelta(minutes=i), day, Session.NIGHT, 120, 120, hi, lo)
        for i in range(int((end - start).total_seconds() // 60))
    ]
    return rows[:-1] if missing else rows


def day(
    day_: date, hi: int = 145, lo: int = 110, close: int = 130, *, missing: bool = False
) -> list[Bar]:
    start = datetime(day_.year, day_.month, day_.day, 9, tzinfo=JST)
    rows = []
    for i in range(76):
        ts = start + timedelta(minutes=i)
        if not (missing and i == 4):
            rows.append(
                bar(
                    ts,
                    day_,
                    Session.DAY,
                    120,
                    close if i == 14 else 120,
                    hi if i < 15 else 120,
                    lo if i < 15 else 120,
                )
            )
    return rows


def event(**kwargs: object) -> dict[str, object]:
    return prior_night_rejection_event(
        classifier(),
        cash(),
        date(2024, 11, 5),
        day(date(2024, 11, 5)),
        night(date(2024, 11, 5)),
        **kwargs,
    )


def test_schedule_range_event_boundaries_and_counterfactuals() -> None:
    upper = event()
    lower = prior_night_rejection_event(
        classifier(),
        cash(),
        date(2024, 11, 5),
        day(date(2024, 11, 5), hi=130, lo=95, close=110),
        night(date(2024, 11, 5)),
    )
    both = prior_night_rejection_event(
        classifier(),
        cash(),
        date(2024, 11, 5),
        day(date(2024, 11, 5), hi=145, lo=95),
        night(date(2024, 11, 5)),
    )
    boundary = prior_night_rejection_event(
        classifier(),
        cash(),
        date(2024, 11, 5),
        day(date(2024, 11, 5), close=140),
        night(date(2024, 11, 5)),
    )
    assert (upper["status"], upper["range_side"], upper["A_direction"]) == (
        "confirmed",
        "upper",
        "short",
    )
    assert (lower["status"], lower["range_side"], lower["A_direction"]) == (
        "confirmed",
        "lower",
        "long",
    )
    assert (
        both["reason"] == "BOTH_OR_NEITHER_NIGHT_EXTREME_BREACH"
        and boundary["status"] == "nonconfirmed"
    )
    assert (
        prior_night_rejection_event(
            classifier(),
            cash(),
            date(2024, 11, 5),
            day(date(2024, 11, 5)),
            night(date(2024, 11, 5), missing=True),
        )["reason"]
        == "REFERENCE_FULL_NORMAL_NIGHT_MISSING"
    )
    assert (
        prior_night_rejection_event(
            classifier(),
            cash(),
            date(2024, 11, 5),
            day(date(2024, 11, 5), missing=True),
            night(date(2024, 11, 5)),
        )["reason"]
        == "WINDOW_0900_TO_0914_MISSING"
    )
    assert (
        event(day_quarantined=True)["reason"] == "DAY_SESSION_QUARANTINED"
        and event(night_quarantined=True)["reason"] == "REFERENCE_NIGHT_SESSION_QUARANTINED"
    )
    assert (
        prior_night_rejection_event(
            classifier(),
            cash(),
            date(2024, 11, 5),
            [
                *day(date(2024, 11, 5)),
                bar(datetime(2024, 11, 5, 11, tzinfo=JST), date(2024, 11, 5), Session.DAY, 1, 999),
            ],
            night(date(2024, 11, 5)),
        )
        == upper
    )


def test_execution_delay_accounting_and_holdout_lock() -> None:
    rows = day(date(2024, 11, 5))
    instrument, sessions, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    signal = datetime(2024, 11, 5, 9, 14, tzinfo=JST)
    trade = engine.run(rows, PriorNightRejectionStrategy("r026", signal, "short")).trades[0]
    delayed = engine.run(
        [x for x in rows if x.ts_jst != signal + timedelta(minutes=1)],
        PriorNightRejectionStrategy("r026-delay", signal, "long"),
    ).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (
        Side.SHORT,
        signal + timedelta(minutes=1),
        signal + timedelta(minutes=61),
        ExitReason.SIGNAL,
    )
    assert (
        delayed.entry_ts == signal + timedelta(minutes=2)
        and delayed.exit_ts == signal + timedelta(minutes=61)
        and delayed.net_pnl_jpy == delayed.gross_pnl_jpy - delayed.fees_jpy
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]
