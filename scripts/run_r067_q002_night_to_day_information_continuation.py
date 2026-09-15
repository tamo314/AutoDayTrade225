"""Execute R067-Q002's preregistered Development-only 240-night recheck."""

from __future__ import annotations

import importlib.util
from collections import Counter, deque
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from subprocess import run
from sys import executable
from typing import Any, cast

import polars as pl

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Session
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r055 import normal_night_end
from n225m_bt.research.r067 import DEVELOPMENT_END, DEVELOPMENT_START, candidate_rows
from n225m_bt.research.runner import snapshot_source, write_json

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r067-q002-20260915-night-to-day-information-continuation-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
LOOKBACK, MIN_REFERENCES = 240, 160
MBB_SEED, WILD_SEED, REPETITIONS = 20261010, 20261011, 10_000
META_COLUMNS = ["ts_jst", "trade_date", "session", "is_eligible", "quality_flags", "series_type", "instrument"]


def load_q001() -> Any:
    """Load the frozen Q001 execution functions without invoking its main()."""
    path = ROOT / "scripts" / "run_r067_q001_night_to_day_information_continuation.py"
    spec = importlib.util.spec_from_file_location("r067_q001_execution", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load frozen R067-Q001 executor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


Q001 = load_q001()


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1_048_576), b""):
            h.update(part)
    return h.hexdigest()


def manifest(root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
        for path in partition_paths(root, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "normalized Development Parquet only",
        "trade_date_range": [str(DEVELOPMENT_START), str(DEVELOPMENT_END)],
        "files": files,
        "files_hash": canonical_hash(files),
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def metadata_rows(root: Path) -> list[dict[str, object]]:
    """Read only timestamps, session labels, isolation flags, and eligibility flags."""
    frame = (
        pl.scan_parquet(partition_paths(root, "development"), hive_partitioning=False)
        .filter(pl.col("trade_date").is_between(DEVELOPMENT_START, DEVELOPMENT_END))
        .select(META_COLUMNS)
        .sort("ts_jst")
        .collect()
    )
    if frame.is_empty():
        raise ValueError("metadata preflight found no Development rows")
    if frame["series_type"].unique().to_list() != ["center_continuous"]:
        raise ValueError("metadata preflight requires center_continuous only")
    if frame["instrument"].unique().to_list() != ["N225M"]:
        raise ValueError("metadata preflight requires N225M only")
    if frame["ts_jst"].dtype != pl.Datetime("us", "Asia/Tokyo"):
        raise ValueError("metadata preflight requires canonical Asia/Tokyo timestamps")
    return frame.unique(subset=["ts_jst"], keep="first", maintain_order=True).to_dicts()


def metadata_groups(rows: list[dict[str, object]]) -> tuple[dict[tuple[date, Session], dict[datetime, bool]], set[tuple[date, Session]]]:
    grouped: dict[tuple[date, Session], dict[datetime, bool]] = {}
    isolated: set[tuple[date, Session]] = set()
    for row in rows:
        target = cast(date, row["trade_date"])
        session = Session(cast(str, row["session"]))
        key = (target, session)
        grouped.setdefault(key, {})[cast(datetime, row["ts_jst"])] = bool(row["is_eligible"])
        if "TICK_GRID_VIOLATION" in cast(list[str], row["quality_flags"]):
            isolated.add(key)
    return grouped, isolated


def scheduled_path_eligible(
    rows: dict[datetime, bool], start: datetime, count: int
) -> bool:
    return all(rows.get(start + timedelta(minutes=offset), False) for offset in range(count))


def preflight(
    classifier: CalendarClassifier,
    calendar: ExchangeCalendar,
    root: Path,
) -> dict[str, object]:
    """Compute Q002 eligibility from schedules, R004 isolation, and missing flags only."""
    rows = metadata_rows(root)
    grouped, isolated = metadata_groups(rows)
    all_days = [record.trade_date for record in calendar.trading_days() if DEVELOPMENT_START <= record.trade_date <= DEVELOPMENT_END]
    availability: list[dict[str, object]] = []
    history: deque[dict[str, object]] = deque(maxlen=LOOKBACK)
    expected_e: list[dict[str, object]] = []
    reasons: Counter[str] = Counter()
    for target in all_days:
        record = calendar.get(target)
        night_rows = grouped.get((target, Session.NIGHT), {})
        try:
            night_start = classifier.session_open(target, Session.NIGHT)
            night_end, _, _, _ = normal_night_end(classifier, target)
            night_count = int((night_end - night_start).total_seconds() // 60) + 1
            night_eligible = (target, Session.NIGHT) not in isolated and scheduled_path_eligible(night_rows, night_start, night_count)
        except ValueError:
            night_eligible = False
        references = list(history)
        valid_references = sum(bool(item["night_eligible"]) for item in references)
        rolling_valid = valid_references >= MIN_REFERENCES
        day_rows = grouped.get((target, Session.DAY), {})
        try:
            day_start = classifier.session_open(target, Session.DAY)
            day_eligible = (target, Session.DAY) not in isolated and all(
                day_rows.get(day_start + timedelta(minutes=offset), False)
                for offset in (0, 1, 5, 60, 120, 180)
            )
        except ValueError:
            day_eligible = False
        chain_valid = record is not None and record.night_calendar_start_date is not None
        expected = chain_valid and night_eligible and day_eligible and rolling_valid
        reason = "EXPECTED_E" if expected else (
            "NO_UNIQUE_NIGHT_TO_TSE_CHAIN" if not chain_valid else
            "R004_SESSION_QUARANTINED" if (target, Session.NIGHT) in isolated or (target, Session.DAY) in isolated else
            "INSUFFICIENT_PRIOR_NIGHT_REFERENCES" if not rolling_valid else
            "INCOMPLETE_OR_INELIGIBLE_SCHEDULED_NIGHT" if not night_eligible else
            "MISSING_OR_INELIGIBLE_TSE_EXECUTION_FLAGS"
        )
        reasons[reason] += 1
        availability.append({
            "trade_date": target.isoformat(),
            "prior_scheduled_night_count": len(references),
            "prior_eligible_night_count": valid_references,
            "rolling_eligible": rolling_valid,
            "target_night_eligible": night_eligible,
            "target_day_execution_flags_eligible": day_eligible,
            "expected_E": expected,
            "reason": reason,
        })
        if expected:
            expected_e.append(availability[-1])
        history.append({"trade_date": target.isoformat(), "night_eligible": night_eligible})
    causal = {
        "prior_only": all(row["prior_scheduled_night_count"] <= LOOKBACK for row in availability),
        "no_backfill": all(row["prior_scheduled_night_count"] <= LOOKBACK for row in availability),
        "rolling_rule_exact": all(bool(row["rolling_eligible"]) == (int(row["prior_eligible_night_count"]) >= MIN_REFERENCES) for row in availability),
    }
    by_year = {
        str(year): {
            "rolling_eligible_days": sum(row["rolling_eligible"] for row in availability if str(row["trade_date"]).startswith(str(year))),
            "expected_E_days": sum(row["expected_E"] for row in availability if str(row["trade_date"]).startswith(str(year))),
        }
        for year in range(2021, 2026)
    }
    return {
        "status": "PASS" if len(expected_e) >= 750 and all(causal.values()) else "INCONCLUSIVE",
        "method": "schedule + R004 tick-grid isolation + is_eligible/missing flags; no OHLC, return, signal, order, fill, trade, or PnL columns selected",
        "selected_parquet_columns": META_COLUMNS,
        "rolling_window_scheduled_nights": LOOKBACK,
        "minimum_eligible_nights": MIN_REFERENCES,
        "days_with_at_least_160_eligible_prior_nights": sum(row["rolling_eligible"] for row in availability),
        "expected_E": len(expected_e),
        "availability_by_year": by_year,
        "reason_counts": dict(reasons),
        "r004_quarantined_sessions": [{"trade_date": day.isoformat(), "session": session.value} for day, session in sorted(isolated)],
        "causality_audit": causal,
        "daily_availability": availability,
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_cfg, base = load_project_config(ROOT / "config")
    if (base.execution.slippage_ticks, base.fees.jpy_per_side_per_contract) != (1, 30):
        raise ValueError("cost contract mismatch")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    inputs = manifest(data_cfg.gold_root)
    paths = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r067.py"), Path("scripts/run_r067_q001_night_to_day_information_continuation.py"), Path("tests/test_r067_q001.py")]
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R067-Q002",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "predecessor": "R067-Q001 ended INCONCLUSIVE before economic evaluation because its exact 120 scheduled-night / 100-valid rule produced E=0. No Q001 price, event direction, future return, order, fill, PnL, or statistic is reused.",
        "only_change": "Replace only the rolling night-only reference window from exactly 120 scheduled nights/minimum 100 eligible to exactly 240 scheduled nights/minimum 160 eligible. Target day exclusion, no older-date backfill, nearest-rank, and separation of the rolling reference from the trading-eligibility population remain unchanged.",
        "hypothesis": "A large, directionally efficient immediately preceding OSE night contains information not exhausted at TSE open and continues in that direction for 120 scheduled TSE minutes.",
        "rule": "Q001 cells, direction, TSE-start entry, 120-minute exit, controls, costs, delays, 60/180-minute holds, q65/q75 and e_q40/e_q60 sensitivities, bootstrap, FWL, and audits are unchanged; U is the target-excluded prior 240 scheduled nights and needs >=160 eligible nights.",
        "information_gates": "preflight expected E>=750 before price access; execution E>=750, B>=300, A/C/D/M>=60, A buy/sell>=25, each delay A>=85, q75 and e_q60 A>=60.",
        "decision": "If preflight fails: INCONCLUSIVE without price read. Otherwise, any information/economic gate failure is REJECT; all gates pass is Development-only INVESTIGATE.",
        "inputs": inputs,
        "source": snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        "implementation": {str(path): digest(ROOT / path) for path in paths},
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "campaign_manifest.json", {"experiment_id": IDENTIFIER, "status": "preregistered_before_price_access", "started_at": datetime.now(timezone.utc).isoformat(), "plan_hash": canonical_hash(plan), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {
        "pytest": [executable, "-m", "pytest", "tests/test_r067_q001.py", "-q"],
        "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r067.py", "tests/test_r067_q001.py", str(paths[0])],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r067.py"],
    }
    validation = {name: {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr} for name, command in commands.items() for result in [run(command, cwd=ROOT, text=True, capture_output=True, check=False)]}
    validation["status"] = "PASS" if all(cast(dict[str, int], value)["returncode"] == 0 for value in validation.values()) else "BLOCKED"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("pre-execution validation failed")

    eligibility = preflight(classifier, calendar, data_cfg.gold_root)
    write_json(OUT / "preflight.json", eligibility)
    if eligibility["status"] != "PASS":
        write_json(OUT / "development_results.json", {"experiment_id": IDENTIFIER, "decision": "INCONCLUSIVE", "reason": "Preflight expected E<750 or causal eligibility audit failed; price/PnL were not read.", "information_gates": {"preflight_expected_E>=750": False}, "economic_gates": "NOT_EVALUATED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": "INCONCLUSIVE", "price_access": "NOT_PERFORMED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return

    data = load_split(data_cfg.gold_root, "development")
    view, isolation, isolated = Q001.quarantine(data)
    all_days, axis = Q001.fixed_axis(calendar, isolated)
    grouped = session_groups(view.bars)
    ledger = candidate_rows(classifier, all_days, grouped, isolated, lookback=LOOKBACK, min_references=MIN_REFERENCES)
    causality = {
        "prior_only": all(ref < row["trade_date"] for row in ledger for ref in cast(list[str], cast(dict[str, object], row["u"])["rolling_reference_trade_dates"])),
        "160_exact": all(bool(cast(dict[str, object], row["u"])["rolling_valid"]) == (int(cast(dict[str, object], row["u"])["rolling_valid_count"]) >= MIN_REFERENCES) for row in ledger),
        "no_backfill": all(int(cast(dict[str, object], row["u"])["rolling_scheduled_count"]) <= LOOKBACK for row in ledger),
    }
    write_json(OUT / "rolling_u_ledger.json", ledger)
    write_json(OUT / "rolling_causality_audit.json", causality)
    if not all(causality.values()):
        raise ValueError("rolling causality failed")
    lookup = {cast(str, row["trade_date"]): row for row in ledger}
    variants = {"base": (70, 50), "q65": (65, 50), "q75": (75, 50), "e_q40": (70, 40), "e_q60": (70, 60)}
    events = {name: [Q001.make_event(classifier, day, grouped, isolated, lookup[day.isoformat()], magnitude_quantile=magnitude, efficiency_quantile=efficiency) for day in axis] for name, (magnitude, efficiency) in variants.items()}
    base_events = events["base"]
    write_json(OUT / "all_eligible_event_ledger.json", base_events)
    write_json(OUT / "sensitivity_event_ledgers.json", {key: value for key, value in events.items() if key != "base"})
    write_json(OUT / "eligibility_audit.json", {"fixed_axis": [day.isoformat() for day in axis], "E": sum(row["status"] == "E" for row in base_events), "preflight_expected_E": eligibility["expected_E"], "reason_counts": dict(Counter(str(row.get("reason")) for row in base_events)), "R004_isolation": isolation})

    fee, multiplier, tick = base.fees.jpy_per_side_per_contract, instrument.instrument.contract_multiplier, instrument.instrument.tick_size
    specs = {"A": (base_events, "follow", "entry", "exit120", 1, fee), "B": (base_events, "follow", "entry", "exit120", 1, fee), "C": (base_events, "follow", "entry", "exit120", 1, fee), "D": (base_events, "follow", "entry", "exit120", 1, fee), "M": (base_events, "follow", "entry", "exit120", 1, fee), "A_fade": (base_events, "fade", "entry", "exit120", 1, fee), "A_buy": (base_events, "long", "entry", "exit120", 1, fee), "A_sell": (base_events, "short", "entry", "exit120", 1, fee), "A0": (base_events, "follow", "entry", "exit120", 0, fee), "A2": (base_events, "follow", "entry", "exit120", 2, fee), "A3": (base_events, "follow", "entry", "exit120", 3, fee), "A_fee2": (base_events, "follow", "entry", "exit120", 1, fee * 2), "A_delay1": (base_events, "follow", "delay1_entry", "exit120", 1, fee), "A_delay5": (base_events, "follow", "delay5_entry", "exit120", 1, fee), "A_h60": (base_events, "follow", "entry", "exit60", 1, fee), "A_h180": (base_events, "follow", "entry", "exit180", 1, fee), "A_q65": (events["q65"], "follow", "entry", "exit120", 1, fee), "A_q75": (events["q75"], "follow", "entry", "exit120", 1, fee), "A_e40": (events["e_q40"], "follow", "entry", "exit120", 1, fee), "A_e60": (events["e_q60"], "follow", "entry", "exit120", 1, fee)}
    trades: dict[str, Any] = {}
    ledgers: dict[str, list[dict[str, object]]] = {}
    results: dict[str, dict[str, object]] = {}
    values: dict[str, Any] = {}
    for name, spec in specs.items():
        condition_trades, condition_ledger = Q001.run_condition(name, *spec, multiplier, tick)
        trades[name], ledgers[name], values[name] = condition_trades, condition_ledger, Q001.aligned(condition_trades, axis)
        results[name] = {"trade_count": len(condition_trades), "overall": Q001.ledger_metrics(condition_trades)}
        folder = OUT / name
        folder.mkdir()
        write_json(folder / "events.json", condition_ledger)
        write_json(folder / "trades.json", [{"trade_id": trade.trade_id, "trade_date": trade.trade_date.isoformat(), "side": trade.side.value, "entry_ts_jst": trade.entry_ts.isoformat(), "exit_ts_jst": trade.exit_ts.isoformat(), "gross_pnl_jpy": trade.gross_pnl_jpy, "fees_jpy": trade.fees_jpy, "slippage_cost_jpy": trade.slippage_cost_jpy, "net_pnl_jpy": trade.net_pnl_jpy} for trade in condition_trades])
        write_json(folder / "research_metrics.json", results[name])
        write_json(folder / "daily_net_pnl_aligned.json", {day.isoformat(): int(values[name][0][index]) for index, day in enumerate(axis)})
    audit = {"cells_exclusive": all(sum(row.get("cell") == cell for cell in ("A", "C", "D", "M")) <= 1 for row in base_events), "A_subset_B": all(not Q001.choose(row, "A") or Q001.choose(row, "B") for row in base_events), "controls_same_A_event_time": all(a["condition_eligible"] == b["condition_eligible"] and a.get("planned_entry_jst") == b.get("planned_entry_jst") and a.get("planned_exit120_jst") == b.get("planned_exit120_jst") for a, b in zip(ledgers["A"], ledgers["A_fade"], strict=True)), "A_fade_opposite": all(a.get("side") != b.get("side") for a, b in zip(ledgers["A"], ledgers["A_fade"], strict=True) if a["condition_eligible"]), "delays_nonextended": all(x.exit_ts == y.exit_ts for x, y in zip(trades["A_delay1"], trades["A"], strict=True)) and all(x.exit_ts == y.exit_ts for x, y in zip(trades["A_delay5"], trades["A"], strict=True)), "one_trade_max": all(len(items) <= 1111 for items in trades.values()), "accounting": all(item.net_pnl_jpy == item.gross_pnl_jpy - item.fees_jpy for items in trades.values() for item in items), "fixed_axis": all(len(value[0]) == 1111 for value in values.values()), "causal_reclassification": all(cast(dict[str, object], row["u"])["rolling_valid"] == (cast(dict[str, object], row["u"])["rolling_valid_count"] >= MIN_REFERENCES) for row in ledger)}
    write_json(OUT / "execution_accounting_audit.json", {"status": "PASS" if all(audit.values()) else "BLOCKED", "checks": audit, "note": "scheduled opens use one adverse fill per side; Gross includes slippage once; Net=Gross-fees"})
    if not all(audit.values()):
        raise ValueError("execution/accounting audit failed")
    directions = {side: sum(item.side.value == side for item in trades["A"]) for side in ("long", "short")}
    info = {"E>=750": sum(row["status"] == "E" for row in base_events) >= 750, "B>=300": len(trades["B"]) >= 300, "A/C/D/M>=60": all(len(trades[name]) >= 60 for name in ("A", "C", "D", "M")), "A_buy_sell>=25": all(value >= 25 for value in directions.values()), "delays_A>=85": all(len(trades[name]) >= 85 for name in ("A_delay1", "A_delay5")), "q75_e_q60_A>=60": all(len(trades[name]) >= 60 for name in ("A_q75", "A_e60"))}
    if not all(info.values()):
        write_json(OUT / "development_results.json", {"experiment_id": IDENTIFIER, "decision": "REJECT", "information_gates": info, "economic_gates": "NOT_EVALUATED_INFORMATION_INSUFFICIENT", "conditions": results, "A_direction_count": directions, "fwl_status": "NOT_EVALUATED_INFORMATION_INSUFFICIENT", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": "REJECT", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return
    Q001.OUT = OUT
    main_boot, index = Q001.bootstrap({name: values[name] for name in ("A", "B", "C", "D", "A_fade", "A_buy", "A_sell")}, 20)
    block10, _ = Q001.bootstrap({name: values[name] for name in ("A", "B", "C", "D", "A_fade", "A_buy", "A_sell")}, 10)
    block40, _ = Q001.bootstrap({name: values[name] for name in ("A", "B", "C", "D", "A_fade", "A_buy", "A_sell")}, 40)
    __import__("numpy").save(OUT / "bootstrap_mbb_indices_block20.npy", index)
    try:
        main_boot["delta"] = Q001.delta(base_events, multiplier, axis)
        fwl = "PASS"
    except Q001.R067QNotIdentifiableError as exc:
        main_boot["delta"] = {"status": "BLOCKED", "error": str(exc)}
        fwl = "BLOCKED"
    write_json(OUT / "bootstrap.json", {"main_block20": main_boot, "sensitivity_block10": block10, "sensitivity_block40": block40})
    def positive(name: str) -> bool:
        return int(cast(dict[str, object], results[name]["overall"])["net_pnl_jpy"]) > 0 and float(cast(dict[str, object], results[name]["overall"])["profit_factor"] or 0) > 1
    def lower(book: dict[str, object], name: str) -> bool:
        return fwl == "PASS" and float(cast(dict[str, object], book[name])["ci95_percentile_linear"][0]) > 0
    years = {str(year): sum(item.net_pnl_jpy for item in trades["A"] if item.trade_date.year == year) for year in range(2021, 2026)}
    months = {f"{year}-{month:02d}": sum(item.net_pnl_jpy for item in trades["A"] if item.trade_date.year == year and item.trade_date.month == month) for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}
    econ = {"A_net_pf_positive": positive("A"), "all_main_comparisons_and_delta_positive": all(lower(main_boot, name) for name in ("A_daily_mean_net_jpy", "A_minus_C_conditional_per_trade_jpy", "A_minus_D_conditional_per_trade_jpy", "A_minus_B_conditional_per_trade_jpy", "A_minus_A_fade_conditional_per_trade_jpy", "A_minus_A_buy_conditional_per_trade_jpy", "A_minus_A_sell_conditional_per_trade_jpy", "delta")), "all_cost_delay_hold_threshold_sensitivities_positive_pf": all(positive(name) for name in ("A2", "A3", "A_fee2", "A_delay1", "A_delay5", "A_h60", "A_h180", "A_q65", "A_q75", "A_e40", "A_e60")), "block10_40_A_AminusD_positive": all(lower(book, name) for book in (block10, block40) for name in ("A_daily_mean_net_jpy", "A_minus_D_conditional_per_trade_jpy")), "three_positive_years_2021_2024": sum(years[str(year)] > 0 for year in range(2021, 2025)) >= 3, "positive_months>=27": sum(value > 0 for value in months.values()) >= 27, "top10_removed_positive": sum(item.net_pnl_jpy for item in trades["A"]) - sum(sorted((item.net_pnl_jpy for item in trades["A"] if item.net_pnl_jpy > 0), reverse=True)[:10]) > 0}
    decision = "BLOCKED" if fwl == "BLOCKED" else "INVESTIGATE" if all(econ.values()) else "REJECT"
    write_json(OUT / "development_results.json", {"experiment_id": IDENTIFIER, "decision": decision, "information_gates": info, "economic_gates": econ, "conditions": results, "A_year_net_jpy": years, "A_month_net_jpy": months, "positive_months": sum(value > 0 for value in months.values()), "A_direction_count": directions, "fwl_status": fwl, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": decision, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
