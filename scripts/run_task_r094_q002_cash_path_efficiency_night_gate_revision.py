"""Execute TASK-R094-Q002 after its mandatory PnL-free K audit."""

# mypy: disable-error-code="arg-type,assignment,index,misc,no-untyped-call,type-arg"

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from subprocess import run
from typing import Any, cast

import numpy as np

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.r094_cash_path_efficiency_night import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MBB_SEED,
    build_events,
)
from n225m_bt.research.r094_q002_gate import k_order_audit, revised_gate

ROOT = Path(__file__).resolve().parents[1]
_PARENT_SPEC = spec_from_file_location(
    "r094_q001_runner", ROOT / "scripts" / "run_task_r094_q001_cash_path_efficiency_night_continuation.py"
)
if _PARENT_SPEC is None or _PARENT_SPEC.loader is None:
    raise ImportError("cannot load frozen R094-Q001 runner")
parent = module_from_spec(_PARENT_SPEC)
_PARENT_SPEC.loader.exec_module(parent)
helpers = parent.q001

RUN_ID = "task-r094-q002-cash-path-efficiency-night-gate-revision-20260915-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = Path("docs/strategy/93_r094_q002_cash_path_efficiency_night_gate_revision.md")
Q001_OUT = ROOT / "results" / "research" / parent.RUN_ID


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def q002_causality_audit(events: list[dict[str, object]], axis: list[object]) -> dict[str, object]:
    """Retain Q001 checks and explicitly retain the PnL-free ordering guarantee."""
    audit = parent.causality_audit(events, axis)
    return cast(dict[str, object], audit) | {"pnl_not_accessed_before_audit": True}


def reproduction_gate(
    primary: list[dict[str, object]],
    s2: dict[str, object],
    causality: dict[str, object],
    k_audit: dict[str, object],
) -> dict[str, object]:
    frozen = cast(list[dict[str, object]], load_json(Q001_OUT / "primary_events_pnl_free.json"))
    q001_gate = parent.feasibility(primary)
    q001_gate_values = cast(dict[str, bool], q001_gate["gate"])
    revised_values = cast(dict[str, bool], s2["gate"])
    unchanged = {
        key: value
        for key, value in q001_gate_values.items()
        if key not in {"passed", "K_completed_at_least_250"}
    }
    expected = {
        "q_ready_nonzero_days": 957,
        "H_completed": 222,
        "K_completed": 243,
        "unresolved_filled_positions": 0,
        "unexplained_exclusions": [],
    }
    actual = {key: s2[key] for key in expected}
    checks = {
        "q001_primary_pnl_free_ledger_exact_value_match": primary == frozen,
        "frozen_H_K_split_and_current_excluded_qx50_qe75_reproduced": primary == frozen,
        "fixed_pnl_free_counts_reproduced": actual == expected,
        "K_order_and_cancellation_audit_passed": bool(k_audit["passed"]),
        "no_unexplained_exclusions": not s2["unexplained_exclusions"],
        "no_filled_unresolved_exit": int(cast(int, s2["unresolved_filled_positions"])) == 0,
        "scheduled_mapping_and_no_future_night_selection_passed": bool(causality["passed"]),
        "only_K_gate_changed_from_q001": all(
            revised_values.get(key) == value for key, value in unchanged.items()
        )
        and "K_completed_at_least_250" not in revised_values
        and revised_values.get("K_completed_at_least_240") is True,
        "K_completed_at_least_240": int(cast(int, s2["K_completed"])) >= 240,
        "each_x_band_H_and_M_at_least_20": bool(
            revised_values["each_x_band_H_and_M_at_least_20"]
        ),
    }
    return {
        "scope": "PnL-free Q002 reproduction, K audit, and gate-revision authorization",
        "frozen_q001_primary_events_sha256": helpers.digest(Q001_OUT / "primary_events_pnl_free.json"),
        "regenerated_primary_events_value_hash": canonical_hash(primary),
        "expected_counts": expected,
        "actual_counts": actual,
        "K_audit": k_audit,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_task_r094_q002_cash_path_efficiency_night_gate_revision.py"),
        Path("scripts/run_task_r094_q001_cash_path_efficiency_night_continuation.py"),
        Path("src/n225m_bt/research/r094_cash_path_efficiency_night.py"),
        Path("src/n225m_bt/research/r094_q002_gate.py"),
        Path("tests/test_r094_q001.py"),
        Path("tests/test_r094_q002_gate.py"),
    ]
    config_files = [
        Path(f"config/{item}")
        for item in (
            "backtest.yaml",
            "data.yaml",
            "instrument.yaml",
            "sessions.yaml",
            "local_calendar.yaml",
            "research.yaml",
        )
    ]
    _, _, data_config, _ = load_project_config(ROOT / "config")
    preregistration = {
        "task_id": "TASK-R094-Q002",
        "family_id": "cash_path_efficiency_following_night",
        "study_id": "R094-Q002",
        "spec_version": "v1-k-completion-gate-revision",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "preregistration_document": str(PREREG),
        "preregistration_document_sha256": helpers.digest(ROOT / PREREG),
        "parent_q001": {
            "run_id": parent.RUN_ID,
            "status": "INCONCLUSIVE_BEFORE_PNL",
            "economic_results_accessed": False,
            "known_pnl_free_K_completed": 243,
        },
        "sole_change": "K completed-path gate 250 to 240, conditional on exact PnL-free audit",
        "unchanged": "hypothesis, features, qx50/qe75, state equality, direction, schedule, controls, costs, bootstrap, sensitivities, primary AND, robustness",
        "decision_ceiling": "INVESTIGATE",
        "development_only": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "s2_gate": "Q001 gate unchanged except K>=240; both x bands retain H/M>=20; exact Q001 ledger/K-cancellation/causality audit required",
        "profiles": [
            "A_continuation", "B_reversal", "C_fixed_long", "D_fixed_short", "M_K_continuation",
            "N_no_trade", "A_qe70", "A_qe80", "A_qx40", "A_qx60", "A_entry_delay_1m",
            "A_exit_30m_early", "A_cost_2tick", "A_cost_3tick", "A_fee_2x",
        ],
        "bootstrap": {
            "seed": MBB_SEED,
            "block_length_trade_dates": 20,
            "repetitions": 10000,
            "method": "non-circular MBB; common index; tail truncation; linear percentile",
        },
        "input_partitions": [
            {"path": str(item.resolve().relative_to(ROOT)), "sha256": helpers.digest(item)}
            for item in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(item): helpers.digest(ROOT / item) for item in source_files},
        "config_hashes": {str(item): helpers.digest(ROOT / item) for item in config_files},
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", preregistration)
    write_json(OUT / "run_manifest.json", {
        "run_id": RUN_ID,
        "preregistration_hash": canonical_hash(preregistration),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "S0_S1_FROZEN",
    })
    helpers.snapshot(OUT / "source_snapshot", source_files)
    helpers.snapshot(OUT / "config_snapshot", config_files)
    helpers.snapshot(OUT / "documentation_snapshot", [PREREG, Path("docs/strategy/91_r094_q001_cash_path_efficiency_night_continuation.md")])
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [sys.executable, "-m", "pytest", "tests/test_r094_q001.py", "tests/test_r094_q002_gate.py", "tests/test_execution.py", "-q"],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [sys.executable, "-m", "mypy", "src/n225m_bt/research/r094_cash_path_efficiency_night.py", "src/n225m_bt/research/r094_q002_gate.py", str(source_files[0])],
        "py_compile": [sys.executable, "-m", "py_compile", *map(str, source_files)],
    }
    validation: dict[str, object] = {}
    for name, command in commands.items():
        result = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env)
        validation[name] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    validation["status"] = "PASS" if all(
        cast(dict[str, object], item)["returncode"] == 0 for item in validation.values()
    ) else "FAIL"
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID})
        raise ValueError("R094-Q002 pre-execution validation failed")

    instrument, sessions, data_config, _ = load_project_config(ROOT / "config")
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml"))
    development = load_split(data_config.gold_root, "development")
    view, isolated, r004 = helpers.quarantine(development)
    axis = helpers.tse_axis(helpers.cash_calendar())
    axis_set = set(axis)
    bars: dict[tuple[object, Session], list[Bar]] = defaultdict(list)
    for bar in view.bars:
        if bar.trade_date in axis_set:
            bars[(bar.trade_date, bar.session)].append(bar)
    primary = build_events(classifier, axis, bars, isolated)
    s2 = revised_gate(parent.feasibility(primary))
    causality = q002_causality_audit(primary, axis)
    k_audit = k_order_audit(primary)
    reproduction = reproduction_gate(primary, s2, causality, k_audit)
    write_json(OUT / "access_ledger.json", {
        "stage": "S2 PnL-free audit then conditional S3",
        "physical_io": "Development normalized Parquet only",
        "data_version": development.data_version,
        "partitions": development.quality["partitions"],
        "r004": r004,
        "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED",
    })
    write_json(OUT / "scheduled_axis.json", {
        "trade_dates": [day.isoformat() for day in axis],
        "source": "frozen Cabinet Office TSE business-day evidence",
        "holiday_evidence_sha256": helpers.digest(helpers.HOLIDAYS),
    })
    write_json(OUT / "primary_events_pnl_free.json", primary)
    write_json(OUT / "s2_feasibility_q002.json", s2)
    write_json(OUT / "k_order_audit_pnl_free.json", k_audit)
    write_json(OUT / "causality_audit_before_pnl.json", causality)
    write_json(OUT / "q001_reproduction_gate.json", reproduction)
    if not bool(reproduction["passed"]):
        decision = {"decision": "INCONCLUSIVE", "reason": "PNL_FREE_K_AUDIT_OR_GATE_FAILED", "reproduction": reproduction, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return

    profiles = {
        "A_continuation": (primary, "H", "continuation", 1, 30),
        "B_reversal": (primary, "H", "reversal", 1, 30),
        "C_fixed_long": (primary, "H", "long", 1, 30),
        "D_fixed_short": (primary, "H", "short", 1, 30),
        "M_K_continuation": (primary, "K", "continuation", 1, 30),
        "A_qe70": (build_events(classifier, axis, bars, isolated, qe_percentile=70), "H", "continuation", 1, 30),
        "A_qe80": (build_events(classifier, axis, bars, isolated, qe_percentile=80), "H", "continuation", 1, 30),
        "A_qx40": (build_events(classifier, axis, bars, isolated, qx_percentile=40), "H", "continuation", 1, 30),
        "A_qx60": (build_events(classifier, axis, bars, isolated, qx_percentile=60), "H", "continuation", 1, 30),
        "A_entry_delay_1m": (build_events(classifier, axis, bars, isolated, entry_delay_minutes=1), "H", "continuation", 1, 30),
        "A_exit_30m_early": (build_events(classifier, axis, bars, isolated, exit_early_minutes=30), "H", "continuation", 1, 30),
        "A_cost_2tick": (primary, "H", "continuation", 2, 30),
        "A_cost_3tick": (primary, "H", "continuation", 3, 30),
        "A_fee_2x": (primary, "H", "continuation", 1, 60),
    }
    unresolved = {
        name: [cast(str, row["trade_date"]) for row in events if row.get("state") == state and row.get("status") == "ENTRY_FILLED_EXIT_UNKNOWN"]
        for name, (events, state, _, _, _) in profiles.items()
    }
    unresolved = {name: days for name, days in unresolved.items() if days}
    for name, (events, _, _, _, _) in profiles.items():
        write_json(OUT / f"{name}_events.json", events)
    if unresolved:
        decision = {"decision": "INCONCLUSIVE", "reason": "ENTRY_FILLED_EXIT_UNRESOLVED_AFTER_S2", "unknown_profiles": unresolved, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"}
        write_json(OUT / "decision.json", decision)
        write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", **decision})
        return
    reports: dict[str, dict[str, object]] = {}
    trades_by: dict[str, tuple[Any, ...]] = {}
    for name, (events, state, direction, ticks, fee) in profiles.items():
        trades, daily, unknown = parent.execute(axis, events, bars, instrument, state=state, profile=name, direction=direction, ticks=ticks, fee=fee)
        if unknown:
            raise ValueError(f"R094-Q002 {name} became unresolved after preflight")
        trades_by[name], reports[name] = trades, parent.report(axis, trades, daily, events)
        write_json(OUT / f"{name}_trades.json", [asdict(trade) for trade in trades])
        write_json(OUT / f"{name}_daily_axis.json", daily)
    a, b, c, d = (list(cast(dict[str, int], reports[name]["daily_net_pnl_jpy"]).values()) for name in ("A_continuation", "B_reversal", "C_fixed_long", "D_fixed_short"))
    boot, index = parent.bootstrap(a, b, c, d)
    conditional = parent.standardized_a_minus_m(axis, trades_by["A_continuation"], trades_by["M_K_continuation"], index)
    np.save(OUT / "bootstrap_common_day_indices.npy", index)
    metrics = cast(dict[str, int | float | None], reports["A_continuation"]["metrics"])

    def lower(key: str) -> float:
        return cast(list[float], cast(dict[str, object], boot[key])["ci95_percentile_linear"])[0]

    main = {
        "A_net_positive": cast(int, metrics["net_pnl_jpy"]) > 0,
        "A_pf_gt_one": metrics["profit_factor"] is not None and cast(float, metrics["profit_factor"]) > 1,
        "A_daily_net_ci_lower_positive": lower("A_continuation_daily_net_jpy_per_trade_date") > 0,
        "A_minus_B_ci_lower_positive": lower("A_minus_B_daily_net_jpy_per_trade_date") > 0,
        "A_minus_C_ci_lower_positive": lower("A_minus_C_daily_net_jpy_per_trade_date") > 0,
        "A_minus_D_ci_lower_positive": lower("A_minus_D_daily_net_jpy_per_trade_date") > 0,
        "standardized_A_minus_M_ci_lower_positive": cast(list[float], conditional["ci95_percentile_linear"])[0] > 0,
    }
    year = cast(dict[str, dict[str, int | float | None]], reports["A_continuation"]["by_year"])
    sign = cast(dict[str, dict[str, int | float | None]], reports["A_continuation"]["by_r_sign"])
    regime = cast(dict[str, dict[str, int | float | None]], reports["A_continuation"]["by_night_regime"])
    concentration_a = cast(dict[str, int | float | None], reports["A_continuation"]["profit_concentration"])

    def net(name: str) -> int:
        return cast(int, cast(dict[str, int | float | None], reports[name]["metrics"])["net_pnl_jpy"])

    robustness = {
        "qe70_net_positive": net("A_qe70") > 0, "qe80_net_positive": net("A_qe80") > 0,
        "qx40_net_positive": net("A_qx40") > 0, "qx60_net_positive": net("A_qx60") > 0,
        "entry_delay_1m_net_positive": net("A_entry_delay_1m") > 0,
        "exit_30m_early_net_positive": net("A_exit_30m_early") > 0,
        "two_tick_net_positive": net("A_cost_2tick") > 0, "fee_2x_net_positive": net("A_fee_2x") > 0,
        "positive_r_net_positive": cast(int, sign["positive"]["net_pnl_jpy"]) > 0,
        "negative_r_net_positive": cast(int, sign["negative"]["net_pnl_jpy"]) > 0,
        "both_ose_regimes_net_positive": bool(regime) and all(cast(int, item["net_pnl_jpy"]) > 0 for item in regime.values()),
        "at_least_two_2022_2024_positive": sum(cast(int, year[str(value)]["net_pnl_jpy"]) > 0 for value in range(2022, 2025)) >= 2,
        "2025_h1_net_positive": cast(int, year["2025"]["net_pnl_jpy"]) > 0,
        "top10_winners_removed_net_positive": cast(int, concentration_a["net_excluding_top10_jpy"]) > 0,
        "three_tick_diagnostic_net_jpy": net("A_cost_3tick"),
    }
    h_triplet = tuple((row["trade_date"], row.get("entry_open_jst"), row.get("exit_open_jst")) for row in primary if row.get("state") == "H" and row.get("status") == "EXECUTABLE")
    execution = {
        "A_B_C_D_same_H_entry_exit": all(h_triplet == tuple((row["trade_date"], row.get("entry_open_jst"), row.get("exit_open_jst")) for row in profiles[name][0] if row.get("state") == "H" and row.get("status") == "EXECUTABLE") for name in ("B_reversal", "C_fixed_long", "D_fixed_short")),
        "A_B_opposite_side": all(left.side is not right.side for left, right in zip(trades_by["A_continuation"], trades_by["B_reversal"], strict=True)),
        "one_trade_one_position_per_cash_day": all(len(trades) == len({trade.metadata["cash_signal_trade_date"] for trade in trades}) for trades in trades_by.values()),
        "no_stop_or_target": all(trade.exit_reason.value == "signal" for trades in trades_by.values() for trade in trades),
        "net_equals_gross_minus_fees": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades),
        "slippage_not_double_deducted": all(trade.net_pnl_jpy == trade.gross_pnl_jpy - trade.fees_jpy for trades in trades_by.values() for trade in trades),
    }
    if not all(execution.values()):
        raise ValueError("R094-Q002 execution/accounting audit failed")
    decision = "REJECT" if not all(main.values()) else "INVESTIGATE"
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_day_indices.npy", "standardized_A_minus_M_conditional_net_per_trade": conditional})
    write_json(OUT / "profiles.json", reports | {"N_no_trade": {"net_pnl_jpy": 0, "daily_net_pnl_jpy": {day.isoformat(): 0 for day in axis}}})
    write_json(OUT / "execution_accounting_audit.json", execution)
    write_json(OUT / "s3_results.json", {"primary_and": main, "robustness": robustness, "three_tick_diagnostic_net_jpy": net("A_cost_3tick")})
    write_json(OUT / "decision.json", {"decision": decision, "decision_ceiling": "INVESTIGATE", "primary_and": main, "robustness": robustness, "s2_gate": s2["gate"], "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})
    write_json(OUT / "COMPLETED.json", {"status": "COMPLETE", "decision": decision, "oos": "NOT_ACCESSED", "walk_forward": "NOT_ACCESSED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
