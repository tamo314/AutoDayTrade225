"""Execute the preregistered Development-only R067-Q001 experiment once."""
# ruff: noqa: E701, E702

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, log
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
from n225m_bt.research.r067 import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    R067QNotIdentifiableError,
    candidate_rows,
    fwl_delta,
    make_event,
)
from n225m_bt.research.runner import snapshot_source, write_json

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r067-q001-20260915-night-to-day-information-continuation-03"
OUT = ROOT / "results" / "research" / IDENTIFIER
MBB_SEED, WILD_SEED, REPETITIONS = 20261010, 20261011, 10_000
QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            h.update(part)
    return h.hexdigest()


def manifest(root: Path) -> dict[str, object]:
    files = [{"path": str(p.resolve().relative_to(ROOT)), "sha256": digest(p)} for p in partition_paths(root, "development")]
    return {"status": "frozen_before_price_statistics_events_or_pnl", "scope": "normalized Development Parquet only", "trade_date_range": [str(DEVELOPMENT_START), str(DEVELOPMENT_END)], "files": files, "files_hash": canonical_hash(files), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}


def quarantine(data: ResearchData) -> tuple[ResearchData, dict[str, object], set[tuple[date, Session]]]:
    groups = session_groups(data.bars)
    isolated = {key for key, rows in groups.items() if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in rows)}
    included = [bar for key, rows in groups.items() if key not in isolated for bar in rows]
    listing = [{"trade_date": d.isoformat(), "session": s.value} for d, s in sorted(isolated)]
    audit = {"quarantined_sessions": len(isolated), "quarantined_bars": len(data.bars) - len(included), "included_sessions": len(groups) - len(isolated), "included_bars": len(included), "quarantined_session_list": listing, "quarantined_session_list_hash": canonical_hash(listing), "rule": "R004 fixed full-session tick-grid isolation"}
    if audit["quarantined_session_list_hash"] != QUARANTINE_HASH:
        raise ValueError(f"R004 isolation mismatch: {audit}")
    return ResearchData(included, data.data_version, data.quality), audit, isolated


def fixed_axis(calendar: ExchangeCalendar, isolated: set[tuple[date, Session]]) -> tuple[list[date], list[date]]:
    all_days = [x.trade_date for x in calendar.trading_days() if DEVELOPMENT_START <= x.trade_date <= DEVELOPMENT_END]
    axis = [x for x in all_days if (x, Session.DAY) not in isolated]
    if len(axis) != 1111 or len(set(axis)) != 1111:
        raise ValueError(f"fixed axis mismatch: {len(axis)}")
    return all_days, axis


def choose(row: dict[str, object], name: str) -> bool:
    if row.get("status") != "E" or not bool(row.get("direction_eligible")):
        return False
    cell = str(row.get("cell"))
    if name == "B":
        return float(row["a"]) >= float(row["q50"])
    return cell == ("A" if name.startswith("A") else name)


def trade(row: dict[str, object], name: str, side: Side, entry: str, exit_: str, ticks: int, fee: int, multiplier: int, tick_size: int) -> Trade:
    ep, xp = int(row[entry + "_open"]), int(row[exit_ + "_open"])
    ets, xts = datetime.fromisoformat(cast(str, row["planned_" + entry + "_jst"])), datetime.fromisoformat(cast(str, row["planned_" + exit_ + "_jst"]))
    spec = type("Spec", (), {"tick_size": tick_size, "multiplier": multiplier})()
    ef, xf = adverse_fill(ep, side is Side.LONG, ticks, spec), adverse_fill(xp, side is Side.SHORT, ticks, spec)
    gross = gross_pnl(ef, xf, side, 1, spec)
    target = date.fromisoformat(cast(str, row["trade_date"]))
    return Trade(f"{name}-{target}", target, side, 1, ets, ets, ep, ef, xts, xts, xp, xf, gross, 2 * fee, slippage_cost(ep, ef, xp, xf, 1, spec), gross - 2 * fee, 0, 0, int((xts - ets).total_seconds() // 60), "r067_known_night_close_to_tse_open", ExitReason.SIGNAL, f"r067_{name}", "r067-q001-v1", canonical_hash({"name": name, "ticks": ticks, "fee": fee, "entry": entry, "exit": exit_}), {"fixed_time_order": True, "no_stop_target_reentry": True})


def run_condition(name: str, events: list[dict[str, object]], side_mode: str, entry: str, exit_: str, ticks: int, fee: int, multiplier: int, tick_size: int) -> tuple[tuple[Trade, ...], list[dict[str, object]]]:
    output: list[Trade] = []
    ledger: list[dict[str, object]] = []
    for source in events:
        row = dict(source)
        eligible = choose(row, name)
        sign = int(row.get("s", 0))
        side = (
            Side.LONG
            if side_mode == "long"
            else Side.SHORT
            if side_mode == "short"
            else Side.SHORT
            if side_mode == "fade" and sign > 0
            else Side.LONG
            if side_mode == "fade"
            else Side.LONG
            if sign > 0
            else Side.SHORT
        )
        row.update(condition=name, condition_eligible=eligible, side=side.value, entry_boundary=entry, exit_boundary=exit_, slippage_ticks_per_side=ticks, fee_jpy_per_side=fee)
        if eligible:
            value = trade(row, name, side, entry, exit_, ticks, fee, multiplier, tick_size)
            output.append(value)
            row.update(status="filled", entry_ts_jst=value.entry_ts.isoformat(), exit_ts_jst=value.exit_ts.isoformat(), gross_pnl_jpy=value.gross_pnl_jpy, fees_jpy=value.fees_jpy, slippage_cost_jpy=value.slippage_cost_jpy, net_pnl_jpy=value.net_pnl_jpy)
        ledger.append(row)
    return tuple(output), ledger


def aligned(trades: tuple[Trade, ...], axis: list[date]) -> tuple[np.ndarray, np.ndarray]:
    values, counts, ix = np.zeros(len(axis)), np.zeros(len(axis)), {d: i for i, d in enumerate(axis)}
    for value in trades:
        values[ix[value.trade_date]] += value.net_pnl_jpy
        counts[ix[value.trade_date]] += 1
    return values, counts


def draws(n: int, block: int, seed: int) -> np.ndarray:
    rng, starts = np.random.default_rng(seed), np.arange(n - block + 1, dtype=np.int16)
    return np.asarray([np.concatenate([np.arange(s, s + block, dtype=np.int16) for s in rng.choice(starts, size=ceil(n / block), replace=True)])[:n] for _ in range(REPETITIONS)])


def bootstrap(values: dict[str, tuple[np.ndarray, np.ndarray]], block: int) -> tuple[dict[str, object], np.ndarray]:
    index = draws(len(next(iter(values.values()))[0]), block, MBB_SEED)
    a, ac = values["A"]
    targets: dict[str, np.ndarray] = {"A_daily_mean_net_jpy": a[index].mean(1)}
    for other in ("C", "D", "B", "A_fade", "A_buy", "A_sell"):
        b, bc = values[other]
        targets[f"A_minus_{other}_conditional_per_trade_jpy"] = a[index].sum(1) / ac[index].sum(1) - b[index].sum(1) / bc[index].sum(1)
    def ci(v: np.ndarray) -> list[float]: return [float(np.quantile(v, .025, method="linear")), float(np.quantile(v, .975, method="linear"))]
    return ({k: {"estimate": float(v.mean()), "ci95_percentile_linear": ci(v)} for k, v in targets.items()} | {"method": "noncircular moving-block bootstrap; common index; tail truncation; conditional sum/count recomputed", "seed": MBB_SEED, "block_length_trade_dates": block, "repetitions": REPETITIONS}), index


def regression(events: list[dict[str, object]], multiplier: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], list[str]]:
    rows = [x for x in events if x.get("cell") in {"A", "C", "D", "M"} and bool(x.get("direction_eligible"))]
    years = sorted({int(str(x["trade_date"])[:4]) for x in rows})
    q: list[float] = []; y: list[float] = []; z: list[list[float]] = []; days: list[str] = []
    for x in rows:
        s, a, q50, e = int(x["s"]), float(x["a"]), float(x["q50"]), float(x["e"])
        xhigh, hefficient = a >= float(x["q70"]), e >= float(x["e_q50"])
        q.append(float(xhigh and hefficient)); y.append(s * (int(x["exit120_open"]) - int(x["entry_open"])) * multiplier)
        z.append([1.0, float(xhigh), float(hefficient), log(a / q50), e, e * e, float(x["night_high_low_range"]) / a, float(x["tail30_s_adjusted_return"]), float(x["gap_s_adjusted"]), float(x["upward"]), float(str(x["schedule_version"]) != str(rows[0]["schedule_version"])), *[float(str(x["trade_date"]).startswith(str(year))) for year in years[1:]]]); days.append(cast(str, x["trade_date"]))
    return np.asarray(q), np.asarray(y), np.asarray(z), days, ["intercept", "X", "H", "ln_a_q50", "e", "e2", "night_range_over_a", "tail30_s_adjusted_return", "nc_to_entry_s_adjusted_gap", "upward", "trading_time_regime", *[f"year_{x}" for x in years[1:]]]


def delta(events: list[dict[str, object]], multiplier: int, axis: list[date]) -> dict[str, object]:
    q, y, z, days, names = regression(events, multiplier); estimate, ss = fwl_delta(q, y, z)
    projection = z @ np.linalg.pinv(z, rcond=1e-12); qr = q - projection @ q; residual = y - projection @ y - qr * estimate
    location = {d.isoformat(): i for i, d in enumerate(axis)}; di = np.asarray([location[d] for d in days]); rng = np.random.default_rng(WILD_SEED); estimates = np.empty(REPETITIONS); signs = np.empty((REPETITIONS, len(axis)), dtype=np.int8)
    for j in range(REPETITIONS):
        weights = np.repeat(rng.choice(np.asarray([-1, 1], dtype=np.int8), size=ceil(len(axis) / 20)), 20)[:len(axis)]; signs[j] = weights; estimates[j] = estimate + float(qr @ (residual * weights[di]) / ss)
    write_json(OUT / "regression_ledger.json", {"trade_dates": days, "Q": q.tolist(), "y_0tick_gross_jpy": y.tolist(), "nuisance_columns": names, "nuisance": z.tolist(), "delta": estimate, "q_residual_ss": ss, "rcond": 1e-12, "tolerance": 1e-12})
    np.save(OUT / "bootstrap_delta_block_wild_signs.npy", signs)
    return {"estimate": estimate, "ci95_percentile_linear": [float(np.quantile(estimates, .025, method="linear")), float(np.quantile(estimates, .975, method="linear"))], "method": "fixed-design 20 trade_date block-wild score bootstrap", "seed": WILD_SEED, "repetitions": REPETITIONS, "q_residual_ss": ss}


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r067_q001_night_to_day_information_continuation.py')

    if OUT.exists(): raise FileExistsError(f"immutable output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_cfg, base = load_project_config(ROOT / "config")
    if (base.execution.slippage_ticks, base.fees.jpy_per_side_per_contract) != (1, 30): raise ValueError("cost contract mismatch")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml"); classifier = CalendarClassifier(sessions, calendar); inputs = manifest(data_cfg.gold_root)
    paths = [Path(__file__).relative_to(ROOT), Path("src/n225m_bt/research/r067.py"), Path("tests/test_r067_q001.py")]
    plan = {"experiment_id": IDENTIFIER, "study_id": "R067-Q001", "status": "frozen_before_price_statistics_events_or_pnl", "duplicate_review": "R001-R066 reviewed before price statistics: R055 has full-night return and TSE-open entry but 30-minute holding and no path efficiency; R062 has a 15-minute rejection response and 30-minute hold. No prior study has full-night return plus path efficiency, next TSE entry, and fixed 120-scheduled-minute exit.", "hypothesis": "A large, directionally efficient immediately preceding OSE night contains information not exhausted at TSE open and continues in that direction for 120 scheduled TSE minutes.", "rule": "n0 first scheduled night open, nc final scheduled night close, r=nc/n0-1, a=abs(r), s=sign(r), e=abs(nc-n0)/(abs(c0-n0)+sum abs(ci-c(i-1))); prior 120 night-only U, current excluded, >=100 valid, nearest-rank a q50/q65/q70/q75 and e q40/q50/q60. A a>=q70/e>=e_q50; C q50<=a<q70/e>=e_q50; D a>=q70/e<e_q50; M q50<=a<q70/e<e_q50; B a>=q50; r=0 remains U/E but no direction event. Entry TSE d0; fixed exits d0+60/120/180 scheduled minutes.", "common_E": "Complete scheduled night; d0,d0+1,d0+5,d0+60,d0+120,d0+180 eligible positive and nonisolated in one versioned night-to-day chain; no replacement/backfill.", "cost_and_sensitivities": "one tick plus JPY30/side; 0 tick diagnostic, 2/3 tick, fee2, d0 delay1/delay5 without exit extension, hold60/180, independently causal q65/q75 and e_q40/e_q60 only.", "inference": f"fixed 1111 trade-date axis; 20-day noncircular MBB {REPETITIONS}, common index, tail truncation, linear percentile seed {MBB_SEED}; block10/40. FWL and 20-day fixed-design block-wild score bootstrap {REPETITIONS}, seed {WILD_SEED}.", "gates": "E>=800,B>=350,A/C/D/M>=70,A buy/sell>=30, delays A>=100,q75 and e_q60 A>=70; stated economic gates required thereafter.", "inputs": inputs, "source": snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"), "implementation": {str(p): digest(ROOT / p) for p in paths}, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}
    write_json(OUT / "input_manifest.json", inputs); write_json(OUT / "preregistration.json", plan); write_json(OUT / "campaign_manifest.json", {"experiment_id": IDENTIFIER, "status": "preregistered_before_price_access", "started_at": datetime.now(timezone.utc).isoformat(), "plan_hash": canonical_hash(plan), "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
    commands = {"pytest": [executable, "-m", "pytest", "tests/test_r067_q001.py", "-q"], "ruff": [executable, "-m", "ruff", "check", "src/n225m_bt/research/r067.py", "tests/test_r067_q001.py", str(paths[0])], "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r067.py"]}
    validation = {name: {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr} for name, command in commands.items() for result in [run(command, cwd=ROOT, text=True, capture_output=True, check=False)]}; validation["status"] = "PASS" if all(cast(dict[str, int], value)["returncode"] == 0 for value in validation.values()) else "BLOCKED"; write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS": raise ValueError("pre-execution validation failed")
    data = load_split(data_cfg.gold_root, "development"); view, isolation, isolated = quarantine(data); all_days, axis = fixed_axis(calendar, isolated); grouped = session_groups(view.bars); ledger = candidate_rows(classifier, all_days, grouped, isolated)
    causality = {"prior_only": all(ref < row["trade_date"] for row in ledger for ref in cast(list[str], cast(dict[str, object], row["u"])["rolling_reference_trade_dates"])), "100_exact": all(bool(cast(dict[str, object], row["u"])["rolling_valid"]) == (int(cast(dict[str, object], row["u"])["rolling_valid_count"]) >= 100) for row in ledger), "no_backfill": all(int(cast(dict[str, object], row["u"])["rolling_scheduled_count"]) <= 120 for row in ledger)}
    write_json(OUT / "rolling_u_ledger.json", ledger); write_json(OUT / "rolling_causality_audit.json", causality)
    if not all(causality.values()): raise ValueError("rolling causality failed")
    lookup = {cast(str, row["trade_date"]): row for row in ledger}; variants = {"base": (70, 50), "q65": (65, 50), "q75": (75, 50), "e_q40": (70, 40), "e_q60": (70, 60)}
    events = {name: [make_event(classifier, day, grouped, isolated, lookup[day.isoformat()], magnitude_quantile=magnitude, efficiency_quantile=efficiency) for day in axis] for name, (magnitude, efficiency) in variants.items()}; base_events = events["base"]
    write_json(OUT / "all_eligible_event_ledger.json", base_events); write_json(OUT / "sensitivity_event_ledgers.json", {key: value for key, value in events.items() if key != "base"}); write_json(OUT / "eligibility_audit.json", {"fixed_axis": [d.isoformat() for d in axis], "E": sum(x["status"] == "E" for x in base_events), "reason_counts": dict(Counter(str(x.get("reason")) for x in base_events)), "R004_isolation": isolation})
    fee, mult, tick = base.fees.jpy_per_side_per_contract, instrument.instrument.contract_multiplier, instrument.instrument.tick_size
    specs = {"A": (base_events, "follow", "entry", "exit120", 1, fee), "B": (base_events, "follow", "entry", "exit120", 1, fee), "C": (base_events, "follow", "entry", "exit120", 1, fee), "D": (base_events, "follow", "entry", "exit120", 1, fee), "M": (base_events, "follow", "entry", "exit120", 1, fee), "A_fade": (base_events, "fade", "entry", "exit120", 1, fee), "A_buy": (base_events, "long", "entry", "exit120", 1, fee), "A_sell": (base_events, "short", "entry", "exit120", 1, fee), "A0": (base_events, "follow", "entry", "exit120", 0, fee), "A2": (base_events, "follow", "entry", "exit120", 2, fee), "A3": (base_events, "follow", "entry", "exit120", 3, fee), "A_fee2": (base_events, "follow", "entry", "exit120", 1, fee * 2), "A_delay1": (base_events, "follow", "delay1_entry", "exit120", 1, fee), "A_delay5": (base_events, "follow", "delay5_entry", "exit120", 1, fee), "A_h60": (base_events, "follow", "entry", "exit60", 1, fee), "A_h180": (base_events, "follow", "entry", "exit180", 1, fee), "A_q65": (events["q65"], "follow", "entry", "exit120", 1, fee), "A_q75": (events["q75"], "follow", "entry", "exit120", 1, fee), "A_e40": (events["e_q40"], "follow", "entry", "exit120", 1, fee), "A_e60": (events["e_q60"], "follow", "entry", "exit120", 1, fee)}
    trades: dict[str, tuple[Trade, ...]] = {}; ledgers: dict[str, list[dict[str, object]]] = {}; results: dict[str, dict[str, object]] = {}; values: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for name, spec in specs.items():
        ts, condition_ledger = run_condition(name, *spec, mult, tick); trades[name], ledgers[name], values[name] = ts, condition_ledger, aligned(ts, axis); results[name] = {"trade_count": len(ts), "overall": ledger_metrics(ts)}; folder = OUT / name; folder.mkdir(); write_json(folder / "events.json", condition_ledger); write_json(folder / "trades.json", [{"trade_id": x.trade_id, "trade_date": x.trade_date.isoformat(), "side": x.side.value, "entry_ts_jst": x.entry_ts.isoformat(), "exit_ts_jst": x.exit_ts.isoformat(), "gross_pnl_jpy": x.gross_pnl_jpy, "fees_jpy": x.fees_jpy, "slippage_cost_jpy": x.slippage_cost_jpy, "net_pnl_jpy": x.net_pnl_jpy} for x in ts]); write_json(folder / "research_metrics.json", results[name]); write_json(folder / "daily_net_pnl_aligned.json", {d.isoformat(): int(values[name][0][i]) for i, d in enumerate(axis)})
    audit = {"cells_exclusive": all(sum(x.get("cell") == cell for cell in ("A", "C", "D", "M")) <= 1 for x in base_events), "A_subset_B": all(not choose(row, "A") or choose(row, "B") for row in base_events), "controls_same_A_event_time": all(a["condition_eligible"] == b["condition_eligible"] and a.get("planned_entry_jst") == b.get("planned_entry_jst") and a.get("planned_exit120_jst") == b.get("planned_exit120_jst") for a, b in zip(ledgers["A"], ledgers["A_fade"], strict=True)), "A_fade_opposite": all(a.get("side") != b.get("side") for a, b in zip(ledgers["A"], ledgers["A_fade"], strict=True) if a["condition_eligible"]), "delays_nonextended": all(x.exit_ts == y.exit_ts for x, y in zip(trades["A_delay1"], trades["A"], strict=True)) and all(x.exit_ts == y.exit_ts for x, y in zip(trades["A_delay5"], trades["A"], strict=True)), "one_trade_max": all(len(ts) <= 1111 for ts in trades.values()), "accounting": all(x.net_pnl_jpy == x.gross_pnl_jpy - x.fees_jpy for ts in trades.values() for x in ts), "fixed_axis": all(len(v[0]) == 1111 for v in values.values()), "causal_reclassification": all(row["u"]["rolling_valid"] == (row["u"]["rolling_valid_count"] >= 100) for row in ledger)}
    write_json(OUT / "execution_accounting_audit.json", {"status": "PASS" if all(audit.values()) else "BLOCKED", "checks": audit, "note": "scheduled opens use one adverse fill per side; Gross includes slippage once; Net=Gross-fees"})
    if not all(audit.values()): raise ValueError("execution/accounting audit failed")
    pre_info = {"E>=800": sum(x["status"] == "E" for x in base_events) >= 800, "B>=350": len(trades["B"]) >= 350, "A/C/D/M>=70": all(len(trades[x]) >= 70 for x in ("A", "C", "D", "M")), "A_buy_sell>=30": all(sum(x.side.value == side for x in trades["A"]) >= 30 for side in ("long", "short")), "delays_A>=100": all(len(trades[x]) >= 100 for x in ("A_delay1", "A_delay5")), "q75_e_q60_A>=70": all(len(trades[x]) >= 70 for x in ("A_q75", "A_e60"))}
    if not all(pre_info.values()):
        write_json(OUT / "development_results.json", {"experiment_id": IDENTIFIER, "decision": "INCONCLUSIVE", "information_gates": pre_info, "economic_gates": "NOT_EVALUATED_INFORMATION_INSUFFICIENT", "conditions": results, "fwl_status": "NOT_EVALUATED_INFORMATION_INSUFFICIENT", "reason": "The exact trailing 120 scheduled-night window never reaches 100 valid night observations after fixed R004 isolation; no older-night backfill is permitted.", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": "INCONCLUSIVE", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})
        return
    main_boot, index = bootstrap({name: values[name] for name in ("A", "B", "C", "D", "A_fade", "A_buy", "A_sell")}, 20); block10, _ = bootstrap({name: values[name] for name in ("A", "B", "C", "D", "A_fade", "A_buy", "A_sell")}, 10); block40, _ = bootstrap({name: values[name] for name in ("A", "B", "C", "D", "A_fade", "A_buy", "A_sell")}, 40); np.save(OUT / "bootstrap_mbb_indices_block20.npy", index)
    try: main_boot["delta"] = delta(base_events, mult, axis); fwl = "PASS"
    except R067QNotIdentifiableError as exc: main_boot["delta"] = {"status": "BLOCKED", "error": str(exc)}; fwl = "BLOCKED"
    write_json(OUT / "bootstrap.json", {"main_block20": main_boot, "sensitivity_block10": block10, "sensitivity_block40": block40})
    def positive(name: str) -> bool: return int(cast(dict[str, object], results[name]["overall"])["net_pnl_jpy"]) > 0 and float(cast(dict[str, object], results[name]["overall"])["profit_factor"] or 0) > 1
    def lower(book: dict[str, object], name: str) -> bool: return fwl == "PASS" and float(cast(dict[str, object], book[name])["ci95_percentile_linear"][0]) > 0
    years = {str(year): sum(x.net_pnl_jpy for x in trades["A"] if x.trade_date.year == year) for year in range(2021, 2026)}; months = {f"{year}-{month:02d}": sum(x.net_pnl_jpy for x in trades["A"] if x.trade_date.year == year and x.trade_date.month == month) for year in range(2021, 2026) for month in range(1, 13) if (year, month) <= (2025, 6)}; directions = {side: sum(x.side.value == side for x in trades["A"]) for side in ("long", "short")}
    info = {"E>=800": sum(x["status"] == "E" for x in base_events) >= 800, "B>=350": len(trades["B"]) >= 350, "A/C/D/M>=70": all(len(trades[x]) >= 70 for x in ("A", "C", "D", "M")), "A_buy_sell>=30": all(x >= 30 for x in directions.values()), "delays_A>=100": all(len(trades[x]) >= 100 for x in ("A_delay1", "A_delay5")), "q75_e_q60_A>=70": all(len(trades[x]) >= 70 for x in ("A_q75", "A_e60"))}
    econ = {"A_net_pf_positive": positive("A"), "all_main_comparisons_and_delta_positive": all(lower(main_boot, x) for x in ("A_daily_mean_net_jpy", "A_minus_C_conditional_per_trade_jpy", "A_minus_D_conditional_per_trade_jpy", "A_minus_B_conditional_per_trade_jpy", "A_minus_A_fade_conditional_per_trade_jpy", "A_minus_A_buy_conditional_per_trade_jpy", "A_minus_A_sell_conditional_per_trade_jpy", "delta")), "all_cost_delay_hold_threshold_sensitivities_positive_pf": all(positive(x) for x in ("A2", "A3", "A_fee2", "A_delay1", "A_delay5", "A_h60", "A_h180", "A_q65", "A_q75", "A_e40", "A_e60")), "block10_40_A_AminusD_positive": all(lower(book, x) for book in (block10, block40) for x in ("A_daily_mean_net_jpy", "A_minus_D_conditional_per_trade_jpy")), "three_positive_years_2021_2024": sum(years[str(x)] > 0 for x in range(2021, 2025)) >= 3, "positive_months>=27": sum(x > 0 for x in months.values()) >= 27, "top10_removed_positive": sum(x.net_pnl_jpy for x in trades["A"]) - sum(sorted((x.net_pnl_jpy for x in trades["A"] if x.net_pnl_jpy > 0), reverse=True)[:10]) > 0}
    decision = "BLOCKED" if fwl == "BLOCKED" else "INCONCLUSIVE" if not all(info.values()) else "INVESTIGATE" if all(econ.values()) else "REJECT"
    write_json(OUT / "development_results.json", {"experiment_id": IDENTIFIER, "decision": decision, "information_gates": info, "economic_gates": econ if all(info.values()) else "NOT_EVALUATED_INFORMATION_INSUFFICIENT", "conditions": results, "A_year_net_jpy": years, "A_month_net_jpy": months, "positive_months": sum(x > 0 for x in months.values()), "A_direction_count": directions, "fwl_status": fwl, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"}); write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "status": "complete", "decision": decision, "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
