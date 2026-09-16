from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from pathlib import Path

from scripts.validate_c01_preparation import next_open_label, validate


def test_calendar_fixture_holiday_weekend_precedence() -> None:
    holiday = {"cash_open": False, "ose_holiday_trading": True}
    weekend = {"cash_open": False, "ose_holiday_trading": False}
    assert next_open_label([holiday, weekend, weekend]) == "TREATMENT_HOLIDAY_REOPEN"
    assert next_open_label([weekend, weekend]) == "OUT_OF_REGIME_OR_EXCLUDED"
    assert next_open_label([]) == "ORDINARY_CASH_OPEN_CONTROL"


def test_accounting_and_stage_regressions_are_rejected(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid.uuid4().hex
    root.mkdir()
    for name in ("evidence.md", "review.md"):
        (root / name).write_text("Synthetic fixture only", encoding="utf-8")
    (root / "design.md").write_text("net = gross_fill - fees", encoding="utf-8")
    start, end = date(2021, 1, 1), date(2025, 6, 30)
    contract = {
        "calendar_version": "synthetic-test-not-market-facts",
        "accounting": {
            "net_basis": "gross_fill",
            "deduct_slippage_again": False,
            "fee_per_side_yen": 30,
            "tick_points": 5,
            "multiplier_yen": 100,
        },
        "stage_gates": {
            "calendar_and_synthetic_require_capital_dd": False,
            "calendar_and_synthetic_require_family_reopen": False,
        },
        "days": [
            {
                "calendar_date": (start + timedelta(days=i)).isoformat(),
                "cash_open": False,
                "ose_holiday_trading": False,
                "ose_trade_date": None,
                "sources": ["https://example.invalid/synthetic-fixture"],
            }
            for i in range((end - start).days + 1)
        ],
    }
    path = root / "calendar_contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    assert validate(root) == []  # Shape/invariants only; truth still requires source review.
    (root / "design.md").write_text(
        "net = gross_fill - slippage_attribution - fees", encoding="utf-8"
    )
    assert any("deducted twice" in issue for issue in validate(root))
    (root / "design.md").write_text("net = gross_fill - fees", encoding="utf-8")
    contract["stage_gates"]["calendar_and_synthetic_require_capital_dd"] = True
    path.write_text(json.dumps(contract), encoding="utf-8")
    assert validate(root)
