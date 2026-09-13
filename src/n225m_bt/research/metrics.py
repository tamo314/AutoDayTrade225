"""Research statistics; trade economics remain owned by the execution engine."""

from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta
from math import sqrt
from statistics import fmean, stdev

from n225m_bt.domain import Bar, Trade

Metrics = dict[str, int | float | None]


def drawdown(values: list[int]) -> int:
    equity = peak = worst = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def ledger_metrics(trades: list[Trade] | tuple[Trade, ...]) -> Metrics:
    net = [trade.net_pnl_jpy for trade in trades]
    wins, losses = [x for x in net if x > 0], [x for x in net if x < 0]
    average_win = fmean(wins) if wins else None
    average_loss = fmean(losses) if losses else None
    return {
        "trade_count": len(net),
        "net_pnl_jpy": sum(net),
        "gross_pnl_jpy": sum(trade.gross_pnl_jpy for trade in trades),
        "fees_jpy": sum(trade.fees_jpy for trade in trades),
        "slippage_cost_jpy": sum(trade.slippage_cost_jpy for trade in trades),
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "expectancy_jpy": fmean(net) if net else None,
        "win_rate": len(wins) / len(net) if net else None,
        "average_win_jpy": average_win,
        "average_loss_jpy": average_loss,
        "payoff_ratio": average_win / abs(average_loss)
        if average_win is not None and average_loss is not None
        else None,
        "max_realized_drawdown_jpy": drawdown(net),
        "average_holding_minutes": fmean(t.holding_minutes for t in trades) if net else None,
        "average_mae_jpy": fmean(t.mae_jpy for t in trades) if net else None,
        "max_mae_jpy": max((t.mae_jpy for t in trades), default=0),
        "average_mfe_jpy": fmean(t.mfe_jpy for t in trades) if net else None,
        "max_mfe_jpy": max((t.mfe_jpy for t in trades), default=0),
    }


def concentration(trades: tuple[Trade, ...], dates: list[date]) -> Metrics:
    net = [t.net_pnl_jpy for t in trades]
    winners = sorted((x for x in net if x > 0), reverse=True)
    monthly = dict.fromkeys(sorted({d.strftime("%Y-%m") for d in dates}), 0)
    for trade in trades:
        key = trade.trade_date.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + trade.net_pnl_jpy
    total = sum(net)
    best = max(monthly.values(), default=0)
    return {
        "largest_winning_trade_jpy": max(winners, default=0),
        "top5_share_of_net": sum(winners[:5]) / total if total > 0 else None,
        "top10_share_of_net": sum(winners[:10]) / total if total > 0 else None,
        "top5_share_of_gross_wins": sum(winners[:5]) / sum(winners) if winners else None,
        "top10_share_of_gross_wins": sum(winners[:10]) / sum(winners) if winners else None,
        "net_excluding_top5_jpy": total - sum(winners[:5]),
        "net_excluding_top10_jpy": total - sum(winners[:10]),
        "best_month_jpy": best,
        "best_month_share_of_net": best / total if total > 0 else None,
        "worst_month_jpy": min(monthly.values(), default=0),
        "positive_month_fraction": sum(v > 0 for v in monthly.values()) / len(monthly)
        if monthly
        else None,
    }


def research_metrics(trades: tuple[Trade, ...], bars: list[Bar]) -> dict[str, object]:
    dates = sorted({bar.trade_date for bar in bars})
    daily = dict.fromkeys(dates, 0)
    for trade in trades:
        daily[trade.trade_date] += trade.net_pnl_jpy
    daily_values = list(daily.values())
    deviation = stdev(daily_values) if len(daily_values) > 1 else 0.0
    downside = sqrt(fmean(min(v, 0) ** 2 for v in daily_values)) if daily_values else 0.0
    mean = fmean(daily_values) if daily_values else 0.0

    # Mark at observed eligible bar closes; exit economics take effect at the recorded exit.
    realized = peak = max_dd = exposed = index = 0
    eligible_count = 0
    for bar in bars:
        if not bar.is_eligible:
            continue
        eligible_count += 1
        while index < len(trades) and trades[index].exit_ts <= bar.ts_jst:
            realized += trades[index].net_pnl_jpy
            index += 1
        mark = realized
        if index < len(trades):
            trade = trades[index]
            if trade.entry_ts <= bar.ts_jst < trade.exit_ts:
                exposed += 1
                mark += (
                    bar.close - trade.entry_fill_price
                ) * 100 * trade.side.sign - trade.fees_jpy // 2
        peak = max(peak, mark)
        max_dd = max(max_dd, peak - mark)
    overall = ledger_metrics(trades) | {
        "sharpe_daily_pnl_annualized": mean / deviation * sqrt(252) if deviation else None,
        "sortino_daily_pnl_annualized": mean / downside * sqrt(252) if downside else None,
        "max_close_marked_drawdown_jpy": max_dd,
        "exposure_observed_bar_fraction": exposed / eligible_count if eligible_count else None,
        "trade_dates": len(dates),
    }
    groups: dict[str, dict[str, list[Trade]]] = {
        name: defaultdict(list)
        for name in ("year", "month", "weekday", "hour", "session", "side", "roll_risk")
    }
    for trade in trades:
        keys = {
            "year": str(trade.trade_date.year),
            "month": trade.trade_date.strftime("%Y-%m"),
            "weekday": str(trade.trade_date.weekday()),
            "hour": f"{trade.entry_ts.hour:02d}",
            "session": str(trade.metadata.get("entry_session", "unknown")),
            "side": trade.side.value,
            "roll_risk": str(trade.metadata.get("entry_roll_risk", False)).lower(),
        }
        for name, key in keys.items():
            groups[name][key].append(trade)
    segments = {
        name: {key: ledger_metrics(values) for key, values in sorted(group.items())}
        for name, group in groups.items()
    }
    return {
        "overall": overall,
        "concentration": concentration(trades, dates),
        "segments": segments,
        "daily_net_pnl_jpy": {str(day): value for day, value in daily.items()},
    }


def adverse_exit_overlay(trades: tuple[Trade, ...], bars: list[Bar]) -> Metrics:
    """One-minute worse-price attribution, not a replay of the execution engine."""
    stamps = [bar.ts_jst for bar in bars]
    adjusted, unavailable = [], 0
    for trade in trades:
        target = trade.exit_ts + timedelta(minutes=1)
        index = bisect_left(stamps, target)
        if (
            index == len(bars)
            or bars[index].ts_jst != target
            or not bars[index].is_eligible
            or bars[index].trade_date != trade.trade_date
            or bars[index].session.value != trade.metadata.get("entry_session")
        ):
            unavailable += 1
            continue
        move = (bars[index].open - trade.exit_reference_price) * trade.side.sign * 100
        adjusted.append(trade.net_pnl_jpy + min(move, 0))
    return {
        "matched_trades": len(adjusted),
        "unavailable_trades": unavailable,
        "net_pnl_jpy_matched_only": sum(adjusted),
        "expectancy_jpy_matched_only": fmean(adjusted) if adjusted else None,
        "max_realized_drawdown_jpy_matched_only": drawdown(adjusted),
    }
