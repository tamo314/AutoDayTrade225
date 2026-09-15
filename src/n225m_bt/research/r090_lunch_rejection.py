"""Causal event construction for TASK-R090-Q001.

The two series deliberately keep their rolling state separate: the morning
placebo can neither provide nor consume a lunch observation.
"""

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
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r081_tse_lunch_continuation import _valid
from n225m_bt.research.r088_cash_open_path_efficiency import (
    MBB_BLOCK_LENGTH,
    MBB_REPETITIONS,
    MBB_SEED,
    mbb_indices,
    nearest_rank,
)

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
REFERENCES = {120, 160, 200}
M_CUTOFFS = {50, 60, 70}
J_UPPERS = {60, 70, 80}
CASH_LUNCH_SCHEDULE_VERSION = "tse_cash_lunch_2021_2025_v1"
INTERVALS = {
    "lunch": (time(11, 30), time(12, 29), time(12, 39), time(14, 55)),
    "placebo": (time(10, 0), time(10, 59), time(11, 9), time(13, 25)),
}


def required_valid_reference_count(window: int) -> int:
    if window not in REFERENCES:
        raise ValueError("unregistered R090 reference window")
    return ceil(window * 7 / 8)


def _observation(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    interval: str,
    confirmation_minutes: int,
) -> dict[str, object]:
    if interval not in INTERVALS or confirmation_minutes not in {5, 10, 15}:
        raise ValueError("unregistered R090 interval")
    p0_clock, p1_clock, _, _ = INTERVALS[interval]
    row: dict[str, object] = {
        "trade_date": target.isoformat(),
        "interval": interval,
        "observation_valid": False,
        "cash_lunch_schedule_version": CASH_LUNCH_SCHEDULE_VERSION if interval == "lunch" else None,
    }
    schedule = classifier.exchange_calendar.get(target)
    if schedule is None:
        row["reason"] = "NO_REGISTERED_SCHEDULE"
        return row
    zone = classifier.session_open(target, Session.DAY).tzinfo
    assert zone is not None
    p0 = datetime.combine(target, p0_clock, zone)
    p1 = datetime.combine(target, p1_clock, zone)
    p2 = p1 + timedelta(minutes=confirmation_minutes)
    row.update(
        schedule_version=schedule.schedule_version,
        p0_open_jst=p0.isoformat(),
        p1_close_jst=p1.isoformat(),
        p2_close_jst=p2.isoformat(),
    )
    if (target, Session.DAY) in isolated:
        row["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return row
    if not (
        classifier.session_open(target, Session.DAY)
        <= p0
        <= p2
        <= classifier.session_close(target, Session.DAY)
    ):
        row["reason"] = "INTERVAL_NOT_INSIDE_REGISTERED_FUTURES_DAY_SESSION"
        return row
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    required = [
        lookup.get(p0 + timedelta(minutes=index))
        for index in range(int((p2 - p0).total_seconds() // 60) + 1)
    ]
    if not _valid(required[0], target):
        row["reason"] = "P0_OPEN_MISSING_OR_INELIGIBLE"
        return row
    if not all(_valid(bar, target) and _valid(bar, target, close=True) for bar in required):
        row["reason"] = "REQUIRED_INTERVAL_BAR_MISSING_OR_INELIGIBLE_OR_NONCONTINUOUS"
        return row
    assert required[0] is not None
    p0_value, p1_value, p2_value = (
        float(required[0].open),
        float(lookup[p1].close),
        float(lookup[p2].close),
    )
    displacement = p1_value - p0_value
    row.update(
        p0_open_points=p0_value,
        p1_close_points=p1_value,
        p2_close_points=p2_value,
        displacement_points=displacement,
    )
    if displacement == 0:
        row["reason"] = "ZERO_DISPLACEMENT"
        return row
    j_value = -(1.0 if displacement > 0 else -1.0) * (p2_value - p1_value) / abs(displacement)
    row.update(
        observation_valid=True,
        reason="VALID_M_J_PATH",
        m_bps=10_000 * abs(displacement) / p0_value,
        j_rejection=j_value,
    )
    return row


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    interval: str = "lunch",
    m_cutoff: int = 60,
    j_upper: int = 70,
    reference_window: int = 160,
    confirmation_minutes: int = 10,
) -> list[dict[str, object]]:
    if (
        interval not in INTERVALS
        or m_cutoff not in M_CUTOFFS
        or j_upper not in J_UPPERS
        or reference_window not in REFERENCES
        or confirmation_minutes not in {5, 10, 15}
    ):
        raise ValueError("unregistered R090 state profile")
    ordered: list[date] = list(axis)
    records: list[dict[str, object]] = []
    for index, target in enumerate(ordered):
        row = _observation(classifier, target, bars, isolated, interval, confirmation_minutes)
        reference = records[max(0, index - reference_window) : index]
        valid = [item for item in reference if bool(item.get("observation_valid"))]
        row.update(
            reference_window_scheduled_trade_dates=reference_window,
            reference_scheduled_trade_dates=[cast(str, x["trade_date"]) for x in reference],
            reference_valid_trade_dates=[cast(str, x["trade_date"]) for x in valid],
            reference_valid_count=len(valid),
            minimum_valid_reference_count=required_valid_reference_count(reference_window),
            m_cutoff_percentile=m_cutoff,
            m_upper_band_percentile=80,
            j_upper_percentile=j_upper,
            j_lower_percentile=100 - j_upper,
            lr_condition=False,
            la_condition=False,
            high_efficiency_condition=False,
            low_efficiency_condition=False,
        )
        if not bool(row["observation_valid"]):
            records.append(row)
            continue
        if len(valid) < required_valid_reference_count(reference_window):
            row["reason"] = "INSUFFICIENT_VALID_M_J_IN_EXACT_PRIOR_SCHEDULED_REFERENCE_WINDOW"
            records.append(row)
            continue
        q_m, q80 = (
            nearest_rank([cast(float, x["m_bps"]) for x in valid], m_cutoff),
            nearest_rank([cast(float, x["m_bps"]) for x in valid], 80),
        )
        row.update(q_m_bps=q_m, q_m_band_upper_bps=q80)
        if cast(float, row["m_bps"]) < q_m:
            row.update(condition="NONE", reason="M_BELOW_CAUSAL_CUTOFF")
            records.append(row)
            continue
        band = "B1" if cast(float, row["m_bps"]) < q80 else "B2"
        in_band = [
            x
            for x in valid
            if cast(float, x["m_bps"]) >= q_m
            and ((cast(float, x["m_bps"]) < q80) == (band == "B1"))
        ]
        if not in_band:
            row["reason"] = "EMPTY_CURRENT_THRESHOLD_M_BAND_REFERENCE"
            records.append(row)
            continue
        q_low, q_high = (
            nearest_rank([cast(float, x["j_rejection"]) for x in in_band], 100 - j_upper),
            nearest_rank([cast(float, x["j_rejection"]) for x in in_band], j_upper),
        )
        j_value = cast(float, row["j_rejection"])
        lr, la = j_value >= q_high, j_value <= q_low
        row.update(
            m_band=band,
            band_reference_trade_dates=[cast(str, x["trade_date"]) for x in in_band],
            band_reference_m_bps=[cast(float, x["m_bps"]) for x in in_band],
            band_reference_j=[cast(float, x["j_rejection"]) for x in in_band],
            q_conditional_j_low=q_low,
            q_conditional_j_high=q_high,
            lr_condition=lr,
            la_condition=la,
            high_efficiency_condition=lr,
            low_efficiency_condition=la,
        )
        if lr and la:
            row.update(condition="LR_AND_LA", reason="LR_AND_LA_AT_EQUAL_J_QUANTILES")
        elif lr:
            row.update(condition="LR", reason="LUNCH_DISPLACEMENT_REJECTION")
        elif la:
            row.update(condition="LA", reason="LUNCH_DISPLACEMENT_ACCEPTANCE")
        else:
            row.update(condition="NONE", reason="OUTSIDE_REJECTION_ACCEPTANCE_CONDITIONS")
        records.append(row)
    return records


def _execution_event(
    state: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_extra_bars: int,
    exit_time: time,
) -> dict[str, object]:
    if entry_extra_bars not in {0, 1}:
        raise ValueError("unregistered R090 entry delay")
    event = dict(state)
    event.update(
        status="SKIPPED", entry_extra_bars=entry_extra_bars, exit_time_jst=exit_time.isoformat()
    )
    if not bool(event.get("lr_condition")) and not bool(event.get("la_condition")):
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    endpoint = datetime.fromisoformat(cast(str, event["p2_close_jst"]))
    signal, exit_open = (
        endpoint + timedelta(minutes=entry_extra_bars),
        datetime.combine(target, exit_time, endpoint.tzinfo),
    )
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    event.update(
        selection_fixed_at_jst=endpoint.isoformat(),
        entry_signal_jst=signal.isoformat(),
        exit_signal_jst=(exit_open - timedelta(minutes=1)).isoformat(),
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
        or not _valid(lookup.get(exit_open - timedelta(minutes=1)), target, close=True)
        or not _valid(lookup.get(exit_open), target)
    ):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    continuation = "long" if cast(float, event["displacement_points"]) > 0 else "short"
    event.update(
        status="EXECUTABLE",
        reason="LR_LA_NEXT_ELIGIBLE_ENTRY_FIXED_EXIT_EXECUTABLE",
        continuation_direction=continuation,
        fade_direction="short" if continuation == "long" else "long",
    )
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    **kwargs: int | time | str,
) -> list[dict[str, object]]:
    interval = cast(str, kwargs.get("interval", "lunch"))
    keys = {
        key: cast(int, value)
        for key, value in kwargs.items()
        if key in {"m_cutoff", "j_upper", "reference_window", "confirmation_minutes"}
    }
    exit_time = cast(time, kwargs.get("exit_time", INTERVALS[interval][3]))
    return [
        _execution_event(
            row,
            bars,
            entry_extra_bars=cast(int, kwargs.get("entry_extra_bars", 0)),
            exit_time=exit_time,
        )
        for row in state_ledger(classifier, axis, bars, isolated, interval=interval, **keys)
    ]


def feasibility(
    primary: list[dict[str, object]], placebo: list[dict[str, object]]
) -> dict[str, object]:
    def executable(events: list[dict[str, object]], flag: str) -> list[dict[str, object]]:
        return [x for x in events if bool(x.get(flag)) and x.get("status") == "EXECUTABLE"]

    lr, la, pr = (
        executable(primary, "lr_condition"),
        executable(primary, "la_condition"),
        executable(placebo, "lr_condition"),
    )
    years = Counter(date.fromisoformat(cast(str, x["trade_date"])).year for x in lr)
    directions = Counter("up" if cast(float, x["displacement_points"]) > 0 else "down" for x in lr)
    bands = {
        band: {
            "LR": sum(x.get("m_band") == band for x in lr),
            "LA": sum(x.get("m_band") == band for x in la),
        }
        for band in ("B1", "B2")
    }
    known = (
        "NO_",
        "R004_",
        "INTERVAL_",
        "P0_",
        "REQUIRED_",
        "ZERO_",
        "VALID_",
        "INSUFFICIENT_",
        "M_BELOW_",
        "EMPTY_",
        "OUTSIDE_",
        "LUNCH_",
        "LR_",
        "LA_",
        "ENTRY_",
        "NEXT_",
        "FIXED_",
        "HE_",
    )
    unexplained = [
        cast(str, x["trade_date"])
        for x in primary + placebo
        if not str(x.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "lr_executable": len(lr),
        "la_executable": len(la),
        "pr_executable": len(pr),
        "lr_la_pr_at_least_90_each": len(lr) >= 90 and len(la) >= 90 and len(pr) >= 90,
        "lr_direction_counts": dict(directions),
        "lr_up_down_at_least_30_each": directions["up"] >= 30 and directions["down"] >= 30,
        "lr_by_year": {str(y): years[y] for y in range(2021, 2026)},
        "lr_2022_2024_at_least_15_each": all(years[y] >= 15 for y in range(2022, 2025)),
        "lr_2025_h1_at_least_8": years[2025] >= 8,
        "band_counts": bands,
        "each_band_lr_la_at_least_25": all(v["LR"] >= 25 and v["LA"] >= 25 for v in bands.values()),
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "lr_la_pr_at_least_90_each",
            "lr_up_down_at_least_30_each",
            "lr_2022_2024_at_least_15_each",
            "lr_2025_h1_at_least_8",
            "each_band_lr_la_at_least_25",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "primary_status_counts": dict(Counter(str(x.get("reason", x["status"])) for x in primary)),
        "placebo_status_counts": dict(Counter(str(x.get("reason", x["status"])) for x in placebo)),
        "gate": gate,
    }


def support_audit(events: list[dict[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for band in ("B1", "B2"):
        groups = {
            name: [
                cast(float, x["m_bps"])
                for x in events
                if x.get("status") == "EXECUTABLE" and x.get("m_band") == band and bool(x.get(flag))
            ]
            for name, flag in (("LR", "lr_condition"), ("LA", "la_condition"))
        }
        if not groups["LR"] or not groups["LA"]:
            result[band] = {
                "lr_m_bps": groups["LR"],
                "la_m_bps": groups["LA"],
                "intersection": None,
                "passed": False,
            }
            continue
        low, high = (
            max(min(groups["LR"]), min(groups["LA"])),
            min(max(groups["LR"]), max(groups["LA"])),
        )
        result[band] = {
            "lr_m_bps": groups["LR"],
            "la_m_bps": groups["LA"],
            "intersection": [low, high],
            "lr_in_intersection": sum(low <= x <= high for x in groups["LR"]),
            "la_in_intersection": sum(low <= x <= high for x in groups["LA"]),
            "passed": low <= high
            and sum(low <= x <= high for x in groups["LR"]) >= 10
            and sum(low <= x <= high for x in groups["LA"]) >= 10,
        }
    return {
        "bands": result,
        "passed": all(bool(cast(dict[str, object], item)["passed"]) for item in result.values()),
    }


def causality_audit(
    primary: list[dict[str, object]], placebo: list[dict[str, object]], axis: list[date]
) -> dict[str, object]:
    events = primary + placebo
    expected = {
        "lunch": (time(11, 30), time(12, 29), time(12, 39), time(14, 55)),
        "placebo": (time(10), time(10, 59), time(11, 9), time(13, 25)),
    }
    checks = {
        "scheduled_axis_complete_unique_each_series": all(
            [
                len(series) == len(axis)
                and [x["trade_date"] for x in series] == [d.isoformat() for d in axis]
                for series in (primary, placebo)
            ]
        ),
        "time_mapping_and_j_sign_denominator": all(
            datetime.fromisoformat(cast(str, x["p0_open_jst"])).time()
            == expected[cast(str, x["interval"])][0]
            and datetime.fromisoformat(cast(str, x["p1_close_jst"])).time()
            == expected[cast(str, x["interval"])][1]
            and datetime.fromisoformat(cast(str, x["p2_close_jst"])).time()
            == expected[cast(str, x["interval"])][2]
            and abs(
                cast(float, x["j_rejection"])
                + (1 if cast(float, x["displacement_points"]) > 0 else -1)
                * (cast(float, x["p2_close_points"]) - cast(float, x["p1_close_points"]))
                / abs(cast(float, x["displacement_points"]))
            )
            < 1e-12
            for x in events
            if bool(x.get("observation_valid"))
        ),
        "strict_prior_and_independent_placebo_references": all(
            cast(list[str], x["reference_scheduled_trade_dates"])
            == [
                d.isoformat()
                for d in axis[
                    max(0, i - cast(int, x["reference_window_scheduled_trade_dates"])) : i
                ]
            ]
            and cast(str, x["trade_date"]) not in cast(list[str], x["reference_valid_trade_dates"])
            and cast(str, x["trade_date"])
            not in cast(list[str], x.get("band_reference_trade_dates", []))
            for series in (primary, placebo)
            for i, x in enumerate(series)
        ),
        "next_eligible_entry_and_fixed_exit": all(
            datetime.fromisoformat(cast(str, x["entry_open_jst"]))
            > datetime.fromisoformat(cast(str, x["entry_signal_jst"]))
            and datetime.fromisoformat(cast(str, x["exit_open_jst"])).time()
            == expected[cast(str, x["interval"])][3]
            for x in events
            if x.get("status") == "EXECUTABLE"
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def bootstrap(
    lr_daily: list[int],
    continuation_daily: list[int],
    la_daily: list[int],
    pr_daily: list[int],
    lr_band: list[list[int]],
    la_band: list[list[int]],
    pr_band: list[list[int]],
    weights: list[float],
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if not (
        len(lr_daily)
        == len(continuation_daily)
        == len(la_daily)
        == len(pr_daily)
        == len(lr_band)
        == len(la_band)
        == len(pr_band)
    ):
        raise ValueError("R090 bootstrap axes are misaligned")
    index = mbb_indices(len(lr_daily))
    lr, continuation, la, pr = (
        np.asarray(x, dtype=float) for x in (lr_daily, continuation_daily, la_daily, pr_daily)
    )
    lr_b, la_b, pr_b = (np.asarray(x, dtype=float) for x in (lr_band, la_band, pr_band))

    def ratio(other: NDArray[np.float64]) -> NDArray[np.float64]:
        result = np.full(MBB_REPETITIONS, np.nan)
        for iteration, chosen in enumerate(index):
            terms: list[float] = []
            for band, weight in enumerate(weights):
                left_n, right_n = lr_b[chosen, band].sum(), other[chosen, band].sum()
                if weight and (left_n == 0 or right_n == 0):
                    break
                if weight:
                    terms.append(
                        weight
                        * (
                            lr[chosen][lr_b[chosen, band] > 0].sum() / left_n
                            - (la if other is la_b else pr)[chosen][other[chosen, band] > 0].sum()
                            / right_n
                        )
                    )
            else:
                result[iteration] = sum(terms)
        return result

    def ci(values: NDArray[np.float64]) -> list[float] | None:
        return (
            None
            if not np.isfinite(values).all()
            else [float(x) for x in np.quantile(values, (0.025, 0.975), method="linear")]
        )

    la_ratio, pr_ratio = ratio(la_b), ratio(pr_b)
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile; common blocks",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "lr_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(lr_daily),
            "ci95_percentile_linear": ci(lr[index].mean(axis=1)),
        },
        "lr_minus_paired_continuation_paired_daily_net_jpy": {
            "estimate": fmean([x - y for x, y in zip(lr_daily, continuation_daily, strict=True)]),
            "ci95_percentile_linear": ci((lr[index] - continuation[index]).mean(axis=1)),
        },
        "lr_minus_la_band_standardized_net_jpy_per_trade": {
            "estimate": None,
            "ci95_percentile_linear": ci(la_ratio),
            "lr_band_standard_weights": weights,
        },
        "lr_minus_pr_band_standardized_net_jpy_per_trade": {
            "estimate": None,
            "ci95_percentile_linear": ci(pr_ratio),
            "lr_band_standard_weights": weights,
        },
    }, index
