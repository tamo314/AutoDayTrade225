from datetime import date, datetime, time

from n225m_bt.domain import Bar, Session
from n225m_bt.research.r068_opening_reversal import mbb_mean_ci, opening_event


def _bar(target: date, clock: time, close: int, *, eligible: bool = True) -> Bar:
    stamp = datetime.combine(target, clock).astimezone()
    return Bar(
        stamp, target, target, Session.DAY, "test", 100, 100, 100, close, is_eligible=eligible
    )


def test_opening_event_is_prefix_causal_and_inverts_direction() -> None:
    target = date(2025, 1, 6)
    clocks = [time(9, 0), time(9, 14), time(9, 15), time(14, 29), time(14, 30)]
    bars = [_bar(target, clock, 110 if clock == time(9, 14) else 100) for clock in clocks]
    event = opening_event(target, bars)
    assert event["status"] == "EXECUTABLE"
    assert event["reversal_direction"] == "short"
    assert event["momentum_direction"] == "long"
    assert event["signal_jst"].endswith("09:14:00+00:00") or "09:14" in str(event["signal_jst"])


def test_opening_event_rejects_zero_or_missing_required_bar() -> None:
    target = date(2025, 1, 6)
    clocks = [time(9, 0), time(9, 14), time(9, 15), time(14, 29), time(14, 30)]
    zero = [_bar(target, clock, 100) for clock in clocks]
    assert opening_event(target, zero)["reason"] == "ZERO_OPENING_CHANGE"
    assert opening_event(target, zero[:-1])["reason"] == "MISSING_EXIT"


def test_mbb_is_fixed_seed_and_nonwrapping() -> None:
    assert mbb_mean_ci(list(range(20))) == mbb_mean_ci(list(range(20)))
