"""Causal TSE intraday liquidity-shock rejection construction for R063-Q001."""

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
START_ORDINAL = 31
END_ORDINAL = 150
COMMON_LAST_ORDINAL = 191
PINV_RCOND = 1e-12
RESIDUAL_SS_TOLERANCE = 1e-12


class R063QNotIdentifiableError(ValueError):
    """The precommitted interaction cannot be identified after FWL projection."""


def nearest_rank(values: list[float], percentile: int) -> float:
    """Nearest rank with equality explicitly retained in the upper tail."""
    if percentile not in {70, 85, 90, 95} or not values:
        raise ValueError("R063 invalid nearest-rank request")
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def fwl_delta(
    q: NDArray[np.float64], y: NDArray[np.float64], nuisance: NDArray[np.float64]
) -> tuple[float, float]:
    """Fixed nuisance-only Moore--Penrose FWL, including its identification gate."""
    if q.ndim != 1 or y.ndim != 1 or nuisance.ndim != 2 or q.shape != y.shape:
        raise ValueError("R063 invalid FWL dimensions")
    if nuisance.shape[0] != q.size:
        raise ValueError("R063 FWL row count differs")
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=PINV_RCOND)
    q_residual = q - projection @ q
    y_residual = y - projection @ y
    ss = float(q_residual @ q_residual)
    if ss <= RESIDUAL_SS_TOLERANCE:
        raise R063QNotIdentifiableError(f"R063 Q residual SS={ss:.17g}")
    return float(q_residual @ y_residual / ss), ss


def _valid(bar: Bar | None, target: date) -> bool:
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and min(bar.open, bar.high, bar.low, bar.close) > 0
    )


def _day_path(
    classifier: CalendarClassifier,
    target: date,
    rows: list[Bar] | None,
    required: int = COMMON_LAST_ORDINAL,
) -> list[Bar] | None:
    start = classifier.session_open(target, Session.DAY)
    by_time = {row.ts_jst: row for row in rows or []}
    selected = [by_time.get(start + timedelta(minutes=index)) for index in range(required)]
    if not all(_valid(row, target) for row in selected):
        return None
    return cast(list[Bar], selected)


def _block_observation(
    path: list[Bar] | None, target: date, length: int, position: int
) -> dict[str, object]:
    start = START_ORDINAL - 1 + position * length
    result: dict[str, object] = {"position": position, "length": length, "u_member": False}
    if path is None or start + length > END_ORDINAL:
        result["u_reason"] = "BLOCK_PATH_INVALID"
        return result
    rows = path[start : start + length]
    if len(rows) != length or not all(_valid(row, target) for row in rows):
        result["u_reason"] = "BLOCK_PATH_INVALID"
        return result
    move = abs(rows[-1].close - rows[0].open)
    signed = rows[-1].close - rows[0].open
    result.update(
        u_member=True,
        u_reason="U_VALID",
        first_ordinal=start + 1,
        last_ordinal=start + length,
        block_open=rows[0].open,
        block_close=rows[-1].close,
        m=move,
        s=1 if signed > 0 else -1 if signed < 0 else 0,
    )
    return result


def rolling_u_ledger(
    candidates: Iterable[dict[str, object]], lengths: tuple[int, ...] = (3, 5, 10)
) -> list[dict[str, object]]:
    """Freeze position-specific U before target response/entry/exit information exists."""
    histories: dict[tuple[int, int], deque[dict[str, object]]] = defaultdict(
        lambda: deque(maxlen=LOOKBACK)
    )
    output: list[dict[str, object]] = []
    previous: str | None = None
    for source in candidates:
        row = dict(source)
        target = str(row["trade_date"])
        if previous is not None and target <= previous:
            raise ValueError("R063 candidates must be chronological")
        u = cast(dict[str, list[dict[str, object]]], row["u"])
        frozen: dict[str, list[dict[str, object]]] = {}
        for length in lengths:
            key = str(length)
            frozen[key] = []
            for current in u[key]:
                position = int(cast(int, current["position"]))
                history = histories[(length, position)]
                references = list(history)
                if any(str(item["trade_date"]) >= target for item in references):
                    raise ValueError("R063 rolling causality violation")
                valid = [float(cast(float, item["m"])) for item in references if item["u_member"]]
                item = dict(current)
                item.update(
                    rolling_reference_trade_dates=[
                        str(entry["trade_date"]) for entry in references
                    ],
                    rolling_scheduled_count=len(references),
                    rolling_valid_count=len(valid),
                    rolling_valid=len(valid) >= MIN_REFERENCES,
                )
                if len(valid) >= MIN_REFERENCES:
                    item.update({f"q{q}": nearest_rank(valid, q) for q in (70, 85, 90, 95)})
                frozen[key].append(item)
                history.append(
                    {"trade_date": target, "u_member": item["u_member"], "m": item.get("m")}
                )
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
    """Create U candidates from only each block's past/current own price path."""
    result: list[dict[str, object]] = []
    for target in days:
        # U deliberately needs only its own block endpoint; response/execution paths are excluded.
        path = (
            None
            if (target, Session.DAY) in isolated
            else _day_path(classifier, target, bars_by_day.get(target), END_ORDINAL)
        )
        u = {
            str(length): [
                _block_observation(path, target, length, pos) for pos in range(120 // length)
            ]
            for length in (3, 5, 10)
        }
        result.append(
            {"trade_date": target.isoformat(), "day_path_valid": path is not None, "u": u}
        )
    return rolling_u_ledger(result)


def make_event(
    classifier: CalendarClassifier,
    target: date,
    rows: list[Bar] | None,
    isolated: set[tuple[date, Session]],
    ledger: dict[str, object],
    *,
    length: int = 5,
    extreme_quantile: int = 90,
    rejection_fraction: float = 0.25,
) -> dict[str, object]:
    """Causally reconstruct a first-q70 event for one preregistered variant."""
    if (
        length not in {3, 5, 10}
        or extreme_quantile not in {85, 90, 95}
        or rejection_fraction not in {0.2, 0.25, 1 / 3}
    ):
        raise ValueError("R063 unregistered sensitivity")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "block_length": length,
        "extreme_quantile": extreme_quantile,
        "rejection_fraction": rejection_fraction,
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    if (target, Session.DAY) in isolated:
        event["reason"] = "R004_DAY_QUARANTINED"
        return event
    path = _day_path(classifier, target, rows)
    if path is None:
        event["reason"] = "TSE_1_TO_191_PATH_INVALID"
        return event
    all_u = cast(dict[str, list[dict[str, object]]], ledger["u"])
    # Common E includes all main and preregistered sensitivity threshold constructions.
    if not all(bool(item["rolling_valid"]) for values in all_u.values() for item in values):
        event["reason"] = "INSUFFICIENT_PRIOR_POSITION_U"
        return event
    position_rows = all_u[str(length)]
    first: dict[str, object] | None = None
    for item in position_rows:
        if int(cast(int, item["s"])) and float(cast(float, item["m"])) >= float(
            cast(float, item["q70"])
        ):
            first = item
            break
    event.update(status="E", reason="COMMON_ELIGIBLE", rolling_ledger="rolling_u_ledger.json")
    if first is None:
        event.update(event_found=False, cell="NONE")
        return event
    first_ordinal = int(cast(int, first["first_ordinal"]))
    last_ordinal = int(cast(int, first["last_ordinal"]))
    response_last = last_ordinal + length
    # E's 191-bar requirement makes every preregistered response/entry/exit a same-segment path.
    shock_close = path[last_ordinal - 1].close
    response_close = path[response_last - 1].close
    sign = int(cast(int, first["s"]))
    move = float(cast(float, first["m"]))
    z = sign * (response_close - shock_close)
    response = (
        "R" if z <= -rejection_fraction * move else "F" if z >= rejection_fraction * move else "N"
    )
    extreme = move >= float(cast(float, first[f"q{extreme_quantile}"]))
    medium = (
        float(cast(float, first["q70"])) <= move < float(cast(float, first[f"q{extreme_quantile}"]))
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
    start = classifier.session_open(target, Session.DAY)
    entry_ordinal = response_last + 1
    event.update(
        event_found=True,
        first_q70_position=int(cast(int, first["position"])),
        first_ordinal=first_ordinal,
        last_ordinal=last_ordinal,
        response_last_ordinal=response_last,
        m=move,
        s=sign,
        q70=float(cast(float, first["q70"])),
        q85=float(cast(float, first["q85"])),
        q90=float(cast(float, first["q90"])),
        q95=float(cast(float, first["q95"])),
        z=z,
        response=response,
        extreme=extreme,
        medium=medium,
        cell=cell,
        shock_open=first["block_open"],
        shock_close=shock_close,
        response_close=response_close,
        planned_signal_jst=(start + timedelta(minutes=response_last - 1)).isoformat(),
        planned_entry_jst=(start + timedelta(minutes=entry_ordinal - 1)).isoformat(),
        planned_delay_entry_jst=(start + timedelta(minutes=entry_ordinal)).isoformat(),
        planned_exit10_jst=(start + timedelta(minutes=entry_ordinal - 1 + 10)).isoformat(),
        planned_exit20_jst=(start + timedelta(minutes=entry_ordinal - 1 + 20)).isoformat(),
        planned_exit30_jst=(start + timedelta(minutes=entry_ordinal - 1 + 30)).isoformat(),
        entry_open=path[entry_ordinal - 1].open,
        exit10_open=path[entry_ordinal - 1 + 10].open,
        exit20_open=path[entry_ordinal - 1 + 20].open,
        exit30_open=path[entry_ordinal - 1 + 30].open,
        prior20_s_adjusted_bps=(
            sign
            * (shock_close - path[last_ordinal - 21].close)
            / path[last_ordinal - 21].close
            * 10_000
            if last_ordinal > 20
            else 0.0
        ),
        opening30_range_bps=(max(bar.high for bar in path[:30]) - min(bar.low for bar in path[:30]))
        / path[0].open
        * 10_000,
        tse_gap_s_adjusted_bps=0.0,
        upward_shock=int(sign > 0),
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
