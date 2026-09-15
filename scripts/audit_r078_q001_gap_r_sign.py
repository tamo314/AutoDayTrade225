"""Derive R078's preregistered gap/R diagnostic only from saved immutable ledgers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "research" / "r078-q001-20260915-cash-first-hour-extreme-fade-01"


def metrics(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
    net = [int(row["net_pnl_jpy"]) for row in rows]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    return {
        "trade_count": len(net),
        "net_pnl_jpy": sum(net),
        "expectancy_jpy": sum(net) / len(net) if net else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
    }


def main() -> None:
    destination = OUT / "gap_r_sign_diagnostic_performance.json"
    if destination.exists():
        raise FileExistsError(f"immutable output already exists: {destination}")
    events = json.loads((OUT / "primary_events.json").read_text(encoding="utf-8"))
    trades = json.loads((OUT / "extreme_fade_trades.json").read_text(encoding="utf-8"))
    event_by_date = {event["trade_date"]: event for event in events}
    partitions = {
        label: [
            trade
            for trade in trades
            if event_by_date[trade["trade_date"]].get("gap_and_r_sign") == label
        ]
        for label in ("SAME", "OPPOSITE", "ZERO_GAP", "ZERO_R", "UNAVAILABLE")
    }
    destination.write_text(
        json.dumps(
            {
                "scope": "Post-execution artifact repair: derived only from saved R078 primary_events and extreme_fade_trades; no market-data read, event selection, execution, PnL recomputation, gate, bootstrap, or decision change.",
                "definition": "calendar-connected prior versioned day close to current 09:00 open gap compared with the same trade_date R=B_endpoint_close-A_0900_open; SAME and OPPOSITE are diagnostic-only.",
                "source_files": ["primary_events.json", "extreme_fade_trades.json"],
                "performance_by_gap_and_r_sign": {
                    label: metrics(rows) for label, rows in partitions.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
