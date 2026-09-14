"""Read-only A/A2 event and execution-path audit for completed R022-Q001."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.runner import snapshot_source, write_json

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "results" / "research" / "r022-q001-20260914-prior-session-range-end-01"
OUT = ROOT / "results" / "research" / "r022-q001-20260914-prior-session-range-end-a2-event-audit-01"
FIELDS = [
    "trade_date", "session", "pre_event_status", "reason", "p_trade_date", "p_session",
    "H_points", "L_points", "O_points", "C_points", "J_points", "range_quartile",
    "A_direction", "E_planned_entry_jst", "X_planned_exit_jst",
]
FILLED_FIELDS = ["side", "entry_ts_jst", "exit_ts_jst", "entry_delay_minutes", "exit_delay_minutes"]


def main() -> None:
    if OUT.exists():
        raise ValueError(f"existing audit output is immutable: {OUT}")
    if not (CAMPAIGN / "COMPLETED.json").exists():
        raise ValueError("completed R022 campaign is unavailable")
    OUT.mkdir(parents=True)
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    a = pl.read_parquet(CAMPAIGN / "A_range_end" / "events.parquet").sort(["trade_date", "session"])
    a2 = pl.read_parquet(CAMPAIGN / "A_range_end_2tick" / "events.parquet").sort(["trade_date", "session"])
    if a.height != a2.height:
        raise ValueError("A/A2 saved event row count differs")
    comparisons = {field: bool((a[field] == a2[field]).all()) for field in FIELDS}
    filled_a = a.filter(pl.col("status") == "filled")
    filled_a2 = a2.filter(pl.col("status") == "filled")
    fill_comparisons = {field: bool((filled_a[field] == filled_a2[field]).all()) for field in FILLED_FIELDS}
    checks = {"saved_events_same_row_count": a.height == a2.height, "all_pre_event_direction_schedule_fields_identical": all(comparisons.values()), "filled_same_row_count": filled_a.height == filled_a2.height, "all_filled_side_and_actual_times_identical": all(fill_comparisons.values()), "A2_cost_change_only": all(comparisons.values()) and all(fill_comparisons.values())}
    write_json(OUT / "audit.json", {"campaign": str(CAMPAIGN.relative_to(ROOT)), "audit_started_after_campaign_completion": True, "read_scope": "saved A/A2 event ledgers only; no raw/normalized price data, OOS, or Final Holdout read", "timestamp": datetime.now(timezone.utc).isoformat(), "source": source, "fields": FIELDS, "filled_fields": FILLED_FIELDS, "event_rows": a.height, "filled_rows": filled_a.height, "field_checks": comparisons, "filled_field_checks": fill_comparisons, "checks": checks, "status": "PASS" if all(checks.values()) else "BLOCKED", "event_hash_A": canonical_hash(a.select(FIELDS).to_dicts()), "event_hash_A2": canonical_hash(a2.select(FIELDS).to_dicts())})
    write_json(OUT / "COMPLETED.json", {"campaign_id": OUT.name, "status": "complete", "result": "PASS" if all(checks.values()) else "BLOCKED", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
