"""Run the narrowly allowlisted TASK-R3C-02 evidence closure audit."""

from __future__ import annotations

from pathlib import Path

from n225m_bt.research.r3c_source_primary_audit import run_source_primary_audit

AUDIT_ID = "AUDIT-R3C-02-20260915T071000Z"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results" / "research_audit" / AUDIT_ID


if __name__ == "__main__":
    run_source_primary_audit(
        repository_root=ROOT,
        output=OUTPUT,
        audit_id=AUDIT_ID,
        pre_execution_incidents=[
            {
                "id": "INC-R3C02-PRE-01",
                "kind": "performance-documentation exposure before this audit's allowlist freeze",
                "detail": (
                    "Two broad research-context/document searches exposed R062 performance text. "
                    "No market-price, trade ledger, backtest, or OOS/Final market input was opened "
                    "by this task."
                ),
            },
            {
                "id": "HISTORICAL-R3C01",
                "kind": "prior audit's recorded scope deviations",
                "detail": (
                    "AUDIT-R3-C-01-20260915T053647Z remains immutable history and is not used as "
                    "clean evidence for this audit."
                ),
            },
            {
                "id": "INC-R3C02-RUN-01",
                "kind": "first R3C-02 runtime scope deviation",
                "detail": (
                    "AUDIT-R3C-02-20260915T070000Z enumerated the source directory and recorded "
                    "the name N225minif_2026.xlsx. It opened no file contents, headers, prices, or "
                    "market metadata; it is retained as an invalid clean-scope attempt."
                ),
            },
        ],
    )
