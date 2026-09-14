"""Reproducible Development-only executor for the preregistered R057-Q001."""

# mypy: disable-error-code="arg-type, no-any-return"
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
from math import ceil, floor, log
from pathlib import Path
from random import Random
from statistics import fmean
from subprocess import run
from sys import executable
from typing import cast

import numpy as np

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import research_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r053 import R053QNotIdentifiableError, fwl_delta
from n225m_bt.research.r057 import LOOKBACK, SCHEDULE_ID, r057_event
from n225m_bt.research.runner import write_json
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[3]
IDENTIFIER, SEED, BLOCK = "r057-q001-20260915-tse-opening-gap-acceptance-02", 20261004, 20
OUT = ROOT / "results" / "research" / IDENTIFIER
R045 = ROOT / "results" / "research" / "r045-q001-20260914-tse-lunch-placebo-reversal-03"
BASE = ("A", "D", "B", "A_fade", "A_buy", "A_sell")


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(part)
    return hasher.hexdigest()


def cash_calendar() -> tuple[TSECashMarketCalendar, dict[str, object]]:
    source = R045 / "institutional_evidence" / "cabinet_office_public_holidays.csv"
    if not source.exists():
        raise FileNotFoundError("R057 frozen TSE holiday evidence unavailable")
    copied = OUT / "institutional_evidence" / source.name
    copied.parent.mkdir()
    copied.write_bytes(source.read_bytes())
    return TSECashMarketCalendar.from_cabinet_office_csv(copied.read_text(encoding="cp932")), {
        "schedule_id": SCHEDULE_ID,
        "holiday_csv_sha256": digest(copied),
    }


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, set[tuple[date, Session]], dict[str, object]]:
    groups = session_groups(data.bars)
    isolated = {
        key
        for key, rows in groups.items()
        if any("TICK_GRID_VIOLATION" in b.quality_flags for b in rows)
    }
    included = [bar for key, rows in groups.items() if key not in isolated for bar in rows]
    listed = [{"trade_date": d.isoformat(), "session": s.value} for d, s in sorted(isolated)]
    audit = {
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(groups) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list_hash": canonical_hash(listed),
    }
    expected = {
        "quarantined_sessions": 45,
        "quarantined_bars": 27345,
        "included_sessions": 2216,
        "included_bars": 1326086,
        "quarantined_session_list_hash": "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa",
    }
    if any(audit[key] != value for key, value in expected.items()):
        raise ValueError(f"R057 R004 fixed isolation mismatch: {audit}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        isolated,
        audit,
    )


def tse_days(calendar: TSECashMarketCalendar) -> list[date]:
    days, current = [], date(2021, 1, 1)
    while current <= date(2025, 6, 30):
        if calendar.is_open(current):
            days.append(current)
        current = current.fromordinal(current.toordinal() + 1)
    return days


def fixed_axis(sessions: object) -> list[date]:
    schedule = ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    return [
        row.trade_date
        for row in schedule.trading_days()
        if date(2021, 1, 1) <= row.trade_date <= date(2025, 6, 30)
    ]


def events(
    days: list[date],
    groups: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    calendar: TSECashMarketCalendar,
    axis: list[date],
) -> list[dict[str, object]]:
    index = {day: i for i, day in enumerate(days)}
    result: list[dict[str, object]] = []
    for target in axis:
        i = index.get(target)
        if i is None:
            result.append(r057_event(target, None, None, None, (), calendar))
            continue
        previous = days[i - 1] if i else None

        def visible(day: date | None) -> list[Bar] | None:
            return (
                None
                if day is None or (day, Session.DAY) in isolated
                else groups.get((day, Session.DAY))
            )

        history = [
            (
                d,
                days[j - 1] if j else None,
                visible(d),
                visible(days[j - 1] if j else None),
                (d, Session.DAY) in isolated,
            )
            for j, d in enumerate(days[max(0, i - LOOKBACK) : i], max(0, i - LOOKBACK))
        ]
        result.append(
            r057_event(
                target,
                visible(target),
                previous,
                visible(previous),
                history,
                calendar,
                quarantined=(target, Session.DAY) in isolated,
            )
        )
    return result


def selected(row: dict[str, object], condition: str, q: int = 75, window: int = 15) -> bool:
    if row.get("status") != "E":
        return False
    observation = cast(
        dict[str, object], cast(dict[int, dict[str, object]], row["observations"])[window]
    )
    same = int(cast(int, observation["confirmation_sign"])) == int(
        cast(int, observation["gap_sign"])
    )
    high = float(cast(float, observation["x"])) >= float(cast(float, row[f"q{q}"]))
    directional = (
        int(cast(int, observation["gap_sign"])) != 0
        and int(cast(int, observation["confirmation_sign"])) != 0
    )
    if condition == "B":
        return high and directional
    if condition == "D":
        return high and directional and not same
    return high and directional and same


def side(row: dict[str, object], condition: str, window: int) -> str:
    sign = int(
        cast(
            int,
            cast(
                dict[str, object], cast(dict[int, dict[str, object]], row["observations"])[window]
            )["gap_sign"],
        )
    )
    if condition == "A_fade":
        sign = -sign
    if condition == "A_buy":
        sign = 1
    if condition == "A_sell":
        sign = -1
    return "long" if sign > 0 else "short"


def run_condition(
    rows: list[dict[str, object]],
    groups: dict[tuple[date, Session], list[Bar]],
    engine: BacktestEngine,
    condition: str,
    *,
    ticks: int = 1,
    delay: int = 0,
    q: int = 75,
    window: int = 15,
    holding: int = 30,
) -> tuple[tuple[Trade, ...], list[dict[str, object]], dict[str, int]]:
    records, trades, audit = [], [], Counter[str]()
    for original in rows:
        row, target = dict(original), date.fromisoformat(cast(str, original["trade_date"]))
        allowed = selected(row, "A" if condition.startswith("A_") else condition, q, window)
        row.update(
            condition=condition,
            condition_eligible=allowed,
            requested_slippage_ticks=ticks,
            requested_delay_minutes=delay,
            confirmation_minutes=window,
            requested_holding_minutes=holding,
        )
        if not allowed:
            row.update(status="skipped", condition_reason="PREDICATE_NOT_MET")
            records.append(row)
            continue
        clocks = cast(
            dict[str, str],
            cast(dict[str, dict[str, dict[str, str]]], row["planned_times"])[str(window)][
                str(holding)
            ],
        )
        entry, exit_time, direction = (
            datetime.fromisoformat(clocks["entry"]),
            datetime.fromisoformat(clocks["exit"]),
            side(row, condition, window),
        )
        row.update(
            side=direction,
            planned_entry_jst=entry.isoformat(),
            planned_exit_jst=exit_time.isoformat(),
        )
        result = engine.run(
            groups.get((target, Session.DAY), []),
            R049FixedTimeStrategy(f"r057_{condition}", entry, exit_time, direction, delay),
            canonical_hash(
                {
                    "condition": condition,
                    "ticks": ticks,
                    "delay": delay,
                    "q": q,
                    "window": window,
                    "holding": holding,
                }
            ),
        )
        audit["canceled_orders"] += result.canceled_orders
        if len(result.trades) > 1:
            raise AssertionError("R057 maximum one position violated")
        if not result.trades:
            row.update(status="eligible_order_unfilled")
            records.append(row)
            continue
        trade = result.trades[0]
        row.update(
            status="filled",
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            exit_reason=trade.exit_reason.value,
            gross_pnl_jpy=trade.gross_pnl_jpy,
            fees_jpy=trade.fees_jpy,
            slippage_cost_jpy=trade.slippage_cost_jpy,
            net_pnl_jpy=trade.net_pnl_jpy,
            entry_reference_price=trade.entry_reference_price,
            exit_reference_price=trade.exit_reference_price,
            entry_delay_minutes=round((trade.entry_ts - entry).total_seconds() / 60),
            exit_delay_minutes=round((trade.exit_ts - exit_time).total_seconds() / 60),
        )
        trades.append(trade)
        records.append(row)
    return (
        tuple(
            replace(t, trade_id=f"trade-{i:06d}")
            for i, t in enumerate(sorted(trades, key=lambda t: t.entry_ts), 1)
        ),
        records,
        dict(audit),
    )


def percentile(values: list[float]) -> list[float]:
    def at(q: float) -> float:
        ordered, point = sorted(values), (len(values) - 1) * q
        lo, hi = floor(point), ceil(point)
        return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (point - lo)

    return [at(0.025), at(0.975)]


def regression(
    records: list[dict[str, object]], groups: dict[tuple[date, Session], list[Bar]]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    items: list[tuple[float, float, list[float], str]] = []
    for row in records:
        if row.get("status") != "filled":
            continue
        day = date.fromisoformat(cast(str, row["trade_date"]))
        prior = date.fromisoformat(cast(str, row["previous_tse_trade_date"]))
        obs = cast(dict[str, object], cast(dict[int, dict[str, object]], row["observations"])[15])
        x, q75 = float(obs["x"]), float(row["q75"])
        prior_open = next(
            (
                bar.open
                for bar in groups.get((prior, Session.DAY), [])
                if bar.ts_jst.hour == 9 and bar.ts_jst.minute == 0 and bar.is_eligible
            ),
            None,
        )
        if x <= 0 or q75 <= 0 or prior_open is None:
            raise ValueError("BLOCKED: R057 preregistered regression covariate unavailable")
        s = int(obs["gap_sign"])
        r = float(obs["r_confirm"])
        items.append(
            (
                float(int(obs["confirmation_sign"]) == s),
                s
                * (float(row["exit_reference_price"]) - float(row["entry_reference_price"]))
                * 100,
                [
                    log(x / q75),
                    abs(r) * 10000,
                    float(obs["confirmation_range_bps"]),
                    s * (float(obs["p_points"]) - prior_open) / prior_open * 10000,
                    float(s > 0),
                ],
                day.isoformat(),
            )
        )
    if not items:
        raise ValueError("BLOCKED: no B regression observations")
    years = sorted({int(item[3][:4]) for item in items})
    q = np.asarray([item[0] for item in items], dtype=float)
    y = np.asarray([item[1] for item in items], dtype=float)
    nuisance = np.asarray(
        [
            [1.0, *item[2], *[float(int(item[3][:4]) == year) for year in years[1:]]]
            for item in items
        ],
        dtype=float,
    )
    return q, y, nuisance, [item[3] for item in items]


def bootstrap(
    daily: dict[str, dict[str, int]],
    b_records: list[dict[str, object]],
    groups: dict[tuple[date, Session], list[Bar]],
) -> dict[str, object]:
    axis = sorted(daily["A"])
    q, y, nuisance, dates = regression(b_records, groups)
    delta, ss = fwl_delta(q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12)
    by_day: dict[str, list[int]] = {}
    for i, day in enumerate(dates):
        by_day.setdefault(day, []).append(i)
    series = {"A_mean_net_jpy": [float(daily["A"][d]) for d in axis]}
    for name in ("D", "B", "A_fade", "A_buy", "A_sell"):
        series[f"A_minus_{name}_mean_jpy"] = [float(daily["A"][d] - daily[name][d]) for d in axis]
    samples = {name: [] for name in (*series, "delta_fwl_jpy")}
    all_indices = np.empty((10000, len(axis)), dtype=np.uint16)
    rng = Random(SEED)
    for replicate in range(10000):
        indices: list[int] = []
        while len(indices) < len(axis):
            start = rng.randrange(len(axis) - BLOCK + 1)
            indices.extend(range(start, start + BLOCK))
        indices = indices[: len(axis)]
        all_indices[replicate] = indices
        for name, values in series.items():
            samples[name].append(fmean(values[i] for i in indices))
        selected_indices = [row for i in indices for row in by_day.get(axis[i], [])]
        try:
            samples["delta_fwl_jpy"].append(
                fwl_delta(
                    q[selected_indices],
                    y[selected_indices],
                    nuisance[selected_indices],
                    pinv_rcond=1e-12,
                    residual_ss_tolerance=1e-12,
                )[0]
            )
        except R053QNotIdentifiableError as exc:
            write_json(
                OUT / "technical_fwl_gate.json",
                {
                    "status": "BLOCKED",
                    "replicate": replicate,
                    "sampled_axis_indices_zero_based": indices,
                    "error": str(exc),
                },
            )
            raise ValueError(
                f"BLOCKED: FWL identification failed in bootstrap replicate {replicate}"
            ) from exc
    np.save(OUT / "bootstrap_common_day_indices.npy", all_indices)
    output = {
        "method": "20 trade_date noncircular moving-block bootstrap; common indices; tail truncation; linear percentile",
        "seed": SEED,
        "repetitions": 10000,
        "block_length_trade_dates": BLOCK,
        "target_trade_dates": len(axis),
        "fwl_delta_observed": delta,
        "fwl_q_residual_ss": ss,
    }
    output.update(
        {
            name: {"mean": fmean(values), "ci95_percentile_linear": percentile(values)}
            for name, values in samples.items()
        }
    )
    write_json(
        OUT / "technical_fwl_gate.json",
        {"status": "PASS", "observed_q_residual_ss": ss, "repetitions": 10000, "tolerance": 1e-12},
    )
    return output


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable R057 output exists: {OUT}")
    OUT.mkdir(parents=True)
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    files = [
        Path("src/n225m_bt/research/r057.py"),
        Path("src/n225m_bt/research/r057_runner.py"),
        Path("scripts/run_r057_q001_tse_opening_gap_acceptance.py"),
        Path("tests/test_r057_q001.py"),
    ]
    inputs = [
        {"path": str(p.resolve().relative_to(ROOT)), "sha256": digest(p)}
        for p in partition_paths(data_config.gold_root, "development")
    ]
    plan: dict[str, object] = {
        "experiment_id": IDENTIFIER,
        "study_id": "R057-Q001",
        "status": "frozen_before_price_statistics_events_or_pnl",
        "seed": SEED,
        "prior_immutable_attempts": "…-01 stopped before any Parquet read, price statistic, event, order, trade, PnL, bootstrap, or decision because input-manifest path serialization mixed a relative partition path with an absolute repository root. …-02 changes only that manifest serialization.",
        "duplicate_review": "R001-R056 reviewed before price statistics. R056 is same-day 09:00 opening-drive displacement/path efficiency; R006/R015/R032 use OSE night boundaries; R053 is the noon discontinuity. None uses the prior scheduled TSE normal close, current TSE open, 15-minute same/opposite confirmation, next-open entry and 30-minute hold.",
        "hypothesis": "Large previous-TSE-close to current-TSE-open gaps that extend in the same direction over the first 15 scheduled TSE minutes continue for the next 30 scheduled minutes and exceed opposite-confirmation and same-event fade/fixed-side controls.",
        "rule": "p=last eligible scheduled minute close of immediately prior TSE normal session; o=current first scheduled TSE minute open; c14=close of minute 15; g=(o-p)/p, x=abs(g), s=sign(g), r15=(c14-o)/o. Exact preceding 120 scheduled TSE business days only; >=100 valid x; q70/q75/q80 nearest rank; equality upper. g=0/r15=0 direction-excluded. A=x>=q75 and sign(r15)=s, D=x>=q75 and sign(r15)=-s, B=A union D; A follows s, controls share A event/entry/exit. Signal after confirmation, entry at next scheduled open, fixed exit entry+30 scheduled minutes; gap/confirmation PnL excluded.",
        "common_E": "prior final bar, first 20 scheduled current TSE bars, rolling thresholds, and all 10/15/20 minute entries plus 15/30/45 minute exits are eligible within TSE segment; missing, isolation, nonpositive price, ambiguous previous session excluded.",
        "costs": "one tick + JPY30 per side; A2/A3 two/three tick; A_delay one scheduled minute later without exit extension",
        "sensitivities_only": "q70/q80; confirmation 10/20 reconstructed causally; holding 15/45",
        "ols": "B events: y=s-adjusted 0-tick pre-cost 30-min gross; Q=1[sign(r15)=s]; nuisance intercept, ln(x/q75), abs(r15) bps, first-15 high-low range bps, previous TSE normal-session s-adjusted return bps, gap-up, calendar-year FE; R053-Q002 nuisance-only Moore-Penrose FWL rcond/tolerance 1e-12; any full/bootstrap nonidentification BLOCKED without discard/redraw/regressor change.",
        "bootstrap": "20 trade_date noncircular MBB, 10000, common index, seed 20261004",
        "information_gate": "E>=850, B>=220, A>=80, D>=80, A buy/sell>=25, q80 and 20-minute A>=35",
        "decision": "information shortfall INCONCLUSIVE; otherwise any fixed economic/robustness failure REJECT; all pass Development-only INVESTIGATE. No WFA/OOS/Final Holdout/rescue exploration.",
        "inputs": {"development_normalized_parquet": inputs, "files_hash": canonical_hash(inputs)},
        "implementation": {str(f): digest(ROOT / f) for f in files},
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", plan)
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "status": "preregistered_before_price_access",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "plan_hash": canonical_hash(plan),
            "seed": SEED,
        },
    )
    commands = {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r057_q001.py",
            "tests/test_r053_q001.py",
            "tests/test_exit_after_entry_cutoff.py",
            "-q",
        ],
        "ruff": [executable, "-m", "ruff", "check", *[str(f) for f in files]],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r057.py",
        ],
    }
    validation = {
        name: {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}
        for name, command in commands.items()
        for done in [run(command, cwd=ROOT, capture_output=True, text=True, check=False)]
    }
    validation["status"] = (
        "PASS"
        if all(
            int(cast(dict[str, object], item)["returncode"]) == 0 for item in validation.values()
        )
        else "BLOCKED"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        raise ValueError("BLOCKED: R057 pre-execution validation failed")
    calendar, evidence = cash_calendar()
    development = load_split(data_config.gold_root, "development")
    view, isolated, q_audit = quarantine(development)
    axis = [d for d in fixed_axis(sessions) if (d, Session.DAY) not in isolated]
    if len(axis) != 1111:
        raise ValueError("R057 fixed 1111-day axis mismatch")
    raw = session_groups(development.bars)
    groups = session_groups(view.bars)
    calendar_days = tse_days(calendar)
    base = events(calendar_days, raw, isolated, calendar, axis)
    metric_bars = [b for b in view.bars if b.trade_date in set(axis)]
    write_json(
        OUT / "preflight.json",
        {
            "status": "PASS_LIMITED",
            "physical_io": "Development normalized Parquet only",
            "calendar": evidence,
            "quarantine": q_audit,
            "fixed_target_trade_dates": [d.isoformat() for d in axis],
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(OUT / "all_candidate_event_ledger.json", base)
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(ROOT / "config" / "local_calendar.yaml")
    )
    results: dict[str, object] = {}
    records: dict[str, list[dict[str, object]]] = {}
    trades: dict[str, tuple[Trade, ...]] = {}
    daily: dict[str, dict[str, int]] = {}
    configs = {name: (name, 1, 0, 75, 15, 30) for name in BASE} | {
        "A2": ("A", 2, 0, 75, 15, 30),
        "A3": ("A", 3, 0, 75, 15, 30),
        "A_delay": ("A", 1, 1, 75, 15, 30),
        "A_q70": ("A", 1, 0, 70, 15, 30),
        "A_q80": ("A", 1, 0, 80, 15, 30),
        "A_w10": ("A", 1, 0, 75, 10, 30),
        "A_w20": ("A", 1, 0, 75, 20, 30),
        "A_h15": ("A", 1, 0, 75, 15, 15),
        "A_h45": ("A", 1, 0, 75, 15, 45),
    }
    for name, (condition, ticks, delay, quantile, window, holding) in configs.items():
        config = baseline.model_copy(
            update={
                "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
                "risk": baseline.risk.model_copy(
                    update={"new_entry_cutoff_minutes_before_session_close": 0}
                ),
            }
        )
        filled, ledger, audit = run_condition(
            base,
            groups,
            BacktestEngine(instrument.instrument.to_spec(), config, classifier),
            condition,
            ticks=ticks,
            delay=delay,
            q=quantile,
            window=window,
            holding=holding,
        )
        values = dict.fromkeys((d.isoformat() for d in axis), 0)
        for trade in filled:
            values[trade.trade_date.isoformat()] += trade.net_pnl_jpy
        folder = OUT / name
        folder.mkdir()
        metrics = research_metrics(filled, metric_bars)
        write_json(folder / "events.json", ledger)
        write_json(
            folder / "trades.json",
            [
                {
                    "trade_id": t.trade_id,
                    "trade_date": t.trade_date.isoformat(),
                    "side": t.side.value,
                    "net_pnl_jpy": t.net_pnl_jpy,
                }
                for t in filled
            ],
        )
        write_json(folder / "daily_net_pnl_aligned.json", values)
        write_json(folder / "research_metrics.json", metrics)
        write_json(folder / "execution_audit.json", audit)
        records[name], trades[name], daily[name], results[name] = (
            ledger,
            filled,
            values,
            {"trade_count": len(filled), "metrics": metrics, "audit": audit},
        )
    a_rows = records["A"]
    checks = {
        "A_D_exclusive": all(
            not (a["condition_eligible"] and d["condition_eligible"])
            for a, d in zip(records["A"], records["D"], strict=True)
        ),
        "B_union_A_D": all(
            bool(b["condition_eligible"])
            == (bool(a["condition_eligible"]) or bool(d["condition_eligible"]))
            for a, b, d in zip(records["A"], records["B"], records["D"], strict=True)
        ),
        "A_controls_event_entry_exit": all(
            all(
                records[name][i].get(k) == a.get(k)
                for k in ("condition_eligible", "planned_entry_jst", "planned_exit_jst")
            )
            for name in ("A_fade", "A_buy", "A_sell")
            for i, a in enumerate(a_rows)
            if a["condition_eligible"]
        ),
        "fade_opposite_side": all(
            a.get("side") != records["A_fade"][i].get("side")
            for i, a in enumerate(a_rows)
            if a.get("status") == "filled"
        ),
        "window_reconstructed": all(
            records[name][i].get("confirmation_minutes") == window
            for name, window in (("A_w10", 10), ("A_w20", 20))
            for i in range(len(a_rows))
        ),
        "fixed_axis": all(len(v) == 1111 for v in daily.values()),
        "one_position": all(
            len({t.trade_date for t in value}) == len(value) for value in trades.values()
        ),
        "accounting": all(
            t.net_pnl_jpy == t.gross_pnl_jpy - t.fees_jpy
            for value in trades.values()
            for t in value
        ),
        "signal_exit": all(
            r.get("exit_reason") == "signal" and r.get("exit_delay_minutes") == 0
            for value in records.values()
            for r in value
            if r.get("status") == "filled"
        ),
        "delay_nonextension": all(
            records["A_delay"][i].get("planned_exit_jst") == a.get("planned_exit_jst")
            for i, a in enumerate(a_rows)
            if a["condition_eligible"]
        ),
    }
    write_json(
        OUT / "execution_accounting_audit.json",
        {
            "checks": checks,
            "all_pass": all(checks.values()),
            "note": "Gross is fill-to-fill with one-sided slippage; Net=Gross-fees, with no double slippage deduction.",
        },
    )
    if not all(checks.values()):
        raise ValueError("BLOCKED: R057 execution/accounting audit failed")
    try:
        boot = bootstrap(daily, records["B"], groups)
    except ValueError as exc:
        if not str(exc).startswith("BLOCKED:"):
            raise
        write_json(
            OUT / "decision.json",
            {
                "status": "BLOCKED",
                "reason": str(exc),
                "oos": "NOT_EVALUATED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
        write_json(OUT / "COMPLETED.json", {"experiment_id": IDENTIFIER, "decision": "BLOCKED"})
        return
    write_json(OUT / "bootstrap.json", boot)
    overall = cast(dict[str, object], cast(dict[str, object], results["A"])["metrics"])["overall"]
    segments = cast(dict[str, object], cast(dict[str, object], results["A"])["metrics"])["segments"]
    concentration = cast(dict[str, object], cast(dict[str, object], results["A"])["metrics"])[
        "concentration"
    ]
    sides = cast(dict[str, dict[str, object]], cast(dict[str, object], segments)["side"])
    years = cast(dict[str, dict[str, object]], cast(dict[str, object], segments)["year"])
    sufficient = (
        len([r for r in base if r.get("status") == "E"]) >= 850
        and len(trades["B"]) >= 220
        and len(trades["A"]) >= 80
        and len(trades["D"]) >= 80
        and all(int(sides.get(v, {}).get("trade_count", 0)) >= 25 for v in ("long", "short"))
        and len(trades["A_q80"]) >= 35
        and len(trades["A_w20"]) >= 35
    )

    def ci(name):
        return (
            cast(list[float], cast(dict[str, object], boot[name])["ci95_percentile_linear"])[0] > 0
        )

    def positive(name):
        return (
            int(
                cast(dict[str, object], cast(dict[str, object], results[name])["metrics"])[
                    "overall"
                ]["net_pnl_jpy"]
            )
            > 0
            and float(
                cast(dict[str, object], cast(dict[str, object], results[name])["metrics"])[
                    "overall"
                ]["profit_factor"]
                or 0
            )
            > 1
        )

    gates = {
        "A_net_positive": int(cast(dict[str, object], overall)["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": float(cast(dict[str, object], overall)["profit_factor"] or 0) > 1,
        "A_mean_AminusD_AminusB_delta_CI": all(
            ci(n)
            for n in ("A_mean_net_jpy", "A_minus_D_mean_jpy", "A_minus_B_mean_jpy", "delta_fwl_jpy")
        ),
        "same_event_controls_CI": all(
            ci(f"A_minus_{n}_mean_jpy") for n in ("A_fade", "A_buy", "A_sell")
        ),
        "all_cost_delay_sensitivities_positive": all(
            positive(n)
            for n in ("A2", "A3", "A_delay", "A_q70", "A_q80", "A_w10", "A_w20", "A_h15", "A_h45")
        ),
        "three_positive_years_2021_2024": sum(
            int(years.get(str(y), {}).get("net_pnl_jpy", 0)) > 0 for y in range(2021, 2025)
        )
        >= 3,
        "positive_months_27": float(
            cast(dict[str, object], concentration)["positive_month_fraction"] or 0
        )
        >= 0.5,
        "top10_removed_positive": int(
            cast(dict[str, object], concentration)["net_excluding_top10_jpy"]
        )
        > 0,
    }
    decision = (
        "INCONCLUSIVE" if not sufficient else "INVESTIGATE" if all(gates.values()) else "REJECT"
    )
    write_json(
        OUT / "breakdowns.json",
        {
            "A_year": cast(dict[str, object], segments)["year"],
            "A_month": cast(dict[str, object], segments)["month"],
            "A_side": sides,
            "positive_months": sum(
                int(v.get("net_pnl_jpy", 0)) > 0
                for v in cast(
                    dict[str, dict[str, object]], cast(dict[str, object], segments)["month"]
                ).values()
            ),
            "concentration": concentration,
        },
    )
    write_json(
        OUT / "development_results.json",
        {
            "experiment_id": IDENTIFIER,
            "decision": decision,
            "information_sufficient": sufficient,
            "E": sum(r.get("status") == "E" for r in base),
            "conditions": results,
            "fixed_gates": gates,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "decision.json",
        {
            "status": decision,
            "information_sufficient": sufficient,
            "fixed_gates": gates,
            "bootstrap": boot,
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "development_complete",
            "decision": decision,
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
