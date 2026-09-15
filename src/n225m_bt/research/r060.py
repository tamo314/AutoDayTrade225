"""Causal prior-TSE-range, opening failed-auction events for R060-Q001."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r022 import normal_session_end
from n225m_bt.research.r025 import previous_tse_open_date


@dataclass(frozen=True)
class R060Specification:
    """The preregistered observation, boundary, and holding parameters."""

    observation_minutes: int = 30
    breach_ticks: int = 1
    reentry_ticks: int = 1
    holding_minutes: int = 30


BASE = R060Specification()


def _valid(row: Bar | None, target: date) -> bool:
    return bool(
        row is not None
        and row.is_eligible
        and row.trade_date == target
        and row.session is Session.DAY
        and min(row.open, row.high, row.low, row.close) > 0
    )


def _prior_normal_rows(
    classifier: CalendarClassifier, target: date, bars: list[Bar] | None
) -> list[Bar] | None:
    if bars is None:
        return None
    start = classifier.session_open(target, Session.DAY)
    end = normal_session_end(classifier, target, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in bars}
    rows = [
        by_time.get(start + timedelta(minutes=index))
        for index in range(int((end - start).total_seconds() // 60))
    ]
    if not all(_valid(row, target) for row in rows):
        return None
    return [row for row in rows if row is not None]


def r060_exec_event(
    classifier: CalendarClassifier,
    cash_calendar: TSECashMarketCalendar,
    target: date,
    target_bars: list[Bar] | None,
    prior_bars: list[Bar] | None,
    *,
    specification: R060Specification = BASE,
    tick_size: int = 5,
    target_quarantined: bool = False,
    prior_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Resolve R060 eligibility at the observation close without common-E exits.

    The legacy :func:`r060_event` retains its common-E reporting semantics.
    This additive adapter intentionally does not require unselected observation
    windows or any entry/exit bar to decide an execution-time candidate.
    """
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "session": "day",
        "status": "skipped",
        "selection_status": "not_selected",
        "execution_status": "not_scheduled",
        "observation_minutes": specification.observation_minutes,
        "breach_ticks": specification.breach_ticks,
        "reentry_ticks": specification.reentry_ticks,
        "holding_minutes": specification.holding_minutes,
        "common_e_contract": "legacy_r060_event",
    }
    if not development_start <= target <= development_end:
        result["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return result
    if tick_size <= 0 or specification.observation_minutes not in {20, 30, 40}:
        result["reason"] = "INVALID_PREREGISTERED_SPECIFICATION"
        return result
    if not cash_calendar.is_open(target):
        result["reason"] = "TSE_CLOSED"
        return result
    prior = previous_tse_open_date(cash_calendar, target)
    if prior is None or prior < development_start:
        result["reason"] = "PRIOR_TSE_SESSION_AMBIGUOUS_OR_OUTSIDE_DEVELOPMENT"
        return result
    result["prior_tse_trade_date"] = prior.isoformat()
    if target_quarantined or prior_quarantined:
        result["reason"] = "R004_QUARANTINED"
        return result
    reference = _prior_normal_rows(classifier, prior, prior_bars)
    if reference is None:
        result["reason"] = "PRIOR_TSE_NORMAL_SESSION_INELIGIBLE"
        return result
    high, low, prior_close = (
        max(row.high for row in reference),
        min(row.low for row in reference),
        reference[-1].close,
    )
    if high <= low:
        result["reason"] = "PRIOR_RANGE_NONPOSITIVE"
        return result
    start = classifier.session_open(target, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in target_bars or []}
    observed_optional = [
        by_time.get(start + timedelta(minutes=index))
        for index in range(specification.observation_minutes)
    ]
    if not all(_valid(row, target) for row in observed_optional):
        result["reason"] = "OBSERVATION_DECISION_PATH_MISSING_OR_INELIGIBLE"
        return result
    observed = [row for row in observed_optional if row is not None]
    opening, close = observed[0].open, observed[-1].close
    if not low <= opening <= high:
        result["reason"] = "OPEN_OUTSIDE_PRIOR_RANGE"
        return result
    upper_reached = max(row.high for row in observed) >= high + specification.breach_ticks * tick_size
    lower_reached = min(row.low for row in observed) <= low - specification.breach_ticks * tick_size
    result.update(
        status="E_EXEC",
        selection_status="eligible",
        execution_status="not_scheduled",
        reason="NO_BOUNDARY_BREACH",
        H_points=high,
        L_points=low,
        p_prior_final_close_points=prior_close,
        opening_open_points=opening,
        observation_last_bar_start_jst=observed[-1].ts_jst.isoformat(),
        c_last_points=close,
        upper_breach=upper_reached,
        lower_breach=lower_reached,
    )
    if upper_reached == lower_reached:
        result["reason"] = "BOTH_BOUNDARIES_BREACHED" if upper_reached else "NO_BOUNDARY_BREACH"
        result["selection_status"] = "not_selected"
        return result
    s = 1 if upper_reached else -1
    returned = (
        close <= high - specification.reentry_ticks * tick_size
        if s == 1
        else close >= low + specification.reentry_ticks * tick_size
    )
    persistent = (
        close >= high + specification.reentry_ticks * tick_size
        if s == 1
        else close <= low - specification.reentry_ticks * tick_size
    )
    result.update(
        s=s,
        breakout_direction="upper" if s == 1 else "lower",
        entry_signal_bar_start_jst=observed[-1].ts_jst.isoformat(),
        E_planned_entry_jst=(observed[-1].ts_jst + timedelta(minutes=1)).isoformat(),
        X_planned_exit_jst=(
            observed[-1].ts_jst + timedelta(minutes=specification.holding_minutes + 1)
        ).isoformat(),
    )
    if returned:
        result.update(selection_status="A", execution_status="scheduled", reason="ONE_WAY_BREACH_RETURNED_INSIDE")
    elif persistent:
        result.update(selection_status="D", execution_status="scheduled", reason="ONE_WAY_BREACH_REMAINS_OUTSIDE")
    else:
        result.update(selection_status="not_selected", reason="NEUTRAL_BAND_AT_OBSERVATION_CLOSE")
    return result


def r060_event(
    classifier: CalendarClassifier,
    cash_calendar: TSECashMarketCalendar,
    target: date,
    target_bars: list[Bar] | None,
    prior_bars: list[Bar] | None,
    *,
    specification: R060Specification = BASE,
    tick_size: int = 5,
    target_quarantined: bool = False,
    prior_quarantined: bool = False,
    development_start: date = date(2021, 1, 1),
    development_end: date = date(2025, 6, 30),
) -> dict[str, object]:
    """Build one common-E event using only data available through its signal close."""
    result: dict[str, object] = {
        "trade_date": target.isoformat(),
        "session": "day",
        "status": "skipped",
        "observation_minutes": specification.observation_minutes,
        "breach_ticks": specification.breach_ticks,
        "reentry_ticks": specification.reentry_ticks,
        "holding_minutes": specification.holding_minutes,
    }
    if not development_start <= target <= development_end:
        result["reason"] = "TARGET_OUTSIDE_DEVELOPMENT"
        return result
    if tick_size <= 0 or specification.observation_minutes not in {20, 30, 40}:
        result["reason"] = "INVALID_PREREGISTERED_SPECIFICATION"
        return result
    if not cash_calendar.is_open(target):
        result["reason"] = "TSE_CLOSED"
        return result
    prior = previous_tse_open_date(cash_calendar, target)
    if prior is None or prior < development_start:
        result["reason"] = "PRIOR_TSE_SESSION_AMBIGUOUS_OR_OUTSIDE_DEVELOPMENT"
        return result
    result["prior_tse_trade_date"] = prior.isoformat()
    if target_quarantined or prior_quarantined:
        result["reason"] = "R004_QUARANTINED"
        return result
    reference = _prior_normal_rows(classifier, prior, prior_bars)
    if reference is None:
        result["reason"] = "PRIOR_TSE_NORMAL_SESSION_INELIGIBLE"
        return result
    high, low, p = (
        max(x.high for x in reference),
        min(x.low for x in reference),
        reference[-1].close,
    )
    result.update(
        H_points=high,
        L_points=low,
        p_prior_final_close_points=p,
        prior_normal_bar_count=len(reference),
    )
    if high <= low:
        result["reason"] = "PRIOR_RANGE_NONPOSITIVE"
        return result
    if target_bars is None:
        result["reason"] = "TARGET_MISSING"
        return result
    start = classifier.session_open(target, Session.DAY)
    by_time = {bar.ts_jst: bar for bar in target_bars}
    # E is intentionally common to 20/30/40m specifications and 15/30/45m exits.
    common_end = 40 + 1 + 45
    common = [by_time.get(start + timedelta(minutes=index)) for index in range(common_end)]
    if not all(_valid(row, target) for row in common):
        result["reason"] = "COMMON_E_PATH_MISSING_OR_INELIGIBLE_OR_SEGMENT_CROSS"
        return result
    rows = [row for row in common if row is not None]
    opening = rows[0].open
    result.update(
        S_scheduled_open_jst=start.isoformat(),
        opening_open_points=opening,
        common_path_last_bar_start_jst=rows[-1].ts_jst.isoformat(),
    )
    if not low <= opening <= high:
        result["reason"] = "OPEN_OUTSIDE_PRIOR_RANGE"
        return result
    observed = rows[: specification.observation_minutes]
    upper_reached = (
        max(row.high for row in observed) >= high + specification.breach_ticks * tick_size
    )
    lower_reached = min(row.low for row in observed) <= low - specification.breach_ticks * tick_size
    c29 = observed[-1].close
    result.update(
        observation_last_bar_start_jst=observed[-1].ts_jst.isoformat(),
        c_last_points=c29,
        upper_breach=upper_reached,
        lower_breach=lower_reached,
        first_observation_range_bps=(
            max(row.high for row in observed) - min(row.low for row in observed)
        )
        / opening
        * 10_000,
    )
    if upper_reached == lower_reached:
        result["reason"] = "BOTH_BOUNDARIES_BREACHED" if upper_reached else "NO_BOUNDARY_BREACH"
        return result
    s = 1 if upper_reached else -1
    depth = (
        (max(row.high for row in observed) - high) // tick_size
        if s == 1
        else (low - min(row.low for row in observed)) // tick_size
    )
    result.update(
        s=s,
        breakout_direction="upper" if s == 1 else "lower",
        breach_depth_ticks=depth,
        entry_signal_bar_start_jst=observed[-1].ts_jst.isoformat(),
        E_planned_entry_jst=(observed[-1].ts_jst + timedelta(minutes=1)).isoformat(),
        X15_planned_exit_jst=(observed[-1].ts_jst + timedelta(minutes=16)).isoformat(),
        X30_planned_exit_jst=(observed[-1].ts_jst + timedelta(minutes=31)).isoformat(),
        X45_planned_exit_jst=(observed[-1].ts_jst + timedelta(minutes=46)).isoformat(),
        first30_s_adjusted_return_bps=s * (c29 - opening) / opening * 10_000,
        s_adjusted_gap_bps=s * (opening - p) / p * 10_000,
        prior_tse_s_adjusted_return_bps=s * (p - reference[0].open) / reference[0].open * 10_000,
        prior_range_bps=(high - low) / p * 10_000,
    )
    returned = (
        c29 <= high - specification.reentry_ticks * tick_size
        if s == 1
        else c29 >= low + specification.reentry_ticks * tick_size
    )
    persistent = (
        c29 >= high + specification.reentry_ticks * tick_size
        if s == 1
        else c29 <= low - specification.reentry_ticks * tick_size
    )
    if returned:
        result.update(status="A", reason="ONE_WAY_BREACH_RETURNED_INSIDE", q_returned=1)
    elif persistent:
        result.update(status="D", reason="ONE_WAY_BREACH_REMAINS_OUTSIDE", q_returned=0)
    else:
        result.update(reason="NEUTRAL_BAND_AT_OBSERVATION_CLOSE")
    return result
