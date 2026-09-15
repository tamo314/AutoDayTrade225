"""Finalize R082-Q002 from its already-written fixed-event ledgers only.

This is an artifact repair, not a second market-data evaluation.  The original
runner wrote every frozen profile ledger and daily axis, then stopped at its
post-execution audit because it compared a string trade_date set with a date
set.  This program refuses to access bars or rerun an engine.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import numpy as np
from run_r082_q001_us_open_continuation import report, write_json

from n225m_bt.domain import ExitReason, Side, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.r082_us_open_continuation import bootstrap

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "research" / "r082-q002-20260915-us-open-continuation-01"
RUNNER = ROOT / "scripts" / "run_r082_q002_us_open_continuation.py"
FIXED_EVENTS = OUT / "fixed_q001_primary_events.json"
EXPECTED_EVENT_SHA256 = "c67345cc1f3ae390fd216464af6128ad7030c83414719e1c4cf18b6e2bc111f0"
PROFILE_NAMES = (
    "continuation",
    "fade",
    "pre_open_sign_control",
    "signal_15m",
    "signal_45m",
    "entry_delay_1m",
    "exit_s_plus_120m",
    "exit_s_plus_240m",
    "cost_2tick",
    "cost_3tick",
    "fee_x2",
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def read_trades(profile: str) -> tuple[Trade, ...]:
    payload = cast(list[dict[str, Any]], json.loads((OUT / f"{profile}_trades.json").read_text()))
    return tuple(
        Trade(
            trade_id=item["trade_id"],
            trade_date=date.fromisoformat(item["trade_date"]),
            side=Side(item["side"]),
            qty=item["qty"],
            entry_signal_ts=datetime.fromisoformat(item["entry_signal_ts"]),
            entry_ts=datetime.fromisoformat(item["entry_ts"]),
            entry_reference_price=item["entry_reference_price"],
            entry_fill_price=item["entry_fill_price"],
            exit_signal_ts=datetime.fromisoformat(item["exit_signal_ts"]),
            exit_ts=datetime.fromisoformat(item["exit_ts"]),
            exit_reference_price=item["exit_reference_price"],
            exit_fill_price=item["exit_fill_price"],
            gross_pnl_jpy=item["gross_pnl_jpy"],
            fees_jpy=item["fees_jpy"],
            slippage_cost_jpy=item["slippage_cost_jpy"],
            net_pnl_jpy=item["net_pnl_jpy"],
            mae_jpy=item["mae_jpy"],
            mfe_jpy=item["mfe_jpy"],
            holding_minutes=item["holding_minutes"],
            entry_reason=item["entry_reason"],
            exit_reason=ExitReason(item["exit_reason"]),
            strategy_id=item["strategy_id"],
            strategy_version=item["strategy_version"],
            parameter_hash=item["parameter_hash"],
            metadata=item["metadata"],
        )
        for item in payload
    )


def main() -> None:
    if (OUT / "COMPLETED.json").exists():
        raise FileExistsError("R082-Q002 is already finalized")
    if digest(FIXED_EVENTS) != EXPECTED_EVENT_SHA256:
        raise ValueError("fixed event ledger hash changed")
    events = cast(list[dict[str, object]], json.loads(FIXED_EVENTS.read_text(encoding="utf-8")))
    axis = [date.fromisoformat(cast(str, event["trade_date"])) for event in events]
    ledgers = {name: read_trades(name) for name in PROFILE_NAMES}
    daily = {
        name: cast(dict[str, int | None], json.loads((OUT / f"{name}_daily_axis.json").read_text()))
        for name in PROFILE_NAMES
    }
    profiles = {
        name: report(
            axis,
            trades,
            daily[name],
            cast(list[dict[str, object]], json.loads((OUT / f"{name}_events.json").read_text())),
            primary=name == "continuation",
        )
        for name, trades in ledgers.items()
    }
    common = cast(dict[str, object], json.loads((OUT / "pre_open_common_event_axis.json").read_text()))
    common_axis = cast(list[str], common["trade_dates"])
    common_cont = [cast(int, daily["continuation"][target]) for target in common_axis]
    common_pre = [cast(int, daily["pre_open_sign_control"][target]) for target in common_axis]
    if common_cont != common["continuation_daily_net_jpy"] or common_pre != common["pre_open_sign_control_daily_net_jpy"]:
        raise ValueError("stored common axis does not match stored daily axes")
    boot, primary_index, common_index = bootstrap(
        [cast(int, value) for value in daily["continuation"].values()],
        [cast(int, value) for value in daily["fade"].values()],
        common_cont,
        common_pre,
    )
    np.save(OUT / "bootstrap_primary_axis_indices.npy", primary_index)
    np.save(OUT / "bootstrap_pre_open_common_axis_indices.npy", common_index)
    metrics = cast(dict[str, int | float | None], profiles["continuation"]["metrics"])
    primary_ci = cast(list[float], cast(dict[str, object], boot["continuation_scheduled_axis_mean_net_jpy_per_trade_date"])["ci95_percentile_linear"])
    fade_ci = cast(list[float], cast(dict[str, object], boot["continuation_minus_fade_paired_daily_net_jpy"])["ci95_percentile_linear"])
    pre_ci = cast(list[float], cast(dict[str, object], boot["continuation_minus_pre_open_sign_control_paired_daily_net_jpy_on_common_u_p_axis"])["ci95_percentile_linear"])
    gates = {
        "net_positive": cast(int, metrics["net_pnl_jpy"]) > 0,
        "pf_gt_one": bool(metrics["profit_factor"] and cast(float, metrics["profit_factor"]) > 1),
        "continuation_mbb_ci95_lower_gt_zero": primary_ci[0] > 0,
        "continuation_minus_fade_ci95_lower_gt_zero": fade_ci[0] > 0,
        "continuation_minus_pre_open_sign_control_ci95_lower_gt_zero": pre_ci[0] > 0,
    }
    u_metrics = cast(dict[str, dict[str, int | float | None]], profiles["continuation"]["by_u_sign"])
    time_metrics = cast(dict[str, dict[str, int | float | None]], profiles["continuation"]["by_us_time"])
    years = cast(dict[str, dict[str, int | float | None]], profiles["continuation"]["by_year"])
    concentration_metrics = cast(dict[str, int | float | None], profiles["continuation"]["profit_concentration"])
    sensitivity_names = PROFILE_NAMES[3:]
    candidate_checks = {
        "primary_gate": all(gates.values()),
        "all_fixed_sensitivity_net_positive": all(cast(int, cast(dict[str, int | float | None], profiles[name]["metrics"])["net_pnl_jpy"]) > 0 for name in sensitivity_names),
        "both_u_sign_net_positive": all(cast(int, u_metrics[sign]["net_pnl_jpy"]) > 0 for sign in ("positive", "negative")),
        "both_us_time_net_positive": all(cast(int, time_metrics[state]["net_pnl_jpy"]) > 0 for state in ("dst", "standard")),
        "at_least_three_2021_2024_positive": sum(cast(int, years[str(year)]["net_pnl_jpy"]) > 0 for year in range(2021, 2025)) >= 3,
        "2025_h1_net_positive": cast(int, years["2025"]["net_pnl_jpy"]) > 0,
        "net_excluding_top10_winners_positive": cast(int, concentration_metrics["net_excluding_top10_jpy"]) > 0,
    }
    fixed_dates = {date.fromisoformat(cast(str, event["trade_date"])) for event in events if event.get("status") == "EXECUTABLE"}
    audit = {
        "all_profiles_only_use_fixed_806_ids": all({trade.trade_date for trade in trades}.issubset(fixed_dates) for trades in ledgers.values()),
        "one_trade_per_trade_date": all(len({trade.trade_date for trade in trades}) == len(trades) for trades in ledgers.values()),
        "primary_continuation_fade_same_event_entry_exit_opposite_side": {(trade.trade_date, trade.entry_ts, trade.exit_ts, trade.side.value) for trade in ledgers["continuation"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts, "short" if trade.side is Side.LONG else "long") for trade in ledgers["fade"]},
        "pre_open_control_is_same_u_p_common_dates_and_entry_exit": {(trade.trade_date, trade.entry_ts, trade.exit_ts) for trade in ledgers["pre_open_sign_control"]} == {(trade.trade_date, trade.entry_ts, trade.exit_ts) for trade in ledgers["continuation"] if trade.trade_date.isoformat() in set(common_axis)},
        "no_stop_or_target": all(trade.exit_reason not in {ExitReason.STOP, ExitReason.TARGET} for trades in ledgers.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in ledgers.values() for trade in trades),
    }
    if not all(audit.values()):
        raise ValueError(f"stored-ledger execution audit failed: {audit}")
    repair = {
        "kind": "post_execution_artifact_finalization",
        "market_data_or_engine_rerun": False,
        "cause": "original post-execution audit compared string trade_date values to date values; all profile ledgers and daily axes were already written",
        "runner_before_repair_sha256": digest(OUT / "source_snapshot" / RUNNER.name),
        "corrected_runner_sha256": digest(RUNNER),
        "finalizer_sha256": digest(Path(__file__)),
        "input_ledger_sha256": {name: digest(OUT / f"{name}_trades.json") for name in PROFILE_NAMES},
        "input_daily_axis_sha256": {name: digest(OUT / f"{name}_daily_axis.json") for name in PROFILE_NAMES},
        "fixed_event_sha256": digest(FIXED_EVENTS),
        "output_summary_hash": canonical_hash({"gates": gates, "candidate_checks": candidate_checks, "audit": audit}),
    }
    write_json(OUT / "profiles.json", profiles | {"no_trade_control": {"daily_net_pnl_jpy": {target.isoformat(): 0 for target in axis}, "net_pnl_jpy": 0}})
    write_json(OUT / "bootstrap.json", boot | {"primary_axis_index_file": "bootstrap_primary_axis_indices.npy", "pre_open_common_axis_index_file": "bootstrap_pre_open_common_axis_indices.npy"})
    write_json(OUT / "execution_accounting_audit.json", audit)
    write_json(OUT / "execution_repair.json", repair)
    decision = {"status": "INVESTIGATE" if all(gates.values()) else "REJECT", "decision_ceiling": "INVESTIGATE", "s3_gates": gates, "candidate_checks": candidate_checks, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED", "artifact_finalization": repair}
    write_json(OUT / "decision.json", decision)
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": OUT.name, **decision})


if __name__ == "__main__":
    main()
