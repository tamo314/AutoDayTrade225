"""Causal conditional-efficiency state and inference for TASK-R088-Q002."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date, time
from math import ceil
from statistics import fmean
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r088_cash_open_path_efficiency import (
    MBB_REPETITIONS,
    MBB_SEED,
    _observation,
    mbb_indices,
    nearest_rank,
    r088_event,
)

WINDOWS, M_CUTOFFS, E_UPPERS, REFERENCES, COMPARISON_SIZES = (
    {30, 60, 90},
    {50, 60, 70},
    {60, 70, 80},
    {80, 120, 160},
    {40, 60, 80},
)


def required_valid_reference_count(window: int) -> int:
    if window not in REFERENCES:
        raise ValueError("unregistered R088-Q002 reference window")
    return ceil(window * 5 / 6)


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
    comparison_size: int = 60,
) -> list[dict[str, object]]:
    """Build Q002 labels using only records preceding the current scheduled date."""
    if (
        window_minutes not in WINDOWS
        or m_cutoff not in M_CUTOFFS
        or e_upper not in E_UPPERS
        or reference_window not in REFERENCES
        or comparison_size not in COMPARISON_SIZES
    ):
        raise ValueError("unregistered R088-Q002 state profile")
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
            m_upper_band_percentile=80,
            e_upper_percentile=e_upper,
            e_lower_percentile=100 - e_upper,
            comparison_size=comparison_size,
            quantile_method="nearest_rank ceil(n*p/100)-1",
            conditional_high_efficiency_condition=False,
            conditional_low_efficiency_condition=False,
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
        m_values = [cast(float, item["m_bps"]) for item in valid]
        q60, q80 = nearest_rank(m_values, m_cutoff), nearest_rank(m_values, 80)
        m_value = cast(float, row["m_bps"])
        row.update(q_m_bps=q60, q_m_band_upper_bps=q80)
        if m_value < q60:
            row.update(condition="NONE", reason="M_BELOW_CAUSAL_CUTOFF")
            records.append(row)
            continue
        comparison = sorted(
            valid,
            key=lambda item: (
                abs(cast(float, item["m_bps"]) - m_value),
                -date.fromisoformat(cast(str, item["trade_date"])).toordinal(),
            ),
        )[:comparison_size]
        if len(comparison) != comparison_size:
            raise ValueError("valid reference count cannot support registered comparison set")
        q_low, q_high = (
            nearest_rank([cast(float, item["e_efficiency"]) for item in comparison], 100 - e_upper),
            nearest_rank([cast(float, item["e_efficiency"]) for item in comparison], e_upper),
        )
        e_value, displacement = (
            cast(float, row["e_efficiency"]),
            cast(float, row["displacement_points"]),
        )
        che, chl = e_value >= q_high, e_value <= q_low
        band = "B1" if m_value < q80 else "B2"
        row.update(
            comparison_trade_dates=[cast(str, item["trade_date"]) for item in comparison],
            comparison_m_bps=[cast(float, item["m_bps"]) for item in comparison],
            comparison_e_efficiency=[cast(float, item["e_efficiency"]) for item in comparison],
            q_conditional_e_low=q_low,
            q_conditional_e_high=q_high,
            m_band=band,
            conditional_high_efficiency_condition=che,
            conditional_low_efficiency_condition=chl,
            high_efficiency_condition=che,
            low_efficiency_condition=chl,
        )
        if displacement == 0:
            row["reason"] = "ZERO_DISPLACEMENT_NOT_AN_ENTRY_EVENT"
        elif che and chl:
            row.update(condition="CHE_AND_CHL", reason="CHE_AND_CHL_AT_EQUAL_E_QUANTILES")
        elif che:
            row.update(condition="CHE", reason="CONDITIONAL_HIGH_EFFICIENCY")
        elif chl:
            row.update(condition="CHL", reason="CONDITIONAL_LOW_EFFICIENCY")
        else:
            row.update(condition="NONE", reason="OUTSIDE_CONDITIONAL_EFFICIENCY_CONDITIONS")
        records.append(row)
    return records


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
        if key in {"window_minutes", "m_cutoff", "e_upper", "reference_window", "comparison_size"}
    }
    entry_extra_bars = cast(int, kwargs.get("entry_extra_bars", 0))
    exit_time = cast(time, kwargs.get("exit_time", time(14, 55)))
    return [
        r088_event(row, bars, entry_extra_bars=entry_extra_bars, exit_time=exit_time)
        for row in state_ledger(classifier, axis, bars, isolated, **state_keys)
    ]


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(events)
    che = [
        row
        for row in rows
        if bool(row.get("conditional_high_efficiency_condition"))
        and row.get("status") == "EXECUTABLE"
    ]
    chl = [
        row
        for row in rows
        if bool(row.get("conditional_low_efficiency_condition"))
        and row.get("status") == "EXECUTABLE"
    ]
    years = Counter(date.fromisoformat(cast(str, row["trade_date"])).year for row in che)
    directions = Counter(
        "up" if cast(float, row["displacement_points"]) > 0 else "down" for row in che
    )
    bands = {
        band: {
            "CHE": sum(row.get("m_band") == band for row in che),
            "CHL": sum(row.get("m_band") == band for row in chl),
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
        "OUTSIDE_",
        "CONDITIONAL_",
        "HE_HL_",
        "CHE_",
        "CHL_",
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
        "che_executable": len(che),
        "chl_executable": len(chl),
        "che_chl_at_least_90_each": len(che) >= 90 and len(chl) >= 90,
        "che_direction_counts": dict(sorted(directions.items())),
        "che_up_down_at_least_30_each": directions["up"] >= 30 and directions["down"] >= 30,
        "che_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "che_2021_2024_at_least_12_each": all(years[year] >= 12 for year in range(2021, 2025)),
        "che_2025_h1_at_least_6": years[2025] >= 6,
        "band_counts": bands,
        "each_band_che_chl_at_least_20": all(
            value["CHE"] >= 20 and value["CHL"] >= 20 for value in bands.values()
        ),
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "che_chl_at_least_90_each",
            "che_up_down_at_least_30_each",
            "che_2021_2024_at_least_12_each",
            "che_2025_h1_at_least_6",
            "each_band_che_chl_at_least_20",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(
            sorted(Counter(str(row.get("reason", row["status"])) for row in rows).items())
        ),
        "condition_counts": {"CHE": len(che), "CHL": len(chl)},
        "gate": gate,
    }


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    executable = [row for row in events if row.get("status") == "EXECUTABLE"]
    checks = {
        "scheduled_axis_complete_unique": [row["trade_date"] for row in events]
        == [day.isoformat() for day in axis]
        and len(events) == len({row["trade_date"] for row in events}),
        "strict_prior_current_excluded_m_reference": all(
            cast(list[str], row["reference_scheduled_trade_dates"])
            == [
                day.isoformat()
                for day in axis[
                    max(0, index - cast(int, row["reference_window_scheduled_trade_dates"])) : index
                ]
            ]
            and cast(str, row["trade_date"])
            not in cast(list[str], row["reference_valid_trade_dates"])
            for index, row in enumerate(events)
        ),
        "conditional_comparison_is_prior_nearest_m_then_newest_date": all(
            len(cast(list[str], row["comparison_trade_dates"])) == cast(int, row["comparison_size"])
            and cast(str, row["trade_date"]) not in cast(list[str], row["comparison_trade_dates"])
            for row in events
            if row.get("m_band") in {"B1", "B2"}
        ),
        "signal_after_0959_close_next_eligible_open_and_fixed_exit": all(
            str(row["selection_fixed_at_jst"]).startswith(str(row["trade_date"]) + "T09:59")
            and str(row["entry_open_jst"]) > str(row["entry_signal_jst"])
            and str(row["exit_open_jst"]).startswith(str(row["trade_date"]) + "T14:55")
            for row in executable
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def bootstrap(
    che_daily: list[int],
    fade_daily: list[int],
    chl_daily: list[int],
    che_band: list[list[int]],
    chl_band: list[list[int]],
    weights: list[float],
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if not (len(che_daily) == len(fade_daily) == len(chl_daily) == len(che_band) == len(chl_band)):
        raise ValueError("R088-Q002 bootstrap axes are misaligned")
    index = mbb_indices(len(che_daily))
    che, fade, chl = (
        np.asarray(value, dtype=float) for value in (che_daily, fade_daily, chl_daily)
    )
    cb, lb = np.asarray(che_band, dtype=float), np.asarray(chl_band, dtype=float)
    samples = np.full(MBB_REPETITIONS, np.nan)
    empty = 0
    for iteration, chosen in enumerate(index):
        values: list[float] = []
        for band, weight in enumerate(weights):
            cn, ln = cb[chosen, band].sum(), lb[chosen, band].sum()
            if weight and (cn == 0 or ln == 0):
                empty += 1
                break
            if weight:
                values.append(
                    weight
                    * (
                        che[chosen][cb[chosen, band] > 0].sum() / cn
                        - chl[chosen][lb[chosen, band] > 0].sum() / ln
                    )
                )
        else:
            samples[iteration] = sum(values)
    return {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile; common blocks",
        "seed": MBB_SEED,
        "repetitions": MBB_REPETITIONS,
        "block_length_trade_dates": 20,
        "che_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(che_daily),
            "ci95_percentile_linear": _ci(che[index].mean(axis=1)),
        },
        "che_minus_paired_fade_paired_daily_net_jpy": {
            "estimate": fmean([a - b for a, b in zip(che_daily, fade_daily, strict=True)]),
            "ci95_percentile_linear": _ci((che[index] - fade[index]).mean(axis=1)),
        },
        "che_minus_chl_band_standardized_net_jpy_per_trade": {
            "estimate": None,
            "ci95_percentile_linear": _ci(samples),
            "empty_cell_resamples": empty,
            "che_band_standard_weights": weights,
        },
    }, index


def _ci(values: NDArray[np.float64]) -> list[float] | None:
    if not len(values) or not np.isfinite(values).all():
        return None
    return [float(value) for value in np.quantile(values, (0.025, 0.975), method="linear")]
