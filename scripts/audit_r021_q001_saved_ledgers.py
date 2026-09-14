"""Read-only supplemental audit for completed R021-Q001 saved ledgers."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import polars as pl

from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.runner import reserve_directory, snapshot_source, write_json

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "results" / "research" / "r021-q001-20260914-opening-cash-close-followthrough-05"
IDENTIFIER = "r021-q001-20260914-opening-cash-close-followthrough-saved-ledger-audit-02"
OUT = ROOT / "results" / "research" / IDENTIFIER
CONDITIONS = ("A_opening_follow", "B_always_long", "C_short", "D_preclose_follow", "F_reverse")
FIELDS = (
    "trade_date",
    "pre_event_status",
    "reason",
    "O_points",
    "M_points",
    "A_direction",
    "t_signal_bar_start_jst",
    "E_planned_entry_jst",
    "X_planned_exit_jst",
    "side",
    "entry_ts_jst",
    "exit_ts_jst",
)


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def loaded(name: str) -> pl.DataFrame:
    path = CAMPAIGN / name / "events.parquet"
    if not path.exists():
        raise ValueError(f"missing saved R021 ledger: {path}")
    return pl.read_parquet(path).sort("trade_date")


def row_value(frame: pl.DataFrame, index: int, field: str) -> object:
    return frame[field][index] if field in frame.columns else None


def regime(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        pl.when(pl.col("trade_date") <= "2024-11-01")
        .then(pl.lit("old_C_1500"))
        .otherwise(pl.lit("new_C_1530"))
        .alias("cash_close_regime")
    )


def summary(frame: pl.DataFrame) -> list[dict[str, object]]:
    filled = regime(frame.filter(pl.col("status") == "filled"))
    return [
        {
            "cash_close_regime": row["cash_close_regime"],
            "trade_count": row["trade_count"],
            "fees_jpy": row["fees_jpy"],
            "net_pnl_jpy": row["net_pnl_jpy"],
        }
        for row in filled.group_by("cash_close_regime")
        .agg(
            pl.len().alias("trade_count"),
            pl.col("fees_jpy").sum().alias("fees_jpy"),
            pl.col("net_pnl_jpy").sum().alias("net_pnl_jpy"),
        )
        .sort("cash_close_regime")
        .to_dicts()
    ]


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    files = {
        f"{name}/events.parquet": sha256_file(CAMPAIGN / name / "events.parquet")
        for name in (*CONDITIONS, "A_opening_follow_2tick")
    }
    files["COMPLETED.json"] = sha256_file(CAMPAIGN / "COMPLETED.json")
    output = reserve_directory(OUT.parent, IDENTIFIER)
    source = snapshot_source(output, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    preregistration = {
        "audit_id": IDENTIFIER,
        "study_id": "R021-Q001",
        "status": "frozen_before_saved_ledger_read",
        "scope": "Read-only saved R021 -03 events ledgers only; no bar/raw/external price/engine/OOS/Final Holdout access.",
        "required": "A/A2 must have the same event universe, direction, planned and actual timestamps; differences are restricted to configured 1 versus 2 tick execution economics. Save old/new TSE C regimes for each condition and A-D difference.",
        "source_artifacts": files,
        "source_artifacts_hash": canonical_hash(files),
        "source_snapshot_hash": source["source_hash"],
    }
    write_json(output / "preregistration.json", preregistration)
    a, a2, d = (
        loaded("A_opening_follow"),
        loaded("A_opening_follow_2tick"),
        loaded("D_preclose_follow"),
    )
    checks = {
        "A_A2_equal_row_count": a.height == a2.height,
        "A_A2_equal_pre_event_direction_and_timestamps": a.height == a2.height
        and all(
            tuple(row_value(a, index, field) for field in FIELDS)
            == tuple(row_value(a2, index, field) for field in FIELDS)
            for index in range(a.height)
        ),
        "A_A2_identical_eligible_count": a.filter(pl.col("pre_event_status") == "eligible").height
        == a2.filter(pl.col("pre_event_status") == "eligible").height,
        "A_A2_filled_count_equal": a.filter(pl.col("status") == "filled").height
        == a2.filter(pl.col("status") == "filled").height,
        "A_D_same_sign_paths_equal": all(
            row_value(a, index, "side") == row_value(d, index, "side")
            and row_value(a, index, "net_pnl_jpy") == row_value(d, index, "net_pnl_jpy")
            for index in range(a.height)
            if row_value(a, index, "pre_event_status") == "eligible"
            and (int(row_value(a, index, "O_points") or 0) > 0)
            == (int(row_value(a, index, "M_points") or 0) > 0)
        ),
    }
    regimes: dict[str, Any] = {name: summary(loaded(name)) for name in CONDITIONS}
    joined = regime(a).join(
        regime(d).select("trade_date", "status", "net_pnl_jpy"), on="trade_date", suffix="_D"
    )
    a_d = [
        {
            "cash_close_regime": row["cash_close_regime"],
            "A_minus_D_net_jpy": row["A_minus_D_net_jpy"],
        }
        for row in joined.filter((pl.col("status") == "filled") & (pl.col("status_D") == "filled"))
        .with_columns((pl.col("net_pnl_jpy") - pl.col("net_pnl_jpy_D")).alias("A_minus_D_net_jpy"))
        .group_by("cash_close_regime")
        .agg(pl.col("A_minus_D_net_jpy").sum())
        .sort("cash_close_regime")
        .to_dicts()
    ]
    result = {
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "checks": checks,
        "A_event_rows": a.height,
        "A_filled_count": a.filter(pl.col("status") == "filled").height,
        "A2_filled_count": a2.filter(pl.col("status") == "filled").height,
        "condition_old_new_cash_close_regime": regimes,
        "A_minus_D_old_new_cash_close_regime": a_d,
        "method": "Saved-ledger grouping only. Old C=15:00 through 2024-11-01; new C=15:30 from 2024-11-05. Clock windows differ by design, so these are time-stability diagnostics, not independent replications.",
    }
    write_json(output / "saved_ledger_audit.json", result)
    write_json(
        output / "COMPLETED.json",
        {
            "audit_id": IDENTIFIER,
            "status": "complete",
            "decision": result["status"],
            "access_scope": preregistration["scope"],
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if result["status"] != "PASS":
        raise ValueError("R021 saved-ledger audit failed")


if __name__ == "__main__":
    main()
