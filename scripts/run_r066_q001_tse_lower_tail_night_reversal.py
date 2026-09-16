"""Execute Development-only preregistered R066-Q001 exactly once."""

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
from n225m_bt.research.r066 import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    R066QNotIdentifiableError,
    candidate_rows,
    fwl_delta,
    make_event,
)
from n225m_bt.research.runner import snapshot_source, write_json

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r066-q001-20260915-tse-lower-tail-night-reversal-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
MBB_SEED, WILD_SEED, REPETITIONS = 20261008, 20261009, 10_000
EXPECTED_QUARANTINE_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    return h.hexdigest()


def input_manifest(root: Path) -> dict[str, object]:
    files = [
        {"path": str(p.resolve().relative_to(ROOT)), "sha256": digest(p)}
        for p in partition_paths(root, "development")
    ]
    return {
        "status": "frozen_before_price_statistics_events_or_pnl",
        "scope": "normalized Development Parquet only; OOS and Final Holdout prohibited",
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
        if any("TICK_GRID_VIOLATION" in b.quality_flags for b in rows)
    }
    included = [b for key, rows in grouped.items() if key not in isolated for b in rows]
    listing = [{"trade_date": d.isoformat(), "session": s.value} for d, s in sorted(isolated)]
    audit = {
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list": listing,
        "quarantined_session_list_hash": canonical_hash(listing),
        "rule": "R004 fixed full-session tick-grid isolation",
    }
    if audit["quarantined_session_list_hash"] != EXPECTED_QUARANTINE_HASH:
        raise ValueError(f"R004 isolation mismatch: {audit}")
    return ResearchData(included, data.data_version, data.quality), audit, isolated


def axis(
    calendar: ExchangeCalendar, isolated: set[tuple[date, Session]]
) -> tuple[list[date], list[date]]:
    all_days = [
        x.trade_date
        for x in calendar.trading_days()
        if DEVELOPMENT_START <= x.trade_date <= DEVELOPMENT_END
    ]
    fixed = [x for x in all_days if (x, Session.DAY) not in isolated]
    if len(fixed) != 1111 or len(set(fixed)) != 1111:
        raise ValueError(f"fixed axis mismatch {len(fixed)}")
    return all_days, fixed


def execute(
    row: dict[str, object],
    name: str,
    side: Side,
    entry: str,
    exit_: str,
    ticks: int,
    fee: int,
    multiplier: int,
    tick: int,
) -> Trade:
    ets, xts = (
        datetime.fromisoformat(cast(str, row[entry + "_jst"])),
        datetime.fromisoformat(cast(str, row[exit_ + "_jst"])),
    )
    ep, xp = int(row[entry + "_open"]), int(row[exit_ + "_open"])
    spec = type("Spec", (), {"tick_size": tick, "multiplier": multiplier})()
    ef, xf = (
        adverse_fill(ep, side is Side.LONG, ticks, spec),
        adverse_fill(xp, side is Side.SHORT, ticks, spec),
    )
    gross = gross_pnl(ef, xf, side, 1, spec)
    target = date.fromisoformat(cast(str, row["trade_date"]))
    return Trade(
        f"{name}-{target}",
        target,
        side,
        1,
        ets,
        ets,
        ep,
        ef,
        xts,
        xts,
        xp,
        xf,
        gross,
        2 * fee,
        slippage_cost(ep, ef, xp, xf, 1, spec),
        gross - 2 * fee,
        0,
        0,
        int((xts - ets).total_seconds() // 60),
        "r066_scheduled_after_tse_signal",
        ExitReason.SIGNAL,
        f"r066_{name}",
        "r066-q001-v1",
        canonical_hash({"name": name, "ticks": ticks, "fee": fee, "entry": entry, "exit": exit_}),
        {"entry_session": "night", "fixed_time_order": True},
    )


def selected(row: dict[str, object], name: str) -> bool:
    if row.get("status") != "E":
        return False
    cell = str(row.get("cell"))
    return (
        cell == ("A" if name.startswith("A") else name)
        if name not in {"U", "A_short"}
        else (cell == "A" if name == "A_short" else True)
    )


def condition(
    name: str,
    events: list[dict[str, object]],
    side: Side,
    entry: str,
    exit_: str,
    ticks: int,
    fee: int,
    multiplier: int,
    tick: int,
) -> tuple[tuple[Trade, ...], list[dict[str, object]]]:
    trades = []
    ledger = []
    for source in events:
        row = dict(source)
        row.update(
            condition=name,
            side=side.value,
            condition_eligible=selected(row, name),
            entry_boundary=entry,
            exit_boundary=exit_,
            slippage_ticks_per_side=ticks,
            fee_jpy_per_side=fee,
        )
        if row["condition_eligible"]:
            t = execute(row, name, side, entry, exit_, ticks, fee, multiplier, tick)
            trades.append(t)
            row.update(
                status="filled",
                entry_ts_jst=t.entry_ts.isoformat(),
                exit_ts_jst=t.exit_ts.isoformat(),
                gross_pnl_jpy=t.gross_pnl_jpy,
                fees_jpy=t.fees_jpy,
                slippage_cost_jpy=t.slippage_cost_jpy,
                net_pnl_jpy=t.net_pnl_jpy,
            )
        ledger.append(row)
    return tuple(trades), ledger


def aligned(trades: tuple[Trade, ...], days: list[date]) -> tuple[np.ndarray, np.ndarray]:
    v = np.zeros(len(days))
    c = np.zeros(len(days))
    index = {x.isoformat(): i for i, x in enumerate(days)}
    for t in trades:
        v[index[t.trade_date.isoformat()]] += t.net_pnl_jpy
        c[index[t.trade_date.isoformat()]] += 1
    return v, c


def draws(n: int, block: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    starts = np.arange(n - block + 1, dtype=np.int16)
    out = np.empty((REPETITIONS, n), dtype=np.int16)
    for i in range(REPETITIONS):
        out[i] = np.concatenate(
            [
                np.arange(s, s + block, dtype=np.int16)
                for s in rng.choice(starts, size=ceil(n / block), replace=True)
            ]
        )[:n]
    return out


def pct(a: np.ndarray, q: float) -> float:
    return float(np.quantile(a, q, method="linear"))


def bootstrap(
    values: dict[str, tuple[np.ndarray, np.ndarray]], block: int
) -> tuple[dict[str, object], np.ndarray]:
    d = draws(len(next(iter(values.values()))[0]), block, MBB_SEED)
    a, ac = values["A"]
    targets = {"A_daily_mean_net_jpy": a[d].mean(1)}
    for other in ("C", "D", "U", "A_short"):
        b, bc = values[other]
        targets[f"A_minus_{other}_conditional_per_trade_jpy"] = (a[d].sum(1) / ac[d].sum(1)) - (
            b[d].sum(1) / bc[d].sum(1)
        )
    return (
        {
            k: {
                "estimate": float(x.mean()),
                "ci95_percentile_linear": [pct(x, 0.025), pct(x, 0.975)],
            }
            for k, x in targets.items()
        }
        | {
            "method": "noncircular moving-block bootstrap; common index; tail truncation; conditional sum/count recomputed",
            "seed": MBB_SEED,
            "block_length_trade_dates": block,
            "repetitions": REPETITIONS,
        }
    ), d


def regression(
    events: list[dict[str, object]], multiplier: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], list[str]]:
    rows = [x for x in events if x.get("cell") in {"A", "D"}]
    years = sorted({int(str(x["trade_date"])[:4]) for x in rows})
    q = []
    y = []
    z = []
    days = []
    for x in rows:
        target = date.fromisoformat(cast(str, x["trade_date"]))
        r = float(x["r"])
        q.append(float(x["cell"] == "A"))
        y.append((int(x["next_d0_open"]) - int(x["n0_open"])) * multiplier)
        z.append(
            [
                1.0,
                abs(r),
                abs(r) ** 2,
                float(x["range_points"]),
                float(x["efficiency"]),
                float(x["pre_gap_points"]),
                float(str(x["schedule_version"]) != str(rows[0]["schedule_version"])),
                *[float(target.year == yr) for yr in years[1:]],
            ]
        )
        days.append(target.isoformat())
    return (
        np.asarray(q),
        np.asarray(y),
        np.asarray(z),
        days,
        [
            "intercept",
            "abs_r",
            "abs_r_squared",
            "tse_range",
            "efficiency",
            "dc_to_n0_gap",
            "schedule_regime",
            *[f"year_{x}" for x in years[1:]],
        ],
    )


def delta(events: list[dict[str, object]], multiplier: int, days: list[date]) -> dict[str, object]:
    q, y, z, rowdays, names = regression(events, multiplier)
    estimate, ss = fwl_delta(q, y, z)
    p = z @ np.linalg.pinv(z, rcond=1e-12)
    qr = q - p @ q
    residual = y - p @ y - qr * estimate
    ix = {x.isoformat(): i for i, x in enumerate(days)}
    di = np.asarray([ix[x] for x in rowdays])
    rng = np.random.default_rng(WILD_SEED)
    estimates = np.empty(REPETITIONS)
    signs = np.empty((REPETITIONS, len(days)), dtype=np.int8)
    for j in range(REPETITIONS):
        w = np.repeat(
            rng.choice(np.asarray([-1, 1], dtype=np.int8), size=ceil(len(days) / 20)), 20
        )[: len(days)]
        signs[j] = w
        estimates[j] = estimate + float(qr @ (residual * w[di]) / ss)
    write_json(
        OUT / "regression_ledger.json",
        {
            "trade_dates": rowdays,
            "Q": q.tolist(),
            "y_0tick_gross_jpy": y.tolist(),
            "nuisance_columns": names,
            "nuisance": z.tolist(),
            "delta": estimate,
            "q_residual_ss": ss,
            "rcond": 1e-12,
            "tolerance": 1e-12,
        },
    )
    np.save(OUT / "bootstrap_delta_block_wild_signs.npy", signs)
    return {
        "estimate": estimate,
        "ci95_percentile_linear": [pct(estimates, 0.025), pct(estimates, 0.975)],
        "method": "fixed-design 20 trade_date block-wild score bootstrap",
        "seed": WILD_SEED,
        "repetitions": REPETITIONS,
        "q_residual_ss": ss,
    }


def positive(result: dict[str, object]) -> bool:
    x = cast(dict[str, object], result["overall"])
    return int(x["net_pnl_jpy"]) > 0 and float(x["profit_factor"] or 0) > 1


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r066_q001_tse_lower_tail_night_reversal.py')

    if OUT.exists():
        raise FileExistsError(f"immutable output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, base = load_project_config(ROOT / "config")
    if (base.execution.slippage_ticks, base.fees.jpy_per_side_per_contract) != (1, 30):
        raise ValueError("cost contract mismatch")
    calendar = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    inputs = input_manifest(data_config.gold_root)
    paths = [
        Path(__file__).relative_to(ROOT),
        Path("src/n225m_bt/research/r066.py"),
        Path("tests/test_r066_q001.py"),
    ]
    plan = {
        "experiment_id": IDENTIFIER,
        "study_id": "R066-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "duplicate_review": "R001-R065 reviewed before price/event/PnL access: R059 is dC-to-n0 gap; R065 is n0-to-d0 fixed buy; R055/R062 signal from night and trade after TSE open. None uses causal full-TSE r lower quartile, following-night fixed buy, and next-TSE-open exit.",
        "hypothesis": "After a causal lower-quartile full TSE return, fixed buy n0-to-next d0 has positive net expectancy and exceeds C, D, U, and same-event short.",
        "rule": "r=dc/d0-1; prior 120 TSE d0/dc-only U, at least 100, nearest-rank q20/q25/q30/q50/q70/q75/q80, current excluded; A r<=q25, C q25<r<=q50, D r>=q75, U all rolling-valid E, A_short same A event.",
        "common_E": "d0/dc, following n0/n0+1/n0+5 and next d0/d0+1 unique, eligible, positive, same versioned chain, R004 nonisolated; no substitutions/backfill.",
        "cost_and_sensitivity": "1 tick + JPY30/side; diagnostic 0 tick; 2/3 tick, fee2, n0 delay1/5 without exit extension, exit delay1, independently rebuilt q20/q30 with q80/q70 upper control.",
        "inference": f"fixed 1111-day axis; 20-day noncircular MBB {REPETITIONS}, common index, linear percentile seed {MBB_SEED}; block10/40; FWL and fixed-design block-wild {REPETITIONS} seed {WILD_SEED}.",
        "gates": "E>=800,A/C/D>=150,A delays>=140,q20 A>=110,q30 A>=180; thereafter all stated profitability/CI/sensitivity/block/year/month/top10 gates mandatory.",
        "inputs": inputs,
        "source": snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml"),
        "implementation": {str(x): digest(ROOT / x) for x in paths},
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "input_manifest.json", inputs)
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "campaign_manifest.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "preregistered_before_price_access",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "plan_hash": canonical_hash(plan),
        },
    )
    checks = {
        "pytest": [executable, "-m", "pytest", "tests/test_r066_q001.py", "-q"],
        "ruff": [
            executable,
            "-m",
            "ruff",
            "check",
            "src/n225m_bt/research/r066.py",
            "tests/test_r066_q001.py",
            str(paths[0]),
        ],
        "mypy": [executable, "-m", "mypy", "src/n225m_bt/research/r066.py"],
    }
    validation = {
        n: {"returncode": x.returncode, "stdout": x.stdout, "stderr": x.stderr}
        for n, c in checks.items()
        for x in [run(c, cwd=ROOT, text=True, capture_output=True, check=False)]
    }
    validation["status"] = (
        "PASS"
        if all(
            cast(dict[str, object], x)["returncode"] == 0
            for n, x in validation.items()
            if n != "status"
        )
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("pre-execution validation failed")
    data = load_split(data_config.gold_root, "development")
    view, isolation, isolated = quarantine(data)
    all_days, fixed = axis(calendar, isolated)
    grouped = session_groups(view.bars)
    ledger = candidate_rows(classifier, all_days, grouped, isolated)
    causality = {
        "prior_only": all(
            ref < row["trade_date"]
            for row in ledger
            for ref in cast(
                list[str], cast(dict[str, object], row["u"])["rolling_reference_trade_dates"]
            )
        ),
        "100_exact": all(
            bool(cast(dict[str, object], row["u"])["rolling_valid"])
            == (int(cast(dict[str, object], row["u"])["rolling_valid_count"]) >= 100)
            for row in ledger
        ),
        "no_backfill": all(
            int(cast(dict[str, object], row["u"])["rolling_scheduled_count"]) <= 120
            for row in ledger
        ),
    }
    write_json(OUT / "rolling_u_ledger.json", ledger)
    write_json(OUT / "rolling_causality_audit.json", causality)
    if not all(causality.values()):
        raise ValueError("rolling causality failed")
    variants = {"base": 25, "q20": 20, "q30": 30}
    events = {
        n: [
            make_event(
                classifier,
                d,
                grouped,
                isolated,
                {x["trade_date"]: x for x in ledger}[d.isoformat()],
                lower_quantile=q,
            )
            for d in fixed
        ]
        for n, q in variants.items()
    }
    base_events = events["base"]
    write_json(OUT / "all_eligible_event_ledger.json", base_events)
    write_json(
        OUT / "sensitivity_event_ledgers.json", {k: v for k, v in events.items() if k != "base"}
    )
    write_json(
        OUT / "eligibility_audit.json",
        {
            "fixed_axis": [d.isoformat() for d in fixed],
            "E": sum(x["status"] == "E" for x in base_events),
            "reason_counts": dict(Counter(str(x.get("reason")) for x in base_events)),
            "R004_isolation": isolation,
        },
    )
    fee, mult, tick = (
        base.fees.jpy_per_side_per_contract,
        instrument.instrument.contract_multiplier,
        instrument.instrument.tick_size,
    )
    specs = {
        "A": (base_events, Side.LONG, "n0", "next_d0", 1, fee),
        "C": (base_events, Side.LONG, "n0", "next_d0", 1, fee),
        "D": (base_events, Side.LONG, "n0", "next_d0", 1, fee),
        "U": (base_events, Side.LONG, "n0", "next_d0", 1, fee),
        "A_short": (base_events, Side.SHORT, "n0", "next_d0", 1, fee),
        "A0": (base_events, Side.LONG, "n0", "next_d0", 0, fee),
        "A2": (base_events, Side.LONG, "n0", "next_d0", 2, fee),
        "A3": (base_events, Side.LONG, "n0", "next_d0", 3, fee),
        "A_fee2": (base_events, Side.LONG, "n0", "next_d0", 1, fee * 2),
        "A_delay1": (base_events, Side.LONG, "n0_delay1", "next_d0", 1, fee),
        "A_delay5": (base_events, Side.LONG, "n0_delay5", "next_d0", 1, fee),
        "A_exitdelay1": (base_events, Side.LONG, "n0", "next_d0_delay1", 1, fee),
        "A_q20": (events["q20"], Side.LONG, "n0", "next_d0", 1, fee),
        "A_q30": (events["q30"], Side.LONG, "n0", "next_d0", 1, fee),
    }
    trades = {}
    ledgers = {}
    results = {}
    values = {}
    for n, s in specs.items():
        t, condition_ledger = condition(n, *s, mult, tick)
        trades[n], ledgers[n] = t, condition_ledger
        results[n] = {"trade_count": len(t), "overall": ledger_metrics(t)}
        values[n] = aligned(t, fixed)
        folder = OUT / n
        folder.mkdir()
        write_json(folder / "events.json", condition_ledger)
        write_json(
            folder / "trades.json",
            [
                {
                    "trade_id": x.trade_id,
                    "trade_date": x.trade_date.isoformat(),
                    "side": x.side.value,
                    "entry_ts_jst": x.entry_ts.isoformat(),
                    "exit_ts_jst": x.exit_ts.isoformat(),
                    "gross_pnl_jpy": x.gross_pnl_jpy,
                    "fees_jpy": x.fees_jpy,
                    "slippage_cost_jpy": x.slippage_cost_jpy,
                    "net_pnl_jpy": x.net_pnl_jpy,
                }
                for x in t
            ],
        )
        write_json(folder / "research_metrics.json", results[n])
        write_json(
            folder / "daily_net_pnl_aligned.json",
            {d.isoformat(): int(values[n][0][i]) for i, d in enumerate(fixed)},
        )
    audit = {
        "cells_exclusive": all(
            sum(x.get("cell") == c for c in ("A", "C", "D")) <= 1 for x in base_events
        ),
        "controls_same_A_event_time": all(
            a.get("condition_eligible") == b.get("condition_eligible")
            and a.get("n0_jst") == b.get("n0_jst")
            and a.get("next_d0_jst") == b.get("next_d0_jst")
            for a, b in zip(ledgers["A"], ledgers["A_short"], strict=True)
        ),
        "delays_nonextended": all(
            x.exit_ts == y.exit_ts for x, y in zip(trades["A_delay1"], trades["A"], strict=True)
        )
        and all(
            x.exit_ts == y.exit_ts for x, y in zip(trades["A_delay5"], trades["A"], strict=True)
        ),
        "exit_delay_only": all(
            x.entry_ts == y.entry_ts and x.exit_ts > y.exit_ts
            for x, y in zip(trades["A_exitdelay1"], trades["A"], strict=True)
        ),
        "one_trade_max": all(len(t) <= 1111 for t in trades.values()),
        "accounting": all(
            x.net_pnl_jpy == x.gross_pnl_jpy - x.fees_jpy for t in trades.values() for x in t
        ),
        "fixed_axis": all(len(v[0]) == 1111 for v in values.values()),
        "q_independent_reclassification": events["q20"] != base_events
        and events["q30"] != base_events,
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "status": "PASS" if all(audit.values()) else "BLOCKED",
            "checks": audit,
            "note": "scheduled opens use adverse fill once per side; Gross includes slippage; Net=Gross-fees",
        },
    )
    if not all(audit.values()):
        raise ValueError("execution/accounting audit failed")
    boot, ind = bootstrap({x: values[x] for x in ("A", "C", "D", "U", "A_short")}, 20)
    b10, _ = bootstrap({x: values[x] for x in ("A", "C", "D", "U", "A_short")}, 10)
    b40, _ = bootstrap({x: values[x] for x in ("A", "C", "D", "U", "A_short")}, 40)
    np.save(OUT / "bootstrap_mbb_indices_block20.npy", ind)
    try:
        boot["delta"] = delta(base_events, mult, fixed)
        fwl = "PASS"
    except R066QNotIdentifiableError as e:
        boot["delta"] = {"status": "BLOCKED", "error": str(e)}
        fwl = "BLOCKED"
    write_json(
        OUT / "bootstrap.json",
        {"main_block20": boot, "sensitivity_block10": b10, "sensitivity_block40": b40},
    )
    years = {
        str(y): sum(x.net_pnl_jpy for x in trades["A"] if x.trade_date.year == y)
        for y in range(2021, 2026)
    }
    months = {
        f"{y}-{m:02d}": sum(
            x.net_pnl_jpy for x in trades["A"] if x.trade_date.year == y and x.trade_date.month == m
        )
        for y in range(2021, 2026)
        for m in range(1, 13)
        if (y, m) <= (2025, 6)
    }

    def lower(book: dict[str, object], name: str) -> bool:
        return (
            fwl == "PASS"
            and float(cast(dict[str, object], book[name])["ci95_percentile_linear"][0]) > 0
        )

    info = {
        "E>=800": sum(x["status"] == "E" for x in base_events) >= 800,
        "A/C/D>=150": all(len(trades[x]) >= 150 for x in ("A", "C", "D")),
        "A_delay>=140": all(len(trades[x]) >= 140 for x in ("A_delay1", "A_delay5")),
        "q20_A>=110": len(trades["A_q20"]) >= 110,
        "q30_A>=180": len(trades["A_q30"]) >= 180,
    }
    econ = {
        "A_net_positive": positive(results["A"]),
        "all_main_ci_positive": all(
            lower(boot, n)
            for n in (
                "A_daily_mean_net_jpy",
                "A_minus_C_conditional_per_trade_jpy",
                "A_minus_D_conditional_per_trade_jpy",
                "A_minus_U_conditional_per_trade_jpy",
                "A_minus_A_short_conditional_per_trade_jpy",
                "delta",
            )
        ),
        "cost_delay_quantile_sensitivities_positive_pf": all(
            positive(results[x])
            for x in (
                "A2",
                "A3",
                "A_fee2",
                "A_delay1",
                "A_delay5",
                "A_exitdelay1",
                "A_q20",
                "A_q30",
            )
        ),
        "block10_40_A_AminusD_positive": all(
            lower(x, n)
            for x in (b10, b40)
            for n in ("A_daily_mean_net_jpy", "A_minus_D_conditional_per_trade_jpy")
        ),
        "three_positive_years_2021_2024": sum(years[str(x)] > 0 for x in range(2021, 2025)) >= 3,
        "positive_months>=27": sum(x > 0 for x in months.values()) >= 27,
        "top10_removed_positive": sum(x.net_pnl_jpy for x in trades["A"])
        - sum(sorted((x.net_pnl_jpy for x in trades["A"] if x.net_pnl_jpy > 0), reverse=True)[:10])
        > 0,
    }
    decision = (
        "BLOCKED"
        if fwl == "BLOCKED"
        else "INCONCLUSIVE"
        if not all(info.values())
        else "INVESTIGATE"
        if all(econ.values())
        else "REJECT"
    )
    write_json(
        OUT / "development_results.json",
        {
            "experiment_id": IDENTIFIER,
            "decision": decision,
            "information_gates": info,
            "economic_gates": econ
            if all(info.values())
            else "NOT_EVALUATED_INFORMATION_INSUFFICIENT",
            "conditions": results,
            "A_year_net_jpy": years,
            "A_month_net_jpy": months,
            "positive_months": sum(x > 0 for x in months.values()),
            "fwl_status": fwl,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "complete",
            "decision": decision,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
