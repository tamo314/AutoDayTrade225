"""Causal state and inference for TASK-R089-Q001."""

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
from n225m_bt.research.r088_cash_open_path_efficiency import (
    MBB_BLOCK_LENGTH,
    MBB_REPETITIONS,
    MBB_SEED,
    _valid,
    mbb_indices,
    nearest_rank,
    r088_event,
)

WINDOWS, RECENT_WINDOWS, M_CUTOFFS, F_UPPERS, REFERENCES = (
    {30, 60, 90},
    {10, 15, 20},
    {50, 60, 70},
    {60, 70, 80},
    {120, 160, 200},
)
DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)


def required_valid_reference_count(window: int) -> int:
    if window not in REFERENCES:
        raise ValueError("unregistered R089 reference window")
    return ceil(window * 7 / 8)


def _observation(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    window_minutes: int,
    recent_minutes: int,
) -> dict[str, object]:
    row: dict[str, object] = {
        "trade_date": target.isoformat(),
        "window_minutes": window_minutes,
        "recent_minutes": recent_minutes,
        "observation_valid": False,
    }
    schedule = classifier.exchange_calendar.get(target)
    if schedule is None:
        row["reason"] = "NO_REGISTERED_SCHEDULE"
        return row
    zone = classifier.session_open(target, Session.DAY).tzinfo
    assert zone is not None
    begin, end = (
        datetime.combine(target, time(9), zone),
        datetime.combine(target, time(9), zone) + timedelta(minutes=window_minutes - 1),
    )
    p_recent = begin + timedelta(minutes=window_minutes - recent_minutes - 1)
    row.update(
        schedule_version=schedule.schedule_version,
        p0_0900_open_jst=begin.isoformat(),
        p_recent_close_jst=p_recent.isoformat(),
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
    rows = [lookup.get(begin + timedelta(minutes=i)) for i in range(window_minutes)]
    if not _valid(rows[0], target):
        row["reason"] = "P0_0900_OPEN_MISSING_OR_INELIGIBLE"
        return row
    if not all(_valid(bar, target) and _valid(bar, target, close=True) for bar in rows):
        row["reason"] = "WINDOW_BAR_MISSING_OR_INELIGIBLE_OR_NONCONTINUOUS"
        return row
    assert rows[0] is not None
    p0, pN = float(rows[0].open), float(cast(Bar, rows[-1]).close)
    recent_close = float(cast(Bar, rows[window_minutes - recent_minutes - 1]).close)
    displacement = pN - p0
    row.update(
        p0_open_points=p0,
        p_recent_close_points=recent_close,
        pN_close_points=pN,
        displacement_points=displacement,
    )
    if displacement == 0:
        row["reason"] = "ZERO_DISPLACEMENT"
        return row
    f_value = (1.0 if displacement > 0 else -1.0) * (pN - recent_close) / abs(displacement)
    row.update(
        observation_valid=True,
        reason="VALID_M_F_PATH",
        m_bps=10_000 * abs(displacement) / p0,
        f_late_concentration=f_value,
    )
    return row


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    window_minutes: int = 60,
    recent_minutes: int = 15,
    m_cutoff: int = 60,
    f_upper: int = 70,
    reference_window: int = 160,
) -> list[dict[str, object]]:
    if (
        window_minutes not in WINDOWS
        or recent_minutes not in RECENT_WINDOWS
        or recent_minutes >= window_minutes
        or m_cutoff not in M_CUTOFFS
        or f_upper not in F_UPPERS
        or reference_window not in REFERENCES
    ):
        raise ValueError("unregistered R089 state profile")
    ordered: list[date] = list(axis)
    records: list[dict[str, object]] = []
    for index, target in enumerate(ordered):
        row = _observation(classifier, target, bars, isolated, window_minutes, recent_minutes)
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
            f_upper_percentile=f_upper,
            f_lower_percentile=100 - f_upper,
            late_concentration_condition=False,
            early_completion_condition=False,
            high_efficiency_condition=False,
            low_efficiency_condition=False,
        )
        if not bool(row["observation_valid"]):
            records.append(row)
            continue
        if len(valid) < required_valid_reference_count(reference_window):
            row["reason"] = "INSUFFICIENT_VALID_M_F_IN_EXACT_PRIOR_SCHEDULED_REFERENCE_WINDOW"
            records.append(row)
            continue
        q_m, q80 = (
            nearest_rank([cast(float, x["m_bps"]) for x in valid], m_cutoff),
            nearest_rank([cast(float, x["m_bps"]) for x in valid], 80),
        )
        m_value = cast(float, row["m_bps"])
        row.update(q_m_bps=q_m, q_m_band_upper_bps=q80)
        if m_value < q_m:
            row.update(condition="NONE", reason="M_BELOW_CAUSAL_CUTOFF")
            records.append(row)
            continue
        band = "B1" if m_value < q80 else "B2"
        in_band = [
            x
            for x in valid
            if (cast(float, x["m_bps"]) < q80) == (band == "B1") and cast(float, x["m_bps"]) >= q_m
        ]
        if not in_band:
            row["reason"] = "EMPTY_CURRENT_THRESHOLD_M_BAND_REFERENCE"
            records.append(row)
            continue
        q_low, q_high = (
            nearest_rank([cast(float, x["f_late_concentration"]) for x in in_band], 100 - f_upper),
            nearest_rank([cast(float, x["f_late_concentration"]) for x in in_band], f_upper),
        )
        f_value = cast(float, row["f_late_concentration"])
        lc, ec = f_value >= q_high, f_value <= q_low
        row.update(
            m_band=band,
            band_reference_trade_dates=[cast(str, x["trade_date"]) for x in in_band],
            band_reference_m_bps=[cast(float, x["m_bps"]) for x in in_band],
            band_reference_f=[cast(float, x["f_late_concentration"]) for x in in_band],
            q_conditional_f_low=q_low,
            q_conditional_f_high=q_high,
            late_concentration_condition=lc,
            early_completion_condition=ec,
            high_efficiency_condition=lc,
            low_efficiency_condition=ec,
        )
        if lc and ec:
            row.update(condition="LC_AND_EC", reason="LC_AND_EC_AT_EQUAL_F_QUANTILES")
        elif lc:
            row.update(condition="LC", reason="LATE_CONCENTRATION")
        elif ec:
            row.update(condition="EC", reason="EARLY_COMPLETION")
        else:
            row.update(condition="NONE", reason="OUTSIDE_LATE_CONCENTRATION_CONDITIONS")
        records.append(row)
    return records


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    **kwargs: int | time,
) -> list[dict[str, object]]:
    keys = {
        key: cast(int, value)
        for key, value in kwargs.items()
        if key in {"window_minutes", "recent_minutes", "m_cutoff", "f_upper", "reference_window"}
    }
    return [
        r088_event(
            row,
            bars,
            entry_extra_bars=cast(int, kwargs.get("entry_extra_bars", 0)),
            exit_time=cast(time, kwargs.get("exit_time", time(14, 55))),
        )
        for row in state_ledger(classifier, axis, bars, isolated, **keys)
    ]


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(events)
    lc = [
        x
        for x in rows
        if bool(x.get("late_concentration_condition")) and x.get("status") == "EXECUTABLE"
    ]
    ec = [
        x
        for x in rows
        if bool(x.get("early_completion_condition")) and x.get("status") == "EXECUTABLE"
    ]
    years, directions = (
        Counter(date.fromisoformat(cast(str, x["trade_date"])).year for x in lc),
        Counter("up" if cast(float, x["displacement_points"]) > 0 else "down" for x in lc),
    )
    bands = {
        band: {
            "LC": sum(x.get("m_band") == band for x in lc),
            "EC": sum(x.get("m_band") == band for x in ec),
        }
        for band in ("B1", "B2")
    }
    known = (
        "NO_",
        "R004_",
        "WINDOW_",
        "P0_",
        "ZERO_",
        "VALID_",
        "INSUFFICIENT_",
        "M_BELOW_",
        "EMPTY_",
        "OUTSIDE_",
        "LATE_",
        "EARLY_",
        "LC_",
        "ENTRY_",
        "NEXT_",
        "FIXED_",
        "HE_",
    )
    unexplained = [
        cast(str, x["trade_date"]) for x in rows if not str(x.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "lc_executable": len(lc),
        "ec_executable": len(ec),
        "lc_ec_at_least_90_each": len(lc) >= 90 and len(ec) >= 90,
        "lc_direction_counts": dict(sorted(directions.items())),
        "lc_up_down_at_least_30_each": directions["up"] >= 30 and directions["down"] >= 30,
        "lc_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "lc_2022_2024_at_least_15_each": all(years[y] >= 15 for y in range(2022, 2025)),
        "lc_2025_h1_at_least_8": years[2025] >= 8,
        "band_counts": bands,
        "each_band_lc_ec_at_least_25": all(v["LC"] >= 25 and v["EC"] >= 25 for v in bands.values()),
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[k])
        for k in (
            "lc_ec_at_least_90_each",
            "lc_up_down_at_least_30_each",
            "lc_2022_2024_at_least_15_each",
            "lc_2025_h1_at_least_8",
            "each_band_lc_ec_at_least_25",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(
            sorted(Counter(str(x.get("reason", x["status"])) for x in rows).items())
        ),
        "condition_counts": {"LC": len(lc), "EC": len(ec)},
        "gate": gate,
    }


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    executable = [x for x in events if x.get("status") == "EXECUTABLE"]
    checks = {
        "scheduled_axis_complete_unique": [x["trade_date"] for x in events]
        == [d.isoformat() for d in axis]
        and len(events) == len({x["trade_date"] for x in events}),
        "p0_p45_p60_mapping_and_f_sign_denominator": all(
            datetime.fromisoformat(cast(str, x["p0_0900_open_jst"])).time() == time(9)
            and datetime.fromisoformat(cast(str, x["p_recent_close_jst"])).time() == time(9, 44)
            and datetime.fromisoformat(cast(str, x["pN_close_jst"])).time() == time(9, 59)
            and abs(
                cast(float, x["f_late_concentration"])
                - (1 if cast(float, x["displacement_points"]) > 0 else -1)
                * (cast(float, x["pN_close_points"]) - cast(float, x["p_recent_close_points"]))
                / abs(cast(float, x["displacement_points"]))
            )
            < 1e-12
            for x in events
            if bool(x.get("observation_valid"))
            and x.get("window_minutes") == 60
            and x.get("recent_minutes") == 15
        ),
        "strict_prior_160_scheduled_window_current_excluded": all(
            cast(list[str], x["reference_scheduled_trade_dates"])
            == [
                d.isoformat()
                for d in axis[
                    max(0, i - cast(int, x["reference_window_scheduled_trade_dates"])) : i
                ]
            ]
            and cast(str, x["trade_date"]) not in cast(list[str], x["reference_valid_trade_dates"])
            for i, x in enumerate(events)
        ),
        "band_reference_is_prior_and_current_threshold_membership": all(
            cast(str, x["trade_date"]) not in cast(list[str], x["band_reference_trade_dates"])
            and all(
                (value < cast(float, x["q_m_band_upper_bps"])) == (x["m_band"] == "B1")
                and value >= cast(float, x["q_m_bps"])
                for value in cast(list[float], x["band_reference_m_bps"])
            )
            for x in events
            if x.get("m_band") in {"B1", "B2"}
        ),
        "signal_after_endpoint_next_eligible_open_and_fixed_exit": all(
            datetime.fromisoformat(cast(str, x["entry_open_jst"]))
            > datetime.fromisoformat(cast(str, x["entry_signal_jst"]))
            and datetime.fromisoformat(cast(str, x["exit_open_jst"])).time()
            == time.fromisoformat(cast(str, x["exit_time_jst"]))
            for x in executable
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def bootstrap(
    lc_daily: list[int],
    fade_daily: list[int],
    ec_daily: list[int],
    lc_band: list[list[int]],
    ec_band: list[list[int]],
    weights: list[float],
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if not (len(lc_daily) == len(fade_daily) == len(ec_daily) == len(lc_band) == len(ec_band)):
        raise ValueError("R089 bootstrap axes are misaligned")
    index = mbb_indices(len(lc_daily))
    lc, fade, ec = (np.asarray(x, dtype=float) for x in (lc_daily, fade_daily, ec_daily))
    lb, eb = np.asarray(lc_band, dtype=float), np.asarray(ec_band, dtype=float)
    samples = np.full(MBB_REPETITIONS, np.nan)
    empty = 0
    for iteration, chosen in enumerate(index):
        values: list[float] = []
        for band, weight in enumerate(weights):
            ln, en = lb[chosen, band].sum(), eb[chosen, band].sum()
            if weight and (ln == 0 or en == 0):
                empty += 1
                break
            if weight:
                values.append(
                    weight
                    * (
                        lc[chosen][lb[chosen, band] > 0].sum() / ln
                        - ec[chosen][eb[chosen, band] > 0].sum() / en
                    )
                )
        else:
            samples[iteration] = sum(values)

    def ci(values: NDArray[np.float64]) -> list[float] | None:
        return (
            None
            if not np.isfinite(values).all()
            else [float(x) for x in np.quantile(values, (0.025, 0.975), method="linear")]
        )

    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile; common blocks",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": MBB_BLOCK_LENGTH,
        "lc_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(lc_daily),
            "ci95_percentile_linear": ci(lc[index].mean(axis=1)),
        },
        "lc_minus_paired_fade_paired_daily_net_jpy": {
            "estimate": fmean([a - b for a, b in zip(lc_daily, fade_daily, strict=True)]),
            "ci95_percentile_linear": ci((lc[index] - fade[index]).mean(axis=1)),
        },
        "lc_minus_ec_band_standardized_net_jpy_per_trade": {
            "estimate": None,
            "ci95_percentile_linear": ci(samples),
            "empty_cell_resamples": empty,
            "lc_band_standard_weights": weights,
        },
    }, index
