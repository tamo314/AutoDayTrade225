from datetime import date, datetime, time, timedelta
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import JST, load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.r074_low_night_range_opening_breakout import (
    DEVELOPMENT_START,
    HISTORY_COUNT,
    nearest_rank,
    previous_calendar_eligible_nights,
    r074_event,
)
from n225m_bt.strategies.r074_fixed_signal import R074FixedSignalStrategy


def _classifier() -> CalendarClassifier:
    _, sessions, _, _ = load_project_config(Path("config"))
    return CalendarClassifier(sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml")))


def _bar(target: date, stamp: datetime, session: Session, value: int, width: int = 0) -> Bar:
    return Bar(stamp, target, stamp.date(), session, "synthetic", value, value + width, value, value)


def _night(classifier: CalendarClassifier, target: date, width: int) -> list[Bar]:
    start = classifier.session_open(target, Session.NIGHT)
    end = datetime.combine(target, time(5, 29), JST)
    return [
        _bar(target, start + timedelta(minutes=index), Session.NIGHT, 100, width)
        for index in range(int((end - start).total_seconds() // 60) + 1)
    ]


def _day(target: date, *, breakout: str | None = "long") -> list[Bar]:
    rows = [
        _bar(target, datetime.combine(target, time(9), JST) + timedelta(minutes=index), Session.DAY, 100)
        for index in range(121)
    ]
    if breakout == "long":
        rows[30] = _bar(target, datetime.combine(target, time(9, 30), JST), Session.DAY, 100, 10)
        rows[30] = Bar(rows[30].ts_jst, target, target, Session.DAY, "synthetic", 100, 110, 100, 110)
    if breakout == "short":
        rows[30] = Bar(rows[30].ts_jst, target, target, Session.DAY, "synthetic", 100, 100, 90, 90)
    rows.extend(
        [
            _bar(target, datetime.combine(target, clock, JST), Session.DAY, 110)
            for clock in (time(14, 14), time(14, 15), time(14, 29), time(14, 30), time(14, 44), time(14, 45))
        ]
    )
    return rows


def _inputs() -> tuple[CalendarClassifier, date, dict[date, list[Bar]]]:
    classifier = _classifier()
    target = date(2021, 3, 4)
    history = previous_calendar_eligible_nights(classifier, target)
    assert history is not None and len(history) == HISTORY_COUNT and min(history) >= DEVELOPMENT_START
    bars = {item: _night(classifier, item, 20 if index < 5 else 40) for index, item in enumerate(history)}
    bars[target] = [*_night(classifier, target, 10), *_day(target)]
    return classifier, target, bars


def test_nearest_rank_and_tie_assignment_are_frozen() -> None:
    assert nearest_rank([0] * 5 + [10] * 15, 25) == 0
    classifier, target, bars = _inputs()
    event = r074_event(classifier, target, bars, isolated=set())
    assert event["status"] == "EXECUTABLE"
    assert event["state_group"] == "compression"
    assert event["breakout_direction"] == "long"
    assert event["current_night_range_points"] <= event["q25_points"]


def test_reference_gap_is_not_replaced_with_an_older_valid_night() -> None:
    classifier, target, bars = _inputs()
    references = previous_calendar_eligible_nights(classifier, target)
    assert references is not None
    bars[references[0]] = bars[references[0]][1:]
    event = r074_event(classifier, target, bars, isolated=set())
    assert event["reason"] == "REFERENCE_NIGHT_RANGE_MISSING_OR_INELIGIBLE"
    assert event["reference_index"] == 1


def test_strict_close_and_future_prefix_do_not_change_the_event() -> None:
    classifier, target, bars = _inputs()
    baseline = r074_event(classifier, target, bars, isolated=set())
    bars[target].append(_bar(target, datetime.combine(target, time(14, 0), JST), Session.DAY, 9999))
    assert r074_event(classifier, target, bars, isolated=set())["breakout_bar_jst"] == baseline["breakout_bar_jst"]
    equality = _inputs()[2]
    equality[target] = [*_night(classifier, target, 10), *_day(target, breakout=None)]
    assert r074_event(classifier, target, equality, isolated=set())["reason"] == "NO_STRICT_CLOSE_BREAKOUT_BY_1100"


def test_isolated_reference_and_middle_state_are_distinct() -> None:
    classifier, target, bars = _inputs()
    refs = previous_calendar_eligible_nights(classifier, target)
    assert refs is not None
    isolated = {(refs[2], Session.NIGHT)}
    assert r074_event(classifier, target, bars, isolated=isolated)["reason"] == "REFERENCE_R004_NIGHT_SESSION_QUARANTINED"
    bars[target] = [*_night(classifier, target, 30), *_day(target, breakout="short")]
    event = r074_event(classifier, target, bars, isolated=set(), state_group="middle")
    assert event["status"] == "EXECUTABLE"
    assert event["breakout_direction"] == "short"


def test_fixed_signal_uses_next_bar_open_and_absolute_exit() -> None:
    classifier, target, _ = _inputs()
    instrument, _, _, baseline = load_project_config(Path("config"))
    config = baseline.model_copy(
        update={
            "mode": "day_only",
            "risk": baseline.risk.model_copy(
                update={"force_flat": False, "new_entry_cutoff_minutes_before_session_close": 0}
            ),
        }
    )
    result = BacktestEngine(instrument.instrument.to_spec(), config, classifier).run(
        _day(target),
        R074FixedSignalStrategy(
            "r074-test",
            datetime.combine(target, time(9, 30), JST),
            datetime.combine(target, time(14, 29), JST),
            "long",
        ),
    )
    trade = result.trades[0]
    assert (trade.side, trade.entry_ts.time(), trade.exit_ts.time(), trade.exit_reason) == (
        Side.LONG,
        time(9, 31),
        time(14, 30),
        ExitReason.SIGNAL,
    )
    assert trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
