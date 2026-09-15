"""Causal OSE-night efficiency events for R067-Q001."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from math import ceil
from typing import cast

import numpy as np
from numpy.typing import NDArray

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r055 import normal_night_end

DEVELOPMENT_START, DEVELOPMENT_END = date(2021, 1, 1), date(2025, 6, 30)
LOOKBACK, MIN_REFERENCES = 120, 100
PINV_RCOND, RESIDUAL_SS_TOLERANCE = 1e-12, 1e-12


class R067QNotIdentifiableError(ValueError):
    """The preregistered interaction is unidentified after FWL projection."""


def nearest_rank(values: list[float], percentile: int) -> float:
    if percentile not in {40, 50, 60, 65, 70, 75} or not values:
        raise ValueError("invalid R067 nearest-rank request")
    return sorted(values)[ceil(percentile * len(values) / 100) - 1]


def fwl_delta(
    q: NDArray[np.float64], y: NDArray[np.float64], nuisance: NDArray[np.float64]
) -> tuple[float, float]:
    projection = nuisance @ np.linalg.pinv(nuisance, rcond=PINV_RCOND)
    qr, yr = q - projection @ q, y - projection @ y
    ss = float(qr @ qr)
    if ss <= RESIDUAL_SS_TOLERANCE:
        raise R067QNotIdentifiableError(f"R067 Q residual SS={ss:.17g}")
    return float(qr @ yr / ss), ss


def _valid(bar: Bar | None, target: date, session: Session) -> bool:
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is session
        and min(bar.open, bar.high, bar.low, bar.close) > 0
    )


def _night_observation(
    classifier: CalendarClassifier, target: date, rows: list[Bar] | None, isolated: bool
) -> dict[str, object]:
    result: dict[str, object] = {"u_member": False}
    if isolated:
        result["reason"] = "R004_NIGHT_QUARANTINED"
        return result
    try:
        n0, (nc, _, version, basis) = classifier.session_open(target, Session.NIGHT), normal_night_end(
            classifier, target
        )
    except ValueError as exc:
        result["reason"] = str(exc)
        return result
    count = int((nc - n0).total_seconds() // 60) + 1
    lookup = {bar.ts_jst: bar for bar in rows or []}
    path = [lookup.get(n0 + timedelta(minutes=i)) for i in range(count)]
    if not all(_valid(bar, target, Session.NIGHT) for bar in path):
        result["reason"] = "INCOMPLETE_OR_INELIGIBLE_SCHEDULED_NIGHT"
        return result
    concrete = cast(list[Bar], path)
    start, final = concrete[0].open, concrete[-1].close
    d = final - start
    steps = abs(concrete[0].close - start) + sum(
        abs(concrete[i].close - concrete[i - 1].close) for i in range(1, len(concrete))
    )
    result.update(
        u_member=True,
        n0_jst=n0.isoformat(),
        nc_bar_start_jst=nc.isoformat(),
        night_schedule_version=version,
        nc_selection_basis=basis,
        scheduled_night_minutes=count,
        n0_open=start,
        nc_close=final,
        r=final / start - 1.0,
        a=abs(final / start - 1.0),
        e=abs(d) / steps if steps else 0.0,
        night_high_low_range=max(x.high for x in concrete) - min(x.low for x in concrete),
    )
    return result


def rolling_u_ledger(
    candidates: Iterable[dict[str, object]],
    *,
    lookback: int = LOOKBACK,
    min_references: int = MIN_REFERENCES,
) -> list[dict[str, object]]:
    """Attach a strictly prior, fixed-size scheduled-night reference ledger."""
    if lookback <= 0 or min_references <= 0 or min_references > lookback:
        raise ValueError("invalid R067 rolling reference requirements")
    history: deque[dict[str, object]] = deque(maxlen=lookback)
    output: list[dict[str, object]] = []
    previous: str | None = None
    for source in candidates:
        row, target = dict(source), str(source["trade_date"])
        if previous is not None and target <= previous:
            raise ValueError("R067 candidates must be chronological")
        refs = list(history)
        if any(str(x["trade_date"]) >= target for x in refs):
            raise ValueError("R067 rolling causality violation")
        valid = [x for x in refs if bool(x["u_member"])]
        u = cast(dict[str, object], row["u"])
        u.update(
            rolling_reference_trade_dates=[str(x["trade_date"]) for x in refs],
            rolling_scheduled_count=len(refs),
            rolling_valid_count=len(valid),
            rolling_valid=len(valid) >= min_references,
        )
        if len(valid) >= min_references:
            a, e = [float(cast(float, x["a"])) for x in valid], [float(cast(float, x["e"])) for x in valid]
            u.update({f"q{q}": nearest_rank(a, q) for q in (50, 65, 70, 75)})
            u.update({f"e_q{q}": nearest_rank(e, q) for q in (40, 50, 60)})
        history.append({"trade_date": target, "u_member": bool(u["u_member"]), "a": u.get("a"), "e": u.get("e")})
        output.append(row)
        previous = target
    return output


def candidate_rows(
    classifier: CalendarClassifier,
    days: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    lookback: int = LOOKBACK,
    min_references: int = MIN_REFERENCES,
) -> list[dict[str, object]]:
    return rolling_u_ledger(
        (
            {
                "trade_date": target.isoformat(),
                "u": _night_observation(
                    classifier, target, bars.get((target, Session.NIGHT)), (target, Session.NIGHT) in isolated
                ),
            }
            for target in days
        ),
        lookback=lookback,
        min_references=min_references,
    )


def make_event(
    classifier: CalendarClassifier,
    target: date,
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    ledger: dict[str, object],
    *,
    magnitude_quantile: int = 70,
    efficiency_quantile: int = 50,
) -> dict[str, object]:
    if magnitude_quantile not in {65, 70, 75} or efficiency_quantile not in {40, 50, 60}:
        raise ValueError("unregistered R067 sensitivity")
    event: dict[str, object] = {"trade_date": target.isoformat(), "status": "skipped"}
    if not DEVELOPMENT_START <= target <= DEVELOPMENT_END:
        event["reason"] = "OUTSIDE_DEVELOPMENT"
        return event
    record = classifier.exchange_calendar.get(target)
    if record is None or record.night_calendar_start_date is None:
        event["reason"] = "NO_UNIQUE_NIGHT_TO_TSE_CHAIN"
        return event
    if (target, Session.NIGHT) in isolated or (target, Session.DAY) in isolated:
        event["reason"] = "R004_SESSION_QUARANTINED"
        return event
    u = cast(dict[str, object], ledger["u"])
    if not bool(u["rolling_valid"]):
        event["reason"] = "INSUFFICIENT_PRIOR_NIGHT_REFERENCES"
        return event
    current = _night_observation(classifier, target, bars.get((target, Session.NIGHT)), False)
    if not bool(current["u_member"]):
        event["reason"] = str(current.get("reason", "TARGET_NIGHT_INVALID"))
        return event
    try:
        d0 = classifier.session_open(target, Session.DAY)
    except ValueError as exc:
        event["reason"] = str(exc)
        return event
    day_rows = bars.get((target, Session.DAY), [])
    lookup = {bar.ts_jst: bar for bar in day_rows}
    required_offsets = (0, 1, 5, 60, 120, 180)
    required = {offset: lookup.get(d0 + timedelta(minutes=offset)) for offset in required_offsets}
    if not all(_valid(bar, target, Session.DAY) for bar in required.values()):
        event["reason"] = "MISSING_INELIGIBLE_OR_NONPOSITIVE_TSE_EXECUTION_PATH"
        return event
    bars_at = cast(dict[int, Bar], required)
    r, a, e = (
        float(cast(float, current["r"])),
        float(cast(float, current["a"])),
        float(cast(float, current["e"])),
    )
    sign = 1 if r > 0 else -1 if r < 0 else 0
    q50, qx, eq = (
        float(cast(float, u["q50"])),
        float(cast(float, u[f"q{magnitude_quantile}"])),
        float(cast(float, u[f"e_q{efficiency_quantile}"])),
    )
    high, medium = a >= qx, q50 <= a < qx
    efficient = e >= eq
    cell = "A" if high and efficient else "C" if medium and efficient else "D" if high else "M" if medium else "NONE"
    nrows = bars.get((target, Session.NIGHT), [])
    nlookup = {bar.ts_jst: bar for bar in nrows}
    nc_ts = datetime.fromisoformat(cast(str, current["nc_bar_start_jst"]))
    tail = [nlookup.get(nc_ts - timedelta(minutes=i)) for i in range(29, -1, -1)]
    tail_return = 0.0
    if sign and all(_valid(bar, target, Session.NIGHT) for bar in tail):
        concrete_tail = cast(list[Bar], tail)
        tail_return = sign * (concrete_tail[-1].close - concrete_tail[0].open) / concrete_tail[0].open
    event.update(
        status="E", reason="COMMON_ELIGIBLE", **current,
        q50=float(cast(float, u["q50"])), q65=float(cast(float, u["q65"])),
        q70=float(cast(float, u["q70"])), q75=float(cast(float, u["q75"])),
        e_q40=float(cast(float, u["e_q40"])), e_q50=float(cast(float, u["e_q50"])),
        e_q60=float(cast(float, u["e_q60"])),
        s=sign, direction_eligible=sign != 0, magnitude_quantile=magnitude_quantile,
        efficiency_quantile=efficiency_quantile, cell=cell,
        planned_entry_jst=d0.isoformat(), planned_delay1_entry_jst=(d0 + timedelta(minutes=1)).isoformat(),
        planned_delay5_entry_jst=(d0 + timedelta(minutes=5)).isoformat(),
        planned_exit60_jst=(d0 + timedelta(minutes=60)).isoformat(), planned_exit120_jst=(d0 + timedelta(minutes=120)).isoformat(),
        planned_exit180_jst=(d0 + timedelta(minutes=180)).isoformat(),
        entry_open=bars_at[0].open, delay1_entry_open=bars_at[1].open, delay5_entry_open=bars_at[5].open,
        exit60_open=bars_at[60].open, exit120_open=bars_at[120].open, exit180_open=bars_at[180].open,
        gap_s_adjusted=(
            sign
            * (bars_at[0].open - int(cast(int, current["nc_close"])))
            / int(cast(int, current["nc_close"]))
            * 10_000
            if sign
            else 0.0
        ),
        tail30_s_adjusted_return=tail_return, upward=int(sign > 0), schedule_version=record.schedule_version,
    )
    return event
