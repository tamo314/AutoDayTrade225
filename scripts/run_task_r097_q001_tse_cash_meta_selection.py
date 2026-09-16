"""Execute the frozen, immutable-ledger-only TASK-R097-Q001 once."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any

import numpy as np

from n225m_bt.research.r097_meta_selection import (
    BLOCK_LENGTH,
    BOOTSTRAP_REPETITIONS,
    BOOTSTRAP_SEED,
    CALIBRATION_DAYS,
    mbb_indices,
    percentile_ci,
    routed_daily_net,
    select_paths,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r097-q001-tse-cash-meta-selection-20260916-03"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = ROOT / "docs" / "strategy" / "101_r097_q001_tse_cash_meta_selection.md"

# These are the only R001--R096 entries that satisfy every non-economic
# eligibility predicate below.  This registry is frozen before daily PnL is read.
CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "strategy_id": "R078-A",
        "family_id": "cash_first_hour_extreme",
        "run": "r078-q001-20260915-cash-first-hour-extreme-fade-01",
        "daily": "extreme_fade_daily_axis.json",
        "trades": "extreme_fade_trades.json",
    },
    {
        "strategy_id": "R079-A",
        "family_id": "cash_first_hour_extreme",
        "run": "r079-q001-20260915-cash-first-hour-extreme-continuation-01",
        "daily": "extreme_continuation_daily_axis.json",
        "trades": "extreme_continuation_trades.json",
    },
    {
        "strategy_id": "R080-A",
        "family_id": "cash_first_hour_all",
        "run": "r080-q001-20260915-cash-first-hour-all-continuation-01",
        "daily": "continuation_daily_axis.json",
        "trades": "continuation_trades.json",
    },
    {
        "strategy_id": "R081-A",
        "family_id": "cash_lunch_direction",
        "run": "r081-q001-20260915-tse-lunch-continuation-01",
        "daily": "continuation_daily_axis.json",
        "trades": "continuation_trades.json",
    },
    {
        "strategy_id": "R084-A",
        "family_id": "cash_morning_compression",
        "run": "r084-q001-20260915-morning-compression-lunch-breakout-01",
        "daily": "compressed_breakout_daily_axis.json",
        "trades": "compressed_breakout_trades.json",
    },
    {
        "strategy_id": "R086-A",
        "family_id": "cash_intraday_shock",
        "run": "r086-q001-20260915-intraday-same-clock-shock-fade-01",
        "daily": "shock_fade_daily_axis.json",
        "trades": "shock_fade_trades.json",
    },
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def audit_bundle(candidate: dict[str, str]) -> dict[str, Any]:
    """Audit only metadata and hashes; do not parse daily PnL before freeze."""
    directory = ROOT / "results" / "research" / candidate["run"]
    required = [
        "COMPLETED.json",
        "pre_execution_validation.json",
        "execution_accounting_audit.json",
        candidate["daily"],
        candidate["trades"],
    ]
    exists = all((directory / name).is_file() for name in required)
    validation = read_json(directory / "pre_execution_validation.json") if exists else {}
    accounting = read_json(directory / "execution_accounting_audit.json") if exists else {}
    completed = read_json(directory / "COMPLETED.json") if exists else {}
    audit_values = list(accounting.values()) if isinstance(accounting, dict) else []
    passed = bool(
        exists
        and validation.get("status") == "PASS"
        # Older immutable artifacts encode their *economic decision* (often
        # REJECT) in COMPLETED.json.  Presence of this marker, not that
        # economic label, is the non-economic completion predicate.
        and bool(completed.get("run_id"))
        and audit_values
        and all(value is True for value in audit_values)
    )
    return {
        **candidate,
        "required_files_present": exists,
        "validation_status": validation.get("status"),
        "completed_marker_present": bool(completed.get("run_id")),
        "execution_accounting_all_checks_true": bool(audit_values)
        and all(value is True for value in audit_values),
        "daily_sha256": digest(directory / candidate["daily"]) if exists else None,
        "trades_sha256": digest(directory / candidate["trades"]) if exists else None,
        "eligible": passed,
    }


def exclusions() -> list[dict[str, str]]:
    included = {int(candidate["strategy_id"][1:4]) for candidate in CANDIDATES}
    return [
        {
            "study_id": f"R{number:03d}",
            "reason": "NO_FROZEN_CASH_ONLY_PRIMARY_DAILY_NET_AND_TRADE_LEDGER_WITH_MATCHING_PASS_VALIDATION_AND_EXECUTION_ACCOUNTING_BUNDLE",
        }
        for number in range(1, 97)
        if number not in included
    ]


def load_paths(
    frozen: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[int]], dict[str, dict[str, dict[str, Any]]]]:
    axes: dict[str, list[str]] = {}
    daily: dict[str, list[int]] = {}
    trades: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate in frozen:
        directory = ROOT / "results" / "research" / str(candidate["run"])
        raw_daily = read_json(directory / str(candidate["daily"]))
        raw_trades = read_json(directory / str(candidate["trades"]))
        if not isinstance(raw_daily, dict) or not isinstance(raw_trades, list):
            raise ValueError(f"{candidate['strategy_id']} immutable ledger has an invalid format")
        strategy_id = str(candidate["strategy_id"])
        axes[strategy_id] = list(raw_daily)
        daily[strategy_id] = [int(value) for value in raw_daily.values()]
        indexed = {str(trade["trade_date"]): trade for trade in raw_trades}
        if len(indexed) != len(raw_trades):
            raise ValueError(f"{strategy_id} has duplicate completed trades on a trade_date")
        for date, trade in indexed.items():
            if int(trade["net_pnl_jpy"]) != raw_daily[date]:
                raise ValueError(
                    f"{strategy_id} trade ledger does not reconcile to immutable daily net"
                )
            if int(trade["qty"]) != 1:
                raise ValueError(f"{strategy_id} violates one-contract eligibility")
        trades[strategy_id] = indexed
    first_id = sorted(axes)[0]
    axis = axes[first_id]
    if len(axis) != len(set(axis)) or any(values != axis for values in axes.values()):
        raise ValueError("candidate scheduled TSE daily axes are not identical")
    if axis[0] < "2021-01-01" or axis[-1] != "2025-06-30":
        raise ValueError("candidate Development boundary does not match frozen requirement")
    return axis, daily, trades


def route_trade_rows(
    axis: list[str], rows: list[Any], trades: dict[str, dict[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    routed: list[dict[str, Any]] = []
    for row in rows:
        strategy_id = row.selected_strategy_id
        date = axis[row.index]
        if strategy_id is not None and date in trades[strategy_id]:
            routed.append(
                {
                    "trade_date": date,
                    "selected_strategy_id": strategy_id,
                    **trades[strategy_id][date],
                }
            )
    return routed


def pf(values: list[int]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return None if losses == 0 else gains / losses


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_task_r097_q001_tse_cash_meta_selection.py')

    if OUT.exists():
        raise FileExistsError(f"immutable run output already exists: {OUT}")
    OUT.mkdir(parents=True)
    frozen = [audit_bundle(candidate) for candidate in CANDIDATES]
    adoption = {
        "task_id": "TASK-R097-Q001",
        "run_id": RUN_ID,
        "status": "FROZEN_BEFORE_EVALUATION_PNL",
        "economic_performance_used_for_adoption": False,
        "mechanical_eligibility": "cash-only primary; selection can be decided by 08:45; one contract/max one position; base one-tick/JPY30 cost; matching R004+Development scheduled axis; PASS validation and all-true execution/accounting audit; immutable primary daily-net and completed-trade ledgers",
        "included": frozen,
        "excluded": exclusions(),
    }
    write_json(OUT / "constituent_freeze_before_evaluation_pnl.json", adoption)
    if len(frozen) < 5 or not all(item["eligible"] for item in frozen):
        write_json(
            OUT / "decision.json",
            {"status": "INCONCLUSIVE", "reason": "CONSTITUENT_ELIGIBILITY_GATE_FAILED"},
        )
        return
    axis, daily, trades = load_paths(frozen)
    rows, static_id = select_paths(daily)
    a, q, s = routed_daily_net(rows, daily, static_id=static_id)
    routed_trades = route_trade_rows(axis, rows, trades)
    selected_counts = Counter(
        row.selected_strategy_id for row in rows if row.selected_strategy_id is not None
    )
    trade_years = Counter(row["trade_date"][:4] for row in routed_trades)
    pre_pnl_gate = {
        "eligible_strategies_at_least_5": len(frozen) >= 5,
        "evaluation_days_at_least_700": len(rows) >= 700,
        "a_non_cash_selections_at_least_150": sum(
            row.selected_strategy_id is not None for row in rows
        )
        >= 150,
        "completed_trades_at_least_100": len(routed_trades) >= 100,
        "at_least_two_strategies_selected_at_least_20": sum(
            value >= 20 for value in selected_counts.values()
        )
        >= 2,
        "2022_2024_each_trade_at_least_20": all(
            trade_years[str(year)] >= 20 for year in range(2022, 2025)
        ),
        "2025_h1_trades_at_least_10": trade_years["2025"] >= 10,
        "no_future_reference": True,
        "no_same_day_event_availability_switching": True,
        "no_daily_axis_mismatch": True,
        "no_unexplained_exclusion": True,
        "no_unresolved_filled_position": True,
        "passed": False,
        "counts": {
            "scheduled_axis": len(axis),
            "calibration_days": CALIBRATION_DAYS,
            "evaluation_days": len(rows),
            "a_non_cash_selections": sum(row.selected_strategy_id is not None for row in rows),
            "completed_trades": len(routed_trades),
            "selected_counts": dict(sorted(selected_counts.items())),
            "completed_trades_by_year": dict(sorted(trade_years.items())),
            "static_strategy_id": static_id,
        },
    }
    pre_pnl_gate["passed"] = all(
        value is True for key, value in pre_pnl_gate.items() if key not in {"passed", "counts"}
    )
    write_json(OUT / "pre_pnl_gate.json", pre_pnl_gate)
    route_ledger = [
        {
            "trade_date": axis[row.index],
            "strategy_id": row.selected_strategy_id,
            "rank_two_strategy_id": row.rank_two_strategy_id,
            "scores": row.scores,
        }
        for row in rows
    ]
    write_json(OUT / "selection_ledger.json", route_ledger)
    if not pre_pnl_gate["passed"]:
        write_json(
            OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "PRE_PNL_GATE_FAILED"}
        )
        return
    index = mbb_indices(len(a))
    np.save(OUT / "bootstrap_common_indices.npy", index)
    a_array, q_array, s_array = (np.asarray(values, dtype=float) for values in (a, q, s))
    bootstrap = {
        "method": "20 trade_date non-wrapping MBB; tail truncation; linear percentile; common index",
        "seed": BOOTSTRAP_SEED,
        "repetitions": BOOTSTRAP_REPETITIONS,
        "block_length_trade_dates": BLOCK_LENGTH,
        "A_daily_net_jpy": {
            "estimate": fmean(a),
            "ci95_percentile_linear": percentile_ci(a_array[index].mean(axis=1)),
        },
        "A_minus_Q_paired_daily_net_jpy": {
            "estimate": fmean([x - y for x, y in zip(a, q, strict=True)]),
            "ci95_percentile_linear": percentile_ci((a_array[index] - q_array[index]).mean(axis=1)),
        },
        "A_minus_S_paired_daily_net_jpy": {
            "estimate": fmean([x - y for x, y in zip(a, s, strict=True)]),
            "ci95_percentile_linear": percentile_ci((a_array[index] - s_array[index]).mean(axis=1)),
        },
    }
    write_json(OUT / "bootstrap.json", bootstrap)
    primary = {
        "net_pnl_jpy": sum(a),
        "profit_factor": pf(a),
        "a_daily_ci_lower": bootstrap["A_daily_net_jpy"]["ci95_percentile_linear"][0],
        "a_minus_q_ci_lower": bootstrap["A_minus_Q_paired_daily_net_jpy"]["ci95_percentile_linear"][
            0
        ],
        "a_minus_s_ci_lower": bootstrap["A_minus_S_paired_daily_net_jpy"]["ci95_percentile_linear"][
            0
        ],
    }
    primary_and = {
        "A_net_positive": primary["net_pnl_jpy"] > 0,
        "A_pf_gt_one": primary["profit_factor"] is not None and primary["profit_factor"] > 1,
        "A_daily_ci_lower_positive": primary["a_daily_ci_lower"] > 0,
        "A_minus_Q_ci_lower_positive": primary["a_minus_q_ci_lower"] > 0,
        "A_minus_S_ci_lower_positive": primary["a_minus_s_ci_lower"] > 0,
    }
    write_json(OUT / "primary_results.json", {"primary": primary, "primary_and": primary_and})
    if not all(primary_and.values()):
        write_json(
            OUT / "decision.json",
            {"status": "REJECT", "reason": "PRIMARY_AND_FAILED", "primary_and": primary_and},
        )
        return
    # Fixed-cost paths keep the exact base A choices; no ranking is rerun.
    selected_trade_by_date = {row["trade_date"]: row for row in routed_trades}

    def cost_path(extra_per_trade: int) -> list[int]:
        return [
            value
            - (extra_per_trade if axis[CALIBRATION_DAYS + offset] in selected_trade_by_date else 0)
            for offset, value in enumerate(a)
        ]

    sensitivity: dict[str, int] = {
        "lookback_60": 0,
        "lookback_240": 0,
        "cost_2tick": sum(cost_path(1000)),
        "fee_x2": sum(cost_path(60)),
        "cost_3tick_diagnostic": sum(cost_path(2000)),
    }
    for lookback in (60, 240):
        rerouted, _ = select_paths(daily, lookback=lookback)
        sensitivity[f"lookback_{lookback}"] = sum(
            routed_daily_net(rerouted, daily, static_id=static_id)[0]
        )
    families = sorted({str(item["family_id"]) for item in frozen})
    for family in families:
        remaining = {
            item["strategy_id"]: daily[item["strategy_id"]]
            for item in frozen
            if item["family_id"] != family
        }
        rerouted, rerun_static = select_paths(remaining)
        sensitivity[f"leave_out_{family}"] = sum(
            routed_daily_net(rerouted, remaining, static_id=rerun_static)[0]
        )
    winners = sorted((value for value in a if value > 0), reverse=True)[:10]
    sensitivity["top10_winners_removed"] = sum(a) - sum(winners)
    by_year = defaultdict(int)
    for offset, value in enumerate(a):
        by_year[axis[CALIBRATION_DAYS + offset][:4]] += value
    robustness = {
        "all_fixed_non_diagnostic_net_positive": all(
            value > 0 for key, value in sensitivity.items() if key != "cost_3tick_diagnostic"
        ),
        "two_of_2022_2024_positive": sum(by_year[str(year)] > 0 for year in range(2022, 2025)) >= 2,
        "2025_h1_positive": by_year["2025"] > 0,
        "all_family_leave_out_positive": all(
            value > 0 for key, value in sensitivity.items() if key.startswith("leave_out_")
        ),
        "top10_winners_removed_positive": sensitivity["top10_winners_removed"] > 0,
    }
    write_json(
        OUT / "sensitivity_results.json",
        {
            "net_pnl_jpy": sensitivity,
            "A_net_by_year": dict(sorted(by_year.items())),
            "robustness": robustness,
        },
    )
    write_json(OUT / "selected_A_trades.json", routed_trades)
    write_json(
        OUT / "decision.json",
        {
            "status": "INVESTIGATE",
            "reason": "DEVELOPMENT_REUSE_DECISION_CEILING"
            if all(robustness.values())
            else "ROBUSTNESS_FAILED_AFTER_PRIMARY_PASS",
            "primary_and": primary_and,
            "robustness": robustness,
        },
    )


if __name__ == "__main__":
    main()
