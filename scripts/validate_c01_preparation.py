"""Read only C01 preparation artifacts; never load market data or run a strategy."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def next_open_label(interval: list[dict[str, Any]]) -> str:
    """Calendar-only fixture: holiday treatment takes precedence over a weekend."""
    if any(row["ose_holiday_trading"] and not row["cash_open"] for row in interval):
        return "TREATMENT_HOLIDAY_REOPEN"
    return "ORDINARY_CASH_OPEN_CONTROL" if not interval else "OUT_OF_REGIME_OR_EXCLUDED"


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(artifacts: Path) -> list[str]:
    issues: list[str] = []
    required = ("evidence.md", "design.md", "calendar_contract.json", "review.md")
    for name in required:
        if (
            not (artifacts / name).is_file()
            or not (artifacts / name).read_text(encoding="utf-8-sig").strip()
        ):
            issues.append(f"Missing artifact: {name}")
    if issues:
        return issues
    design = (artifacts / "design.md").read_text(encoding="utf-8-sig")
    if re.search(r"net\s*=\s*gross_fill\s*[-\u2212]\s*slippage", design, re.IGNORECASE):
        issues.append("Slippage is deducted twice from gross_fill")
    if not re.search(r"net\s*=\s*gross_fill\s*[-\u2212]\s*fees", design, re.IGNORECASE):
        issues.append("Document the canonical net = gross_fill - fees equation")
    try:
        payload = json.loads((artifacts / "calendar_contract.json").read_text(encoding="utf-8-sig"))
        accounting = payload["accounting"]
        require(accounting["net_basis"] == "gross_fill", "net_basis must be gross_fill")
        require(accounting["deduct_slippage_again"] is False, "Do not deduct slippage twice")
        require(accounting["fee_per_side_yen"] == 30, "Fee must remain 30 yen per side")
        require(
            accounting["tick_points"] == 5 and accounting["multiplier_yen"] == 100,
            "Tick/multiplier mismatch",
        )
        gates = payload["stage_gates"]
        require(
            gates["calendar_and_synthetic_require_capital_dd"] is False,
            "Capital/DD cannot block preparation",
        )
        require(
            gates["calendar_and_synthetic_require_family_reopen"] is False,
            "Family reopening cannot block preparation",
        )
        require(
            isinstance(payload["calendar_version"], str) and payload["calendar_version"].strip(),
            "Missing calendar version",
        )
        rows = payload["days"]
        require(isinstance(rows, list), "days must be an array")
        seen: set[date] = set()
        for row in rows:
            day = date.fromisoformat(row["calendar_date"])
            require(day not in seen, f"Duplicate calendar date: {day}")
            seen.add(day)
            require(
                type(row["cash_open"]) is bool and type(row["ose_holiday_trading"]) is bool,
                f"Unknown boolean: {day}",
            )
            require(isinstance(row["sources"], list) and row["sources"], f"Missing sources: {day}")
            require(
                all(isinstance(url, str) and url.startswith("https://") for url in row["sources"]),
                f"Invalid source URL: {day}",
            )
            require(
                not (row["cash_open"] and row["ose_holiday_trading"]),
                f"Overlapping holiday/open: {day}",
            )
            require(
                row["ose_trade_date"] is None or isinstance(row["ose_trade_date"], str),
                f"Invalid trade_date: {day}",
            )
            require(
                not (row["cash_open"] or row["ose_holiday_trading"])
                or row["ose_trade_date"] is not None,
                f"Missing trade_date for trading day: {day}",
            )
            if row["ose_trade_date"] is not None:
                date.fromisoformat(row["ose_trade_date"])
            require(
                not row["ose_holiday_trading"] or day >= date(2022, 9, 23),
                f"Holiday trading before introduction: {day}",
            )
        start, end = date(2021, 1, 1), date(2025, 6, 30)
        require(
            seen == {start + timedelta(days=i) for i in range((end - start).days + 1)},
            "Calendar does not cover exactly 2021-01-01 through 2025-06-30",
        )
    except (KeyError, ValueError, TypeError) as exc:
        issues.append(
            f"Incomplete or invalid calendar/accounting/stage contract: {type(exc).__name__}: {exc}"
        )
    # Synthetic invariants, independent of market prices and research outcomes.
    reference_gross, slippage, fees = 2000, 1000, 60
    gross_fill = reference_gross - slippage
    require(gross_fill - fees == 940, "Synthetic net calculation failed")
    holiday_weekend = [
        {"cash_open": False, "ose_holiday_trading": True},
        {"cash_open": False, "ose_holiday_trading": False},
        {"cash_open": False, "ose_holiday_trading": False},
    ]
    require(
        next_open_label(holiday_weekend) == "TREATMENT_HOLIDAY_REOPEN",
        "Holiday/weekend priority failed",
    )
    require(
        next_open_label(holiday_weekend[1:]) == "OUT_OF_REGIME_OR_EXCLUDED",
        "Weekend exclusion failed",
    )
    require(next_open_label([]) == "ORDINARY_CASH_OPEN_CONTROL", "Ordinary control failed")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    issues = validate(args.artifacts)
    print(
        json.dumps(
            {
                "passed": not issues,
                "issues": issues,
                "limitations": "Structural/synthetic checks only; Planner must verify source truth, economic reasoning and full scope.",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
