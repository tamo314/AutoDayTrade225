from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r061 import R061Specification, r061_event, r061_exec_event
from n225m_bt.strategies.opening_range_compression_breakout import (
    OpeningRangeCompressionBreakoutStrategy,
)
from scripts.run_r061_q001_tse_local_compression_breakout import (
    qualifies,
)
from scripts.run_r061_q001_tse_local_compression_breakout import side as condition_side


@lru_cache
def classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def cash() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset(), date(2020, 1, 1), date(2026, 12, 31))


def rows(day: date, *, breakout_ordinal: int = 91, missing: int | None = None) -> list[Bar]:
    start, output = classifier().session_open(day, Session.DAY), []
    for index in range(166):
        open_, high, low, close = 100, 101, 99, 100
        # Main 61--90 compression block has H=102, L=98, a=100.
        if index == 60:
            high = 102
        if index == 61:
            low = 98
        if index == breakout_ordinal - 1:
            high = close = 107
        if index != missing:
            output.append(Bar(start + timedelta(minutes=index), day, day, Session.DAY, "x", open_, high, low, close))
    return output


def prior_rows(day: date) -> list[Bar]:
    start = classifier().session_open(day, Session.DAY)
    end = classifier().session_close(day, Session.DAY)
    return [
        Bar(start + timedelta(minutes=index), day, day, Session.DAY, "x", 100, 101, 99, 100)
        for index in range(int((end - start).total_seconds() // 60))
    ]


def history(day: date, w_count: int = 120) -> list[tuple[date, list[Bar] | None, bool]]:
    value: list[tuple[date, list[Bar] | None, bool]] = []
    for index in range(w_count):
        prior = day - timedelta(days=index + 1)
        value.append((prior, rows(prior), False))
    return list(reversed(value))


def event(day: date, **kwargs: object) -> dict[str, object]:
    return r061_event(classifier(), cash(), day, rows(day, **kwargs), history(day), prior_rows(day - timedelta(days=1)))


def test_causal_window_quantile_close_breakout_and_prefix() -> None:
    day = date(2024, 11, 5)
    base = event(day)
    assert base["status"] == "event"
    assert (base["compression_start_ordinal"], base["compression_end_ordinal"]) == (61, 90)
    assert (base["breakout_signal_ordinal"], base["breakout_search_minutes"]) == (91, 1)
    assert base["A_qualifies"] and not base["D_qualifies"]
    assert base["q35"] == base["w_bps"]  # equality belongs to the low-range side.
    changed = rows(day)
    changed[130] = Bar(changed[130].ts_jst, day, day, Session.DAY, "x", 999, 999, 999, 999)
    assert r061_event(classifier(), cash(), day, changed, history(day), prior_rows(day - timedelta(days=1))) == base
    no_close = rows(day)
    no_close[90] = Bar(no_close[90].ts_jst, day, day, Session.DAY, "x", 100, 107, 99, 102)
    assert r061_event(classifier(), cash(), day, no_close, history(day), prior_rows(day - timedelta(days=1)))["reason"] == "NO_CLOSE_BREAKOUT_IN_91_120"


def test_common_e_history_and_independent_sensitivities() -> None:
    day = date(2024, 11, 5)
    assert event(day, missing=165)["reason"] == "COMMON_E_PATH_OR_PREVIOUS_TSE_CLOSE_MISSING_OR_INELIGIBLE"
    assert r061_event(classifier(), cash(), day, rows(day), history(day, 119), prior_rows(day - timedelta(days=1)))["reason"] == "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
    assert r061_event(classifier(), cash(), date(2025, 7, 1), rows(day), history(day), prior_rows(day - timedelta(days=1)))["reason"] == "OUTSIDE_DEVELOPMENT"
    for minutes, start in ((20, 71), (40, 51)):
        selected = r061_event(classifier(), cash(), day, rows(day), history(day), prior_rows(day - timedelta(days=1)), specification=R061Specification(compression_minutes=minutes))
        assert selected["compression_start_ordinal"] == start


def test_r1_exec_adapter_ignores_future_exit_availability() -> None:
    day = date(2024, 11, 5)
    base = r061_exec_event(
        classifier(), cash(), day, rows(day), history(day), prior_rows(day - timedelta(days=1))
    )
    missing_exit = r061_exec_event(
        classifier(), cash(), day, rows(day, missing=150), history(day), prior_rows(day - timedelta(days=1))
    )
    assert base["status"] == "E_EXEC"
    assert base["event_found"] is True
    assert missing_exit == base
    assert event(day, missing=150)["reason"] == "COMMON_E_PATH_OR_PREVIOUS_TSE_CLOSE_MISSING_OR_INELIGIBLE"


def test_next_open_fixed_exit_and_delay_nonextension() -> None:
    day = date(2024, 11, 5)
    instrument, _, _, config = load_project_config(Path("config"))
    signal = classifier().session_open(day, Session.DAY) + timedelta(minutes=90)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, classifier())
    trade = engine.run(rows(day), OpeningRangeCompressionBreakoutStrategy("r061", signal, "long", exit_after_minutes=30)).trades[0]
    delayed = engine.run(rows(day), OpeningRangeCompressionBreakoutStrategy("r061-delay", signal, "long", 1, 30)).trades[0]
    assert (trade.side, trade.entry_ts, trade.exit_ts, trade.exit_reason) == (Side.LONG, signal + timedelta(minutes=1), signal + timedelta(minutes=31), ExitReason.SIGNAL)
    assert (delayed.entry_ts, delayed.exit_ts) == (signal + timedelta(minutes=2), signal + timedelta(minutes=31))
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy


def test_invalid_specification_is_rejected() -> None:
    day = date(2024, 11, 5)
    invalid = r061_event(
        classifier(),
        cash(),
        day,
        rows(day),
        history(day),
        prior_rows(day - timedelta(days=1)),
        specification=R061Specification(compression_minutes=25),
    )
    assert invalid["reason"] == "INVALID_PREREGISTERED_SPECIFICATION"


def test_same_event_fade_side_is_opposite() -> None:
    assert condition_side("A", {"s": 1}) == "long"
    assert condition_side("A_fade", {"s": 1}) == "short"
    assert condition_side("A", {"s": -1}) == "short"
    assert condition_side("A_fade", {"s": -1}) == "long"


def test_filled_ledger_retains_fixed_event_eligibility() -> None:
    event = {"status": "filled", "base_event_status": "event", "w_bps": 1.0, "q35": 1.0, "q65": 2.0}
    assert qualifies("A", event)
