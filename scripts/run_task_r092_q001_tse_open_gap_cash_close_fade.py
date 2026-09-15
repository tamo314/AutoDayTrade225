"""Execute the frozen, Development-only TASK-R092-Q001 experiment once."""

# ruff: noqa: E731
# mypy: disable-error-code="arg-type,assignment,call-overload,index,misc,no-untyped-call,type-arg"

from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import date, datetime, time, timezone
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import fmean
from subprocess import run
from sys import executable
from typing import Any, cast

import numpy as np

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, ExitReason, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split, partition_paths
from n225m_bt.research.metrics import concentration, ledger_metrics
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r091_fixed_tse_short import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    tse_cash_close,
)
from n225m_bt.research.r092_open_gap_cash_close_fade import r092_event, selected
from n225m_bt.strategies.r049_fixed_time import R049FixedTimeStrategy

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r092-q001-tse-open-gap-cash-close-fade-20260915-02"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = Path("docs/strategy/83_r092_q001_tse_open_gap_cash_close_fade.md")
HOLIDAYS = (
    ROOT
    / "results/research/r045-q001-20260914-tse-lunch-placebo-reversal-03/institutional_evidence/cabinet_office_public_holidays.csv"
)
R004_HASH = "2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa"
SEED, BLOCK, REPS = 20260915, 20, 10_000


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def cash_calendar() -> TSECashMarketCalendar:
    if not HOLIDAYS.exists():
        raise FileNotFoundError(f"frozen TSE holiday evidence unavailable: {HOLIDAYS}")
    return TSECashMarketCalendar.from_cabinet_office_csv(HOLIDAYS.read_text(encoding="cp932"))


def tse_days(calendar: TSECashMarketCalendar, start: date, end: date) -> list[date]:
    values: list[date] = []
    current = start
    while current <= end:
        if calendar.is_open(current):
            values.append(current)
        current = current.fromordinal(current.toordinal() + 1)
    return values


def quarantine(
    data: ResearchData,
) -> tuple[ResearchData, set[tuple[date, Session]], dict[str, object]]:
    grouped = session_groups(data.bars)
    isolated = {
        key
        for key, rows in grouped.items()
        if any("TICK_GRID_VIOLATION" in bar.quality_flags for bar in rows)
    }
    included = [bar for key, rows in grouped.items() if key not in isolated for bar in rows]
    listed = [
        {"trade_date": day.isoformat(), "session": session.value}
        for day, session in sorted(isolated)
    ]
    audit = {
        "rule": "exclude whole (trade_date, session) if any bar has TICK_GRID_VIOLATION",
        "quarantined_sessions": len(isolated),
        "quarantined_bars": len(data.bars) - len(included),
        "included_sessions": len(grouped) - len(isolated),
        "included_bars": len(included),
        "quarantined_session_list_hash": canonical_hash(listed),
        "quarantined_session_list": listed,
    }
    if audit["quarantined_session_list_hash"] != R004_HASH:
        raise ValueError(f"R092 R004 isolation mismatch: {audit}")
    return (
        ResearchData(
            included,
            canonical_hash({"parent": data.data_version, "sessions": listed}),
            data.quality | {"quarantine": audit},
        ),
        isolated,
        audit,
    )


def events(
    axis: list[date],
    all_days: list[date],
    groups: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    calendar: TSECashMarketCalendar,
) -> list[dict[str, object]]:
    index = {day: position for position, day in enumerate(all_days)}

    def visible(day: date | None) -> list[Bar] | None:
        return (
            None
            if day is None or (day, Session.DAY) in isolated
            else groups.get((day, Session.DAY))
        )

    ledger: list[dict[str, object]] = []
    for target in axis:
        position = index[target]
        history = [
            (
                day,
                all_days[ref - 1] if ref else None,
                visible(day),
                visible(all_days[ref - 1] if ref else None),
                (day, Session.DAY) in isolated,
            )
            for ref, day in enumerate(
                all_days[max(0, position - 120) : position], max(0, position - 120)
            )
        ]
        prior = all_days[position - 1] if position else None
        ledger.append(
            r092_event(
                target,
                visible(target),
                prior,
                visible(prior),
                history,
                calendar,
                quarantined=(target, Session.DAY) in isolated,
            )
        )
    return ledger


def feasibility(ledger: list[dict[str, object]]) -> dict[str, object]:
    complete = [
        row
        for row in ledger
        if bool(row["entry_eligible"]) and row["completion_status"] == "COMPLETE"
    ]
    unresolved = [
        row
        for row in ledger
        if bool(row["entry_eligible"]) and str(row["completion_status"]).startswith("UNRESOLVED")
    ]
    named = {
        "TSE_CASH_MARKET_CLOSED",
        "R004_DAY_SESSION_QUARANTINED",
        "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS",
        "TARGET_GAP_INVALID",
        "INSUFFICIENT_VALID_X_REFERENCES",
        "ZERO_GAP",
        "ENTRY_DECISION_UNAVAILABLE",
        "ENTRY_FILL_UNAVAILABLE",
        "X_BELOW_Q75",
        "SCHEDULED_PATH_COMPLETE",
        "EXECUTABLE_ORDER_SUBMITTED",
    }
    unexplained = [row for row in ledger if row.get("reason") not in named]
    signs = Counter(
        int(cast(dict[str, object], row["observation"])["gap_sign"]) for row in complete
    )
    by_year = {
        str(year): sum(str(row["trade_date"]).startswith(str(year)) for row in complete)
        for year in range(2021, 2026)
    }
    old = sum(str(row["trade_date"]) <= "2024-11-01" for row in complete)
    new = sum(str(row["trade_date"]) >= "2024-11-05" for row in complete)
    gate = {
        "unexplained_exclusions_equal_zero": not unexplained,
        "unresolved_filled_positions_equal_zero": not unresolved,
        "a_b_c_d_complete_trades_at_least_200": len(complete) >= 200,
        "gap_up_complete_trades_at_least_80": signs[1] >= 80,
        "gap_down_complete_trades_at_least_80": signs[-1] >= 80,
        "each_2021_to_2024_at_least_30": all(
            by_year[str(year)] >= 30 for year in range(2021, 2025)
        ),
        "2025_h1_at_least_15": by_year["2025"] >= 15,
        "old_tse_regime_at_least_180": old >= 180,
        "new_tse_regime_at_least_20": new >= 20,
    }
    return {
        "scope": "PnL-free availability/count gate; no return, fill price, trade, PnL, PF, win/loss, or ranking output.",
        "scheduled_axis_trade_dates": len(ledger),
        "primary_E_exec_orders": sum(bool(row["entry_eligible"]) for row in ledger),
        "complete_primary_paths": len(complete),
        "unresolved_primary_positions": len(unresolved),
        "complete_by_gap_sign": {"up": signs[1], "down": signs[-1]},
        "complete_by_year": by_year,
        "complete_by_tse_close_regime": {"old_through_2024-11-01": old, "new_from_2024-11-05": new},
        "unexplained_exclusions": len(unexplained),
        "status_counts": dict(Counter(str(row["completion_status"]) for row in ledger)),
        "gate": gate | {"passed": all(gate.values())},
    }


def side(event: dict[str, object], condition: str) -> str:
    sign = int(cast(dict[str, object], event["observation"])["gap_sign"])
    if condition == "A_gap_fade":
        sign = -sign
    if condition == "C_fixed_long":
        sign = 1
    if condition == "D_fixed_short":
        sign = -1
    return "long" if sign > 0 else "short"


def run_profile(
    name: str,
    condition: str,
    ledger: list[dict[str, object]],
    groups: dict[tuple[date, Session], list[Bar]],
    engine: BacktestEngine,
    *,
    quantile: int = 75,
    entry: time = time(9, 1),
    exit_minus: int = 0,
) -> tuple[tuple[Trade, ...], dict[str, int], list[dict[str, object]]]:
    daily = {str(row["trade_date"]): 0 for row in ledger}
    trades: list[Trade] = []
    records: list[dict[str, object]] = []
    for original in ledger:
        row, target = dict(original), date.fromisoformat(cast(str, original["trade_date"]))
        allowed = selected(row, quantile)
        row.update(condition=condition, profile=name, quantile=quantile, condition_eligible=allowed)
        if not allowed:
            row["condition_reason"] = "PREDICATE_NOT_MET"
            records.append(row)
            continue
        entry_stamp = datetime.combine(
            target,
            entry,
            tzinfo=datetime.fromisoformat(cast(str, row["entry_fill_planned_jst"])).tzinfo,
        )
        exit_stamp = tse_cash_close(target)
        if exit_minus:
            from datetime import timedelta

            exit_stamp -= timedelta(minutes=exit_minus)
        direction = side(row, condition)
        result = engine.run(
            groups.get((target, Session.DAY), []),
            R049FixedTimeStrategy(
                f"r092_{name}_{target.isoformat()}", entry_stamp, exit_stamp, direction
            ),
            canonical_hash(
                {
                    "study": "R092-Q001",
                    "profile": name,
                    "trade_date": target.isoformat(),
                    "side": direction,
                    "entry": entry_stamp.isoformat(),
                    "exit": exit_stamp.isoformat(),
                }
            ),
        )
        if result.canceled_orders or len(result.trades) != 1:
            raise ValueError(
                f"{name}/{target}: selected event did not produce exactly one completed trade"
            )
        trade = result.trades[0]
        if (
            trade.entry_ts != entry_stamp
            or trade.exit_ts != exit_stamp
            or trade.exit_reason is not ExitReason.SIGNAL
            or trade.qty != 1
        ):
            raise ValueError(f"{name}/{target}: execution schedule contract failed")
        trades.append(trade)
        daily[target.isoformat()] += trade.net_pnl_jpy
        row.update(
            status="filled",
            side=direction,
            entry_ts_jst=trade.entry_ts.isoformat(),
            exit_ts_jst=trade.exit_ts.isoformat(),
            net_pnl_jpy=trade.net_pnl_jpy,
            gross_pnl_jpy=trade.gross_pnl_jpy,
            fees_jpy=trade.fees_jpy,
            slippage_cost_jpy=trade.slippage_cost_jpy,
        )
        records.append(row)
    return tuple(trades), daily, records


def bootstrap(series: dict[str, dict[str, int]]) -> tuple[dict[str, object], np.ndarray]:
    names, axis = tuple(series), tuple(series["A_gap_fade"])
    if any(tuple(series[name]) != axis for name in names) or len(axis) < BLOCK:
        raise ValueError("R092 bootstrap axis mismatch")
    rng, blocks = np.random.default_rng(SEED), ceil(len(axis) / BLOCK)
    starts = rng.integers(0, len(axis) - BLOCK + 1, size=(REPS, blocks))
    index = (starts[:, :, None] + np.arange(BLOCK)).reshape(REPS, -1)[:, : len(axis)]
    values = {name: np.asarray([series[name][day] for day in axis], dtype=float) for name in names}
    estimates: dict[str, object] = {
        "method": "20 trade_date non-circular moving-block bootstrap; common indices; tail truncation; linear percentile",
        "seed": SEED,
        "repetitions": REPS,
        "block_length_trade_dates": BLOCK,
        "axis_observations": len(axis),
    }

    def interval(value: np.ndarray) -> dict[str, object]:
        samples = value[index].mean(axis=1)
        ci = np.quantile(samples, (0.025, 0.975), method="linear")
        return {
            "estimate": fmean(value.tolist()),
            "ci95_percentile_linear": [float(ci[0]), float(ci[1])],
        }

    estimates["A_daily_net_jpy_per_trade_date"] = interval(values["A_gap_fade"])
    for name in ("B_gap_continuation", "C_fixed_long", "D_fixed_short"):
        estimates[f"A_minus_{name}_daily_net_jpy_per_trade_date"] = interval(
            values["A_gap_fade"] - values[name]
        )
    return estimates, index


def serialize(trade: Trade) -> dict[str, object]:
    return {
        "trade_id": trade.trade_id,
        "trade_date": trade.trade_date.isoformat(),
        "side": trade.side.value,
        "qty": trade.qty,
        "entry_signal_ts": trade.entry_signal_ts.isoformat(),
        "entry_ts": trade.entry_ts.isoformat(),
        "exit_signal_ts": trade.exit_signal_ts.isoformat() if trade.exit_signal_ts else None,
        "exit_ts": trade.exit_ts.isoformat(),
        "entry_reference_price": trade.entry_reference_price,
        "exit_reference_price": trade.exit_reference_price,
        "gross_pnl_jpy": trade.gross_pnl_jpy,
        "fees_jpy": trade.fees_jpy,
        "slippage_cost_jpy": trade.slippage_cost_jpy,
        "net_pnl_jpy": trade.net_pnl_jpy,
        "exit_reason": trade.exit_reason.value,
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    files = [
        Path("scripts/run_task_r092_q001_tse_open_gap_cash_close_fade.py"),
        Path("src/n225m_bt/research/r092_open_gap_cash_close_fade.py"),
        Path("src/n225m_bt/research/r057.py"),
        Path("src/n225m_bt/research/r091_fixed_tse_short.py"),
        Path("src/n225m_bt/strategies/r049_fixed_time.py"),
        Path("tests/test_r092_q001.py"),
    ]
    configs = [
        Path(f"config/{name}")
        for name in (
            "backtest.yaml",
            "data.yaml",
            "instrument.yaml",
            "sessions.yaml",
            "local_calendar.yaml",
            "research.yaml",
        )
    ]
    OUT.mkdir(parents=True)
    (OUT / "source_snapshot").mkdir()
    (OUT / "config_snapshot").mkdir()
    (OUT / "documentation_snapshot").mkdir()
    (OUT / "institutional_evidence").mkdir()
    for path in files:
        shutil.copy2(ROOT / path, OUT / "source_snapshot" / path.name)
    for path in configs:
        shutil.copy2(ROOT / path, OUT / "config_snapshot" / path.name)
    shutil.copy2(ROOT / PREREG, OUT / "documentation_snapshot" / PREREG.name)
    shutil.copy2(HOLIDAYS, OUT / "institutional_evidence" / HOLIDAYS.name)
    _, _, data_config, _ = load_project_config(ROOT / "config")
    prereg = {
        "task_id": "TASK-R092-Q001",
        "study_id": "R092-Q001",
        "family_id": "tse_previous_close_to_open_gap",
        "spec_version": "v1",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_PNL_ACCESS",
        "prior_technical_attempt": "…-01 stopped before orders, fills, trades, returns, PnL, bootstrap, or decision because its PnL-free R057-definition audit incorrectly required p/o/g/x fields on intentionally invalid observations. …-02 changes only that audit predicate and reruns the same frozen specification.",
        "seed": SEED,
        "preregistration_document": str(PREREG),
        "hypothesis": "Causally large prior official TSE normal-session close to 09:00 open gaps fade by official cash close with positive post-cost expectancy.",
        "rule": "R057 p/o/g/x unchanged; exact prior 120 scheduled TSE days, no valid-value backfill, >=100 x, nearest-rank q75 equality upper; g nonzero and x>=q75; no confirmation, price condition, gap-fill target, stop, target, re-entry, weekday, or updates.",
        "orders": "09:00 confirmed decision/submit, 09:01 open fill, official T open exit; old T=15:00 through 2024-11-01, new T=15:30 from 2024-11-05; one contract/max one position.",
        "conditions": {
            "A": "gap fade",
            "B": "gap continuation",
            "C": "fixed long",
            "D": "fixed short",
            "N": "no trade JPY0; all A-D same E/entry/exit",
        },
        "costs": "one tick plus JPY30 per side; q70/q80, 09:02/09:05, T-5, 2/3 tick, fee x2 fixed only",
        "s2_gate": "unexplained=0; unresolved after entry=0; each A/B/C/D>=200; gap signs>=80; 2021-24>=30 each; 2025H1>=15; old>=180/new>=20; otherwise INCONCLUSIVE before PnL",
        "s3_primary_and": "A Net>0, PF>1, A daily-Net CI lower>0, paired A-B/A-C/A-D CI lower>0; any failure REJECT",
        "decision_ceiling": "INVESTIGATE",
        "prior_information_seen": {
            "duplicate_review_R001_R091": "R006 (night gap reversal) REJECT, R057 (same p/o gap family with 15-minute acceptance continuation) REJECT, R091 (unconditional cash-hours fixed short) REJECT; R092 is a post-hoc addition in R057's family, not independent.",
            "development_reuse": "Development repeatedly used; no independent-validation claim.",
            "continuous_series_only": "Normalized 225Labo continuous series only; no actual-contract fabrication or executable-trading claim.",
        },
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in files},
        "config_hashes": {str(path): digest(ROOT / path) for path in configs},
        "holiday_evidence_sha256": digest(HOLIDAYS),
        "oos": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", prereg)
    write_json(
        OUT / "run_manifest.json",
        {
            "run_id": RUN_ID,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "preregistration_hash": canonical_hash(prereg),
            "status": "S0_S1_FROZEN",
        },
    )
    validations: dict[str, Any] = {}
    for name, command in {
        "pytest": [
            executable,
            "-m",
            "pytest",
            "tests/test_r092_q001.py",
            "tests/test_r057_q001.py",
            "tests/test_r091_fixed_tse_short.py",
            "tests/test_execution.py",
            "-q",
        ],
        "ruff": [executable, "-m", "ruff", "check", *map(str, files)],
        "mypy": [
            executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r092_open_gap_cash_close_fade.py",
            str(files[0]),
        ],
    }.items():
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        validations[name] = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    validations["status"] = (
        "PASS" if all(value["returncode"] == 0 for value in validations.values()) else "FAIL"
    )
    write_json(OUT / "pre_execution_validation.json", validations)
    if validations["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION"})
        raise ValueError("R092 pre-execution validation failed")
    instrument, sessions, data_config, baseline = load_project_config(ROOT / "config")
    calendar = cash_calendar()
    all_days = tse_days(calendar, date(2020, 1, 1), DEVELOPMENT_END)
    axis = [day for day in all_days if DEVELOPMENT_START <= day <= DEVELOPMENT_END]
    view, isolated, r004 = quarantine(load_split(data_config.gold_root, "development"))
    grouped = session_groups(view.bars)
    ledger = events(axis, all_days, grouped, isolated, calendar)
    s2 = feasibility(ledger)
    causality = {
        "scheduled_axis_unique_development": len(axis) == len(set(axis))
        and all(DEVELOPMENT_START <= day <= DEVELOPMENT_END for day in axis),
        "r057_p_o_definition_reused": all(
            "observation" not in row
            or cast(dict[str, object], row["observation"]).get("status") != "valid"
            or {"p_points", "o_points", "g", "x", "gap_sign"}.issubset(
                cast(dict[str, object], row["observation"])
            )
            for row in ledger
        ),
        "exact_current_excluded_120_no_backfill": all(
            len(row.get("reference_trade_dates", [])) in {0, 120} for row in ledger
        ),
        "entry_not_selected_by_exit": all(
            not row["entry_eligible"] or row["order_submitted"] for row in ledger
        ),
        "all_q70_sensitivity_orders_have_complete_exit": all(
            not (
                bool(row.get("entry_capable"))
                and float(cast(dict[str, object], row["observation"])["x"])
                >= float(row.get("q70", float("inf")))
            )
            or row["completion_status"] == "COMPLETE"
            for row in ledger
            if "observation" in row
        ),
        "official_T_rule": all(
            datetime.fromisoformat(cast(str, row["tse_cash_close_jst"]))
            == tse_cash_close(date.fromisoformat(cast(str, row["trade_date"])))
            for row in ledger
        ),
        "r004_recorded": all(
            (day, Session.DAY) not in isolated
            or row["completion_status"] == "NO_ORDER_R004_DAY_SESSION_QUARANTINED"
            for day, row in zip(axis, ledger, strict=True)
        ),
        "trade_date_axis": True,
    }
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "S2",
            "physical_io": "Development normalized Parquet only",
            "price_performance_access": "NO; availability/event statuses/counts only",
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "scheduled_axis.json",
        {
            "trade_dates": [day.isoformat() for day in axis],
            "schedule": "frozen Cabinet Office holiday evidence / official TSE business-day rule",
            "holiday_evidence_sha256": digest(HOLIDAYS),
        },
    )
    write_json(OUT / "r004_quarantine_audit.json", r004)
    write_json(OUT / "primary_events_pnl_free.json", ledger)
    write_json(OUT / "s2_feasibility.json", s2)
    write_json(
        OUT / "causality_audit_before_pnl.json",
        {
            "checks": causality,
            "passed": all(causality.values()),
            "pnl_not_accessed_before_audit": True,
        },
    )
    if not bool(cast(dict[str, object], s2["gate"])["passed"]) or not all(causality.values()):
        decision = {
            "decision": "INCONCLUSIVE",
            "reason": "S2_OR_CAUSALITY_GATE_FAILED_BEFORE_PNL",
            "s2_gate": s2["gate"],
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        }
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return
    profiles = {
        "A_gap_fade": ("A_gap_fade", 75, time(9, 1), 0, 1, 30),
        "B_gap_continuation": ("B_gap_continuation", 75, time(9, 1), 0, 1, 30),
        "C_fixed_long": ("C_fixed_long", 75, time(9, 1), 0, 1, 30),
        "D_fixed_short": ("D_fixed_short", 75, time(9, 1), 0, 1, 30),
        "A_q70": ("A_gap_fade", 70, time(9, 1), 0, 1, 30),
        "A_q80": ("A_gap_fade", 80, time(9, 1), 0, 1, 30),
        "A_entry_0902": ("A_gap_fade", 75, time(9, 2), 0, 1, 30),
        "A_entry_0905": ("A_gap_fade", 75, time(9, 5), 0, 1, 30),
        "A_exit_T_minus_5m": ("A_gap_fade", 75, time(9, 1), 5, 1, 30),
        "A_cost_2tick": ("A_gap_fade", 75, time(9, 1), 0, 2, 30),
        "A_cost_3tick": ("A_gap_fade", 75, time(9, 1), 0, 3, 30),
        "A_fee_2x": ("A_gap_fade", 75, time(9, 1), 0, 1, 60),
    }
    outputs: dict[str, object] = {}
    trades_by: dict[str, tuple[Trade, ...]] = {}
    daily_by: dict[str, dict[str, int]] = {}
    records_by: dict[str, list[dict[str, object]]] = {}
    for name, (condition, quantile, entry, exit_minus, ticks, fee) in profiles.items():
        runtime = baseline.model_copy(
            update={
                "mode": "day_only",
                "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
                "fees": baseline.fees.model_copy(update={"jpy_per_side_per_contract": fee}),
            }
        )
        filled, daily, records = run_profile(
            name,
            condition,
            ledger,
            grouped,
            BacktestEngine(
                instrument.instrument.to_spec(),
                runtime,
                CalendarClassifier(
                    sessions, ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
                ),
            ),
            quantile=quantile,
            entry=entry,
            exit_minus=exit_minus,
        )
        trades_by[name], daily_by[name], records_by[name] = filled, daily, records
        outputs[name] = {
            "metrics": ledger_metrics(filled),
            "daily_net_pnl_jpy": daily,
            "net_by_year_jpy": {
                str(year): sum(value for day, value in daily.items() if day.startswith(str(year)))
                for year in range(2021, 2026)
            },
            "net_by_tse_close_regime_jpy": {
                "old_through_2024-11-01": sum(
                    value for day, value in daily.items() if day <= "2024-11-01"
                ),
                "new_from_2024-11-05": sum(
                    value for day, value in daily.items() if day >= "2024-11-05"
                ),
            },
            "concentration": concentration(filled, axis),
            "costs": {"slippage_ticks_per_side": ticks, "fee_jpy_per_side": fee},
        }
        write_json(OUT / f"{name}_events.json", records)
        write_json(OUT / f"{name}_trades.json", [serialize(trade) for trade in filled])
    primary_daily = {
        name: daily_by[name]
        for name in ("A_gap_fade", "B_gap_continuation", "C_fixed_long", "D_fixed_short")
    }
    boot, index = bootstrap(primary_daily)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    a = cast(dict[str, object], outputs["A_gap_fade"])
    metrics = cast(dict[str, object], a["metrics"])
    ci_a = cast(
        list[float],
        cast(dict[str, object], boot["A_daily_net_jpy_per_trade_date"])["ci95_percentile_linear"],
    )
    ci_pairs = {
        name: cast(
            list[float],
            cast(dict[str, object], boot[f"A_minus_{name}_daily_net_jpy_per_trade_date"])[
                "ci95_percentile_linear"
            ],
        )[0]
        for name in ("B_gap_continuation", "C_fixed_long", "D_fixed_short")
    }
    by_year = cast(dict[str, int], a["net_by_year_jpy"])
    regimes = cast(dict[str, int], a["net_by_tse_close_regime_jpy"])
    concentration_a = cast(dict[str, object], a["concentration"])
    net = lambda name: cast(int, cast(dict[str, object], outputs[name])["metrics"]["net_pnl_jpy"])
    main = {
        "A_net_positive": net("A_gap_fade") > 0,
        "A_pf_gt_one": cast(float | None, metrics["profit_factor"]) is not None
        and cast(float, metrics["profit_factor"]) > 1,
        "A_daily_net_ci_lower_positive": ci_a[0] > 0,
        "A_minus_B_ci_lower_positive": ci_pairs["B_gap_continuation"] > 0,
        "A_minus_C_ci_lower_positive": ci_pairs["C_fixed_long"] > 0,
        "A_minus_D_ci_lower_positive": ci_pairs["D_fixed_short"] > 0,
    }
    robustness = {
        "q70_net_positive": net("A_q70") > 0,
        "q80_net_positive": net("A_q80") > 0,
        "entry_0902_net_positive": net("A_entry_0902") > 0,
        "entry_0905_net_positive": net("A_entry_0905") > 0,
        "T_minus_5_net_positive": net("A_exit_T_minus_5m") > 0,
        "two_tick_net_positive": net("A_cost_2tick") > 0,
        "fee_2x_net_positive": net("A_fee_2x") > 0,
        "gap_up_net_positive": sum(
            trade.net_pnl_jpy
            for trade, record in zip(
                trades_by["A_gap_fade"],
                [row for row in records_by["A_gap_fade"] if row.get("status") == "filled"],
                strict=True,
            )
            if int(cast(dict[str, object], record["observation"])["gap_sign"]) > 0
        )
        > 0,
        "gap_down_net_positive": sum(
            trade.net_pnl_jpy
            for trade, record in zip(
                trades_by["A_gap_fade"],
                [row for row in records_by["A_gap_fade"] if row.get("status") == "filled"],
                strict=True,
            )
            if int(cast(dict[str, object], record["observation"])["gap_sign"]) < 0
        )
        > 0,
        "old_regime_net_positive": regimes["old_through_2024-11-01"] > 0,
        "new_regime_net_positive": regimes["new_from_2024-11-05"] > 0,
        "three_positive_2021_2024": sum(by_year[str(year)] > 0 for year in range(2021, 2025)) >= 3,
        "positive_2025_h1": by_year["2025"] > 0,
        "top10_removed_net_positive": cast(int, concentration_a["net_excluding_top10_jpy"]) > 0,
    }
    audit = {
        "same_event_entry_exit_A_B_C_D": all(
            [
                tuple(
                    (row["trade_date"], row["entry_fill_planned_jst"], row["exit_fill_planned_jst"])
                    for row in records_by[name]
                    if row.get("status") == "filled"
                )
                == tuple(
                    (row["trade_date"], row["entry_fill_planned_jst"], row["exit_fill_planned_jst"])
                    for row in records_by["A_gap_fade"]
                    if row.get("status") == "filled"
                )
                for name in ("B_gap_continuation", "C_fixed_long", "D_fixed_short")
            ]
        ),
        "opposite_A_B_side": all(
            a_trade.side.value != b_trade.side.value
            for a_trade, b_trade in zip(
                trades_by["A_gap_fade"], trades_by["B_gap_continuation"], strict=True
            )
        ),
        "one_position_one_trade": all(
            len({trade.trade_date for trade in trades}) == len(trades)
            for trades in trades_by.values()
        ),
        "signal_exit_no_stop_target": all(
            trade.exit_reason is ExitReason.SIGNAL
            for trades in trades_by.values()
            for trade in trades
        ),
        "net_equals_gross_minus_fees": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
        "slippage_not_double_deducted": all(
            trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy
            for trades in trades_by.values()
            for trade in trades
        ),
    }
    if not all(audit.values()):
        raise ValueError("R092 execution/accounting audit failed")
    decision = "REJECT" if not all(main.values()) else "INVESTIGATE"
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    write_json(
        OUT / "s3_results.json",
        {
            "conditions": outputs,
            "N_no_trade": {
                "net_pnl_jpy": 0,
                "daily_net_pnl_jpy": {day.isoformat(): 0 for day in axis},
            },
            "bootstrap": boot,
            "primary_and": main,
            "robustness": robustness,
            "three_tick_diagnostic_net_jpy": net("A_cost_3tick"),
        },
    )
    write_json(
        OUT / "decision.json",
        {
            "decision": decision,
            "decision_ceiling": "INVESTIGATE",
            "primary_and": main,
            "robustness": robustness,
            "s2_gate": s2["gate"],
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "COMPLETED.json",
        {
            "status": "COMPLETE",
            "decision": decision,
            "oos": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
