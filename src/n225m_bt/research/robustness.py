"""Reproducible ledger resampling and causal rolling selection."""

import calendar
import random
from datetime import date, timedelta
from statistics import fmean

from n225m_bt.domain import Trade
from n225m_bt.research.metrics import drawdown, ledger_metrics


def distribution(values: list[int]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    if not values:
        return {"p05": None, "p50": None, "p95": None, "mean": None}
    return {
        "p05": ordered[int((len(values) - 1) * 0.05)],
        "p50": ordered[int((len(values) - 1) * 0.50)],
        "p95": ordered[int((len(values) - 1) * 0.95)],
        "mean": fmean(values),
    }


def resample(trades: tuple[Trade, ...], seed: int, iterations: int) -> dict[str, object]:
    rng = random.Random(seed)
    values = [t.net_pnl_jpy for t in trades]
    if not values:
        return {"status": "no_trades", "seed": seed, "iterations": iterations}
    output: dict[str, object] = {"seed": seed, "iterations": iterations}
    for mode in ("drop_10_percent", "shuffle", "bootstrap"):
        totals, dds = [], []
        for _ in range(iterations):
            if mode == "drop_10_percent":
                indices = sorted(rng.sample(range(len(values)), int(len(values) * 0.9)))
                sample = [values[index] for index in indices]
            elif mode == "shuffle":
                sample = values.copy()
                rng.shuffle(sample)
            else:
                sample = rng.choices(values, k=len(values))
            totals.append(sum(sample))
            dds.append(drawdown(sample))
        output[mode] = {
            "net_pnl_jpy": distribution(totals),
            "max_realized_drawdown_jpy": distribution(dds),
            "positive_total_fraction": sum(x > 0 for x in totals) / iterations,
        }
    return output


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def walk_forward(
    grid: dict[tuple[int, int], tuple[Trade, ...]],
    representative: tuple[int, int],
    minimum_positive: int,
    start: date,
    end: date,
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
) -> dict[str, object]:
    folds: list[dict[str, object]] = []
    combined: list[Trade] = []
    train_start = start
    while add_months(train_start, train_months + test_months) - timedelta(days=1) <= end:
        test_start = add_months(train_start, train_months)
        test_end = add_months(test_start, test_months) - timedelta(days=1)
        train = {
            key: tuple(t for t in trades if train_start <= t.trade_date < test_start)
            for key, trades in grid.items()
        }
        positive = sum(sum(t.net_pnl_jpy for t in trades) > 0 for trades in train.values())
        active = (
            positive >= minimum_positive and sum(t.net_pnl_jpy for t in train[representative]) > 0
        )
        test = (
            tuple(t for t in grid[representative] if test_start <= t.trade_date <= test_end)
            if active
            else ()
        )
        combined.extend(test)
        folds.append(
            {
                "train_start": str(train_start),
                "train_end": str(test_start - timedelta(days=1)),
                "test_start": str(test_start),
                "test_end": str(test_end),
                "train_positive_grid_points": positive,
                "train_representative": ledger_metrics(train[representative]),
                "active": active,
                "test_metrics": ledger_metrics(test),
            }
        )
        train_start = add_months(train_start, step_months)
    active_folds = [fold for fold in folds if fold["active"]]
    # Use the already selected trade dates to count profitable test periods.
    positive_folds = sum(
        sum(
            t.net_pnl_jpy
            for t in combined
            if str(fold["test_start"]) <= str(t.trade_date) <= str(fold["test_end"])
        )
        > 0
        for fold in active_folds
    )
    return {
        "folds": folds,
        "overall": ledger_metrics(combined),
        "active_folds": len(active_folds),
        "positive_active_folds": positive_folds,
        "passes": bool(folds)
        and len(active_folds) >= len(folds) / 2
        and positive_folds > len(active_folds) / 2
        and sum(t.net_pnl_jpy for t in combined) > 0,
    }
