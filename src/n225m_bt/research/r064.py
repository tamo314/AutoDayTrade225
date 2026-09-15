"""Causal TSE opening-drive partial-pullback construction for R064-Q001."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import date, timedelta
from math import ceil
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
LOOKBACK = 120
MIN_REFERENCES = 100
OBSERVATION_WINDOWS = (45, 60, 75)
RESPONSE = 15
COMMON_LAST_ORDINAL = 136
PINV_RCOND = 1e-12
RESIDUAL_SS_TOLERANCE = 1e-12


class R064QNotIdentifiableError(ValueError):
    """The preregistered interaction is not identified after FWL projection."""


def nearest_rank(values: list[float], percentile: int) -> float:
    if percentile not in {50, 70, 75, 80} or not values:
        raise ValueError("R064 invalid nearest-rank request")
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def fwl_delta(
    q: NDArray[np.float64], y: NDArray[np.float64], nuisance: NDArray[np.float64]
) -> tuple[float, float]:
    if q.ndim != 1 or y.ndim != 1 or nuisance.ndim != 2 or q.shape != y.shape:
        raise ValueError("R064 invalid FWL dimensions")
    if nuisance.shape[0] != q.size:
        raise ValueError("R064 FWL row count differs")
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=PINV_RCOND)
    q_residual = q - projection @ q
    y_residual = y - projection @ y
    ss = float(q_residual @ q_residual)
    if ss <= RESIDUAL_SS_TOLERANCE:
        raise R064QNotIdentifiableError(f"R064 Q residual SS={ss:.17g}")
    return float(q_residual @ y_residual / ss), ss


def _valid(bar: Bar | None, target: date) -> bool:
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and min(bar.open, bar.high, bar.low, bar.close) > 0
    )


def _path(
    classifier: CalendarClassifier, target: date, rows: list[Bar] | None, required: int
) -> list[Bar] | None:
    start = classifier.session_open(target, Session.DAY)
    by_time = {row.ts_jst: row for row in rows or []}
    selected = [by_time.get(start + timedelta(minutes=index)) for index in range(required)]
    if not all(_valid(row, target) for row in selected):
        return None
    return cast(list[Bar], selected)


def _u_observation(path: list[Bar] | None, target: date, window: int) -> dict[str, object]:
    record: dict[str, object] = {"window": window, "u_member": False}
    if path is None or len(path) < window:
        record["u_reason"] = "OBSERVATION_PATH_INVALID"
        return record
    rows = path[:window]
    open_ = rows[0].open
    close = rows[-1].close
    d = close - open_
    steps = abs(rows[0].close - open_) + sum(
        abs(rows[index].close - rows[index - 1].close) for index in range(1, window)
    )
    record.update(
        u_member=True,
        u_reason="U_VALID",
        m=abs(d) / open_,
        d=d,
        s=1 if d > 0 else -1 if d < 0 else 0,
        efficiency=abs(d) / steps if steps > 0 else 0.0,
    )
    return record


def rolling_u_ledger(candidates: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    """Freeze each window's rolling U before any response or execution inputs exist."""
    histories: dict[int, deque[dict[str, object]]] = defaultdict(lambda: deque(maxlen=LOOKBACK))
    output: list[dict[str, object]] = []
    previous: str | None = None
    for source in candidates:
        row = dict(source)
        target = str(row["trade_date"])
        if previous is not None and target <= previous:
            raise ValueError("R064 candidates must be chronological")
        values_by_window = cast(dict[str, dict[str, object]], row["u"])
        frozen: dict[str, dict[str, object]] = {}
        for window in OBSERVATION_WINDOWS:
            history = histories[window]
            references = list(history)
            if any(str(item["trade_date"]) >= target for item in references):
                raise ValueError("R064 rolling causality violation")
            valid = [float(cast(float, item["m"])) for item in references if item["u_member"]]
            item = dict(values_by_window[str(window)])
            item.update(
                rolling_reference_trade_dates=[str(entry["trade_date"]) for entry in references],
                rolling_scheduled_count=len(references),
                rolling_valid_count=len(valid),
                rolling_valid=len(valid) >= MIN_REFERENCES,
            )
            if len(valid) >= MIN_REFERENCES:
                item.update({f"q{q}": nearest_rank(valid, q) for q in (50, 70, 75, 80)})
            frozen[str(window)] = item
            history.append({"trade_date": target, "u_member": item["u_member"], "m": item.get("m")})
        row["u"] = frozen
        output.append(row)
        previous = target
    return output


def candidate_rows(
    classifier: CalendarClassifier,
    days: Iterable[date],
    bars_by_day: dict[date, list[Bar]],
    isolated: set[tuple[date, Session]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for target in days:
        # U uses only the observation window.  Response/execution availability is deliberately absent.
        path = (
            None
            if (target, Session.DAY) in isolated
            else _path(classifier, target, bars_by_day.get(target), max(OBSERVATION_WINDOWS))
        )
        result.append(
            {
                "trade_date": target.isoformat(),
                "u": {
                    str(window): _u_observation(path, target, window)
                    for window in OBSERVATION_WINDOWS
                },
            }
        )
    return rolling_u_ledger(result)


def r064_exec_event(
    classifier: CalendarClassifier,
    target: date,
    rows: list[Bar] | None,
    isolated: set[tuple[date, Session]],
    ledger: dict[str, object],
    *,
    observation_window: int = 60,
    extreme_quantile: int = 75,
    pullback_low: float = 0.20,
    pullback_high: float = 0.50,
) -> dict[str, object]:
    """Resolve R064 at the selected response close without common-E exits.

    This is additive to ``make_event``.  It uses the selected frozen U window
    and only the observation-plus-response prefix; entry, exit, and other
    sensitivity windows remain outside execution-time eligibility.
    """
    if (
        observation_window not in OBSERVATION_WINDOWS
        or extreme_quantile not in {70, 75, 80}
        or (pullback_low, pullback_high) not in {(0.20, 0.50), (0.15, 0.45), (0.25, 0.55)}
    ):
        raise ValueError("R064 unregistered sensitivity")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "selection_status": "not_selected",
        "execution_status": "not_scheduled",
        "observation_window": observation_window,
        "extreme_quantile": extreme_quantile,
        "pullback_band": [pullback_low, pullback_high],
        "common_e_contract": "legacy_r064_make_event",
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if (target, Session.DAY) in isolated:
        event["reason"] = "R004_DAY_QUARANTINED"
        return event
    all_u = cast(dict[str, dict[str, object]], ledger["u"])
    feature = all_u[str(observation_window)]
    if not bool(feature["rolling_valid"]):
        event["reason"] = "INSUFFICIENT_PRIOR_U_REFERENCES"
        return event
    path = _path(classifier, target, rows, observation_window + RESPONSE)
    if path is None:
        event["reason"] = "OBSERVATION_OR_RESPONSE_DECISION_PATH_INVALID"
        return event
    open_, close = path[0].open, path[observation_window - 1].close
    d = close - open_
    m = abs(d) / open_
    sign = 1 if d > 0 else -1 if d < 0 else 0
    response_rows = path[observation_window : observation_window + RESPONSE]
    response_close = response_rows[-1].close
    entry_ordinal = observation_window + RESPONSE + 1
    event.update(
        status="E_EXEC",
        reason="RESPONSE_CLASSIFIED",
        q50=float(cast(float, feature["q50"])),
        q70=float(cast(float, feature["q70"])),
        q75=float(cast(float, feature["q75"])),
        q80=float(cast(float, feature["q80"])),
        o=open_,
        c_observation=close,
        c_response=response_close,
        d=d,
        m=m,
        s=sign,
        efficiency=float(cast(float, feature["efficiency"])),
        direction_eligible=sign != 0,
        planned_signal_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal - 2)
        ).isoformat(),
        planned_entry_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal - 1)
        ).isoformat(),
        planned_exit_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal - 1 + 30)
        ).isoformat(),
    )
    if not sign:
        event.update(event_found=False, response="NONE", cell="NONE")
        return event
    pullback = -sign * (response_close - close) / abs(d)
    non_destructive = all(sign * (bar.close - open_) > 0 for bar in response_rows)
    response = (
        "R"
        if pullback_low <= pullback <= pullback_high and non_destructive
        else "F"
        if -0.10 <= pullback < 0.10
        else "N"
    )
    extreme = m >= float(cast(float, feature[f"q{extreme_quantile}"]))
    medium = float(cast(float, feature["q50"])) <= m < float(
        cast(float, feature[f"q{extreme_quantile}"])
    )
    cell = (
        "A"
        if extreme and response == "R"
        else "C"
        if medium and response == "R"
        else "D"
        if extreme and response == "F"
        else "M"
        if medium and response == "F"
        else "NONE"
    )
    selected = cell in {"A", "C", "D", "M"}
    event.update(
        event_found=m >= float(cast(float, feature["q50"])),
        selection_status=cell if selected else "not_selected",
        execution_status="scheduled" if selected else "not_scheduled",
        p=pullback,
        non_destructive=non_destructive,
        response=response,
        extreme=extreme,
        medium=medium,
        cell=cell,
    )
    return event


def make_event(
    classifier: CalendarClassifier,
    target: date,
    rows: list[Bar] | None,
    isolated: set[tuple[date, Session]],
    ledger: dict[str, object],
    *,
    observation_window: int = 60,
    extreme_quantile: int = 75,
    pullback_low: float = 0.20,
    pullback_high: float = 0.50,
) -> dict[str, object]:
    if (
        observation_window not in OBSERVATION_WINDOWS
        or extreme_quantile not in {70, 75, 80}
        or (pullback_low, pullback_high) not in {(0.20, 0.50), (0.15, 0.45), (0.25, 0.55)}
    ):
        raise ValueError("R064 unregistered sensitivity")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "observation_window": observation_window,
        "extreme_quantile": extreme_quantile,
        "pullback_band": [pullback_low, pullback_high],
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if (target, Session.DAY) in isolated:
        event["reason"] = "R004_DAY_QUARANTINED"
        return event
    path = _path(classifier, target, rows, COMMON_LAST_ORDINAL)
    if path is None:
        event["reason"] = "TSE_COMMON_PATH_INVALID"
        return event
    all_u = cast(dict[str, dict[str, object]], ledger["u"])
    if not all(bool(item["rolling_valid"]) for item in all_u.values()):
        event["reason"] = "INSUFFICIENT_PRIOR_U_REFERENCES"
        return event
    feature = all_u[str(observation_window)]
    open_ = path[0].open
    close = path[observation_window - 1].close
    d = close - open_
    m = abs(d) / open_
    sign = 1 if d > 0 else -1 if d < 0 else 0
    response_rows = path[observation_window : observation_window + RESPONSE]
    response_close = response_rows[-1].close
    entry_ordinal = observation_window + RESPONSE + 1
    event.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        rolling_ledger="rolling_u_ledger.json",
        q50=float(cast(float, feature["q50"])),
        q70=float(cast(float, feature["q70"])),
        q75=float(cast(float, feature["q75"])),
        q80=float(cast(float, feature["q80"])),
        o=open_,
        c_observation=close,
        c_response=response_close,
        d=d,
        m=m,
        s=sign,
        efficiency=float(cast(float, feature["efficiency"])),
        direction_eligible=sign != 0,
        planned_signal_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal - 2)
        ).isoformat(),
        planned_entry_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal - 1)
        ).isoformat(),
        planned_delay_entry_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal)
        ).isoformat(),
        planned_exit15_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal - 1 + 15)
        ).isoformat(),
        planned_exit30_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal - 1 + 30)
        ).isoformat(),
        planned_exit45_jst=(
            classifier.session_open(target, Session.DAY) + timedelta(minutes=entry_ordinal - 1 + 45)
        ).isoformat(),
        entry_open=path[entry_ordinal - 1].open,
        exit15_open=path[entry_ordinal - 1 + 15].open,
        exit30_open=path[entry_ordinal - 1 + 30].open,
        exit45_open=path[entry_ordinal - 1 + 45].open,
        first15_s_adjusted_return_bps=(
            sign * (path[14].close - open_) / open_ * 10_000 if sign else 0.0
        ),
        response_range_over_abs_d=(
            (max(bar.high for bar in response_rows) - min(bar.low for bar in response_rows))
            / abs(d)
            if d
            else 0.0
        ),
        upward=int(sign > 0),
    )
    if not sign:
        event.update(response="NONE", cell="NONE", event_found=False)
        return event
    pullback = -sign * (response_close - close) / abs(d)
    non_destructive = all(sign * (bar.close - open_) > 0 for bar in response_rows)
    response = (
        "R"
        if pullback_low <= pullback <= pullback_high and non_destructive
        else "F"
        if -0.10 <= pullback < 0.10
        else "N"
    )
    extreme = m >= float(cast(float, feature[f"q{extreme_quantile}"]))
    medium = (
        float(cast(float, feature["q50"]))
        <= m
        < float(cast(float, feature[f"q{extreme_quantile}"]))
    )
    cell = (
        "A"
        if extreme and response == "R"
        else "C"
        if medium and response == "R"
        else "D"
        if extreme and response == "F"
        else "M"
        if medium and response == "F"
        else "NONE"
    )
    event.update(
        event_found=m >= float(cast(float, feature["q50"])),
        p=pullback,
        non_destructive=non_destructive,
        response=response,
        extreme=extreme,
        medium=medium,
        cell=cell,
    )
    return event


def make_events(
    classifier: CalendarClassifier,
    days: Iterable[date],
    bars_by_day: dict[date, list[Bar]],
    isolated: set[tuple[date, Session]],
    ledger: Iterable[dict[str, object]],
    **kwargs: Any,
) -> list[dict[str, object]]:
    by_day = {date.fromisoformat(cast(str, row["trade_date"])): row for row in ledger}
    return [
        make_event(classifier, day, bars_by_day.get(day), isolated, by_day[day], **kwargs)
        for day in days
    ]
