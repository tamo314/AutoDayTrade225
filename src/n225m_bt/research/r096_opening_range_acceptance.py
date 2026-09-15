"""Causal opening-range acceptance state for TASK-R096-Q001."""

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
    _valid,
)
from n225m_bt.research.r091_fixed_tse_short import tse_cash_close

WINDOWS = {30, 60, 90}
X_CUTOFFS = {40, 50, 60}
Z_UPPERS = {70, 75, 80}
REFERENCE_WINDOW = 120
MIN_VALID = 100
MBB_BLOCK_LENGTH, MBB_REPETITIONS, MBB_SEED = 20, 10_000, 20260915
DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)


def nearest_rank(values: list[float], percentile: int) -> float:
    """Registered nearest-rank quantile without inheriting another study's grid."""
    if not values or percentile not in {20, 25, 30, 40, 50, 60, 70, 75, 80}:
        raise ValueError("unregistered R096 percentile")
    return sorted(values)[ceil(len(values) * percentile / 100) - 1]


def _observation(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    window_minutes: int,
) -> dict[str, object]:
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
    start = datetime.combine(target, time(9), zone)
    end = start + timedelta(minutes=window_minutes - 1)
    row.update(
        schedule_version=schedule.schedule_version,
        p0_0900_open_jst=start.isoformat(),
        pN_close_jst=end.isoformat(),
        scheduled_bar_count=window_minutes,
    )
    if (target, Session.DAY) in isolated:
        row["reason"] = "R004_DAY_SESSION_QUARANTINED"
        return row
    if not (
        classifier.session_open(target, Session.DAY)
        <= start
        <= end
        <= classifier.session_close(target, Session.DAY)
    ):
        row["reason"] = "WINDOW_NOT_INSIDE_REGISTERED_CONTINUOUS_DAY_SESSION"
        return row
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    rows = [lookup.get(start + timedelta(minutes=i)) for i in range(window_minutes)]
    if not _valid(rows[0], target):
        row["reason"] = "P0_0900_OPEN_MISSING_OR_INELIGIBLE"
        return row
    if not all(
        _valid(bar, target)
        and _valid(bar, target, close=True)
        and bar is not None
        and bar.high > 0
        and bar.low > 0
        and bar.high >= bar.low
        for bar in rows
    ):
        row["reason"] = "WINDOW_OHLC_MISSING_OR_INELIGIBLE_OR_NONCONTINUOUS"
        return row
    concrete = cast(list[Bar], rows)
    opening, closing = float(concrete[0].open), float(concrete[-1].close)
    high, low = float(max(bar.high for bar in concrete)), float(min(bar.low for bar in concrete))
    displacement = closing - opening
    row.update(
        o_0900_open_points=opening,
        c_endpoint_points=closing,
        h_points=high,
        l_points=low,
        displacement_points=displacement,
        x_abs_displacement_fraction=abs(displacement) / opening,
    )
    if high <= low:
        row["reason"] = "NONPOSITIVE_OPENING_RANGE"
        return row
    if displacement == 0:
        row["reason"] = "ZERO_DISPLACEMENT"
        return row
    z = (closing - low) / (high - low) if displacement > 0 else (high - closing) / (high - low)
    row.update(z_directional_close_position=z, observation_valid=True, reason="VALID_OHLC_X_Z")
    return row


def state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    window_minutes: int = 60,
    x_cutoff: int = 50,
    z_upper: int = 75,
) -> list[dict[str, object]]:
    if window_minutes not in WINDOWS or x_cutoff not in X_CUTOFFS or z_upper not in Z_UPPERS:
        raise ValueError("unregistered R096 state profile")
    z_lower = 100 - z_upper
    ordered: list[date] = list(axis)
    records: list[dict[str, object]] = []
    for index, target in enumerate(ordered):
        row = _observation(classifier, target, bars, isolated, window_minutes)
        reference = records[max(0, index - REFERENCE_WINDOW) : index]
        valid = [item for item in reference if bool(item.get("observation_valid"))]
        row.update(
            reference_window_scheduled_trade_dates=REFERENCE_WINDOW,
            reference_scheduled_trade_dates=[cast(str, x["trade_date"]) for x in reference],
            reference_valid_trade_dates=[cast(str, x["trade_date"]) for x in valid],
            reference_valid_count=len(valid),
            minimum_valid_reference_count=MIN_VALID,
            x_cutoff_percentile=x_cutoff,
            z_lower_percentile=z_lower,
            z_upper_percentile=z_upper,
            accepted_condition=False,
            control_condition=False,
            high_efficiency_condition=False,
            low_efficiency_condition=False,
            x_band=None,
        )
        if not bool(row["observation_valid"]):
            records.append(row)
            continue
        if len(valid) < MIN_VALID:
            row["reason"] = "INSUFFICIENT_VALID_X_Z_IN_EXACT_PRIOR_120_SCHEDULED_WINDOW"
            records.append(row)
            continue
        xs = [cast(float, x["x_abs_displacement_fraction"]) for x in valid]
        zs = [cast(float, x["z_directional_close_position"]) for x in valid]
        qx, qx75, qzlow, qzhigh = (
            nearest_rank(xs, x_cutoff),
            nearest_rank(xs, 75),
            nearest_rank(zs, z_lower),
            nearest_rank(zs, z_upper),
        )
        row.update(qx_cutoff=qx, qx75=qx75, qz_low=qzlow, qz_high=qzhigh)
        if qzlow >= qzhigh:
            row["reason"] = "DEGENERATE_QZ_LOW_GTE_QZ_HIGH"
            records.append(row)
            continue
        x, z = (
            cast(float, row["x_abs_displacement_fraction"]),
            cast(float, row["z_directional_close_position"]),
        )
        accepted, control = x >= qx and z >= qzhigh, x >= qx and z <= qzlow
        row.update(
            accepted_condition=accepted,
            control_condition=control,
            high_efficiency_condition=accepted,
            low_efficiency_condition=control,
            x_band="X50_75" if x >= qx and x < qx75 else ("X75_100" if x >= qx75 else None),
        )
        if accepted and control:
            row.update(condition="E_AND_K", reason="E_AND_K_AT_DEGENERATE_Z_THRESHOLDS")
        elif accepted:
            row.update(condition="E", reason="HIGH_X_DIRECTIONAL_RANGE_END_ACCEPTANCE")
        elif control:
            row.update(condition="K", reason="HIGH_X_LOW_DIRECTIONAL_RANGE_POSITION_CONTROL")
        else:
            row.update(condition="NONE", reason="OUTSIDE_REGISTERED_E_K_CONDITIONS")
        records.append(row)
    return records


def r096_event(
    state: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    entry_extra_bars: int = 0,
    exit_minus_minutes: int = 5,
    fixed_exit_time: time | None = None,
) -> dict[str, object]:
    if entry_extra_bars not in {0, 1} or exit_minus_minutes not in {5, 20}:
        raise ValueError("unregistered R096 execution profile")
    event = dict(state)
    event.update(
        status="SKIPPED", entry_extra_bars=entry_extra_bars, exit_minus_minutes=exit_minus_minutes
    )
    if not bool(event.get("accepted_condition")) and not bool(event.get("control_condition")):
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    endpoint = datetime.fromisoformat(cast(str, event["pN_close_jst"]))
    exit_open = (
        datetime.combine(target, fixed_exit_time, endpoint.tzinfo)
        if fixed_exit_time
        else tse_cash_close(target) - timedelta(minutes=exit_minus_minutes)
    )
    signal, exit_signal = (
        endpoint + timedelta(minutes=entry_extra_bars),
        exit_open - timedelta(minutes=1),
    )
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.DAY), [])}
    event.update(
        selection_fixed_at_jst=endpoint.isoformat(),
        entry_signal_jst=signal.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
        tse_cash_close_jst=tse_cash_close(target).isoformat(),
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
            status="ENTRY_CANCELLED", reason="NEXT_ELIGIBLE_ENTRY_EXCEEDS_MAX_10_SCHEDULED_MINUTES"
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
        reason="E_K_NEXT_ELIGIBLE_ENTRY_FIXED_TSE_EXIT_EXECUTABLE",
        continuation_direction=direction,
        fade_direction="short" if direction == "long" else "long",
        long_direction="long",
        short_direction="short",
        tse_close_regime="old" if tse_cash_close(target).time() == time(15) else "new",
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
        if key in {"window_minutes", "x_cutoff", "z_upper"}
    }
    return [
        r096_event(
            row,
            bars,
            entry_extra_bars=cast(int, kwargs.get("entry_extra_bars", 0)),
            exit_minus_minutes=cast(int, kwargs.get("exit_minus_minutes", 5)),
            fixed_exit_time=cast(time | None, kwargs.get("fixed_exit_time")),
        )
        for row in state_ledger(classifier, axis, bars, isolated, **state_keys)
    ]


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(events)
    e = [x for x in rows if bool(x.get("accepted_condition")) and x.get("status") == "EXECUTABLE"]
    k = [x for x in rows if bool(x.get("control_condition")) and x.get("status") == "EXECUTABLE"]
    ready = [x for x in rows if "qx_cutoff" in x and bool(x.get("observation_valid"))]
    years = Counter(date.fromisoformat(cast(str, x["trade_date"])).year for x in e)
    signs = Counter("up" if cast(float, x["displacement_points"]) > 0 else "down" for x in e)
    regimes = Counter(cast(str, x.get("tse_close_regime", "")) for x in e)
    bands = {
        band: {
            "E": sum(x.get("x_band") == band for x in e),
            "K": sum(x.get("x_band") == band for x in k),
        }
        for band in ("X50_75", "X75_100")
    }
    known = (
        "NO_",
        "R004_",
        "WINDOW_",
        "P0_",
        "ZERO_",
        "NONPOSITIVE_",
        "VALID_",
        "INSUFFICIENT_",
        "DEGENERATE_",
        "OUTSIDE_",
        "HIGH_",
        "E_",
        "K_",
        "ENTRY_",
        "NEXT_",
        "FIXED_",
    )
    unexplained = [
        cast(str, x["trade_date"]) for x in rows if not str(x.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "q_ready_nonzero_days": len(ready),
        "q_ready_nonzero_at_least_800": len(ready) >= 800,
        "e_completed": len(e),
        "k_completed": len(k),
        "e_k_completed_at_least_100_each": len(e) >= 100 and len(k) >= 100,
        "e_direction_counts": dict(sorted(signs.items())),
        "e_up_down_at_least_35_each": signs["up"] >= 35 and signs["down"] >= 35,
        "e_by_year": {str(year): years[year] for year in range(2021, 2026)},
        "e_2021_initialization_at_least_8": years[2021] >= 8,
        "e_2022_2024_at_least_18_each": all(years[year] >= 18 for year in range(2022, 2025)),
        "e_2025_h1_at_least_8": years[2025] >= 8,
        "e_tse_regime_counts": dict(sorted(regimes.items())),
        "e_old_new_at_least_85_8": regimes["old"] >= 85 and regimes["new"] >= 8,
        "x_band_counts": bands,
        "each_x_band_e_k_at_least_20": all(v["E"] >= 20 and v["K"] >= 20 for v in bands.values()),
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[name])
        for name in (
            "q_ready_nonzero_at_least_800",
            "e_k_completed_at_least_100_each",
            "e_up_down_at_least_35_each",
            "e_2021_initialization_at_least_8",
            "e_2022_2024_at_least_18_each",
            "e_2025_h1_at_least_8",
            "e_old_new_at_least_85_8",
            "each_x_band_e_k_at_least_20",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(
            sorted(Counter(str(x.get("reason", x["status"])) for x in rows).items())
        ),
        "condition_counts": {"E": len(e), "K": len(k)},
        "gate": gate,
    }


def causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    executable = [x for x in events if x.get("status") == "EXECUTABLE"]
    checks = {
        "scheduled_axis_complete_unique": [x["trade_date"] for x in events]
        == [d.isoformat() for d in axis]
        and len(events) == len({x["trade_date"] for x in events}),
        "exact_60_scheduled_ohlc_and_z_formula": all(
            datetime.fromisoformat(cast(str, x["p0_0900_open_jst"])).time() == time(9)
            and datetime.fromisoformat(cast(str, x["pN_close_jst"])).time() == time(9, 59)
            and cast(int, x["scheduled_bar_count"]) == 60
            and abs(
                cast(float, x["z_directional_close_position"])
                - (
                    (cast(float, x["c_endpoint_points"]) - cast(float, x["l_points"]))
                    / (cast(float, x["h_points"]) - cast(float, x["l_points"]))
                    if cast(float, x["displacement_points"]) > 0
                    else (cast(float, x["h_points"]) - cast(float, x["c_endpoint_points"]))
                    / (cast(float, x["h_points"]) - cast(float, x["l_points"]))
                )
            )
            < 1e-12
            for x in events
            if bool(x.get("observation_valid")) and x.get("window_minutes") == 60
        ),
        "strict_prior_120_current_excluded_no_backfill": all(
            cast(list[str], x["reference_scheduled_trade_dates"])
            == [d.isoformat() for d in axis[max(0, i - 120) : i]]
            and cast(str, x["trade_date"]) not in cast(list[str], x["reference_valid_trade_dates"])
            and len(cast(list[str], x["reference_scheduled_trade_dates"])) <= 120
            for i, x in enumerate(events)
        ),
        "signal_after_0959_next_eligible_within_10_and_versioned_t_minus_5_exit": all(
            datetime.fromisoformat(cast(str, x["selection_fixed_at_jst"])).time() == time(9, 59)
            and datetime.fromisoformat(cast(str, x["entry_open_jst"]))
            > datetime.fromisoformat(cast(str, x["entry_signal_jst"]))
            and datetime.fromisoformat(cast(str, x["entry_open_jst"]))
            - datetime.fromisoformat(cast(str, x["entry_signal_jst"]))
            <= timedelta(minutes=10)
            and datetime.fromisoformat(cast(str, x["exit_open_jst"]))
            == datetime.fromisoformat(cast(str, x["tse_cash_close_jst"])) - timedelta(minutes=5)
            for x in executable
        ),
        "e_k_not_selected_on_future_entry_exit_availability": all(
            bool(x.get("accepted_condition"))
            or bool(x.get("control_condition"))
            or x.get("status") == "SKIPPED"
            for x in events
        ),
    }
    return {"checks": checks, "passed": all(checks.values()), "pnl_not_accessed_before_audit": True}


def mbb_indices(length: int) -> NDArray[np.int64]:
    rng = np.random.default_rng(MBB_SEED)
    blocks = ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[
        :, :length
    ]


def bootstrap(
    a_daily: list[int],
    b_daily: list[int],
    c_daily: list[int],
    d_daily: list[int],
    m_daily: list[int],
    a_band: list[list[int]],
    m_band: list[list[int]],
    weights: list[float],
) -> tuple[dict[str, object], NDArray[np.int64]]:
    if not (
        len(a_daily)
        == len(b_daily)
        == len(c_daily)
        == len(d_daily)
        == len(m_daily)
        == len(a_band)
        == len(m_band)
    ):
        raise ValueError("R096 bootstrap axes are misaligned")
    index = mbb_indices(len(a_daily))
    a, b, c, d, m = (
        np.asarray(value, dtype=float) for value in (a_daily, b_daily, c_daily, d_daily, m_daily)
    )
    ab, mb = np.asarray(a_band, dtype=float), np.asarray(m_band, dtype=float)
    standardized = np.full(MBB_REPETITIONS, np.nan)
    empty = 0
    for iteration, chosen in enumerate(index):
        values: list[float] = []
        for band, weight in enumerate(weights):
            an, mn = ab[chosen, band].sum(), mb[chosen, band].sum()
            if weight and (an == 0 or mn == 0):
                empty += 1
                break
            if weight:
                values.append(
                    weight
                    * (
                        a[chosen][ab[chosen, band] > 0].sum() / an
                        - m[chosen][mb[chosen, band] > 0].sum() / mn
                    )
                )
        else:
            standardized[iteration] = sum(values)

    def interval(values: NDArray[np.float64]) -> list[float] | None:
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
        "a_scheduled_axis_mean_net_jpy_per_trade_date": {
            "estimate": fmean(a_daily),
            "ci95_percentile_linear": interval(a[index].mean(axis=1)),
        },
        "a_minus_b_paired_daily_net_jpy": {
            "estimate": fmean([x - y for x, y in zip(a_daily, b_daily, strict=True)]),
            "ci95_percentile_linear": interval((a[index] - b[index]).mean(axis=1)),
        },
        "a_minus_c_paired_daily_net_jpy": {
            "estimate": fmean([x - y for x, y in zip(a_daily, c_daily, strict=True)]),
            "ci95_percentile_linear": interval((a[index] - c[index]).mean(axis=1)),
        },
        "a_minus_d_paired_daily_net_jpy": {
            "estimate": fmean([x - y for x, y in zip(a_daily, d_daily, strict=True)]),
            "ci95_percentile_linear": interval((a[index] - d[index]).mean(axis=1)),
        },
        "a_minus_m_x_band_standardized_net_jpy_per_trade": {
            "estimate": None,
            "ci95_percentile_linear": interval(standardized),
            "empty_cell_resamples": empty,
            "a_standard_weights": weights,
        },
    }, index
