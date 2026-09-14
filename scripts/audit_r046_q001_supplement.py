"""Append-only audit supplement for R046-Q001 completed execution ledgers.

This script deliberately reads only R046-01's saved ledgers.  It neither opens
normalized Parquet nor reruns event selection, execution, price statistics, or
bootstrap; it exposes two preregistered summaries omitted from the first report.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import polars as pl

from n225m_bt.research.runner import write_json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results" / "research" / "r046-q001-20260914-opening-path-efficiency-placebo-01"
OUT = (
    ROOT
    / "results"
    / "research"
    / "r046-q001-20260915-opening-path-efficiency-placebo-03-audit-supplement"
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"append-only output exists: {OUT}")
    if not (SOURCE / "COMPLETED.json").exists():
        raise ValueError("R046-01 completed ledger is unavailable")
    OUT.mkdir(parents=True)
    source_hashes = {
        name: digest(SOURCE / name)
        for name in (
            "COMPLETED.json",
            "development_results.json",
            "bootstrap.json",
            "B_open_all/events.json",
            "B_open_all/trades.parquet",
        )
    }
    write_json(
        OUT / "audit_preregistration.json",
        {
            "experiment_id": OUT.name,
            "parent_experiment": SOURCE.name,
            "purpose": "Append-only audit completion only: save full open/placebo high/nonhigh x direction groups and explicit q70/q80 filtered execution ledgers from the parent B_open_all ledger.",
            "invariant": "No hypothesis, state definition, event, price, execution, cost, bootstrap, OLS, input, calendar, seed, decision, OOS or Final Holdout access is changed or rerun.",
            "parent_hashes": source_hashes,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    events = cast(
        list[dict[str, Any]],
        json.loads((SOURCE / "B_open_all/events.json").read_text(encoding="utf-8")),
    )
    groups: dict[str, dict[str, int]] = {}
    for window in ("open", "placebo"):
        for high in (True, False):
            for direction in ("long", "short"):
                rows = [
                    row
                    for row in events
                    if row.get("base_event_status") == "E"
                    and row.get(f"{window}_high_q75") is high
                    and row.get(f"{window}_direction") == direction
                ]
                groups[f"{window}_{'high' if high else 'nonhigh'}_{direction}"] = {
                    "event_count": len(rows),
                    "filled_trade_count": sum(row.get("status") == "filled" for row in rows),
                    "net_pnl_jpy_from_B_open_all": sum(
                        cast(int, row.get("net_pnl_jpy", 0))
                        for row in rows
                        if row.get("status") == "filled"
                    ),
                }
    write_json(
        OUT / "all_window_event_groups.json",
        {
            "parent_condition": "B_open_all",
            "definition": "All common-E events classified by each window's own causal q75; B's W0-direction economics are shown only where a parent B fill exists.",
            "groups": groups,
        },
    )
    trades = pl.read_parquet(SOURCE / "B_open_all" / "trades.parquet")
    parent_events = pl.DataFrame(events)
    for percentile in (70, 80):
        folder = OUT / f"A{percentile}"
        folder.mkdir()
        filtered = parent_events.filter(
            (pl.col("base_event_status") == "E") & pl.col(f"open_high_q{percentile}")
        )
        dates = filtered.select(pl.col("trade_date").str.to_date().alias("trade_date"))
        selected = trades.join(dates, on="trade_date", how="inner")
        filtered.write_parquet(folder / "events.parquet")
        selected.write_parquet(folder / "trades.parquet")
        net = selected.get_column("net_pnl_jpy").to_list()
        wins, losses = [value for value in net if value > 0], [value for value in net if value < 0]
        write_json(
            folder / "metrics.json",
            {
                "parent_execution": "B_open_all; same causal common-E event, W0 side, planned/actual times, baseline one-tick plus JPY30/side fills.",
                "trade_count": len(net),
                "net_pnl_jpy": sum(net),
                "expectancy_jpy": sum(net) / len(net) if net else None,
                "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
            },
        )
    write_json(
        OUT / "COMPLETED.json",
        {
            "experiment_id": OUT.name,
            "status": "audit_complete",
            "parent_decision": json.loads((SOURCE / "COMPLETED.json").read_text(encoding="utf-8"))[
                "decision"
            ],
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )


if __name__ == "__main__":
    main()
