"""Development-only R003-Q001 session-quarantine sensitivity.

This intentionally does not open OOS/Final Holdout and does not alter the parent R003 study.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.data import ResearchData, load_split
from n225m_bt.research.features import OpeningHistory, summarize_opening
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json
from n225m_bt.strategies.compression import CompressionBreakoutStrategy

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r003-q001-20260913-development-campaign-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
THRESHOLDS = (Fraction(3, 5), Fraction(3, 4), Fraction(9, 10))
HOLDINGS = (30, 60, 90)


def groups(bars: list[Bar]) -> dict[tuple[date, Session], list[Bar]]:
    result: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in bars:
        result[(bar.trade_date, bar.session)].append(bar)
    return {key: sorted(value, key=lambda item: item.ts_jst) for key, value in result.items()}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object]]:
    all_groups = groups(data.bars)
    bad = {
        key
        for key, session_bars in all_groups.items()
        if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in session_bars)
    }
    included = [bar for key, session_bars in all_groups.items() if key not in bad for bar in session_bars]
    if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in included):
        raise AssertionError("quarantine did not remove every tick-grid bar")
    by_type = Counter(session.value for _, session in bad)
    audit: dict[str, object] = {
        "parent_data_version": data.data_version,
        "parent_bars": len(data.bars),
        "quarantined_bars": len(data.bars) - len(included),
        "quarantined_sessions": len(bad),
        "quarantined_sessions_by_type": dict(sorted(by_type.items())),
        "included_bars": len(included),
        "included_sessions": len(all_groups) - len(bad),
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
    }
    view_version = canonical_hash({"parent": data.data_version, "policy": audit["rule"], "sessions": sorted((str(day), session.value) for day, session in bad)})
    return ResearchData(included, view_version, data.quality | {"quarantine": audit}), audit


def evaluate(
    data: ResearchData,
    classifier: CalendarClassifier,
    engine: BacktestEngine,
    threshold: Fraction,
    holding: int,
    filtered: bool,
) -> tuple[tuple[Trade, ...], dict[str, int]]:
    history = OpeningHistory(20)
    collected: list[Trade] = []
    audit = Counter()
    for (trade_day, session), session_bars in sorted(groups(data.bars).items(), key=lambda item: item[1][0].ts_jst):
        session_start = classifier.session_open(trade_day, session)
        snapshot = history.snapshot(session, session_start)
        strategy = CompressionBreakoutStrategy(
            strategy_id="r003_q001_compression" if filtered else "r003_q001_matched_control",
            session_open=session_start,
            history=snapshot,
            threshold=threshold,
            holding_minutes=holding,
            filtered=filtered,
        )
        parameter_hash = canonical_hash({"threshold": str(threshold), "holding": holding, "filtered": filtered, "data_version": data.data_version})
        result = engine.run(session_bars, strategy, parameter_hash)
        audit["sessions"] += 1
        audit[f"history_{snapshot.status}"] += 1
        audit[f"disabled_{strategy.disabled_reason or 'none'}"] += 1
        audit["canceled_orders"] += result.canceled_orders
        audit["end_of_data_exits"] += sum(trade.exit_reason is ExitReason.END_OF_DATA for trade in result.trades)
        collected.extend(result.trades)
        history.append(summarize_opening(session_bars, session, session_start, opening_minutes=30))
    collected.sort(key=lambda trade: trade.entry_ts)
    trades = tuple(replace(trade, trade_id=f"trade-{index:06d}") for index, trade in enumerate(collected, 1))
    return trades, dict(sorted(audit.items()))


def main() -> None:
    if not (OUT / "campaign_plan.json").exists():
        raise ValueError("missing frozen campaign plan")
    if (OUT / "COMPLETED.json").exists():
        raise ValueError("campaign output exists; never overwrite a campaign")
    plan = json.loads((OUT / "campaign_plan.json").read_text(encoding="utf-8"))
    if plan["status"] != "frozen_before_pnl" or plan["stage"] != "development_only":
        raise ValueError("campaign is not correctly preregistered")
    instrument, sessions, data_config, backtest = load_project_config(ROOT / "config")
    if backtest.execution.slippage_ticks != 1 or backtest.fees.jpy_per_side_per_contract != 30:
        raise ValueError("frozen cost contract differs")
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    write_json(OUT / "campaign_manifest.json", {"campaign_id": IDENTIFIER, "source": source, "plan_hash": canonical_hash(plan), "started_at": datetime.now(timezone.utc).isoformat(), "oos": "not_requested", "final_holdout": "not_accessed"})
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit = quarantine(development)
    write_json(OUT / "quarantine_audit.json", quarantine_audit | {"included_tick_grid_violations": 0, "preflight_status": "PASS_LIMITED"})
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"))
    engine = BacktestEngine(instrument.instrument.to_spec(), backtest, classifier)
    runs: list[dict[str, object]] = []
    definitions = [
        (f"compression-theta-{float(theta):.2f}-H{holding}", theta, holding, True)
        for theta in THRESHOLDS for holding in HOLDINGS
    ] + [(f"matched-control-H{holding}", Fraction(3, 4), holding, False) for holding in HOLDINGS]
    for ordinal, (label, threshold, holding, filtered) in enumerate(definitions, 1):
        folder = reserve_directory(OUT, f"{ordinal:02d}-{label}")
        trades, audit = evaluate(view, classifier, engine, threshold, holding, filtered)
        manifest = {"experiment_id": folder.name, "campaign_id": IDENTIFIER, "threshold": str(threshold), "holding_minutes": holding, "filtered": filtered, "data_version": view.data_version, "costs": {"slippage_ticks_per_side": 1, "fee_jpy_per_side": 30}, "status": "complete", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}
        write_results(folder, trades, (), manifest | {"audit": audit})
        metrics = research_metrics(trades, view.bars)
        write_json(folder / "metrics.json", metrics)
        write_json(folder / "execution_audit.json", audit)
        runs.append({"experiment_id": folder.name, "label": label, "threshold": str(threshold), "holding_minutes": holding, "filtered": filtered, "trades": len(trades), "overall": metrics["overall"], "concentration": metrics["concentration"], "execution_audit": audit})
    write_json(OUT / "development_results.json", {"campaign_id": IDENTIFIER, "quality_status": "PASS_LIMITED", "decision": "INVESTIGATE", "data_policy": quarantine_audit, "runs": runs, "development_pnl_scope": "R003-Q001 only; not frozen R003", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"campaign_id": IDENTIFIER, "status": "development_sensitivity_complete", "experiments": len(runs), "development_pnl": "RUN_FOR_R003_Q001_ONLY", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
