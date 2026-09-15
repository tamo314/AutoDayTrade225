"""Schedule-only helpers for TASK-R091-Q001.

The event ledger deliberately separates an entry decision from the later exit
observation.  In particular, a missing exit can never remove a 09:00 order
from ``E_exec``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from math import ceil
from statistics import fmean

import numpy as np

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session

DEVELOPMENT_START = date(2021, 1, 1)
DEVELOPMENT_END = date(2025, 6, 30)
ENTRY_TIME = time(9, 0)
MBB_BLOCK_LENGTH = 20
MBB_REPETITIONS = 10_000
MBB_SEED = 20260915


def tse_cash_close(trade_date: date) -> datetime:
    """Return the versioned official TSE cash-close boundary, never a bar-derived time."""
    clock = (15, 0) if trade_date <= date(2024, 11, 1) else (15, 30)
    return datetime(trade_date.year, trade_date.month, trade_date.day, *clock, tzinfo=JST)


def _valid(bar: Bar | None, target: date, *, close: bool = False) -> bool:
    price = bar.close if close and bar is not None else bar.open if bar is not None else 0
    return bool(
        bar is not None
        and bar.is_eligible
        and bar.trade_date == target
        and bar.session is Session.DAY
        and price > 0
    )


def scheduled_event(
    target: date,
    bars: list[Bar],
    *,
    r004_day_quarantined: bool,
    entry_time: time = ENTRY_TIME,
    exit_time: datetime | None = None,
) -> dict[str, object]:
    """Create a PnL-free scheduled event and retain any independent entry order.

    ``entry_eligible`` only reads the entry decision/fill pair.  The exit is
    evaluated afterward as an analysis/settlement observation, preventing the
    R004/M04-style future-exit filter from flowing back into E_exec.
    """
    exit_open = exit_time or tse_cash_close(target)
    entry_open = datetime.combine(target, entry_time, JST)
    entry_decision = entry_open - timedelta(minutes=1)
    exit_decision = exit_open - timedelta(minutes=1)
    row: dict[str, object] = {
        "trade_date": target.isoformat(),
        "schedule_source": "official_tse_cash_calendar_and_versioned_cash_close_rule",
        "tse_cash_close_jst": tse_cash_close(target).isoformat(),
        "submit_jst": entry_decision.isoformat(),
        "entry_decision_jst": entry_decision.isoformat(),
        "entry_fill_planned_jst": entry_open.isoformat(),
        "exit_decision_jst": exit_decision.isoformat(),
        "exit_fill_planned_jst": exit_open.isoformat(),
        "side_candidates": ["short", "long"],
        "entry_eligible": False,
        "order_submitted": False,
        "completion_status": "NOT_EVALUATED",
    }
    if r004_day_quarantined:
        row.update(
            {
                "entry_status": "R004_DAY_SESSION_QUARANTINED",
                "completion_status": "NO_ORDER_R004_DAY_SESSION_QUARANTINED",
                "reason": "R004_DAY_SESSION_QUARANTINED",
            }
        )
        return row
    lookup = {bar.ts_jst: bar for bar in bars}
    if not _valid(lookup.get(entry_decision), target, close=True):
        row.update({"entry_status": "ENTRY_DECISION_UNAVAILABLE", "reason": "ENTRY_DECISION_UNAVAILABLE"})
        return row
    if not _valid(lookup.get(entry_open), target):
        row.update({"entry_status": "ENTRY_FILL_UNAVAILABLE", "reason": "ENTRY_FILL_UNAVAILABLE"})
        return row
    # This is E_exec: it is committed before either exit row is inspected.
    row.update({"entry_status": "EXECUTABLE_ORDER_SUBMITTED", "entry_eligible": True, "order_submitted": True})
    if not _valid(lookup.get(exit_decision), target, close=True):
        row.update({"completion_status": "UNRESOLVED_EXIT_DECISION", "reason": "EXIT_DECISION_UNAVAILABLE"})
        return row
    if not _valid(lookup.get(exit_open), target):
        row.update({"completion_status": "UNRESOLVED_EXIT_FILL", "reason": "EXIT_FILL_UNAVAILABLE"})
        return row
    row.update({"completion_status": "COMPLETE", "reason": "SCHEDULED_PATH_COMPLETE"})
    return row


def feasibility(events: list[dict[str, object]]) -> dict[str, object]:
    """Apply the frozen availability gate without producing a return or PnL."""
    complete = [event for event in events if event["completion_status"] == "COMPLETE"]
    entry = [event for event in events if bool(event["entry_eligible"])]
    unresolved = [event for event in events if str(event["completion_status"]).startswith("UNRESOLVED")]
    counts = {str(year): sum(str(event["trade_date"]).startswith(str(year)) for event in complete) for year in range(2021, 2026)}
    old = sum(str(event["trade_date"]) <= "2024-11-01" for event in complete)
    new = sum(str(event["trade_date"]) >= "2024-11-05" for event in complete)
    named = {
        "R004_DAY_SESSION_QUARANTINED",
        "ENTRY_DECISION_UNAVAILABLE",
        "ENTRY_FILL_UNAVAILABLE",
        "EXIT_DECISION_UNAVAILABLE",
        "EXIT_FILL_UNAVAILABLE",
        "SCHEDULED_PATH_COMPLETE",
    }
    unexplained = [event for event in events if event.get("reason") not in named]
    gate = {
        "unexplained_exclusions_equal_zero": not unexplained,
        "unresolved_filled_positions_equal_zero": not unresolved,
        "a_short_complete_trades_at_least_900": len(complete) >= 900,
        "b_long_complete_trades_at_least_900": len(complete) >= 900,
        "each_2021_to_2024_at_least_180": all(counts[str(year)] >= 180 for year in range(2021, 2025)),
        "2025_h1_at_least_80": counts["2025"] >= 80,
        "old_tse_regime_at_least_700": old >= 700,
        "new_tse_regime_at_least_100": new >= 100,
    }
    return {
        "scope": "PnL-free scheduled-path availability only; no return, fill price, trade, PnL, PF, win/loss, or ranking statistic.",
        "scheduled_axis_trade_dates": len(events),
        "entry_orders_in_E_exec": len(entry),
        "complete_paths_for_E_analysis": len(complete),
        "unresolved_entry_positions": len(unresolved),
        "complete_by_year": counts,
        "complete_by_tse_close_regime": {"old_through_2024-11-01": old, "new_from_2024-11-05": new},
        "unexplained_exclusions": len(unexplained),
        "status_counts": {
            key: sum(event["completion_status"] == key for event in events)
            for key in sorted({str(event["completion_status"]) for event in events})
        },
        "gate": gate | {"passed": all(gate.values())},
    }


def bootstrap(
    short_daily: list[int], long_daily: list[int], *, seed: int = MBB_SEED
) -> tuple[dict[str, object], np.ndarray]:
    """Return common-index 20-day non-circular MBB intervals with linear percentiles."""
    if len(short_daily) != len(long_daily) or len(short_daily) < MBB_BLOCK_LENGTH:
        raise ValueError("R091 bootstrap axes are misaligned or too short")
    short = np.asarray(short_daily, dtype=np.float64)
    long = np.asarray(long_daily, dtype=np.float64)
    rng = np.random.default_rng(seed)
    blocks = ceil(len(short) / MBB_BLOCK_LENGTH)
    starts = rng.integers(0, len(short) - MBB_BLOCK_LENGTH + 1, size=(MBB_REPETITIONS, blocks))
    offsets = np.arange(MBB_BLOCK_LENGTH, dtype=np.int64)
    index = (starts[:, :, None] + offsets).reshape(MBB_REPETITIONS, -1)[:, : len(short)]

    def interval(values: np.ndarray) -> dict[str, object]:
        samples = values[index].mean(axis=1)
        ci = np.quantile(samples, (0.025, 0.975), method="linear")
        return {"estimate": fmean(values.tolist()), "ci95_percentile_linear": [float(ci[0]), float(ci[1])]}

    return (
        {
            "method": "20 trade_date non-circular moving-block bootstrap; common indices; tail truncation; linear percentile",
            "block_length_trade_dates": MBB_BLOCK_LENGTH,
            "repetitions": MBB_REPETITIONS,
            "seed": seed,
            "axis_observations": len(short),
            "A_short_daily_net_jpy_per_trade_date": interval(short),
            "A_minus_B_daily_net_jpy_per_trade_date": interval(short - long),
        },
        index,
    )
