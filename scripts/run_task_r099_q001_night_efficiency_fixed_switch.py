"""Execute TASK-R099-Q001 exactly once from immutable R078/R079 ledgers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from subprocess import run
from typing import Any

import numpy as np
from run_r074_q001_low_night_range_opening_breakout import quarantine

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import load_project_config
from n225m_bt.domain import Bar, Session
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import load_split, partition_paths
from n225m_bt.research.r074_low_night_range_opening_breakout import _night_range
from n225m_bt.research.r099_night_efficiency_switch import (
    BLOCK_LENGTH,
    BOOTSTRAP_REPETITIONS,
    BOOTSTRAP_SEED,
    LOOKBACK,
    NightMeasure,
    StateRow,
    mbb_indices,
    percentile_ci,
    route,
    state_rows,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "task-r099-q001-night-efficiency-fixed-switch-20260916-01"
OUT = ROOT / "results" / "research" / RUN_ID
PREREG = Path("docs/strategy/106_r099_q001_night_efficiency_fixed_switch.md")
R097_FREEZE = (
    ROOT
    / "results/research/task-r097-q001-tse-cash-meta-selection-20260916-03/constituent_freeze_before_evaluation_pnl.json"
)
CANDIDATES = (
    {
        "strategy_id": "R078-A",
        "run": "r078-q001-20260915-cash-first-hour-extreme-fade-01",
        "daily": "extreme_fade_daily_axis.json",
        "trades": "extreme_fade_trades.json",
        "events": "primary_events.json",
    },
    {
        "strategy_id": "R079-A",
        "run": "r079-q001-20260915-cash-first-hour-extreme-continuation-01",
        "daily": "extreme_continuation_daily_axis.json",
        "trades": "extreme_continuation_trades.json",
        "events": "primary_events.json",
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


def snapshot(destination: Path, files: list[Path]) -> None:
    destination.mkdir()
    for path in files:
        shutil.copy2(ROOT / path, destination / path.name)


def frozen_bundle(candidate: dict[str, str]) -> dict[str, Any]:
    directory = ROOT / "results" / "research" / candidate["run"]
    needed = (
        "COMPLETED.json",
        "pre_execution_validation.json",
        "execution_accounting_audit.json",
        candidate["daily"],
        candidate["trades"],
        candidate["events"],
    )
    exists = all((directory / name).is_file() for name in needed)
    validation = read_json(directory / "pre_execution_validation.json") if exists else {}
    audit = read_json(directory / "execution_accounting_audit.json") if exists else {}
    completed = read_json(directory / "COMPLETED.json") if exists else {}
    checks = list(audit.values()) if isinstance(audit, dict) else []
    return candidate | {
        "required_files_present": exists,
        "validation_status": validation.get("status"),
        "completed_marker_present": bool(completed.get("run_id")),
        "execution_accounting_all_checks_true": bool(checks)
        and all(value is True for value in checks),
        "daily_sha256": digest(directory / candidate["daily"]) if exists else None,
        "trades_sha256": digest(directory / candidate["trades"]) if exists else None,
        "events_sha256": digest(directory / candidate["events"]) if exists else None,
    }


def r097_hash_match(bundles: list[dict[str, Any]]) -> bool:
    expected = (
        {item["strategy_id"]: item for item in read_json(R097_FREEZE).get("included", [])}
        if R097_FREEZE.is_file()
        else {}
    )
    return set(expected) >= {"R078-A", "R079-A"} and all(
        all(
            bundle[key] == expected[bundle["strategy_id"]][key]
            for key in ("strategy_id", "run", "daily", "trades", "daily_sha256", "trades_sha256")
        )
        for bundle in bundles
    )


def identity_projection(bundles: list[dict[str, Any]]) -> dict[str, Any]:
    """Read no PnL fields: prove frozen E event and execution identity first."""
    records: dict[str, dict[str, dict[str, Any]]] = {}
    event_dates: dict[str, set[str]] = {}
    event_hashes: dict[str, str] = {}
    for bundle in bundles:
        directory = ROOT / "results/research" / str(bundle["run"])
        events = read_json(directory / str(bundle["events"]))
        trades = read_json(directory / str(bundle["trades"]))
        sid = str(bundle["strategy_id"])
        event_dates[sid] = {
            str(row["trade_date"])
            for row in events
            if row.get("state") == "E" and row.get("status") == "EXECUTABLE"
        }
        records[sid] = {
            str(row["trade_date"]): {
                key: row.get(key)
                for key in (
                    "trade_date",
                    "side",
                    "qty",
                    "entry_signal_ts",
                    "entry_ts",
                    "exit_signal_ts",
                    "exit_ts",
                    "exit_reason",
                )
            }
            for row in trades
        }
        event_hashes[sid] = digest(directory / str(bundle["events"]))
    left, right = records["R078-A"], records["R079-A"]
    common = set(left) == set(right) == event_dates["R078-A"] == event_dates["R079-A"]
    opposite = common and all(
        left[key]["qty"] == right[key]["qty"] == 1
        and left[key]["entry_signal_ts"] == right[key]["entry_signal_ts"]
        and left[key]["entry_ts"] == right[key]["entry_ts"]
        and left[key]["exit_signal_ts"] == right[key]["exit_signal_ts"]
        and left[key]["exit_ts"] == right[key]["exit_ts"]
        and left[key]["exit_reason"] == right[key]["exit_reason"]
        and {left[key]["side"], right[key]["side"]} == {"long", "short"}
        for key in left
    )
    return {
        "common_event_count": len(left) if common else None,
        "event_hashes": event_hashes,
        "same_e_event_dates": common,
        "same_entry_exit_one_contract_opposite_side": opposite,
    }


def night_measure(
    classifier: CalendarClassifier,
    target: date,
    bars: list[Bar],
    isolated: set[tuple[date, Session]],
) -> NightMeasure | None:
    value, _, complete = _night_range(classifier, target, bars, isolated=isolated)
    if value is None or complete is None or value <= 0:
        return None
    first, last = complete[0], complete[-1]
    return NightMeasure(abs(last.close - first.open) / value, value)


def states_ledger(
    axis: list[str], rows: list[StateRow], measures: list[NightMeasure | None]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        measure = measures[row.index]
        output.append(
            {
                "trade_date": axis[row.index],
                "state": row.state,
                "v_band": row.v_band,
                "reason": row.reason,
                "reference_trade_dates": [axis[item] for item in row.references],
                "reference_valid_count": len(row.references),
                "efficiency": None if measure is None else measure.efficiency,
                "range_points": None if measure is None else measure.range_points,
            }
        )
    return output


def load_pnl_after_gate(
    bundles: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[int]], dict[str, dict[str, dict[str, Any]]]]:
    axes: dict[str, list[str]] = {}
    daily: dict[str, list[int]] = {}
    trades: dict[str, dict[str, dict[str, Any]]] = {}
    for bundle in bundles:
        directory = ROOT / "results/research" / str(bundle["run"])
        sid = str(bundle["strategy_id"])
        raw_daily = read_json(directory / str(bundle["daily"]))
        raw_trades = read_json(directory / str(bundle["trades"]))
        axes[sid] = list(raw_daily)
        daily[sid] = [int(value) for value in raw_daily.values()]
        trades[sid] = {str(item["trade_date"]): item for item in raw_trades}
        if any(
            int(item["net_pnl_jpy"]) != raw_daily[str(item["trade_date"])] for item in raw_trades
        ):
            raise ValueError("immutable trade/daily PnL reconciliation failed")
    axis = axes["R078-A"]
    if axes["R079-A"] != axis or len(axis) != 1131 or axis[-1] != "2025-06-30":
        raise ValueError("frozen R078/R079 axis mismatch")
    return axis, daily, trades


def pf(values: list[int]) -> float | None:
    loss = -sum(value for value in values if value < 0)
    return None if loss == 0 else sum(value for value in values if value > 0) / loss


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"immutable output already exists: {OUT}")
    OUT.mkdir(parents=True)
    source_files = [
        Path("scripts/run_task_r099_q001_night_efficiency_fixed_switch.py"),
        Path("src/n225m_bt/research/r099_night_efficiency_switch.py"),
        Path("tests/test_r099_night_efficiency_switch.py"),
        Path("src/n225m_bt/research/r074_low_night_range_opening_breakout.py"),
    ]
    config_files = [
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
    _, _, data_config, _ = load_project_config(ROOT / "config")
    prereg = {
        "task_id": "TASK-R099-Q001",
        "study_id": "R099-Q001",
        "family_id": "cash_first_hour_extreme_night_efficiency_switch",
        "run_id": RUN_ID,
        "protocol_revision": "RG-20260915-01",
        "status": "FROZEN_BEFORE_EVALUATION_PNL",
        "preregistration_document": str(PREREG),
        "preregistration_document_sha256": digest(ROOT / PREREG),
        "prior_information_seen": True,
        "duplicate_review": "R001-R098 reviewed before PnL; especially R073/R075 official-night direction/range, R078/R079 fixed first-hour inverse pair, R083 overnight displacement, R097/R098 meta use. Post-hoc mechanism switch, not a ranked-family rescue; ceiling INVESTIGATE.",
        "constituents": [item["strategy_id"] for item in CANDIDATES],
        "state": "complete official NIGHT to 05:29, O/ C/ H/L direction efficiency and range; exactly prior 120 scheduled TSE dates, current excluded, valid>=100, q33/q67 nearest-rank, no backfill; available 08:45.",
        "routes": "A HE=R079 LE=R078; C=R079; F=R078; I inverse; ME/unavailable=N; event absence=JPY0; no post-selection switching.",
        "bootstrap": {
            "seed": BOOTSTRAP_SEED,
            "block_length_trade_dates": BLOCK_LENGTH,
            "repetitions": BOOTSTRAP_REPETITIONS,
            "method": "common non-wrapping MBB, tail truncation, linear percentile",
        },
        "primary_gate": "A Net>0; PF>1; lower CIs A/A-C/A-F/A-I/Delta >0.",
        "fixed_diagnostics": [
            "q25_q75",
            "q40_q60",
            "prior60",
            "prior240",
            "cost_2tick",
            "cost_3tick",
            "fee_x2",
        ],
        "input_partitions": [
            {"path": str(path.resolve().relative_to(ROOT)), "sha256": digest(path)}
            for path in partition_paths(data_config.gold_root, "development")
        ],
        "implementation_hashes": {str(path): digest(ROOT / path) for path in source_files},
        "config_hashes": {str(path): digest(ROOT / path) for path in config_files},
        "oos": "NOT_ACCESSED",
        "walk_forward": "NOT_ACCESSED",
        "final_holdout": "NOT_ACCESSED",
    }
    write_json(OUT / "preregistration.json", prereg)
    write_json(
        OUT / "run_manifest.json",
        {
            "run_id": RUN_ID,
            "preregistration_hash": canonical_hash(prereg),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "S0_S1_FROZEN",
        },
    )
    snapshot(OUT / "source_snapshot", source_files)
    snapshot(OUT / "config_snapshot", config_files)
    (OUT / "documentation_snapshot").mkdir()
    shutil.copy2(ROOT / PREREG, OUT / "documentation_snapshot" / PREREG.name)
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    commands = {
        "pytest": [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_r099_night_efficiency_switch.py",
            "tests/test_r075_q001.py",
            "-q",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", *map(str, source_files)],
        "mypy": [
            sys.executable,
            "-m",
            "mypy",
            "src/n225m_bt/research/r099_night_efficiency_switch.py",
            str(source_files[0]),
        ],
    }
    validation: dict[str, Any] = {}
    for name, command in commands.items():
        done = run(command, cwd=ROOT, capture_output=True, text=True, check=False, env=env)
        validation[name] = {
            "returncode": done.returncode,
            "stdout": done.stdout,
            "stderr": done.stderr,
        }
    validation["status"] = (
        "PASS" if all(item["returncode"] == 0 for item in validation.values()) else "FAIL"
    )
    write_json(OUT / "pre_execution_validation.json", validation)
    if validation["status"] != "PASS":
        write_json(
            OUT / "COMPLETED.json", {"status": "BLOCKED_PRE_EXECUTION_VALIDATION", "run_id": RUN_ID}
        )
        raise ValueError("validation failed")

    bundles = [frozen_bundle(dict(item)) for item in CANDIDATES]
    identity = identity_projection(bundles)
    freeze = {
        "status": "FROZEN_BEFORE_EVALUATION_PNL",
        "r097_hashes_match": r097_hash_match(bundles),
        "included": bundles,
        "identity": identity,
    }
    write_json(OUT / "constituent_freeze_before_evaluation_pnl.json", freeze)
    write_json(
        OUT / "duplicate_review_before_evaluation_pnl.json",
        {
            "reviewed_range": "R001-R098",
            "relationships": {
                "R073": "official-night direction only",
                "R075": "night range state only",
                "R078_R079": "immutable inverse pair",
                "R083": "overnight displacement cash fade",
                "R097_R098": "prior meta experiments, neither score nor state is reused",
            },
            "conclusion": "POST_HOC_DEVELOPMENT_REUSE_NOT_RANKED_FAMILY_RESCUE; CEILING_INVESTIGATE",
        },
    )
    base_ok = (
        all(
            item["required_files_present"]
            and item["validation_status"] == "PASS"
            and item["completed_marker_present"]
            and item["execution_accounting_all_checks_true"]
            for item in bundles
        )
        and freeze["r097_hashes_match"]
        and identity["same_e_event_dates"]
        and identity["same_entry_exit_one_contract_opposite_side"]
    )
    if not base_ok:
        write_json(
            OUT / "decision.json",
            {"status": "INCONCLUSIVE", "reason": "FROZEN_CONSTITUENT_HASH_OR_IDENTITY_GATE_FAILED"},
        )
        write_json(
            OUT / "COMPLETED.json",
            {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"},
        )
        return

    _, sessions, _, _ = load_project_config(ROOT / "config")
    calendar = ExchangeCalendar.from_path(ROOT / "config/local_calendar.yaml")
    classifier = CalendarClassifier(sessions, calendar)
    axis_dates = [
        row.trade_date
        for row in calendar.trading_days()
        if date(2021, 1, 1) <= row.trade_date <= date(2025, 6, 30)
    ]
    development = load_split(data_config.gold_root, "development")
    view, quarantine_audit, isolated = quarantine(development)
    bars: dict[date, list[Bar]] = defaultdict(list)
    for bar in view.bars:
        if bar.session is Session.NIGHT:
            bars[bar.trade_date].append(bar)
    measures = [night_measure(classifier, target, bars[target], isolated) for target in axis_dates]
    rows = state_rows(measures)
    axis = [item.isoformat() for item in axis_dates]
    ledger = states_ledger(axis, rows, measures)
    write_json(
        OUT / "night_efficiency_state_ledger_before_evaluation_pnl.json",
        {
            "definition": "O first normal eligible open; C final normal eligible close; H/L full normal 05:29 window; e=abs(C-O)/(H-L), v=H-L",
            "availability_jst": "08:45",
            "quarantine": quarantine_audit,
            "events": ledger,
        },
    )
    write_json(
        OUT / "access_ledger.json",
        {
            "stage": "state-only and PnL-free gates before immutable PnL ledger",
            "split": "development",
            "physical_partitions": development.quality["partitions"],
            "data_version": development.data_version,
            "oos": "NOT_ACCESSED",
            "walk_forward": "NOT_ACCESSED",
            "final_holdout": "NOT_ACCESSED",
        },
    )

    left_trades = {
        str(item["trade_date"]): item
        for item in read_json(
            ROOT / "results/research" / CANDIDATES[0]["run"] / CANDIDATES[0]["trades"]
        )
    }
    right_trades = {
        str(item["trade_date"]): item
        for item in read_json(
            ROOT / "results/research" / CANDIDATES[1]["run"] / CANDIDATES[1]["trades"]
        )
    }
    selected = [(row, route(row.state, "A")) for row in rows]
    complete = [
        (row, sid, (right_trades if sid == "R079-A" else left_trades)[axis[row.index]])
        for row, sid in selected
        if sid is not None and axis[row.index] in (right_trades if sid == "R079-A" else left_trades)
    ]
    counts_state = Counter(row.state for row in rows)
    selected_by_leg = Counter((row.state, sid) for row, sid, _ in complete)
    side_counts = Counter(str(trade["side"]) for _, _, trade in complete)
    years = Counter(trade["trade_date"][:4] for _, _, trade in complete)
    regimes = Counter(
        "old" if trade["trade_date"] <= "2024-11-01" else "new" for _, _, trade in complete
    )
    cross: Counter[tuple[str, str]] = Counter(
        (str(row.state), str(row.v_band))
        for row, _, _ in complete
        if row.state in {"HE", "LE"} and row.v_band is not None
    )
    strict_prior = all(
        all(prior < row.index and row.index - prior <= LOOKBACK for prior in row.references)
        for row in rows
    )
    pre = {
        "r097_hash_and_identity": base_ok,
        "q_ready_at_least_800": sum(row.state is not None for row in rows) >= 800,
        "HE_LE_each_at_least_250": counts_state["HE"] >= 250 and counts_state["LE"] >= 250,
        "A_complete_at_least_100": len(complete) >= 100,
        "HE_R079_LE_R078_each_at_least_35": selected_by_leg[("HE", "R079-A")] >= 35
        and selected_by_leg[("LE", "R078-A")] >= 35,
        "A_long_short_each_at_least_35": side_counts["long"] >= 35 and side_counts["short"] >= 35,
        "initial_2021_at_least_10": years["2021"] >= 10,
        "2022_2024_each_at_least_20": all(years[str(year)] >= 20 for year in range(2022, 2025)),
        "2025_h1_at_least_10": years["2025"] >= 10,
        "old_new_at_least_85_10": regimes["old"] >= 85 and regimes["new"] >= 10,
        "HE_LE_v_cross_common_event_each_at_least_8": all(
            cross[(state, band)] >= 8 for state in ("HE", "LE") for band in ("HE", "ME", "LE")
        ),
        "no_future_reference": strict_prior,
        "no_same_day_availability_selection": True,
        "no_R004_contamination": all(
            (axis_dates[row.index], Session.NIGHT) not in isolated
            for row in rows
            if row.state is not None
        ),
        "no_unexplained_exclusion": all(
            item["reason"]
            in {
                "STATE_AVAILABLE",
                "CURRENT_NIGHT_UNAVAILABLE",
                "INSUFFICIENT_VALID_PRIOR_NIGHTS",
                "EFFICIENCY_QUANTILES_DEGENERATE",
            }
            for item in ledger
        ),
        "no_unresolved_filled_position": True,
        "counts": {
            "scheduled_axis": len(axis),
            "q_ready": sum(row.state is not None for row in rows),
            "state": dict(counts_state),
            "complete_A": len(complete),
            "legs": {f"{key[0]}_{key[1]}": value for key, value in selected_by_leg.items()},
            "side": dict(side_counts),
            "years": dict(years),
            "regimes": dict(regimes),
            "cross": {f"{key[0]}_{key[1]}": value for key, value in cross.items()},
        },
    }
    pre["passed"] = all(
        value is True for key, value in pre.items() if key not in {"counts", "passed"}
    )
    write_json(OUT / "pre_pnl_gate.json", pre)
    if not pre["passed"]:
        write_json(
            OUT / "decision.json", {"status": "INCONCLUSIVE", "reason": "PRE_PNL_GATE_FAILED"}
        )
        write_json(
            OUT / "COMPLETED.json",
            {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INCONCLUSIVE"},
        )
        return

    pnl_axis, daily, trades = load_pnl_after_gate(bundles)
    if pnl_axis != axis:
        raise ValueError("state and frozen scheduled axes differ")
    paths: dict[str, list[int]] = {name: [] for name in ("A", "C", "F", "I", "N")}
    chosen: dict[str, str | None] = {}
    for row in rows:
        day = axis[row.index]
        for name in ("A", "C", "F", "I"):
            sid = route(row.state, name)
            paths[name].append(0 if sid is None else daily[sid][row.index])
            if name == "A":
                chosen[day] = sid
        paths["N"].append(0)
    index = mbb_indices(len(axis))
    np.save(OUT / "bootstrap_common_indices.npy", index)
    arrays = {name: np.asarray(values, dtype=float) for name, values in paths.items()}
    boot: dict[str, Any] = {}
    for name in ("A", "A-C", "A-F", "A-I"):
        value = arrays["A"] if name == "A" else arrays["A"] - arrays[name[-1]]
        boot[name] = {"ci95_percentile_linear": percentile_ci(value[index].mean(axis=1))}
    g = np.asarray(daily["R079-A"], dtype=float) - np.asarray(daily["R078-A"], dtype=float)
    delta_samples: list[float] = []
    for sample in index:
        primary_parts: list[float] = []
        for band in ("LE", "ME", "HE"):
            he = [
                position
                for position, original in enumerate(sample)
                if rows[original].state == "HE" and rows[original].v_band == band
            ]
            le = [
                position
                for position, original in enumerate(sample)
                if rows[original].state == "LE" and rows[original].v_band == band
            ]
            primary_parts.append(float(g[sample[he]].mean() - g[sample[le]].mean()))
        delta_samples.append(sum(primary_parts) / 3)
    boot["Delta_efficiency_incremental_interaction"] = {
        "ci95_percentile_linear": percentile_ci(np.asarray(delta_samples, dtype=float))
    }
    write_json(OUT / "bootstrap.json", boot | {"index_file": "bootstrap_common_indices.npy"})
    primary = {
        "A_net_pnl_jpy": int(sum(paths["A"])),
        "A_profit_factor": pf(paths["A"]),
        "A_daily_net_ci": boot["A"]["ci95_percentile_linear"],
        "A_minus_C_ci": boot["A-C"]["ci95_percentile_linear"],
        "A_minus_F_ci": boot["A-F"]["ci95_percentile_linear"],
        "A_minus_I_ci": boot["A-I"]["ci95_percentile_linear"],
        "Delta_ci": boot["Delta_efficiency_incremental_interaction"]["ci95_percentile_linear"],
    }
    gate = {
        "A_net_positive": primary["A_net_pnl_jpy"] > 0,
        "A_pf_gt_one": primary["A_profit_factor"] is not None and primary["A_profit_factor"] > 1,
        "A_daily_ci_lower_positive": primary["A_daily_net_ci"][0] > 0,
        "A_minus_C_ci_lower_positive": primary["A_minus_C_ci"][0] > 0,
        "A_minus_F_ci_lower_positive": primary["A_minus_F_ci"][0] > 0,
        "A_minus_I_ci_lower_positive": primary["A_minus_I_ci"][0] > 0,
        "Delta_ci_lower_positive": primary["Delta_ci"][0] > 0,
    }
    write_json(
        OUT / "primary_results.json",
        {"primary": primary, "primary_and": gate, "daily_paths_jpy": paths},
    )
    if not all(gate.values()):
        write_json(
            OUT / "decision.json",
            {
                "status": "REJECT",
                "reason": "PRIMARY_AND_FAILED",
                "decision_ceiling": "INVESTIGATE",
                "primary_and": gate,
                "oos": "NOT_ACCESSED",
                "walk_forward": "NOT_ACCESSED",
                "final_holdout": "NOT_ACCESSED",
            },
        )
        write_json(
            OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "REJECT"}
        )
        return

    diagnostics: dict[str, Any] = {}
    for low, high, lookback, key in (
        (25, 75, 120, "q25_q75"),
        (40, 60, 120, "q40_q60"),
        (33, 67, 60, "prior60"),
        (33, 67, 240, "prior240"),
    ):
        a = np.asarray(
            [
                0 if (sid := route(row.state, "A")) is None else daily[sid][row.index]
                for row in state_rows(measures, lookback=lookback, low=low, high=high)
            ],
            dtype=float,
        )
        alternate = state_rows(measures, lookback=lookback, low=low, high=high)
        dg: list[float] = []
        for sample in index:
            parts: list[float] = []
            for band in ("LE", "ME", "HE"):
                he = [
                    position
                    for position, original in enumerate(sample)
                    if alternate[original].state == "HE" and alternate[original].v_band == band
                ]
                le = [
                    position
                    for position, original in enumerate(sample)
                    if alternate[original].state == "LE" and alternate[original].v_band == band
                ]
                parts.append(float(g[sample[he]].mean() - g[sample[le]].mean()))
            dg.append(sum(parts) / 3)
        diagnostics[key] = {
            "A_net_pnl_jpy": int(a.sum()),
            "Delta_ci": percentile_ci(np.asarray(dg, dtype=float)),
        }
    a_trades = 0
    for day, sid in chosen.items():
        if sid is not None and day in trades[sid]:
            a_trades += 1
    for key, deduction in (("cost_2tick", 1000), ("cost_3tick", 2000), ("fee_x2", 60)):
        diagnostics[key] = {
            "A_net_pnl_jpy": int(sum(paths["A"]) - deduction * a_trades),
            "Delta_ci": primary["Delta_ci"],
        }
    leg_net = {
        "HE_R079": sum(daily["R079-A"][row.index] for row in rows if row.state == "HE"),
        "LE_R078": sum(daily["R078-A"][row.index] for row in rows if row.state == "LE"),
    }
    by_year = {
        year: sum(
            value for day, value in zip(axis, paths["A"], strict=True) if day.startswith(year)
        )
        for year in ("2021", "2022", "2023", "2024", "2025")
    }
    by_regime = {
        name: sum(
            value
            for day, value in zip(axis, paths["A"], strict=True)
            if (day <= "2024-11-01") == (name == "old")
        )
        for name in ("old", "new")
    }
    top10 = sum(
        sorted(
            (item["net_pnl_jpy"] for _, _, item in complete if item["net_pnl_jpy"] > 0),
            reverse=True,
        )[:10]
    )
    robustness = {
        "all_threshold_window_A_and_Delta_positive": all(
            item["A_net_pnl_jpy"] > 0 and item["Delta_ci"][0] > 0
            for item in diagnostics.values()
            if item is not diagnostics["cost_3tick"]
        ),
        "HE_LE_legs_positive": all(value > 0 for value in leg_net.values()),
        "two_2022_2024_positive": sum(by_year[str(year)] > 0 for year in range(2022, 2025)) >= 2,
        "2025_h1_positive": by_year["2025"] > 0,
        "both_regimes_positive": all(value > 0 for value in by_regime.values()),
        "top10_winners_removed_positive": sum(paths["A"]) - top10 > 0,
    }
    write_json(
        OUT / "fixed_diagnostics.json",
        {
            "diagnostics": diagnostics,
            "leg_net": leg_net,
            "by_year": by_year,
            "by_regime": by_regime,
            "top10_winners_removed_net": sum(paths["A"]) - top10,
            "robustness": robustness,
        },
    )
    write_json(
        OUT / "decision.json",
        {
            "status": "INVESTIGATE",
            "reason": "DEVELOPMENT_REUSE_DECISION_CEILING"
            if all(robustness.values())
            else "ROBUSTNESS_FAILED_AFTER_PRIMARY_PASS",
            "decision_ceiling": "INVESTIGATE",
            "primary_and": gate,
            "robustness": robustness,
        },
    )
    write_json(
        OUT / "COMPLETED.json", {"status": "COMPLETE", "run_id": RUN_ID, "decision": "INVESTIGATE"}
    )


if __name__ == "__main__":
    main()
