"""Execute preregistered Development-only R065-Q001 fixed boundary orders."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil
from pathlib import Path
from subprocess import run
from sys import executable
from typing import cast

import numpy as np

from n225m_bt.backtest.costs import adverse_fill, gross_pnl, slippage_cost
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import ExitReason, Session, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import ledger_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r065 import DEVELOPMENT_END, DEVELOPMENT_START, boundary_event
from n225m_bt.research.runner import snapshot_source, write_json

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r065-q001-20260915-overnight-risk-premium-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
SEED = 20261007
REPETITIONS = 10_000
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def manifest(gold_root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(gold_root, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "normalized Development Parquet only; raw, OOS, and Final Holdout prohibited",
        "trade_date_range": [str(DEVELOPMENT_START), str(DEVELOPMENT_END)],
        "files": files,
        "files_hash": canonical_hash(files),
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in row.quality_flags for row in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listing = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit = {
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listing,
        "quarantined_session_list_hash": canonical_hash(listing),
        "rule": "R004 fixed full-session tick-grid isolation",
    }
    expected = {"quarantined_sessions": 45, "quarantined_bars": 27_345, "included_sessions": 2_216, "included_bars": 1_326_086, "quarantined_session_list_hash": EXPECTED_QUARANTINE_HASH}
    mismatch = {key: {"actual": audit[key], "expected": value} for key, value in expected.items() if audit[key] != value}
    if mismatch:
        raise ValueError(f"R004 fixed isolation mismatch: {mismatch}")
    return ResearchData(included, data.data_version, data.quality), audit, isolated


def scheduled_axis(calendar: ExchangeCalendar, isolated: set[tuple[date, Session]]) -> list[date]:
    axis = [
        row.trade_date
        for row in calendar.trading_days()
        if DEVELOPMENT_START <= row.trade_date <= DEVELOPMENT_END
        and (row.trade_date, Session.DAY) not in isolated
    ]
    if len(axis) != 1111 or len(set(axis)) != 1111:
        raise ValueError(f"fixed 1,111 trade_date axis mismatch: {len(axis)}")
    return axis


def execute(
    row: dict[str, object],
    name: str,
    side: Side,
    entry_key: str,
    exit_key: str,
    ticks: int,
    fee_side: int,
    multiplier: int,
    tick_size: int,
) -> Trade:
    entry_ts = datetime.fromisoformat(cast(str, row[entry_key + "_jst"]))
    exit_ts = datetime.fromisoformat(cast(str, row[exit_key + "_jst"]))
    entry_reference = int(row[entry_key + "_open"])
    exit_reference = int(row[exit_key + "_open"])
    # These are standing, time-only market orders placed before n0/d0/n1, not
    # bar-close signals.  The same adverse-fill primitive as the engine applies
    # one tick-side cost at each scheduled opening and no second deduction.
    spec = type("Spec", (), {"tick_size": tick_size, "multiplier": multiplier})()
    entry_fill = adverse_fill(entry_reference, side is Side.LONG, ticks, spec)
    exit_fill = adverse_fill(exit_reference, side is Side.SHORT, ticks, spec)
    gross = gross_pnl(entry_fill, exit_fill, side, 1, spec)
    fees = 2 * fee_side
    target = date.fromisoformat(cast(str, row["trade_date"]))
    return Trade(
        trade_id=f"{name}-{target.isoformat()}",
        trade_date=target,
        side=side,
        qty=1,
        entry_signal_ts=entry_ts,
        entry_ts=entry_ts,
        entry_reference_price=entry_reference,
        entry_fill_price=entry_fill,
        exit_signal_ts=exit_ts,
        exit_ts=exit_ts,
        exit_reference_price=exit_reference,
        exit_fill_price=exit_fill,
        gross_pnl_jpy=gross,
        fees_jpy=fees,
        slippage_cost_jpy=slippage_cost(entry_reference, entry_fill, exit_reference, exit_fill, 1, spec),
        net_pnl_jpy=gross - fees,
        mae_jpy=0,
        mfe_jpy=0,
        holding_minutes=int((exit_ts - entry_ts).total_seconds() // 60),
        entry_reason="r065_preregistered_scheduled_boundary_order",
        exit_reason=ExitReason.SIGNAL,
        strategy_id=f"r065_{name}",
        strategy_version="r065-q001-v1",
        parameter_hash=canonical_hash({"condition": name, "ticks": ticks, "fee_side": fee_side, "entry": entry_key, "exit": exit_key}),
        metadata={"entry_session": "night" if entry_key.startswith("n") else "day", "fixed_time_order": True},
    )


def run_condition(
    name: str,
    base: list[dict[str, object]],
    *,
    side: Side,
    entry_key: str,
    exit_key: str,
    ticks: int,
    fee_side: int,
    multiplier: int,
    tick_size: int,
) -> tuple[tuple[Trade, ...], list[dict[str, object]]]:
    trades: list[Trade] = []
    ledger: list[dict[str, object]] = []
    for source in base:
        row = dict(source)
        row.update(condition=name, side=side.value, entry_boundary=entry_key, exit_boundary=exit_key, slippage_ticks_per_side=ticks, fee_jpy_per_side=fee_side)
        if row["status"] != "E":
            row["condition_eligible"] = False
            ledger.append(row)
            continue
        trade = execute(row, name, side, entry_key, exit_key, ticks, fee_side, multiplier, tick_size)
        row.update(
            condition_eligible=True,
            status="filled",
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            entry_reference_price=trade.entry_reference_price,
            exit_reference_price=trade.exit_reference_price,
            entry_fill_price=trade.entry_fill_price,
            exit_fill_price=trade.exit_fill_price,
            gross_pnl_jpy=trade.gross_pnl_jpy,
            fees_jpy=trade.fees_jpy,
            slippage_cost_jpy=trade.slippage_cost_jpy,
            net_pnl_jpy=trade.net_pnl_jpy,
            exit_reason=trade.exit_reason.value,
        )
        trades.append(trade)
        ledger.append(row)
    return tuple(trades), ledger


def daily(trades: tuple[Trade, ...], axis: list[date]) -> dict[str, int]:
    values = dict.fromkeys((day.isoformat() for day in axis), 0)
    for trade in trades:
        values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
    return values


def indices(days: int, block: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    starts = np.arange(days - block + 1, dtype=np.int16)
    result = np.empty((REPETITIONS, days), dtype=np.int16)
    for rep in range(REPETITIONS):
        selected = rng.choice(starts, size=ceil(days / block), replace=True)
        result[rep] = np.concatenate([np.arange(start, start + block, dtype=np.int16) for start in selected])[:days]
    return result


def percentile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q, method="linear"))


def bootstrap(series: dict[str, dict[str, int]], axis: list[date], block: int) -> tuple[dict[str, object], np.ndarray]:
    draw = indices(len(axis), block)
    values = {name: np.asarray([item[day.isoformat()] for day in axis], dtype=np.float64) for name, item in series.items()}
    targets = {"A_daily_mean_net_jpy": values["A"], "A_minus_D_net_jpy": values["A"] - values["D"], "A_minus_A_short_net_jpy": values["A"] - values["A_short"]}
    return ({name: {"estimate": float(value.mean()), "ci95_percentile_linear": [percentile(value[draw].mean(axis=1), 0.025), percentile(value[draw].mean(axis=1), 0.975)]} for name, value in targets.items()} | {"method": "noncircular moving-block bootstrap; common index; tail truncation; linear percentile", "seed": SEED, "block_length_trade_dates": block, "repetitions": REPETITIONS}), draw


def condition_summary(trades: tuple[Trade, ...]) -> dict[str, object]:
    return {"trade_count": len(trades), "overall": ledger_metrics(trades)}


def ci_lower(book: dict[str, object], name: str) -> bool:
    return cast(list[float], cast(dict[str, object], book[name])["ci95_percentile_linear"])[0] > 0


def net_pf_positive(results: dict[str, dict[str, object]], name: str) -> bool:
    overall = cast(dict[str, object], results[name]["overall"])
    factor = overall["profit_factor"]
    return int(overall["net_pnl_jpy"]) > 0 and factor is not None and float(factor) > 1


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r065_q001_overnight_risk_premium.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    inputs = manifest(data_config.gold_root)
    files = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r065.py"), Path("tests/test_r065_q001.py")]
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R065-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "seed": SEED,
        "duplicate_review": "R001-R064 reviewed before price access. R055/R062 trade after TSE open from night-derived signals; R059 trades TSE-close to subsequent OSE-open gaps. No registered fixed n0-to-d0 buy versus fixed d0-to-n1 buy with the same versioned mapping exists.",
        "hypothesis": "A fixed long from the uniquely mapped OSE night open n0 to TSE open d0 has positive net expectancy after one tick plus JPY30 each side and exceeds same-trade-date d0-to-n1 fixed long D.",
        "boundaries": "For target TSE trade_date t, n0=session_open(t, night), d0=session_open(t, day), n1=session_open(next_trade_date(t), night). Reciprocal calendar links, monotonic timestamps, positive eligible prices, R004 isolation, and n0+1/n0+5/d0+1 sensitivity paths are common E requirements. No calendar-date inference, substitutions, or backfill.",
        "conditions": "A long n0-d0; D long d0-n1; A_short short n0-d0. A0 is diagnostic. A2/A3 use 2/3 tick; A_fee2 doubles fee; A_delay1/A_delay5 enter n0+1/n0+5 and retain d0 exit; A_exitdelay1 exits d0+1.",
        "execution": "Standing time-only scheduled market orders at predetermined opens. The unmodified bar-close engine cannot emit a causal signal before the first n0 bar, so the runner uses its adverse-fill/gross-PnL cost primitives directly: one adverse fill per entry/exit and Net=Gross-2*fee. No price-derived signal, Stop, Target, re-entry, early exit, or overlapping position.",
        "technical_lineage": "…-01 stopped before any order, fill, trade, PnL, bootstrap, or decision because the ledger omitted the already-fixed n0+1/n0+5/d0+1 timestamp fields. …-02 adds those derived timestamps only; hypothesis, boundaries, prices, costs, sensitivities, seed, inference, and gates are unchanged.",
        "inference": "fixed 1,111 trade_date axis; 20-day noncircular MBB 10,000 common-index replicates, seed 20261007, tail truncation and linear percentile; 10/40-day inference sensitivities.",
        "gates": "E>=800 and entry-delay sensitivity trades>=750 else INCONCLUSIVE; thereafter all user-specified profitability, CI, cost, delay, block, annual/monthly, and top10 gates are mandatory.",
        "inputs": inputs,
        "implementation": {str(path): digest(ROOT / path) for path in files},
        "source": source,
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "campaign_manifest.json", {"experiment_id": IDENTIFIER, "status": "preregistered", "started_at": datetime.now(timezone.utc).isoformat(), "plan_hash": canonical_hash(plan), "seed": SEED})
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r065_q001.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r065.py", "tests/test_r065_q001.py", str(Path(__file__).relative_to(ROOT))],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r065.py"],
    }
    validation = {name: {"returncode": value.returncode, "stdout": value.stdout, "stderr": value.stderr} for name, command in commands.items() for value in [run(command, cwd=ROOT, text=True, capture_output=True, check=False)]}
    validation["status"] = "PASS" if all(value["returncode"] == 0 for value in validation.values()) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("static/synthetic validation failed before Development price access")
    data = load_split(data_config.gold_root, "development")
    view, isolation, isolated = quarantine(data)
    axis = scheduled_axis(calendar, isolated)
    grouped = session_groups(view.bars)
    base = [boundary_event(classifier, day, grouped, isolated) for day in axis]
    reason_counts = Counter(str(row.get("reason")) for row in base)
    write_json(OUT / "all_candidate_event_ledger.json", base)
    write_json(OUT / "preflight.json", {"status": "PASS_LIMITED", "development_input": data.quality, "r004_isolation": isolation, "fixed_axis": [day.isoformat() for day in axis], "reason_counts": dict(reason_counts), "E": sum(row["status"] == "E" for row in base), "physical_io": "Development normalized Parquet only"})
    multiplier, tick_size, fee = instrument.instrument.contract_multiplier, instrument.instrument.tick_size, baseline.fees.jpy_per_side_per_contract
    specs = {"A": (Side.LONG, "n0", "d0", 1, fee), "D": (Side.LONG, "d0", "n1", 1, fee), "A_short": (Side.SHORT, "n0", "d0", 1, fee), "A0": (Side.LONG, "n0", "d0", 0, fee), "A2": (Side.LONG, "n0", "d0", 2, fee), "A3": (Side.LONG, "n0", "d0", 3, fee), "A_fee2": (Side.LONG, "n0", "d0", 1, fee * 2), "A_delay1": (Side.LONG, "n0_delay1", "d0", 1, fee), "A_delay5": (Side.LONG, "n0_delay5", "d0", 1, fee), "A_exitdelay1": (Side.LONG, "n0", "d0_delay1", 1, fee)}
    trades: dict[str, tuple[Trade, ...]] = {}
    ledgers: dict[str, list[dict[str, object]]] = {}
    daily_values: dict[str, dict[str, int]] = {}
    results: dict[str, dict[str, object]] = {}
    for name, (side, entry, exit_, ticks, fee_side) in specs.items():
        result, ledger = run_condition(name, base, side=side, entry_key=entry, exit_key=exit_, ticks=ticks, fee_side=fee_side, multiplier=multiplier, tick_size=tick_size)
        folder = OUT / name
        folder.mkdir()
        trades[name], ledgers[name], daily_values[name], results[name] = result, ledger, daily(result, axis), condition_summary(result)
        write_json(folder / "events.json", ledger)
        write_json(folder / "trades.json", [{"trade_id": item.trade_id, "trade_date": item.trade_date.isoformat(), "side": item.side.value, "entry_ts_jst": item.entry_ts.isoformat(), "exit_ts_jst": item.exit_ts.isoformat(), "entry_reference_price": item.entry_reference_price, "exit_reference_price": item.exit_reference_price, "entry_fill_price": item.entry_fill_price, "exit_fill_price": item.exit_fill_price, "gross_pnl_jpy": item.gross_pnl_jpy, "fees_jpy": item.fees_jpy, "slippage_cost_jpy": item.slippage_cost_jpy, "net_pnl_jpy": item.net_pnl_jpy} for item in result])
        write_json(folder / "daily_net_pnl_aligned.json", daily_values[name])
        write_json(folder / "research_metrics.json", results[name])
    audit = {
        "fixed_axis": all(len(values) == 1111 for values in daily_values.values()),
        "common_E_all_conditions": all(len(value) == sum(row["status"] == "E" for row in base) for value in trades.values()),
        "A_D_same_target_dates": [item.trade_date for item in trades["A"]] == [item.trade_date for item in trades["D"]],
        "A_short_same_timestamps": all(a.entry_ts == b.entry_ts and a.exit_ts == b.exit_ts and a.side is not b.side for a, b in zip(trades["A"], trades["A_short"], strict=True)),
        "delay_entry_nonextended": all(item.exit_ts == base_trade.exit_ts for item, base_trade in zip(trades["A_delay1"], trades["A"], strict=True)) and all(item.exit_ts == base_trade.exit_ts for item, base_trade in zip(trades["A_delay5"], trades["A"], strict=True)),
        "exit_delay_only": all(item.entry_ts == base_trade.entry_ts and item.exit_ts > base_trade.exit_ts for item, base_trade in zip(trades["A_exitdelay1"], trades["A"], strict=True)),
        "one_position_per_condition_per_trade_date": all(len({item.trade_date for item in value}) == len(value) for value in trades.values()),
        "accounting": all(item.net_pnl_jpy == item.gross_pnl_jpy - item.fees_jpy and item.slippage_cost_jpy == 2 * ticks * tick_size * multiplier for name, value in trades.items() for item in value for ticks in [specs[name][3]]),
    }
    write_json(OUT / "execution_accounting_audit.json", {"status": "PASS" if all(audit.values()) else "BLOCKED", "checks": audit, "execution_note": "scheduled-open orders use the engine adverse-fill primitives exactly once per side; slippage is already included in Gross."})
    if not all(audit.values()):
        raise ValueError("execution/accounting audit failed")
    main_boot, main_indices = bootstrap({name: daily_values[name] for name in ("A", "D", "A_short")}, axis, 20)
    boot10, indices10 = bootstrap({name: daily_values[name] for name in ("A", "D", "A_short")}, axis, 10)
    boot40, indices40 = bootstrap({name: daily_values[name] for name in ("A", "D", "A_short")}, axis, 40)
    np.save(OUT / "bootstrap_indices_block20.npy", main_indices)
    np.save(OUT / "bootstrap_indices_block10.npy", indices10)
    np.save(OUT / "bootstrap_indices_block40.npy", indices40)
    write_json(OUT / "bootstrap.json", {"main_block20": main_boot, "sensitivity_block10": boot10, "sensitivity_block40": boot40})
    years = {str(year): sum(daily_values["A"][day.isoformat()] for day in axis if day.year == year) for year in range(2021, 2026)}
    months = {f"{year}-{month:02d}": sum(daily_values["A"][day.isoformat()] for day in axis if day.year == year and day.month == month) for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    report_only = {"schedule_version_t": dict(Counter(str(row.get("schedule_version_t")) for row in base if row["status"] == "E")), "schedule_version_n1": dict(Counter(str(row.get("schedule_version_n1")) for row in base if row["status"] == "E")), "weekday_A_net": {str(day): sum(daily_values["A"][item.isoformat()] for item in axis if item.weekday() == day) for day in range(5)}, "month_A_net": months, "calendar_gap_days_distribution": dict(Counter((datetime.fromisoformat(cast(str, row["n1_jst"])).date() - datetime.fromisoformat(cast(str, row["d0_jst"])).date()).days for row in base if row["status"] == "E"))}
    write_json(OUT / "report_only_breakdowns.json", report_only)
    metrics_a = cast(dict[str, object], results["A"]["overall"])
    info = {"E>=800": sum(row["status"] == "E" for row in base) >= 800, "A_delay1>=750": len(trades["A_delay1"]) >= 750, "A_delay5>=750": len(trades["A_delay5"]) >= 750}
    gates = {"A_net_positive": int(metrics_a["net_pnl_jpy"]) > 0, "A_pf_gt1": metrics_a["profit_factor"] is not None and float(metrics_a["profit_factor"]) > 1, "three_main_ci_lower_positive": all(ci_lower(main_boot, name) for name in ("A_daily_mean_net_jpy", "A_minus_D_net_jpy", "A_minus_A_short_net_jpy")), "cost_fee_delay_sensitivities_net_pf_positive": all(net_pf_positive(results, name) for name in ("A2", "A3", "A_fee2", "A_delay1", "A_delay5", "A_exitdelay1")), "block10_40_A_and_AminusD_ci_lower_positive": all(ci_lower(book, name) for book in (boot10, boot40) for name in ("A_daily_mean_net_jpy", "A_minus_D_net_jpy")), "three_positive_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3, "positive_months>=27": sum(value > 0 for value in months.values()) >= 27, "top10_removed_positive": int(metrics_a["net_pnl_jpy"]) - sum(sorted((item.net_pnl_jpy for item in trades["A"] if item.net_pnl_jpy > 0), reverse=True)[:10]) > 0}
    decision = "INCONCLUSIVE" if not all(info.values()) else "INVESTIGATE" if all(gates.values()) else "REJECT"
    write_json(OUT / "development_results.json", {"experiment_id": IDENTIFIER, "decision": decision, "quality": "PASS_LIMITED", "E": sum(row["status"] == "E" for row in base), "information_gate": info, "fixed_gates": gates, "conditions": results, "A_year_net_jpy": years, "positive_months": sum(value > 0 for value in months.values()), "scope": "Development only; OOS NOT_EVALUATED, Final Holdout NOT_ACCESSED."})
    write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "development_complete", "decision": decision, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
