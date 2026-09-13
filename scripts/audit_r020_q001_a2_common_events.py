"""Read-only A/A2 event-path audit for completed R020-Q001."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import polars as pl

from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.runner import write_json

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-02"
OUT = ROOT / "results" / "research" / "r020-q001-20260914-tse-lunch-reversal-a2-event-audit-01"
FIELDS = [
    "trade_date",
    "session",
    "pre_event_status",
    "reason",
    "P_points",
    "L_points",
    "A_direction",
    "E_planned_entry_jst",
    "X_planned_exit_jst",
]
FILLED_FIELDS = ["side", "entry_ts_jst", "exit_ts_jst", "entry_delay_minutes", "exit_delay_minutes", "exit_reason"]


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    if OUT.exists():
        raise ValueError(f"immutable audit output already exists: {OUT}")
    source_paths = {
        "A_events": CAMPAIGN / "A_lunch_reversal" / "events.parquet",
        "A2_events": CAMPAIGN / "A_lunch_reversal_2tick" / "events.parquet",
        "A_fills": CAMPAIGN / "A_lunch_reversal" / "fills.parquet",
        "A2_fills": CAMPAIGN / "A_lunch_reversal_2tick" / "fills.parquet",
        "A_completed": CAMPAIGN / "COMPLETED.json",
    }
    if not all(path.exists() for path in source_paths.values()):
        raise ValueError("completed R020 source ledger is incomplete")
    OUT.mkdir(parents=True, exist_ok=False)
    hashes = {name: sha256_file(path) for name, path in source_paths.items()}
    write_json(OUT / "preregistration.json", {"study_id": "R020-Q001", "audit_id": OUT.name, "status": "frozen_before_reading_saved_R020_ledgers", "scope": "Read-only comparison of saved R020 -02 A/A2 events and fills only; no bar/raw/external price/OOS/Final Holdout access.", "required": "A/A2 share every pre-event, direction and planned/actual timestamp; execution difference must be attributable solely to configured 1 versus 2 tick per-side slippage.", "source_hashes": hashes, "source_hash": canonical_hash(hashes)})
    a = pl.read_parquet(source_paths["A_events"]).sort(["trade_date", "session"])
    a2 = pl.read_parquet(source_paths["A2_events"]).sort(["trade_date", "session"])
    if a.height != a2.height:
        raise ValueError("A/A2 event row counts differ")
    pre_equal = all(a.get_column(field).to_list() == a2.get_column(field).to_list() for field in FIELDS)
    filled_a = a.filter(pl.col("status") == "filled")
    filled_a2 = a2.filter(pl.col("status") == "filled")
    filled_equal = filled_a.height == filled_a2.height and all(filled_a.get_column(field).to_list() == filled_a2.get_column(field).to_list() for field in FILLED_FIELDS)
    net_delta = (filled_a2.get_column("net_pnl_jpy") - filled_a.get_column("net_pnl_jpy")).unique().to_list()
    gross_delta = (filled_a2.get_column("gross_pnl_jpy") - filled_a.get_column("gross_pnl_jpy")).unique().to_list()
    fee_delta = (filled_a2.get_column("fees_jpy") - filled_a.get_column("fees_jpy")).unique().to_list()
    audit = {"status": "PASS" if pre_equal and filled_equal and net_delta == [-1000] and gross_delta == [-1000] and fee_delta == [0] else "BLOCKED", "event_rows": a.height, "filled_events": filled_a.height, "common_pre_event_fields": FIELDS, "common_pre_event_identical": pre_equal, "common_filled_execution_fields": FILLED_FIELDS, "common_filled_execution_identical": filled_equal, "A2_minus_A_net_jpy_per_trade": net_delta, "A2_minus_A_gross_jpy_per_trade": gross_delta, "A2_minus_A_fees_jpy_per_trade": fee_delta, "explanation": "One additional tick per side is 2*5 points*100 JPY = 1,000 JPY adverse fill-to-fill Gross/Net difference per completed trade; fees remain 30 JPY/side in both runs."}
    write_json(OUT / "a2_event_audit.json", audit)
    write_json(OUT / "COMPLETED.json", {"audit_id": OUT.name, "status": "complete", "decision": audit["status"], "access_scope": "saved R020 ledgers only", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED"})


if __name__ == "__main__":
    main()
