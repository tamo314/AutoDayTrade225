"""Causal state construction and fixed inference for TASK-R088-Q001."""

from __future__ import annotations

from collections import Counter
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
MBB_BLOCK_LENGTH, MBB_REPETITIONS, MBB_SEED = 20, 10_000, 20260915
WINDOWS, M_CUTOFFS, E_UPPERS, REFERENCES = {30, 60, 90}, {50, 60, 70}, {60, 70, 80}, {80, 120, 160}
EXITS = {time(14, 30), time(14, 55), time(15, 10)}


def scheduled_axis(calendar: ExchangeCalendar) -> list[date]:
    return [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
    ]


def nearest_rank(values: list[float], percentile: int) -> float:
    if not values or percentile not in {20, 30, 40, 50, 60, 70, 80}:
        raise ValueError("unregistered R088 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def required_valid_reference_count(window: int) -> int:
    if window not in REFERENCES:
        raise ValueError("unregistered R088 reference window")
    return ceil(window * 5 / 6)


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    value = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and value > 0
    )


def _observation(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    window_minutes: int,
) -> dict[str, object]:
    """Build p0..pN and all L increments without reading past the endpoint close."""
    row: dict[str, object] = {
        "trade_date": target.isoformat(),
        "window_minutes": window_minutes,
        "observation_valid": False,
    }
    schedule = classifier.exchange_calendar.get(target)
    if schedule is None:
        row["reason"] = "NO_REGISTERED_SCHEDULE"
        return row
    zone = classifier.session_open(target, Session.DAY).tzinfo
    assert zone is not None
    begin = datetime.combine(target, time(9), zone)
    end = begin + timedelta(minutes=window_minutes - 1)
    row.update(
        schedule_version=schedule.schedule_version,
        p0_0900_open_jst=begin.isoformat(),
        pN_close_jst=end.isoformat(),
    )
    if (target, Session.DAY) in isolated:
        row["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return row
    if not (
        classifier.session_open(target, Session.DAY)
        <= begin
        <= end
        <= classifier.session_close(target, Session.DAY)
    ):
        row["reason"] = "WINDOW_NOT_INSIDE_REGISTERED_CONTINUOUS_DAY_SESSION"
        return row
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    stamps = [begin + timedelta(minutes=index) for index in range(window_minutes)]
    rows = [lookup.get(stamp) for stamp in stamps]
    if not _valid(rows[0], target):
        row["reason"] = "P0_0900_OPEN_MISSING_OR_INELIGIBLE"
        return row
    if not all(_valid(bar, target) and _valid(bar, target, close=True) for bar in rows):
        row["reason"] = "WINDOW_BAR_MISSING_OR_INELIGIBLE_OR_NONCONTINUOUS"
        return row
    assert rows[0] is not None
    prices = [float(rows[0].open)] + [float(cast(Bar, bar).close) for bar in rows]
    increments = [abs(prices[index] - prices[index - 1]) for index in range(1, len(prices))]
    length = sum(increments)
    row.update(
        p0_open_points=prices[0],
        p_close_points=prices[1:],
        l_terms_points=increments,
        l_points=length,
        pN_close_points=prices[-1],
    )
    if length == 0:
        row["reason"] = "ZERO_PATH_LENGTH"
        return row
    displacement = prices[-1] - prices[0]
    row.update(
        observation_valid=True,
        reason="VALID_M_E_PATH",
        displacement_points=displacement,
        m_bps=10_000 * abs(displacement) / prices[0],
        e_efficiency=abs(displacement) / length,
    )
    return row


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    window_minutes: int = 60,
    m_cutoff: int = 60,
    e_upper: int = 70,
    reference_window: int = 120,
) -> list[dict[str, object]]:
    if (
        window_minutes not in WINDOWS
        or m_cutoff not in M_CUTOFFS
        or e_upper not in E_UPPERS
        or reference_window not in REFERENCES
    ):
        raise ValueError("unregistered R088 state profile")
    ordered = list(axis)
    records: list[dict[str, object]] = []
    for index, target in enumerate(ordered):
        row = _observation(classifier, target, bars, isolated, window_minutes)
        reference = records[max(0, index - reference_window) : index]
        valid = [item for item in reference if bool(item.get("observation_valid"))]
        row.update(
            reference_window_scheduled_trade_dates=reference_window,
            reference_scheduled_trade_dates=[cast(str, item["trade_date"]) for item in reference],
            reference_valid_trade_dates=[cast(str, item["trade_date"]) for item in valid],
            reference_valid_count=len(valid),
            minimum_valid_reference_count=required_valid_reference_count(reference_window),
            m_cutoff_percentile=m_cutoff,
            e_upper_percentile=e_upper,
            e_lower_percentile=100 - e_upper,
            quantile_method="nearest_rank ceil(n*p/100)-1",
            high_efficiency_condition=False,
            low_efficiency_condition=False,
        )
        if not bool(row["observation_valid"]):
            records.append(row)
            continue
        if len(valid) < required_valid_reference_count(reference_window):
            row["reason"] = "INSUFFICIENT_VALID_M_E_IN_EXACT_PRIOR_SCHEDULED_REFERENCE_WINDOW"
            records.append(row)
            continue
        m_values, e_values = (
            [cast(float, item["m_bps"]) for item in valid],
            [cast(float, item["e_efficiency"]) for item in valid],
        )
        q_m, q_elow, q_ehigh = (
            nearest_rank(m_values, m_cutoff),
            nearest_rank(e_values, 100 - e_upper),
            nearest_rank(e_values, e_upper),
        )
        quintiles = [nearest_rank(m_values, p) for p in (20, 40, 60, 80)]
        m_value, e_value, displacement = (
            cast(float, row["m_bps"]),
            cast(float, row["e_efficiency"]),
            cast(float, row["displacement_points"]),
        )
        quintile = 1 + sum(m_value > boundary for boundary in quintiles)
        he, hl = m_value >= q_m and e_value >= q_ehigh, m_value >= q_m and e_value <= q_elow
        row.update(
            q_m_bps=q_m,
            q_e_low=q_elow,
            q_e_high=q_ehigh,
            causal_m_quintile=quintile,
            high_efficiency_condition=he,
            low_efficiency_condition=hl,
        )
        if displacement == 0:
            row["reason"] = "ZERO_DISPLACEMENT_NOT_AN_ENTRY_EVENT"
        elif he and hl:
            row.update(condition="HE_AND_HL", reason="HE_AND_HL_AT_EQUAL_E_QUANTILES")
        elif he:
            row.update(condition="HE", reason="HIGH_EFFICIENCY_M_ABOVE_CUTOFF")
        elif hl:
            row.update(condition="HL", reason="LOW_EFFICIENCY_MAGNITUDE_CONTROL")
        else:
            row.update(condition="NONE", reason="OUTSIDE_REGISTERED_HE_HL_CONDITIONS")
        records.append(row)
    return records


def r088_event(
    state: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_extra_bars: int = 0,
    exit_time: time = time(14, 55),
) -> dict[str, object]:
    if entry_extra_bars not in {0, 1} or exit_time not in EXITS:
        raise ValueError("unregistered R088 execution profile")
    event = dict(state)
    event.update(
        status="SKIPPED", entry_extra_bars=entry_extra_bars, exit_time_jst=exit_time.isoformat()
    )
    if not bool(event.get("high_efficiency_condition")) and not bool(
        event.get("low_efficiency_condition")
    ):
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    endpoint = datetime.fromisoformat(cast(str, event["pN_close_jst"]))
    signal, exit_open = (
        endpoint + timedelta(minutes=entry_extra_bars),
        datetime.combine(target, exit_time, endpoint.tzinfo),
    )
    exit_signal, lookup = (
        exit_open - timedelta(minutes=1),
        {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])},
    )
    event.update(
        selection_fixed_at_jst=endpoint.isoformat(),
        entry_signal_jst=signal.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
    )
    if not _valid(lookup.get(signal), target, close=True):
        event.update(status="ENTRY_CANCELLED", reason="ENTRY_SIGNAL_CLOSE_MISSING_OR_INELIGIBLE")
        return event
    choices = [
        bar
        for bar in bars.get((target, Session.DAY), [])
        if bar.ts_jst > signal and _valid(bar, target)
    ]
    if not choices:
        event.update(
            status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_OPEN_MISSING_OR_INELIGIBLE"
        )
        return event
    entry = choices[0]
    event["entry_open_jst"] = entry.ts_jst.isoformat()
    if entry.ts_jst - signal > timedelta(minutes=10):
        event.update(
            status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_EXCEEDS_ENGINE_MAX_DELAY"
        )
        return event
    if (
        entry.ts_jst >= exit_open
        or not _valid(lookup.get(exit_signal), target, close=True)
        or not _valid(lookup.get(exit_open), target)
    ):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    direction = "long" if cast(float, event["displacement_points"]) > 0 else "short"
    event.update(
        status="EXECUTABLE",
        reason="HE_HL_NEXT_ELIGIBLE_ENTRY_FIXED_EXIT_EXECUTABLE",
        continuation_direction=direction,
        fade_direction="short" if direction == "long" else "long",
    )
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    **kwargs: int | time,
) -> list[dict[str, object]]:
    state_keys = {
        key: cast(int, value)
        for key, value in kwargs.items()
        if key in {"window_minutes", "m_cutoff", "e_upper", "reference_window"}
    }
    entry_extra_bars = cast(int, kwargs.get("entry_extra_bars", 0))
    exit_time = cast(time, kwargs.get("exit_time", time(14, 55)))
    return [
        r088_event(row, bars, entry_extra_bars=entry_extra_bars, exit_time=exit_time)
        for row in state_ledger(classifier, axis, bars, isolated, **state_keys)
    ]


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(events)
    he = [
        row
        for row in rows
        if bool(row.get("high_efficiency_condition")) and row.get("status") == "EXECUTABLE"
    ]
    hl = [
        row
        for row in rows
        if bool(row.get("low_efficiency_condition")) and row.get("status") == "EXECUTABLE"
    ]
    years, directions = (
        Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in he),
        Counter("up" if cast(float, row["displacement_points"]) > 0 else "down" for row in he),
    )
    known = (
        "NO_",
        "R004_",
        "WINDOW_",
        "P0_",
        "ZERO_",
        "VALID_",
        "INSUFFICIENT_",
        "OUTSIDE_",
        "HIGH_",
        "LOW_",
        "HE_",
        "ENTRY_",
        "NEXT_",
        "FIXED_",
    )
    unexplained = [
        cast(str, row["trade_date"])
        for row in rows
        if not str(row.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "he_executable": len(he),
        "hl_executable": len(hl),
        "he_hl_at_least_90_each": len(he) >= 90 and len(hl) >= 90,
        "he_direction_counts": dict(sorted(directions.items())),
        "he_up_down_at_least_30_each": directions["up"] >= 30 and directions["down"] >= 30,
        "he_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "he_2021_2024_at_least_12_each": all(years[year] >= 12 for year in range(2021, 2025)),
        "he_2025_h1_at_least_6": years[2025] >= 6,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "he_hl_at_least_90_each",
            "he_up_down_at_least_30_each",
            "he_2021_2024_at_least_12_each",
            "he_2025_h1_at_least_6",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(
            sorted(Counter(str(row.get("reason", row["status"])) for row in rows).items())
        ),
        "condition_counts": {"HE": len(he), "HL": len(hl)},
        "gate": gate,
    }


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    executable = [row for row in events if row.get("status") == "EXECUTABLE"]
    checks = {
        "scheduled_axis_complete_unique": [row["trade_date"] for row in events]
        == [target.isoformat() for target in axis]
        and len(events) == len({row["trade_date"] for row in events}),
        "p0_to_pN_timestamp_mapping_and_all_l_terms": all(
            len(cast(list[float], row.get("p_close_points", [])))
            == cast(int, row["window_minutes"])
            and len(cast(list[float], row.get("l_terms_points", [])))
            == cast(int, row["window_minutes"])
            and abs(sum(cast(list[float], row["l_terms_points"])) - cast(float, row["l_points"]))
            < 1e-9
            for row in events
            if bool(row.get("observation_valid"))
        ),
        "reference_is_exact_prior_scheduled_window_current_excluded": all(
            cast(list[str], row["reference_scheduled_trade_dates"])
            == [
                target.isoformat()
                for target in axis[
                    max(0, index - cast(int, row["reference_window_scheduled_trade_dates"])) : index
                ]
            ]
            and cast(str, row["trade_date"])
            not in cast(list[str], row["reference_valid_trade_dates"])
            for index, row in enumerate(events)
        ),
        "signal_after_0959_close_next_eligible_open_and_fixed_exit": all(
            datetime.fromisoformat(cast(str, row["selection_fixed_at_jst"])).time() == time(9, 59)
            and datetime.fromisoformat(cast(str, row["entry_open_jst"]))
            > datetime.fromisoformat(cast(str, row["entry_signal_jst"]))
            and datetime.fromisoformat(cast(str, row["exit_open_jst"])).time()
            == time.fromisoformat(cast(str, row["exit_time_jst"]))
            for row in executable
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def aligned_daily_net(
    axis: list[date], trades: tuple[Trade, ...], unknown: set[date]
) -> dict[str, int | None]:
    daily: dict[date, int | None] = {target: None if target in unknown else 0 for target in axis}
    for trade in trades:
        if (
            trade.trade_date not in daily
            or daily[trade.trade_date] is None
            or daily[trade.trade_date] != 0
        ):
            raise ValueError("R088 trade outside observable axis or duplicate event")
        daily[trade.trade_date] = trade.net_pnl_jpy
    return {target.isoformat(): daily[target] for target in axis}


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    rng = np.random.default_rng(seed)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[
        :, :length
    ]


def _ci(samples: NDArray[np.float64]) -> list[float] | None:
    return (
        None
        if not len(samples) or not np.isfinite(samples).all()
        else [float(value) for value in np.quantile(samples, (0.025, 0.975), method="linear")]
    )


def bootstrap(
    he_daily: list[int],
    fade_daily: list[int],
    hl_daily: list[int],
    he_q_counts: list[list[int]],
    hl_q_counts: list[list[int]],
    he_weights: list[float],
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if not (
        len(he_daily) == len(fade_daily) == len(hl_daily) == len(he_q_counts) == len(hl_q_counts)
    ):
        raise ValueError("R088 bootstrap axes are misaligned")
    index = mbb_indices(len(he_daily))
    he, fade, hl = (np.asarray(value, dtype=float) for value in (he_daily, fade_daily, hl_daily))
    heq, hlq = np.asarray(he_q_counts, dtype=float), np.asarray(hl_q_counts, dtype=float)
    samples = np.full(MBB_REPETITIONS, np.nan)
    empty = 0
    for iteration, chosen in enumerate(index):
        values: list[float] = []
        for q, weight in enumerate(he_weights):
            hn, ln = heq[chosen, q].sum(), hlq[chosen, q].sum()
            if weight and (hn == 0 or ln == 0):
                empty += 1
                break
            if weight:
                values.append(
                    weight
                    * (
                        he[chosen][heq[chosen, q] > 0].sum() / hn
                        - hl[chosen][hlq[chosen, q] > 0].sum() / ln
                    )
                )
        else:
            samples[iteration] = sum(values)
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile; common blocks",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "he_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(he_daily),
            "ci95_percentile_linear": _ci(he[index].mean(axis=1)),
        },
        "he_minus_paired_fade_paired_daily_net_jpy": {
            "estimate": fmean([a - b for a, b in zip(he_daily, fade_daily, strict=True)]),
            "ci95_percentile_linear": _ci((he[index] - fade[index]).mean(axis=1)),
        },
        "he_minus_hl_standardized_net_jpy_per_trade": {
            "estimate": None,
            "ci95_percentile_linear": _ci(samples),
            "empty_cell_resamples": empty,
            "he_standard_weights": he_weights,
        },
    }, index
