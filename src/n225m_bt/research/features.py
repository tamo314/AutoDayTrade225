"""Causal opening-range summaries for the R003 compression study."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from hashlib import sha256

from n225m_bt.domain import Bar, Session


@dataclass(frozen=True, slots=True)
class OpeningSummary:
    """Opening-only information, frozen when the scheduled 30th bar closes."""

    trade_date: str
    session: Session
    scheduled_start_ts: datetime
    available_at: datetime | None
    upper: int | None
    lower: int | None
    range_points: int | None
    observed_opening_bars: int
    expected_opening_bars: int
    valid: bool
    invalid_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """The immutable, same-session-type history available before one session."""

    summaries: tuple[OpeningSummary, ...]
    status: str
    baseline_median: Fraction | None

    @property
    def ranges(self) -> tuple[int | None, ...]:
        return tuple(item.range_points if item.valid else None for item in self.summaries)


@dataclass(frozen=True, slots=True)
class CompressionState:
    baseline_median: Fraction
    ratio: Fraction
    allowed: bool


def compression_state(
    current_range: int,
    previous_ranges: Sequence[int | None],
    threshold: Fraction,
    *,
    baseline_sessions: int,
) -> CompressionState | None:
    """Calculate an exact compression ratio without selecting the history window."""
    if current_range < 0:
        raise ValueError("current_range must be nonnegative")
    if len(previous_ranges) != baseline_sessions:
        return None
    if any(value is None for value in previous_ranges):
        return None
    values = [value for value in previous_ranges if value is not None]
    if any(value < 0 for value in values):
        raise ValueError("previous ranges must be nonnegative")
    values.sort()
    midpoint = len(values) // 2
    median = (
        Fraction(values[midpoint], 1)
        if len(values) % 2
        else Fraction(values[midpoint - 1] + values[midpoint], 2)
    )
    if current_range == 0 or median == 0:
        return None
    ratio = Fraction(current_range, 1) / median
    return CompressionState(median, ratio, ratio <= threshold)


class OpeningHistory:
    """Append opening summaries only after their session has been evaluated."""

    def __init__(self, baseline_sessions: int) -> None:
        if baseline_sessions <= 0:
            raise ValueError("baseline_sessions must be positive")
        self._baseline_sessions = baseline_sessions
        self._by_session: dict[Session, list[OpeningSummary]] = {
            Session.DAY: [],
            Session.NIGHT: [],
        }

    def snapshot(self, session: Session, start: datetime) -> HistorySnapshot:
        previous = tuple(self._by_session[session][-self._baseline_sessions :])
        if len(previous) != self._baseline_sessions:
            return HistorySnapshot(previous, "insufficient_count", None)
        if any(not item.valid for item in previous):
            return HistorySnapshot(previous, "invalid_member", None)
        if any(item.available_at is None or item.available_at > start for item in previous):
            raise ValueError("history summary is not available as of current session start")
        values = [item.range_points for item in previous]
        assert all(value is not None for value in values)
        ordered = sorted(int(value) for value in values if value is not None)
        middle = len(ordered) // 2
        median = Fraction(ordered[middle - 1] + ordered[middle], 2)
        status = "zero_median" if median == 0 else "ready"
        return HistorySnapshot(previous, status, median)

    def append(self, summary: OpeningSummary) -> None:
        self._by_session[summary.session].append(summary)


def summarize_opening(
    bars: Sequence[Bar],
    session: Session,
    session_start: datetime,
    opening_minutes: int = 30,
) -> OpeningSummary:
    """Create one summary without inspecting bars after the opening window."""

    opening = [
        bar
        for bar in bars
        if 0 <= int((bar.ts_jst - session_start).total_seconds() // 60) < opening_minutes
    ]
    reasons: list[str] = []

    from datetime import timedelta

    expected = [session_start + timedelta(minutes=index) for index in range(opening_minutes)]
    indexed = {bar.ts_jst: bar for bar in opening}
    if len(indexed) != len(opening):
        reasons.append("DUPLICATE_OPENING_BAR")
    if any(timestamp not in indexed for timestamp in expected):
        reasons.append("MISSING_OPENING_BAR")
    if any(not indexed[timestamp].is_eligible for timestamp in expected if timestamp in indexed):
        reasons.append("INELIGIBLE_OPENING_BAR")
    valid = not reasons and len(opening) == opening_minutes
    upper = max((bar.high for bar in opening), default=None) if valid else None
    lower = min((bar.low for bar in opening), default=None) if valid else None
    return OpeningSummary(
        str(bars[0].trade_date) if bars else "",
        session,
        session_start,
        session_start + timedelta(minutes=opening_minutes),
        upper,
        lower,
        upper - lower if upper is not None and lower is not None else None,
        len(opening),
        opening_minutes,
        valid,
        tuple(reasons),
    )


def opening_input_hash(summary: OpeningSummary) -> str:
    """Stable identifier used in audit outputs without retaining raw prices separately."""
    payload = repr(summary).encode("utf-8")
    return sha256(payload).hexdigest()

