"""Causal US cash-open event construction for TASK-R082-Q001."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean
from typing import cast
from zoneinfo import ZoneInfo

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session

NY = ZoneInfo("America/New_York")
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915
REGISTERED_SIGNAL_MINUTES = {15, 30, 45}
REGISTERED_EXIT_MINUTES = {120, 180, 240}


def nyse_open_jst(nyse_date: date) -> datetime:
    """Convert a real 09:30 America/New_York open; never approximate DST in JST."""
    return datetime.combine(nyse_date, time(9, 30), NY).astimezone(JST)


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    value = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.NIGHT
        and value > 0
    )


def _execution_event(
    base: dict[str, object],
    bars: dict[tuple[date, Session], list[Bar]],
    *,
    signal_minutes: int = 30,
    entry_delay_minutes: int = 0,
    exit_minutes: int = 180,
) -> dict[str, object]:
    if (
        signal_minutes not in REGISTERED_SIGNAL_MINUTES
        or entry_delay_minutes not in {0, 1}
        or exit_minutes not in REGISTERED_EXIT_MINUTES
    ):
        raise ValueError("unregistered R082 execution profile")
    event = dict(base)
    event.update(
        status="SKIPPED",
        signal_minutes=signal_minutes,
        entry_delay_minutes=entry_delay_minutes,
        exit_minutes=exit_minutes,
    )
    if not bool(event.get("u_valid")):
        return event
    target = date.fromisoformat(cast(str, event["trade_date"]))
    s = datetime.fromisoformat(cast(str, event["s_jst"]))
    endpoint = s + timedelta(minutes=signal_minutes - 1)
    selection = endpoint + timedelta(minutes=entry_delay_minutes)
    entry, exit_open, exit_signal = (
        selection + timedelta(minutes=1),
        s + timedelta(minutes=exit_minutes),
        s + timedelta(minutes=exit_minutes - 1),
    )
    lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.NIGHT), [])}
    event.update(
        signal_endpoint_jst=endpoint.isoformat(),
        selection_fixed_at_jst=endpoint.isoformat(),
        entry_signal_jst=selection.isoformat(),
        entry_open_jst=entry.isoformat(),
        exit_signal_jst=exit_signal.isoformat(),
        exit_open_jst=exit_open.isoformat(),
    )
    if not _valid(lookup.get(selection), target, close=True) or not _valid(
        lookup.get(entry), target
    ):
        event.update(
            status="ENTRY_CANCELLED", reason="ENTRY_SIGNAL_OR_NEXT_OPEN_MISSING_OR_INELIGIBLE"
        )
        return event
    if not _valid(lookup.get(exit_signal), target, close=True) or not _valid(
        lookup.get(exit_open), target
    ):
        event.update(status="ENTRY_FILLED_EXIT_UNKNOWN", reason="FIXED_EXIT_MISSING_OR_INELIGIBLE")
        return event
    movement = cast(int, event["u_points"])
    event.update(
        status="EXECUTABLE",
        reason="NONZERO_U_EXECUTABLE_NO_MAGNITUDE_SELECTION",
        continuation_direction="long" if movement > 0 else "short",
        fade_direction="short" if movement > 0 else "long",
    )
    return event


def build_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    nyse_trading_days: set[date],
    *,
    signal_minutes: int = 30,
) -> list[dict[str, object]]:
    """Build the fixed US-open signal and record, but never select on, pre-open P."""
    if signal_minutes not in REGISTERED_SIGNAL_MINUTES:
        raise ValueError("unregistered R082 signal length")
    result: list[dict[str, object]] = []
    for target in axis:
        event: dict[str, object] = {
            "trade_date": target.isoformat(),
            "u_valid": False,
            "p_valid": False,
            "signal_minutes": signal_minutes,
        }
        try:
            night_open, night_close = (
                classifier.session_open(target, Session.NIGHT),
                classifier.session_close(target, Session.NIGHT),
            )
        except ValueError:
            event.update(reason="N225_NIGHT_SESSION_UNAVAILABLE")
            result.append(event)
            continue
        nyse_date = night_open.astimezone(NY).date()
        s = nyse_open_jst(nyse_date)
        event.update(
            nyse_trade_date=nyse_date.isoformat(),
            s_et=datetime.combine(nyse_date, time(9, 30), NY).isoformat(),
            s_jst=s.isoformat(),
            n225_night_open_jst=night_open.isoformat(),
            n225_night_close_jst=night_close.isoformat(),
            us_dst=bool(s.astimezone(NY).dst()),
        )
        if nyse_date not in nyse_trading_days:
            event.update(reason="NYSE_NON_TRADING_DAY")
            result.append(event)
            continue
        if not night_open <= s <= night_close:
            event.update(reason="NYSE_OPEN_OUTSIDE_N225_NIGHT_SESSION")
            result.append(event)
            continue
        event["n225_night_contains_s"] = True
        if (target, Session.NIGHT) in isolated:
            event.update(
                reason="R004_NIGHT_SESSION_QUARANTINED", p_reason="R004_NIGHT_SESSION_QUARANTINED"
            )
            result.append(event)
            continue
        lookup = {bar.ts_jst: bar for bar in bars.get((target, Session.NIGHT), [])}
        a, b = lookup.get(s), lookup.get(s + timedelta(minutes=signal_minutes - 1))
        p_a, p_b = lookup.get(s - timedelta(minutes=30)), lookup.get(s - timedelta(minutes=1))
        if not _valid(p_a, target):
            event["p_reason"] = "P_WINDOW_A_OPEN_MISSING_OR_INELIGIBLE"
        elif not _valid(p_b, target, close=True):
            event["p_reason"] = "P_WINDOW_B_CLOSE_MISSING_OR_INELIGIBLE"
        else:
            assert p_a is not None and p_b is not None
            p = p_b.close - p_a.open
            event.update(
                p_valid=p != 0,
                p_reason="ZERO_P" if p == 0 else "VALID_P",
                p_a_open_points=p_a.open,
                p_b_close_points=p_b.close,
                p_points=p,
            )
        if not _valid(a, target):
            event["reason"] = "U_WINDOW_A_OPEN_MISSING_OR_INELIGIBLE"
        elif not _valid(b, target, close=True):
            event["reason"] = "U_WINDOW_B_CLOSE_MISSING_OR_INELIGIBLE"
        else:
            assert a is not None and b is not None
            u = b.close - a.open
            event.update(
                u_valid=u != 0,
                reason="ZERO_U" if u == 0 else "VALID_U",
                u_a_open_points=a.open,
                u_b_close_points=b.close,
                u_points=u,
                abs_u_points=abs(u),
            )
        result.append(_execution_event(event, bars, signal_minutes=signal_minutes))
    return result


def pre_open_sign_events(events: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for source in events:
        event = dict(source)
        if event.get("status") == "EXECUTABLE" and bool(event.get("p_valid")):
            p = cast(int, event["p_points"])
            event.update(
                pre_open_direction="long" if p > 0 else "short",
                pre_open_sign_control_status="EXECUTABLE_COMMON_U_AND_P_NONZERO",
            )
        else:
            event.update(
                status="SKIPPED"
                if event.get("status") != "ENTRY_FILLED_EXIT_UNKNOWN"
                else event["status"],
                pre_open_sign_control_status="NOT_IN_COMMON_U_AND_P_NONZERO_SET",
            )
        result.append(event)
    return result


def assign_abs_u_quintiles(events: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    result = [dict(event) for event in events]
    eligible = sorted(
        (event for event in result if event.get("status") == "EXECUTABLE"),
        key=lambda item: (cast(int, item["abs_u_points"]), cast(str, item["trade_date"])),
    )
    for rank, event in enumerate(eligible, start=1):
        event["abs_u_quintile"] = min(5, ceil(rank * 5 / len(eligible)))
    return result


def feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    rows, executable = list(events), []
    executable = [event for event in rows if event.get("status") == "EXECUTABLE"]
    years = Counter(date.fromisoformat(cast(str, event["trade_date"])).year for event in executable)
    signs = Counter(
        "positive" if cast(int, event["u_points"]) > 0 else "negative" for event in executable
    )
    common = [event for event in executable if bool(event.get("p_valid"))]
    disagreement = sum(
        cast(int, event["u_points"]) * cast(int, event["p_points"]) < 0 for event in common
    )
    known = (
        "NYSE_",
        "N225_",
        "R004_",
        "U_WINDOW_",
        "ZERO_",
        "VALID_",
        "NONZERO_",
        "ENTRY_",
        "FIXED_",
    )
    unexplained = [
        cast(str, event["trade_date"])
        for event in rows
        if not str(event.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "executable_trades": len(executable),
        "minimum_executable_trades": 800,
        "executable_at_least_850": len(executable) >= 800,
        "u_sign_counts": dict(sorted(signs.items())),
        "minimum_positive_and_negative_each": 300,
        "u_sign_minima_passed": signs["positive"] >= 300 and signs["negative"] >= 300,
        "by_year": {str(year): years[year] for year in range(2021, 2026)},
        "minimum_2021_through_2024_each": 140,
        "annual_2021_2024_minima_passed": all(years[year] >= 140 for year in range(2021, 2025)),
        "minimum_2025_h1": 60,
        "year_2025_h1_minimum_passed": years[2025] >= 60,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "executable_at_least_850",
            "u_sign_minima_passed",
            "annual_2021_2024_minima_passed",
            "year_2025_h1_minimum_passed",
            "unexplained_exclusions_equal_zero",
        )
    )
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(
            sorted(
                Counter(
                    str(event.get("reason", event.get("status", "UNEXPLAINED"))) for event in rows
                ).items()
            )
        ),
        "pre_open_sign_control": {
            "definition": "main executable U!=0 and P=S-1 close-(S-30) open is eligible and nonzero",
            "common_u_and_p_nonzero_executable_count": len(common),
            "u_p_sign_disagreement_count": disagreement,
            "u_p_sign_agreement_count": len(common) - disagreement,
            "p_reason_counts": dict(
                sorted(
                    Counter(str(event.get("p_reason", "NOT_RECORDED")) for event in rows).items()
                )
            ),
        },
        "gate": gate,
    }


def mbb_indices(length: int, *, seed: int = MBB_SEED) -> NDArray[np.int64]:
    if length < MBB_BLOCK_LENGTH:
        raise ValueError("daily series is shorter than frozen R082 MBB block")
    rng, blocks = np.random.default_rng(seed), ceil(length / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, length - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    return (starts[:, :, None] + np.arange(MBB_BLOCK_LENGTH)).reshape(MBB_REPETITIONS, -1)[
        :, :length
    ]


def bootstrap(
    continuation_daily: list[int],
    fade_daily: list[int],
    common_continuation: list[int],
    pre_open_daily: list[int],
) -> tuple[dict[str, object], NDArray[np.int64], NDArray[np.int64]]:
    if len(continuation_daily) != len(fade_daily) or len(common_continuation) != len(
        pre_open_daily
    ):
        raise ValueError("R082 bootstrap axes are misaligned")
    primary, common = mbb_indices(len(continuation_daily)), mbb_indices(len(pre_open_daily))
    cont, fade, common_cont, pre = (
        np.asarray(values, dtype=float)
        for values in (continuation_daily, fade_daily, common_continuation, pre_open_daily)
    )

    def ci(samples: NDArray[np.float64]) -> list[float]:
        return [float(value) for value in np.quantile(samples, (0.025, 0.975), method="linear")]

    return (
        {
            "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile",
            "seed": MBB_SEED,
            "repetitions": MBB_REPETITIONS,
            "block_length_trade_dates": MBB_BLOCK_LENGTH,
            "continuation_scheduled_axis_mean_net_jpy_per_trade_date": {
                "estimate": fmean(continuation_daily),
                "ci95_percentile_linear": ci(cont[primary].mean(axis=1)),
            },
            "continuation_minus_fade_paired_daily_net_jpy": {
                "estimate": fmean(
                    [
                        left - right
                        for left, right in zip(continuation_daily, fade_daily, strict=True)
                    ]
                ),
                "ci95_percentile_linear": ci((cont[primary] - fade[primary]).mean(axis=1)),
            },
            "continuation_minus_pre_open_sign_control_paired_daily_net_jpy_on_common_u_p_axis": {
                "estimate": fmean(
                    [
                        left - right
                        for left, right in zip(common_continuation, pre_open_daily, strict=True)
                    ]
                ),
                "ci95_percentile_linear": ci((common_cont[common] - pre[common]).mean(axis=1)),
            },
        },
        primary,
        common,
    )
