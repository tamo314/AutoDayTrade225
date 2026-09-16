"""Preregister and gate R052-Q001 before any market-data access.

The hypothesis requires a causal one-minute volume VWAP. The frozen volume
semantic gate is deliberately evaluated before loading a Parquet partition,
inspecting any bar value, or constructing a strategy/order.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from n225m_bt.io.manifest import canonical_hash  # type: ignore[import-untyped]
from n225m_bt.research.runner import snapshot_source, write_json  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = "r052-q001-20260915-tse-morning-vwap-extreme-fade-01"
OUT = ROOT / "results" / "research" / IDENTIFIER
PARENT = ROOT / "results" / "research" / "r010-q001-20260913-volume-input-evidence-audit-01"
REQUIRED_ASSERTIONS = (
    "same_one_minute_executed_quantity",
    "field_unit_and_product_scope",
    "not_cumulative_open_interest_turnover_or_trade_count",
    "timestamp_to_aggregation_interval_boundary",
    "observable_after_bar_final_without_future_information",
    "missing_zero_and_post_publication_correction_handling",
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def volume_gate() -> dict[str, object]:
    """Reuse only the documented evidence gate; never inspect market data."""
    manifest_path = PARENT / "audit_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("R052 requires the frozen R010 volume evidence audit")
    parent = json.loads(manifest_path.read_text(encoding="utf-8"))
    if parent.get("gate", {}).get("decision") != "BLOCKED":
        raise ValueError("R010 volume evidence audit no longer has its frozen BLOCKED decision")
    assertions = parent.get("assertions")
    if not isinstance(assertions, dict):
        raise ValueError("R010 volume evidence assertions are malformed")
    status = {name: assertions.get(name, "MISSING") for name in REQUIRED_ASSERTIONS}
    return {
        "decision": "BLOCKED",
        "reason": (
            "The only available supplier-evidence audit leaves every required volume "
            "semantic/timing assertion UNCONFIRMED. A causal, same-scale one-minute "
            "executed-volume VWAP cannot be constructed without assuming field meaning."
        ),
        "assertions": status,
        "source_lineage_confirmed_only": parent.get("input_lineage"),
        "inherited_evidence": {
            "audit_id": parent.get("audit_id"),
            "path": str(manifest_path.relative_to(ROOT)),
            "sha256": digest(manifest_path),
            "parent_gate": parent.get("gate"),
        },
        "required_external_dependency": parent.get("gate", {}).get("required_external_dependency"),
        "access_log": {
            "read": ["R010 evidence-audit manifest", "repository source/config/document metadata"],
            "not_read": [
                "data/raw",
                "bronze/silver/gold or normalized Parquet",
                "price values/statistics",
                "volume values/statistics",
                "event ledger",
                "orders/fills/trades/PnL/bootstrap",
                "OOS",
                "Final Holdout",
            ],
        },
    }


def preregistration(source: dict[str, object]) -> dict[str, Any]:
    return {
        "experiment_id": IDENTIFIER,
        "study_id": "R052-Q001",
        "status": "frozen_before_price_or_volume_statistics_events_or_pnl",
        "scope": "Development trade_date 2021-01-01..2025-06-30 only; no OOS or Final Holdout access.",
        "duplicate_review": {
            "R001_R051": (
                "R049 shares an mS+90 anchor but is a six-anchor, five-minute price-shock "
                "study with 15-minute holding. No R001-R051 registration combines the 90 "
                "scheduled-minute typical-price, executed-volume anchor VWAP; causal 120-day "
                "same-observation q75/q85/q90/q95; and next-minute-to-fixed-30-minute fade."
            ),
            "conclusion": "No already registered identical time, VWAP deviation, threshold and holding specification.",
        },
        "hypothesis": (
            "At the TSE normal-session mS+90 observation, an extreme causal deviation from "
            "same-day anchor VWAP reverses toward VWAP over the following fixed 30 scheduled "
            "minutes and exceeds smaller-deviation fade and same-event continuation/fixed-side controls."
        ),
        "calendar_and_state": (
            "Use only versioned TSE/OSE schedules and R004 fixed isolation. For the exact 90 "
            "scheduled bars [mS,mS+90), typical=(high+low+close)/3 and VWAP=sum(typical*volume)/sum(volume). "
            "p=close(mS+89), d=(p-VWAP)/VWAP, x=abs(d). All 90 bars must be eligible, complete, "
            "same trade_date/session, with present non-negative volume and positive total volume. "
            "No price, volume, missingness, zero, negative, or institutional-change assumption may be repaired."
        ),
        "causal_thresholds_and_E": (
            "Target excluded; exactly the immediately prior 120 scheduled TSE business days at "
            "the same mS+90 observation; no backfill; >=100 valid observations; nearest-rank "
            "q75/q85/q90/q95, ties included above. Common E requires valid observation/all thresholds, "
            "the next eligible scheduled entry, and 15/30/45 scheduled-minute exits. r=0 or d=0 has no direction."
        ),
        "conditions": {
            "A": "x>=q90, -sign(d), next scheduled open to +30 scheduled-minute open",
            "B": "x>=q75, -sign(d), independently selected",
            "C": "q75<=x<q90, -sign(d), independently selected",
            "A_continue": "A event, sign(d)",
            "A_buy_A_sell": "A event, fixed long/fixed short",
            "A2_A3": "A event/side at 2/3 ticks per side",
            "A_delay": "A event/side, entry one scheduled minute later; original exit unchanged",
            "A85_A95": "same rule with q85/q95",
            "A_h15_A_h45": "same A event/side, fixed 15/45 scheduled-minute exits",
        },
        "execution": "One contract, one position/date; no Stop/Target/re-entry/update/early exit. Baseline is 1 tick + JPY30 per side; Gross is fill-to-fill/slippage-inclusive and Net=Gross-fees.",
        "fixed_ols": (
            "All direction-valid E observations: y=fade-direction-adjusted zero-tick/pre-fee 30-minute Gross; "
            "Q=1[x>=q90], z=ln(x/q90), absolute mS-to-signal return bps, preceding-five-minute fade-adjusted "
            "return bps, p-above-VWAP indicator, and calendar-year FE. Full-rank design is required; delta is Q coefficient."
        ),
        "bootstrap": "20 trade_date noncircular moving blocks, 10,000 repetitions, fixed seed 20261001, common 1,111-day index, tail truncation and linear percentile. Do not reestimate thresholds/events.",
        "gates": {
            "blocked": "input/volume semantics, calendar, synthetic, execution, accounting, OLS-rank failure",
            "inconclusive": "E<850 or A<90 or B<220 or C<100 or either A side<30 or A95<40",
            "investigate_only_if": "All specified Net/PF/CI/control/cost-delay/sensitivity/year-month/concentration tests pass; otherwise REJECT.",
        },
        "required_artifacts_if_volume_gate_passes": "all observation/event ledgers, condition ledgers, fixed 1,111-day PnL axis, regression ledger, bootstrap, year/month/direction performance, positive-month count, and top5/top10-removal net.",
        "prohibited": ["WFA", "OOS", "Final Holdout", "rescue search", "other anchors/windows/thresholds/holds/volume filters"],
        "source": source,
        "oos": "NOT_EVALUATED",
        "final_holdout": "NOT_ACCESSED",
    }


def main() -> None:
    from n225m_bt.research.execution import require_entry
    require_entry('script:scripts/run_r052_q001_tse_morning_vwap_extreme_fade.py')

    if OUT.exists():
        raise FileExistsError(f"immutable R052 output exists: {OUT}")
    OUT.mkdir(parents=True)
    source = snapshot_source(OUT, ROOT / "config", ROOT / "config" / "local_calendar.yaml")
    plan = preregistration(source)
    gate = volume_gate()
    write_json(OUT / "preregistration.json", plan)
    write_json(OUT / "volume_input_gate.json", gate)
    write_json(
        OUT / "campaign_manifest.json",
        {
            "campaign_id": IDENTIFIER,
            "status": "blocked_before_market_data_access",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "seed": 20261001,
            "preregistration_hash": canonical_hash(plan),
            "volume_gate_hash": canonical_hash(gate),
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    write_json(
        OUT / "BLOCKED.json",
        {
            "experiment_id": IDENTIFIER,
            "status": "BLOCKED",
            "stage": "volume_semantics_audit_before_market_data_access",
            "reason": gate["reason"],
            "required_external_dependency": gate["required_external_dependency"],
            "not_run": ["strategy implementation", "synthetic execution tests", "backtest", "OLS", "bootstrap"],
            "oos": "NOT_EVALUATED",
            "final_holdout": "NOT_ACCESSED",
        },
    )
    print(f"{IDENTIFIER}: BLOCKED before market-data access (volume semantic gate)")


if __name__ == "__main__":
    main()
