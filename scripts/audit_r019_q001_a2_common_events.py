"""Verify the saved R019 A and A2 pre-event universe without reading market data."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import polars as pl

from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.runner import reserve_directory, write_json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/research/r019-q001-20260914-prior-same-session-range-regime-03"
OUT = ROOT / "results/research/r019-q001-20260914-prior-same-session-range-regime-a2-event-audit-02"
FIELDS = (
    "trade_date",
    "session",
    "pre_event_status",
    "reason",
    "S_session_open_bar_start_jst",
    "t_signal_bar_start_jst",
    "E_planned_entry_jst",
    "X_planned_exit_jst",
    "scheduled_prior_trade_dates_p1_to_p20",
    "R_points_p1_to_p20",
    "V_points",
    "M_points",
    "range_regime",
    "A_direction",
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing output is immutable: {OUT}")
    required = [
        SOURCE / "campaign_manifest.json",
        SOURCE / "preregistration.json",
        SOURCE / "A_regime_switch/events.parquet",
        SOURCE / "A_regime_switch_2tick/events.parquet",
        SOURCE / "A_regime_switch/trades.parquet",
        SOURCE / "A_regime_switch_2tick/trades.parquet",
    ]
    if any(not item.is_file() for item in required):
        raise ValueError("saved R019 execution artefacts needed for A2 audit are unavailable")
    output = reserve_directory(ROOT / "results/research", OUT.name)
    source_manifest = {str(item.relative_to(ROOT)): digest(item) for item in required}
    script_hash = digest(Path(__file__))
    with ZipFile(output / "source_snapshot.zip", "w", ZIP_DEFLATED) as archive:
        archive.write(Path(__file__), str(Path(__file__).relative_to(ROOT)))
    write_json(output / "preregistration.json", {"audit_id": output.name, "status": "frozen_before_saved_event_comparison", "scope": "Only listed saved R019 -03 artefacts; no bars/raw/OOS/Final Holdout/backtest rerun.", "source_artifacts": source_manifest, "script_sha256": script_hash, "comparison_fields": FIELDS, "expected": "A2 has exactly the same pre-event universe and selected A direction as the 1-tick A replay; fill price and economics are intentionally not compared."})
    a, a2 = (pl.read_parquet(item) for item in required[2:4])
    missing = [field for field in FIELDS if field not in a.columns or field not in a2.columns]
    if missing:
        raise ValueError(f"saved event schema misses required A/A2 comparison fields: {missing}")
    left, right = a.select(FIELDS).rows(), a2.select(FIELDS).rows()
    mismatches = [index for index, pair in enumerate(zip(left, right, strict=True)) if pair[0] != pair[1]]
    filled_a = a.filter(pl.col("status") == "filled")
    filled_a2 = a2.filter(pl.col("status") == "filled")
    checks = {"same_event_row_count": len(left) == len(right), "same_pre_event_fields": not mismatches, "same_filled_count": filled_a.height == filled_a2.height, "same_filled_schedule_and_side": filled_a.select("trade_date", "session", "side", "entry_ts_jst", "exit_ts_jst").rows() == filled_a2.select("trade_date", "session", "side", "entry_ts_jst", "exit_ts_jst").rows()}
    result = {"status": "PASS" if all(checks.values()) else "BLOCKED", "checks": checks, "event_row_count": len(left), "filled_count": filled_a.height, "mismatch_indexes": mismatches[:10], "source_manifest_hash": canonical_hash(source_manifest), "access_scope": "saved R019 -03 artefacts only; no market data or engine execution"}
    write_json(output / "a2_common_event_audit.json", result)
    write_json(output / "COMPLETED.json", {"audit_id": output.name, "status": "complete", "result": result["status"]})
    if result["status"] != "PASS":
        raise ValueError("R019 A2 common-event audit failed")


if __name__ == "__main__":
    main()
