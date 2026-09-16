"""Audit the R053-Q001 legacy bootstrap rank failure without evaluating PnL."""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any, cast

import numpy as np

from n225m_bt.config import load_project_config
from n225m_bt.research.data import load_split
from n225m_bt.research.r006 import session_groups
from n225m_bt.research.runner import write_json

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "results" / "research" / "r053-q001-20260915-tse-afternoon-open-gap-fade-06"
OUT = ROOT / "results" / "research" / "r053-q001-20260915-tse-afternoon-open-gap-fade-07-bootstrap-rank-audit"
SEED = 20261001
BLOCK = 20


def load_legacy_module() -> Any:
    path = ROOT / "scripts" / "run_r053_q001_tse_afternoon_open_gap_fade.py"
    specification = importlib.util.spec_from_file_location("r053_q001_legacy", path)
    if specification is None or specification.loader is None:
        raise ImportError("unable to load R053-Q001 legacy executor")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def make_design_rows() -> tuple[list[dict[str, object]], list[str]]:
    legacy = load_legacy_module()
    _, _, data_config, _ = load_project_config(ROOT / "config")
    development = legacy.quarantine(load_split(data_config.gold_root, "development"))[0]
    bars_by_day = {
        key[0]: sorted(rows, key=lambda bar: bar.ts_jst)
        for key, rows in session_groups(development.bars).items()
        if key[1].value == "day"
    }
    events = json.loads((LEGACY / "all_candidate_event_ledger.json").read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise ValueError("R053-Q001 event ledger must be a list")
    rows: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict) or event.get("status") != "E" or not event.get("direction_eligible"):
            continue
        trade_date = cast(str, event["trade_date"])
        target = date.fromisoformat(trade_date)
        lookup = {bar.ts_jst: bar for bar in bars_by_day[target]}
        entry = datetime.fromisoformat(cast(str, event["planned_entry_jst"]))
        morning = [
            lookup[entry.replace(hour=9, minute=0) + timedelta(minutes=index)]
            for index in range(150)
        ]
        rows.append(
            {
                "trade_date": trade_date,
                "Q": int(float(event["x"]) >= float(event["q90"])),
                "z": float(np.log(float(event["x"]) / float(event["q90"]))),
                "morning_reverse_adjusted_return_bps": float(
                    event["morning_reverse_adjusted_return_bps"]
                ),
                "morning_range_bps": (max(bar.high for bar in morning) - min(bar.low for bar in morning))
                / morning[0].open
                * 10_000,
                "gap_up": int(int(event["gap_sign"]) > 0),
                "gap_direction": "up" if int(event["gap_sign"]) > 0 else "down",
            }
        )
    axis = legacy.fixed_axis()
    return rows, axis


def design_matrix(rows: list[dict[str, object]]) -> tuple[np.ndarray, list[str]]:
    years = sorted({date.fromisoformat(cast(str, row["trade_date"])).year for row in rows})
    names = [
        "intercept",
        "Q",
        "z_ln_x_over_q90",
        "morning_reverse_adjusted_return_bps",
        "morning_range_bps",
        "gap_up",
        *[f"calendar_year_{year}" for year in years[1:]],
    ]
    matrix = np.asarray(
        [
            [
                1.0,
                float(row["Q"]),
                float(row["z"]),
                float(row["morning_reverse_adjusted_return_bps"]),
                float(row["morning_range_bps"]),
                float(row["gap_up"]),
                *[
                    float(date.fromisoformat(cast(str, row["trade_date"])).year == year)
                    for year in years[1:]
                ],
            ]
            for row in rows
        ],
        dtype=float,
    )
    return matrix, names


def counts(rows: list[dict[str, object]], weights: np.ndarray | None = None) -> list[dict[str, object]]:
    values: Counter[tuple[int, int, str]] = Counter()
    for index, row in enumerate(rows):
        amount = 1 if weights is None else int(weights[index])
        if amount:
            values[(date.fromisoformat(cast(str, row["trade_date"])).year, int(row["Q"]), cast(str, row["gap_direction"]))] += amount
    return [
        {"year": year, "Q": q, "gap_direction": direction, "count": value}
        for (year, q, direction), value in sorted(values.items())
    ]


def diagnostics(design: np.ndarray, names: list[str]) -> dict[str, object]:
    singular = np.linalg.svd(design, full_matrices=False)
    rank = int(np.linalg.matrix_rank(design))
    prior_rank = 0
    dependent: list[str] = []
    for column, name in enumerate(names):
        next_rank = int(np.linalg.matrix_rank(design[:, : column + 1]))
        if next_rank == prior_rank:
            dependent.append(name)
        prior_rank = next_rank
    null_count = design.shape[1] - rank
    vectors = singular.Vh[-null_count:] if null_count else np.empty((0, design.shape[1]))
    return {
        "rows": int(design.shape[0]),
        "columns": names,
        "rank": rank,
        "column_count": int(design.shape[1]),
        "singular_values": [float(value) for value in singular.S],
        "matrix_rank_default_tolerance": float(max(design.shape) * np.finfo(float).eps * singular.S[0]),
        "greedy_dependent_columns": dependent,
        "nullspace_right_singular_vectors": [
            {name: float(value) for name, value in zip(names, vector, strict=True)} for vector in vectors
        ],
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry("script:scripts/audit_r053_q001_bootstrap_rank.py")

    if OUT.exists():
        raise FileExistsError(f"immutable R053 rank-audit output exists: {OUT}")
    blocked = json.loads((LEGACY / "BLOCKED.json").read_text(encoding="utf-8"))
    if blocked.get("status") != "BLOCKED":
        raise ValueError("R053-Q001 legacy record is not BLOCKED")
    OUT.mkdir(parents=True)
    rows, axis = make_design_rows()
    full, names = design_matrix(rows)
    row_index = {cast(str, row["trade_date"]): index for index, row in enumerate(rows)}
    rng = Random(SEED)
    failure: dict[str, object] | None = None
    for replicate in range(1, 10_001):
        indices: list[int] = []
        while len(indices) < len(axis):
            start = rng.randrange(len(axis) - BLOCK + 1)
            indices.extend(range(start, start + BLOCK))
        indices = indices[: len(axis)]
        weights = np.zeros(len(rows), dtype=float)
        for axis_index in indices:
            row = row_index.get(axis[axis_index])
            if row is not None:
                weights[row] += 1.0
        included = weights > 0
        active = np.any(full[included] != 0.0, axis=0)
        weighted = full[included][:, active] * np.sqrt(weights[included])[:, None]
        if np.linalg.matrix_rank(weighted) != weighted.shape[1] or not active[1]:
            active_names = [name for name, value in zip(names, active, strict=True) if value]
            failure = {
                "replicate_number_one_based": replicate,
                "sampled_axis_indices_zero_based": indices,
                "sampled_trade_dates": [axis[index] for index in indices],
                "unique_sampled_axis_index_count": len(set(indices)),
                "legacy_absent_calendar_year_columns_removed": [
                    name for name, value in zip(names, active, strict=True) if not value
                ],
                "Q_active_after_legacy_column_filter": bool(active[1]),
                "resample_counts_by_year_Q_gap_direction": counts(rows, weights),
                "legacy_active_design": diagnostics(weighted, active_names),
            }
            break
    if failure is None:
        raise ValueError("legacy bootstrap failure was not reproducible with the frozen seed and index")
    write_json(
        OUT / "bootstrap_rank_audit.json",
        {
            "audit_scope": "Design-matrix rank only. No y, orders, fills, trade PnL, price performance, or conditional PnL were computed or emitted.",
            "legacy_experiment_id": blocked["experiment_id"],
            "legacy_blocked_record": blocked,
            "bootstrap": {
                "seed": SEED,
                "repetitions_examined_until_first_failure": failure["replicate_number_one_based"],
                "block_length_trade_dates": BLOCK,
                "axis_length": len(axis),
                "noncircular": True,
                "tail_truncation": True,
            },
            "full_observational_design": {
                **diagnostics(full, names),
                "counts_by_year_Q_gap_direction": counts(rows),
            },
            "first_legacy_rank_deficient_replicate": failure,
        },
    )


if __name__ == "__main__":
    main()
