"""Causal state/event construction and fixed inference for TASK-R084-Q001."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.domain import Bar, Session, Trade

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915
MORNING_START = time(9)
MORNING_END = time(11, 29)
BREAKOUT_START = time(12, 30)
BREAKOUT_END = time(13, 29)
REGISTERED_EXITS = {time(14, 15), time(14, 30), time(14, 45)}
REGISTERED_LOOKBACKS = {40, 60, 80}
REGISTERED_CUTOFFS = {20, 30, 40}


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    """Return the immutable Development trade-date axis before price filtering."""
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def nearest_rank(values: list[float], percentile: int) -> float:
    """Use the frozen nearest-rank percentile without interpolation."""
    if not values or percentile not in {20, 30, 40, 70}:
        raise ValueError("unregistered R084 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def _stamp(target: date, clock: time, classifier: CalendarClassifier) -> datetime:
    return datetime.combine(target, clock, classifier.session_close(target, Session.DAY).tzinfo)


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    price = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and price > 0
    )


def _morning_observation(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
) -> dict[str, object]:
    """Build W only after every registered 09:00--11:29 bar is observable."""
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "w_valid": False,
        "morning_start_jst": _stamp(target, MORNING_START, classifier).isoformat(),
        "morning_end_jst": _stamp(target, MORNING_END, classifier).isoformat(),
        "morning_expected_bar_count": 150,
    }
    if (target, Session.DAY) in isolated:
        result["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return result
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    expected = [
        _stamp(target, MORNING_START, classifier) + timedelta(minutes=offset)
        for offset in range(150)
    ]
    missing = [stamp for stamp in expected if not _valid(lookup.get(stamp), target)]
    if missing:
        result.update(
            reason="MORNING_WINDOW_BAR_MISSING_OR_INELIGIBLE",
            morning_missing_or_ineligible_bar_count=len(missing),
            morning_first_missing_or_ineligible_jst=missing[0].isoformat(),
        )
        return result
    morning = [lookup[stamp] for stamp in expected]
    a = morning[0]
    assert _valid(a, target)
    if any(bar.high <= 0 or bar.low <= 0 or bar.high < bar.low for bar in morning):
        result["reason"] = "MORNING_OHLC_INVALID"
        return result
    high, low = max(bar.high for bar in morning), min(bar.low for bar in morning)
    result.update(
        w_valid=True,
        reason="VALID_W",
        a_0900_open_points=a.open,
        h_0900_1129_points=high,
        l_0900_1129_points=low,
        w_bps=10_000 * (high - low) / a.open,
    )
    return result


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = 60,
    compression_percentile: int = 30,
) -> list[dict[str, object]]:
    """Build current-excluded W states; invalid W values never fill an old slot."""
    if lookback not in REGISTERED_LOOKBACKS or compression_percentile not in REGISTERED_CUTOFFS:
        raise ValueError("unregistered R084 state profile")
    history: deque[dict[str, object]] = deque(maxlen=lookback)
    rows: list[dict[str, object]] = []
    for target in axis:
        base = _morning_observation(classifier, target, bars, isolated)
        row: dict[str, object] = {
            **base,
            "lookback_valid_trade_dates_required": lookback,
            "compression_percentile": compression_percentile,
            "upper_percentile": 70,
            "quantile_method": "nearest_rank ceil(n*p/100)-1",
            "reference_valid_trade_dates_oldest_to_newest": [
                str(item["trade_date"]) for item in history
            ],
            "reference_valid_count": len(history),
        }
        if not bool(base["w_valid"]):
            row["state"] = "NONE"
        elif len(history) < lookback:
            row.update(state="NONE", reason="INSUFFICIENT_PRIOR_VALID_W_HISTORY")
        else:
            reference = [cast(float, item["w_bps"]) for item in history]
            q_low = nearest_rank(reference, compression_percentile)
            q70 = nearest_rank(reference, 70)
            current = cast(float, base["w_bps"])
            row.update(q_compression_bps=q_low, q70_bps=q70)
            if q_low >= q70:
                row.update(state="NONE", reason="DEGENERATE_Q_COMPRESSION_GTE_Q70")
            elif current <= q_low:
                row.update(state="C", reason="COMPRESSED_W")
            elif current < q70:
                row.update(state="M", reason="MIDDLE_W")
            else:
                row.update(state="NONE", reason="OUTSIDE_EXCLUSIVE_STATE")
        if bool(base["w_valid"]):
            history.append(base)
        rows.append(row)
    return rows


def breakout_event(
    state: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    extra_entry_delay_bars: int = 0,
    exit_time: time = time(14, 30),
) -> dict[str, object]:
    """Observe the first strict noon breakout, then use a later eligible open only."""
    if extra_entry_delay_bars not in {0, 1} or exit_time not in REGISTERED_EXITS:
        raise ValueError("unregistered R084 execution profile")
    event = dict(state)
    event.update(
        status="SKIPPED",
        extra_entry_delay_bars=extra_entry_delay_bars,
        exit_open_jst_planned_clock=exit_time.isoformat(timespec="minutes"),
    )
    if event.get("state") not in {"C", "M"}:
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    start = datetime.fromisoformat(cast(str, event["morning_start_jst"])).replace(
        hour=BREAKOUT_START.hour, minute=BREAKOUT_START.minute
    )
    h, low = cast(int, event["h_0900_1129_points"]), cast(int, event["l_0900_1129_points"])
    breakout: Bar | None = None
    direction: str | None = None
    for offset in range(60):
        candidate = lookup.get(start + timedelta(minutes=offset))
        if not _valid(candidate, target, close=True):
            event.update(
                status="SKIPPED",
                reason="BREAKOUT_WINDOW_BAR_MISSING_OR_INELIGIBLE",
                breakout_window_first_missing_or_ineligible_jst=(start + timedelta(minutes=offset)).isoformat(),
            )
            return event
        assert candidate is not None
        if candidate.close > h:
            breakout, direction = candidate, "long"
            break
        if candidate.close < low:
            breakout, direction = candidate, "short"
            break
    if breakout is None or direction is None:
        event.update(status="SKIPPED", reason="NO_STRICT_BREAKOUT_IN_1230_1329")
        return event
    eligible_after = [
        bar
        for bar in bars.get((target, Session.DAY), [])
        if bar.ts_jst > breakout.ts_jst and _valid(bar, target)
    ]
    if len(eligible_after) <= extra_entry_delay_bars:
        event.update(status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_OPEN_MISSING_OR_INELIGIBLE")
        return event
    entry_bar = eligible_after[extra_entry_delay_bars]
    entry_signal_bar = breakout if extra_entry_delay_bars == 0 else eligible_after[0]
    exit_open = datetime.combine(target, exit_time, breakout.ts_jst.tzinfo)
    exit_signal = exit_open - timedelta(minutes=1)
    event.update(
        selection_fixed_at_jst=breakout.ts_jst.isoformat(),
        breakout_close_jst=breakout.ts_jst.isoformat(),
        breakout_close_points=breakout.close,
        breakout_direction=direction,
        entry_signal_jst=entry_signal_bar.ts_jst.isoformat(),
        entry_open_jst=entry_bar.ts_jst.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
    )
    if entry_bar.ts_jst >= exit_open:
        event.update(status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_NOT_BEFORE_FIXED_EXIT")
        return event
    if not _valid(lookup.get(exit_signal), target, close=True) or not _valid(
        lookup.get(exit_open), target
    ):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    event.update(status="EXECUTABLE", reason="STRICT_BREAKOUT_NEXT_ELIGIBLE_OPEN_EXECUTABLE")
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = 60,
    compression_percentile: int = 30,
    extra_entry_delay_bars: int = 0,
    exit_time: time = time(14, 30),
) -> list[dict[str, object]]:
    return [
        breakout_event(
            row,
            bars,
            extra_entry_delay_bars=extra_entry_delay_bars,
            exit_time=exit_time,
        )
        for row in state_ledger(
            classifier,
            axis,
            bars,
            isolated,
            lookback=lookback,
            compression_percentile=compression_percentile,
        )
    ]


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    """Return only preregistered PnL-free availability/composition checks."""
    rows = list(events)
    compressed = [row for row in rows if row.get("state") == "C"]
    trades = [row for row in compressed if row.get("status") == "EXECUTABLE"]
    by_year = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in trades)
    directions = Counter(cast(str, row["breakout_direction"]) for row in trades)
    known_prefixes = (
        "R004_", "MORNING_", "VALID_", "INSUFFICIENT_", "DEGENERATE_", "COMPRESSED_",
        "MIDDLE_", "OUTSIDE_", "BREAKOUT_", "NO_STRICT_", "NEXT_ELIGIBLE_", "FIXED_",
        "STRICT_",
    )
    unexplained = [
        cast(str, row["trade_date"])
        for row in rows
        if not str(row.get("reason", "")).startswith(known_prefixes)
    ]
    gate: dict[str, object] = {
        "compressed_state_eligible_days": len(compressed),
        "minimum_compressed_state_eligible_days": 280,
        "compressed_days_at_least_280": len(compressed) >= 280,
        "compressed_breakout_executable_trades": len(trades),
        "minimum_compressed_breakout_trades": 80,
        "compressed_breakout_trades_at_least_80": len(trades) >= 80,
        "breakout_direction_counts": dict(sorted(directions.items())),
        "minimum_long_and_short_each": 25,
        "direction_minima_passed": directions["long"] >= 25 and directions["short"] >= 25,
        "compressed_breakout_by_year": {str(year): by_year[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 10,
        "annual_2021_2024_minima_passed": all(by_year[year] >= 10 for year in range(2021, 2025)),
        "minimum_2025_h1": 5,
        "year_2025_h1_minimum_passed": by_year[2025] >= 5,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "compressed_days_at_least_280",
            "compressed_breakout_trades_at_least_80",
            "direction_minima_passed",
            "annual_2021_2024_minima_passed",
            "year_2025_h1_minimum_passed",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(sorted(Counter(str(row.get("reason", row["status"])) for row in rows).items())),
        "state_counts": dict(sorted(Counter(str(row.get("state", "NONE")) for row in rows).items())),
        "gate": gate,
    }


def aligned_daily_net(
    axis: list[date], trades: tuple[Trade, ...], unknown: set[date]
) -> dict[str, int | None]:
    """Keep known no-trades at zero and a filled unknown exit as null."""
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if trade.trade_date not in daily or daily[trade.trade_date] is None or daily[trade.trade_date] != 0:
            raise ValueError("R084 trade outside observable axis or duplicate trade date")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R084 MBB block")
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[:, :length]


def _ci(samples: NDArray[np.float64]) -> list[float] | None:
    if not len(samples) or not np.isfinite(samples).all():
        return None
    low, high = np.quantile(samples, (0.025, 0.975), method="linear")
    return [float(low), float(high)]


def bootstrap(
    compressed_daily: list[int], fade_daily: list[int], middle_daily: list[int]
) -> tuple[dict[str, object], NDArray[np.int64]]:
    """Use one frozen date-block index for the three common scheduled-day axes."""
    if not (len(compressed_daily) == len(fade_daily) == len(middle_daily)):
        raise ValueError("R084 bootstrap axes are misaligned")
    index = mbb_indices(len(compressed_daily))
    compressed, fade, middle = (
        np.asarray(values, dtype=float) for values in (compressed_daily, fade_daily, middle_daily)
    )
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "compressed_breakout_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(compressed_daily),
            "ci95_percentile_linear": _ci(compressed[index].mean(axis=1)),
        },
        "compressed_breakout_minus_fade_paired_daily_net_jpy": {
            "estimate": fmean([left - right for left, right in zip(compressed_daily, fade_daily, strict=True)]),
            "ci95_percentile_linear": _ci((compressed[index] - fade[index]).mean(axis=1)),
        },
        "compressed_minus_middle_group_mean_daily_net_jpy": {
            "estimate": fmean(compressed_daily) - fmean(middle_daily),
            "ci95_percentile_linear": _ci((compressed[index] - middle[index]).mean(axis=1)),
        },
    }, index
