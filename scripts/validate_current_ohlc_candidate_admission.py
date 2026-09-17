#!/usr/bin/env python3
"""Validate the fixed safety and finite-search contract for task 138."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TASK_ID = "TASK-OHLC-CANDIDATE-ADMISSION-10"
PLAN = Path("docs/strategy/plans") / TASK_ID
ALLOWED_COLUMNS = {"timestamp", "open", "high", "low", "close", "trade_date", "calendar_date", "session", "is_eligible"}
FORBIDDEN_CATEGORIES = {"volume", "vwap", "order_book", "external_price", "event_value"}


def fail(message: str) -> None:
    raise ValueError(message)


def read_json(name: str) -> dict[str, Any]:
    path = PLAN / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path}: expected a JSON object")
    return {str(key): item for key, item in value.items()}


def require_keys(value: dict[str, Any], keys: set[str], name: str) -> None:
    missing = keys - value.keys()
    if missing:
        fail(f"{name}: missing keys {sorted(missing)}")


def validate() -> None:
    admission = read_json("input_admission.json")
    require_keys(
        admission,
        {
            "input_profile",
            "allowed_columns",
            "forbidden_input_categories",
            "pass_limited_scope",
            "market_data_read",
            "pnl_access",
            "decision",
        },
        "input_admission.json",
    )
    if admission["input_profile"] != "CURRENT_225LABO_OHLC":
        fail("input_admission.json: wrong input_profile")
    if admission["decision"] != "CURRENT_OHLC_ONLY":
        fail("input_admission.json: wrong decision")
    if admission["market_data_read"] is not False or admission["pnl_access"] is not False:
        fail("input_admission.json: this design task must not read market data or PnL")
    if not set(admission["allowed_columns"]) >= ALLOWED_COLUMNS:
        fail("input_admission.json: incomplete OHLC-only column boundary")
    if not set(admission["forbidden_input_categories"]) >= FORBIDDEN_CATEGORIES:
        fail("input_admission.json: incomplete forbidden-input boundary")

    screen = read_json("candidate_screen.json")
    require_keys(screen, {"decision", "candidate"}, "candidate_screen.json")
    if screen["decision"] not in {"ADMISSIBLE_FOR_S0", "NO_ADMISSIBLE_CANDIDATE"}:
        fail("candidate_screen.json: invalid decision")
    candidate = screen["candidate"]
    if screen["decision"] == "NO_ADMISSIBLE_CANDIDATE":
        if candidate is not None:
            fail("candidate_screen.json: no-candidate decision requires candidate=null")
    else:
        if not isinstance(candidate, dict):
            fail("candidate_screen.json: admissible decision requires a candidate object")
        require_keys(
            candidate,
            {
                "mechanism",
                "family_difference",
                "prior_information",
                "allowed_inputs_only",
                "falsification_prediction",
            },
            "candidate_screen.json candidate",
        )
        if candidate["allowed_inputs_only"] is not True:
            fail("candidate_screen.json: candidate must be OHLC-only")

    contract = read_json("s0_feasibility_contract.json")
    require_keys(contract, {"decision", "requires_registered_s0"}, "s0_feasibility_contract.json")
    if contract["decision"] != screen["decision"]:
        fail("s0_feasibility_contract.json: decision must match candidate screen")
    if screen["decision"] == "NO_ADMISSIBLE_CANDIDATE":
        if contract["requires_registered_s0"] is not False:
            fail("s0_feasibility_contract.json: no candidate cannot request S0")
    else:
        if contract["requires_registered_s0"] is not True:
            fail("s0_feasibility_contract.json: candidate must require separate registered S0")
        require_keys(
            contract,
            {"warmup_policy", "precision_target", "minimum_effect", "non_pnl_outputs", "stop_conditions"},
            "s0_feasibility_contract.json",
        )

    for name in ("verification.md", "review.md"):
        text = (PLAN / name).read_text(encoding="utf-8")
        for required in ("market_data_read", "pnl_access", "grant"):
            if required not in text:
                fail(f"{name}: missing audit term {required}")


def main() -> int:
    try:
        validate()
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    print("PASS: current OHLC candidate admission contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
