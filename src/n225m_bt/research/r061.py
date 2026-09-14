"""Causal TSE local-compression breakout event selection for R061-Q001."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r025 import previous_tse_open_date

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
LOOKBACK = 120
MIN_REFERENCES = 100
TICK = 5


@dataclass(frozen=True)
class R061Specification:
    """Only preregistered compression-window/threshold/holding variations."""

    compression_minutes: int = 30
    low_percentile: int = 35
    holding_minutes: int = 30


BASE = R061Specification()


def nearest_rank(values: list[float], percentile: int) -> float:
    if not values or not 0 < percentile < 100:
        raise ValueError("R061 nearest-rank inputs are invalid")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def _valid(bar: Bar | None, target: date) -> bool:
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and min(bar.open, bar.high, bar.low, bar.close) > 0
    )


def _block_start(minutes: int) -> int:
    if minutes not in {20, 30, 40}:
        raise ValueError("R061 compression window must be 20, 30, or 40 planned minutes")
    # Scheduled ordinal (one based): 71/61/51 through ordinal 90.
    return 90 - minutes


def _path(
    classifier: CalendarClassifier, target: date, bars: list[Bar] | None, count: int
) -> list[Bar] | None:
    if bars is None:
        return None
    start = classifier.session_open(target, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in bars}
    candidate = [by_time.get(start + timedelta(minutes=index)) for index in range(count)]
    if not all(_valid(bar, target) for bar in candidate):
        return None
    return [bar for bar in candidate if bar is not None]


def _range_w(rows: list[Bar], minutes: int) -> tuple[float, int, int, int] | None:
    start = _block_start(minutes)
    block = rows[start : start + minutes]
    if len(block) != minutes:
        return None
    a, high, low = block[0].open, max(bar.high for bar in block), min(bar.low for bar in block)
    if a <= 0 or high <= low:
        return None
    return 10_000 * (high - low) / a, a, high, low


def _prior_close(
    classifier: CalendarClassifier, target: date, bars: list[Bar] | None
) -> int | None:
    if bars is None:
        return None
    end = normal_session_end(classifier, target, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in bars}
    bar = by_time.get(end - timedelta(minutes=1))
    return bar.close if bar is not None and _valid(bar, target) else None


def r061_event(
    classifier: CalendarClassifier,
    cash_calendar: TSECashMarketCalendar,
    target: date,
    target_bars: list[Bar] | None,
    history: Iterable[tuple[date, list[Bar] | None, bool]],
    prior_bars: list[Bar] | None,
    *,
    specification: R061Specification = BASE,
    tick_size: int = TICK,
    quarantined: bool = False,
    prior_quarantined: bool = False,
) -> dict[str, object]:
    """Build one common-E R061 event without using target/future information in q."""
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "compression_minutes": specification.compression_minutes,
        "low_percentile": specification.low_percentile,
        "holding_minutes": specification.holding_minutes,
        "lookback_scheduled_tse_days": LOOKBACK,
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        result["reason"] = "OUTSIDE_DEVELOPMENT"
        return result
    if tick_size <= 0 or specification.low_percentile not in {30, 35, 40}:
        result["reason"] = "INVALID_PREREGISTERED_SPECIFICATION"
        return result
    try:
        _block_start(specification.compression_minutes)
    except ValueError:
        result["reason"] = "INVALID_PREREGISTERED_SPECIFICATION"
        return result
    if not cash_calendar.is_open(target):
        result["reason"] = "TSE_CLOSED"
        return result
    reference = list(history)
    if len(reference) != LOOKBACK:
        result["reason"] = "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
        return result
    if quarantined or prior_quarantined:
        result["reason"] = "R004_QUARANTINED"
        return result
    # Common E requires every fixed window's causal rolling reference as well as 166 target bars.
    history_w: dict[int, list[float]] = {20: [], 30: [], 40: []}
    for history_day, rows, isolated in reference:
        if isolated:
            continue
        prior_rows = _path(classifier, history_day, rows, 90)
        if prior_rows is None:
            continue
        for minutes in history_w:
            observed = _range_w(prior_rows, minutes)
            if observed is not None:
                history_w[minutes].append(observed[0])
    result.update(
        reference_trade_dates=[item[0].isoformat() for item in reference],
        reference_scheduled_day_count=len(reference),
        reference_valid_w_count={str(minutes): len(values) for minutes, values in history_w.items()},
    )
    if any(len(values) < MIN_REFERENCES for values in history_w.values()):
        result["reason"] = "INSUFFICIENT_VALID_W_REFERENCES"
        return result
    rows = _path(classifier, target, target_bars, 166)
    prior = previous_tse_open_date(cash_calendar, target)
    prior_close = _prior_close(classifier, prior, prior_bars) if prior is not None else None
    if rows is None or prior is None or prior_close is None:
        result["reason"] = "COMMON_E_PATH_OR_PREVIOUS_TSE_CLOSE_MISSING_OR_INELIGIBLE"
        return result
    q: dict[int, dict[int, float]] = {
        minutes: {percentile: nearest_rank(values, percentile) for percentile in (30, 35, 40, 65)}
        for minutes, values in history_w.items()
    }
    result.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        q_by_window={str(k): {str(p): v for p, v in x.items()} for k, x in q.items()},
        prior_tse_trade_date=prior.isoformat(),
        prior_tse_final_close_points=prior_close,
        common_path_last_ordinal=166,
        common_path_last_bar_start_jst=rows[-1].ts_jst.isoformat(),
    )
    observation = _range_w(rows, specification.compression_minutes)
    if observation is None:
        result["reason"] = "COMPRESSION_RANGE_NONPOSITIVE"
        return result
    w, a, high, low = observation
    start = _block_start(specification.compression_minutes)
    result.update(
        a_points=a,
        H_points=high,
        L_points=low,
        w_bps=w,
        q30=q[specification.compression_minutes][30],
        q35=q[specification.compression_minutes][35],
        q40=q[specification.compression_minutes][40],
        q65=q[specification.compression_minutes][65],
        compression_start_ordinal=start + 1,
        compression_end_ordinal=90,
    )
    signal_index: int | None = None
    s = 0
    for index in range(90, 120):
        close = rows[index].close
        if close >= high + tick_size:
            signal_index, s = index, 1
            break
        if close <= low - tick_size:
            signal_index, s = index, -1
            break
    if signal_index is None:
        result["reason"] = "NO_CLOSE_BREAKOUT_IN_91_120"
        return result
    signal = rows[signal_index]
    entry = rows[signal_index + 1]
    exit_row = rows[signal_index + 1 + specification.holding_minutes]
    overshoot = (
        (signal.close - high) // tick_size if s == 1 else (low - signal.close) // tick_size
    )
    compression = rows[start : start + specification.compression_minutes]
    first60 = rows[:60]
    result.update(
        status="event",
        reason="FIRST_CLOSE_BREAKOUT",
        s=s,
        breakout_direction="upper" if s == 1 else "lower",
        breakout_signal_ordinal=signal_index + 1,
        breakout_search_minutes=signal_index - 90 + 1,
        signal_close_overshoot_ticks=overshoot,
        signal_bar_start_jst=signal.ts_jst.isoformat(),
        planned_entry_jst=entry.ts_jst.isoformat(),
        planned_exit_jst=exit_row.ts_jst.isoformat(),
        compression_return_s_adjusted_bps=s * (compression[-1].close - a) / a * 10_000,
        first60_range_bps=(max(bar.high for bar in first60) - min(bar.low for bar in first60))
        / first60[0].open
        * 10_000,
        tse_open_gap_s_adjusted_bps=s * (first60[0].open - prior_close) / prior_close * 10_000,
        A_qualifies=w <= q[specification.compression_minutes][specification.low_percentile],
        # Equality is assigned to the low-range side; this keeps A/D disjoint under tied ranks.
        D_qualifies=w >= q[specification.compression_minutes][65]
        and w > q[specification.compression_minutes][35],
    )
    return result
