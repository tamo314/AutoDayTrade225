"""Causal TSE-day lower-tail to following-night reversal events for R066-Q001."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import date, timedelta
from math import ceil
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START, DEVELOPMENT_END = date(2021, 1, 1), date(2025, 6, 30)
LOOKBACK, MIN_REFERENCES = 120, 100
PINV_RCOND, RESIDUAL_SS_TOLERANCE = 1e-12, 1e-12


class R066QNotIdentifiableError(ValueError):
    """The preregistered lower-tail indicator is collinear with nuisance columns."""


def nearest_rank(values: list[float], percentile: int) -> float:
    if percentile not in {20, 25, 30, 50, 70, 75, 80} or not values:
        raise ValueError("invalid R066 nearest-rank request")
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def fwl_delta(
    q: NDArray[np.float64], y: NDArray[np.float64], nuisance: NDArray[np.float64]
) -> tuple[float, float]:
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=PINV_RCOND)
    qr, yr = q - projection @ q, y - projection @ y
    ss = float(qr @ qr)
    if ss <= RESIDUAL_SS_TOLERANCE:
        raise R066QNotIdentifiableError(f"R066 Q residual SS={ss:.17g}")
    return float(qr @ yr / ss), ss


def _valid(bar: Bar | None, target: date, session: Session, *, close: bool = False) -> bool:
    price = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is session
        and price > 0
    )


def rolling_u_ledger(candidates: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    history: deque[dict[str, object]] = deque(maxlen=LOOKBACK)
    output: list[dict[str, object]] = []
    previous: str | None = None
    for source in candidates:
        row, target = dict(source), str(source["trade_date"])
        if previous is not None and target <= previous:
            raise ValueError("R066 candidates must be chronological")
        refs, valid = list(history), [cast(float, x["r"]) for x in history if x["u_member"]]
        u = cast(dict[str, object], row["u"])
        u.update(
            rolling_reference_trade_dates=[str(x["trade_date"]) for x in refs],
            rolling_scheduled_count=len(refs),
            rolling_valid_count=len(valid),
            rolling_valid=len(valid) >= MIN_REFERENCES,
        )
        if len(valid) >= MIN_REFERENCES:
            u.update({f"q{x}": nearest_rank(valid, x) for x in (20, 25, 30, 50, 70, 75, 80)})
        history.append(
            {"trade_date": target, "u_member": bool(u["u_member"]), "r": u.get("r", 0.0)}
        )
        output.append(row)
        previous = target
    return output


def candidate_rows(
    classifier: CalendarClassifier,
    days: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for target in days:
        u: dict[str, object] = {"u_member": False}
        try:
            d0, dc = (
                classifier.session_open(target, Session.DAY),
                classifier.session_close(target, Session.DAY),
            )
            lookup = {x.ts_jst: x for x in bars.get((target, Session.DAY), [])}
            first, last = lookup.get(d0), lookup.get(dc)
            if (target, Session.DAY) in isolated:
                u["reason"] = "R004_DAY_QUARANTINED"
            elif _valid(first, target, Session.DAY) and _valid(
                last, target, Session.DAY, close=True
            ):
                assert first is not None and last is not None
                u.update(
                    u_member=True,
                    r=last.close / first.open - 1.0,
                    d0_open=first.open,
                    dc_close=last.close,
                )
            else:
                u["reason"] = "TSE_BOUNDARY_INVALID"
        except ValueError as exc:
            u["reason"] = str(exc)
        result.append({"trade_date": target.isoformat(), "u": u})
    return rolling_u_ledger(result)


def make_event(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    ledger: dict[str, object],
    *,
    lower_quantile: int = 25,
) -> dict[str, object]:
    if lower_quantile not in {20, 25, 30}:
        raise ValueError("unregistered R066 sensitivity")
    event: dict[str, object] = {
        "trade_date": target.isoformat(),
        "status": "skipped",
        "lower_quantile": lower_quantile,
    }
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    record = classifier.exchange_calendar.get(target)
    if record is None or record.next_trade_date is None:
        event["reason"] = "NO_UNIQUE_NEXT_TRADE_DATE"
        return event
    next_target, next_record = (
        record.next_trade_date,
        classifier.exchange_calendar.get(record.next_trade_date),
    )
    if next_record is None or next_record.previous_trade_date != target:
        event["reason"] = "NONRECIPROCAL_SCHEDULE_CHAIN"
        return event
    try:
        d0, dc = (
            classifier.session_open(target, Session.DAY),
            classifier.session_close(target, Session.DAY),
        )
        n0, nd0 = (
            classifier.session_open(next_target, Session.NIGHT),
            classifier.session_open(next_target, Session.DAY),
        )
    except ValueError as exc:
        event["reason"] = str(exc)
        return event
    event.update(
        d0_jst=d0.isoformat(),
        dc_jst=dc.isoformat(),
        n0_jst=n0.isoformat(),
        n0_delay1_jst=(n0 + timedelta(minutes=1)).isoformat(),
        n0_delay5_jst=(n0 + timedelta(minutes=5)).isoformat(),
        next_d0_jst=nd0.isoformat(),
        next_d0_delay1_jst=(nd0 + timedelta(minutes=1)).isoformat(),
        schedule_version=record.schedule_version,
        next_schedule_version=next_record.schedule_version,
    )
    if not dc < n0 < nd0:
        event["reason"] = "NONMONOTONIC_SCHEDULE_CHAIN"
        return event
    keys = ((target, Session.DAY), (next_target, Session.NIGHT), (next_target, Session.DAY))
    if any(key in isolated for key in keys):
        event["reason"] = "R004_SESSION_QUARANTINED"
        return event
    lookup = {key: {x.ts_jst: x for x in bars.get(key, [])} for key in keys}
    required = {
        "d0": (lookup[keys[0]].get(d0), target, Session.DAY, False),
        "dc": (lookup[keys[0]].get(dc), target, Session.DAY, True),
        "n0": (lookup[keys[1]].get(n0), next_target, Session.NIGHT, False),
        "n0_delay1": (
            lookup[keys[1]].get(n0 + timedelta(minutes=1)),
            next_target,
            Session.NIGHT,
            False,
        ),
        "n0_delay5": (
            lookup[keys[1]].get(n0 + timedelta(minutes=5)),
            next_target,
            Session.NIGHT,
            False,
        ),
        "next_d0": (lookup[keys[2]].get(nd0), next_target, Session.DAY, False),
        "next_d0_delay1": (
            lookup[keys[2]].get(nd0 + timedelta(minutes=1)),
            next_target,
            Session.DAY,
            False,
        ),
    }
    bad = [
        name
        for name, (bar, day, ses, close) in required.items()
        if not _valid(bar, day, ses, close=close)
    ]
    u = cast(dict[str, object], ledger["u"])
    if bad:
        event["reason"] = "MISSING_INELIGIBLE_OR_NONPOSITIVE_" + "_".join(bad).upper()
        return event
    if not bool(u["rolling_valid"]):
        event["reason"] = "INSUFFICIENT_PRIOR_TSE_RETURNS"
        return event
    concrete = {name: cast(Bar, val[0]) for name, val in required.items()}
    r, low, high = (
        cast(float, u["r"]),
        cast(float, u[f"q{lower_quantile}"]),
        cast(float, u[f"q{100 - lower_quantile}"]),
    )
    prices = {
        "d0_open": concrete["d0"].open,
        "dc_close": concrete["dc"].close,
        "n0_open": concrete["n0"].open,
        "n0_delay1_open": concrete["n0_delay1"].open,
        "n0_delay5_open": concrete["n0_delay5"].open,
        "next_d0_open": concrete["next_d0"].open,
        "next_d0_delay1_open": concrete["next_d0_delay1"].open,
    }
    dayrows = list(lookup[keys[0]].values())
    event.update(
        status="E",
        reason="COMMON_ELIGIBLE",
        **prices,
        r=r,
        q20=cast(float, u["q20"]),
        q25=cast(float, u["q25"]),
        q30=cast(float, u["q30"]),
        q50=cast(float, u["q50"]),
        q70=cast(float, u["q70"]),
        q75=cast(float, u["q75"]),
        q80=cast(float, u["q80"]),
        cell="A"
        if r <= low
        else "C"
        if r <= cast(float, u["q50"])
        else "D"
        if r >= high
        else "NONE",
        range_points=max(x.high for x in dayrows) - min(x.low for x in dayrows),
        efficiency=abs(concrete["dc"].close - concrete["d0"].open)
        / (
            abs(dayrows[0].close - dayrows[0].open)
            + sum(abs(dayrows[i].close - dayrows[i - 1].close) for i in range(1, len(dayrows)))
            or 1
        ),
        pre_gap_points=concrete["n0"].open - concrete["dc"].close,
    )
    return event
