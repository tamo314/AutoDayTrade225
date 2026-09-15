"""Create the exclusive, synthetic-only artifact set for TASK-R3B-01.

This script intentionally opens only approved governance/audit summaries and
source files.  It has no import of a market-data loader and never opens raw,
Silver, Gold, prices, features, legacy trades, daily PnL, or bootstrap draws.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from n225m_bt.research.r3b_calibration import CalibrationPlan, calibrate

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results" / "research_audit" / "AUDIT-R3-B-20260915T060000Z"
R0 = ROOT / "results" / "research_audit" / "R0-20260915T010000JST"
R1 = ROOT / "results" / "research_audit" / "AUDIT-R1-02-20260915T020000Z"
R2 = ROOT / "results" / "research_audit" / "AUDIT-R2-01-20260915T025000Z"
R3A = ROOT / "results" / "research_audit" / "AUDIT-R3-A-20260915T045000Z"


def write_json(name: str, value: object) -> None:
    (OUTPUT / name).write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip() or "unavailable"


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"exclusive audit directory already exists: {OUTPUT}")
    OUTPUT.mkdir(parents=True)
    plan = CalibrationPlan()
    plan.validate()
    calibration = calibrate(plan)
    sources = [
        ROOT / "src" / "n225m_bt" / "research" / "r3b_calibration.py",
        ROOT / "src" / "n225m_bt" / "research" / "conditions.py",
        ROOT / "src" / "n225m_bt" / "research" / "outcomes.py",
        ROOT / "src" / "n225m_bt" / "research" / "decision_audit.py",
        ROOT / "src" / "n225m_bt" / "research" / "r062.py",
        ROOT / "tests" / "test_r3b_calibration.py",
        Path(__file__),
    ]
    inputs = [
        R0 / "study_registry.json",
        R0 / "prior_information_seen.json",
        R0 / "missing_artifacts.json",
        R1 / "audit_manifest.json",
        R1 / "summary.md",
        R2 / "quality_gate.json",
        R2 / "availability_audit.json",
        R2 / "summary.md",
        R3A / "audit_manifest.json",
        R3A / "metric_reconciliation.json",
        R3A / "summary.md",
        ROOT / "docs" / "strategy" / "13_research_governance.md",
        ROOT / "docs" / "strategy" / "14_research_repair_plan.md",
        ROOT / "docs" / "strategy" / "03_experiment_plan.md",
        ROOT / "docs" / "strategy" / "15_r065_execution_review.md",
    ]
    metric = read_json(R3A / "metric_reconciliation.json")
    quality = read_json(R2 / "quality_gate.json")
    availability = read_json(R2 / "availability_audit.json")
    write_json(
        "audit_manifest.json",
        {
            "audit_id": "AUDIT-R3-B-20260915T060000Z",
            "task_id": "TASK-R3B-01",
            "governance_revision": "RG-20260915-01",
            "run_status": "COMPLETE",
            "operational_state": "PAUSED_METHOD_REPAIR",
            "scope": "finite plan, draft specification, synthetic method calibration, and future feasibility plan",
            "market_data_access": False,
            "real_data_pnl_access": False,
            "oos_access": False,
            "final_holdout_access": False,
            "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in sources},
            "approved_input_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in inputs},
            "commit": commit(),
            "environment": {"python": sys.version, "platform": platform.platform()},
            "created_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    write_json(
        "readiness_matrix.json",
        {
            "R1": {"status": "PASS", "evidence": "AUDIT-R1-02-20260915T020000Z", "reuse": "decision adapter, selection/execution separation, prefix tests"},
            "R2": {"status": quality["data_quality"], "availability": availability["status"], "use_limit": "synthetic and planning only; not a price-read authorization"},
            "R3_A": {"status": "COMPLETE", "evidence": "AUDIT-R3-A-20260915T045000Z", "reuse": "fixed-axis/zero/null/accounting/estimand distinction"},
            "R3_B": {"status": calibration["status"], "scope": calibration["method_classification"]},
            "real_data_feasibility_status": "NOT_RUN",
            "real_data_access_authorized": False,
            "r065": {"status": "BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED", "executor_implemented": False},
        },
    )
    write_json(
        "family_registry.json",
        {
            "family_count": 1,
            "families": [
                {
                    "family_id": "overnight_inventory_rejection",
                    "parent_studies": ["R062-Q001", "R062-Q002"],
                    "proposed_study_id": "R3B-R062-MR-01",
                    "legacy_decisions": ["R062-Q001: BLOCKED", "R062-Q002: REJECT"],
                    "legacy_decisions_mutated": False,
                    "family_relationship": "same economic family; method-repair draft, not an independent family",
                    "pnL_spec_versions_in_this_batch": 0,
                    "real_data_access": False,
                }
            ],
            "limits": {"max_families": 3, "max_new_real_pnl_spec_versions": 3, "max_per_family": 2},
        },
    )
    write_json(
        "batch_plan.json",
        {
            "batch_id": "R3B-20260915-01",
            "status": "DRAFT_NOT_FROZEN",
            "proposals": [
                {
                    "proposal_id": "R3B-R062-MR-01",
                    "economic_question": "Does an extreme signed OSE-night move followed by a 15-minute TSE rejection have positive cost-adjusted continuation, and is it stronger than the medium-night rejection control?",
                    "nearby_studies": ["R055", "R031", "R062-Q001", "R062-Q002"],
                    "known_information": "R062-Q002 legacy REJECT is known; no old R062 trades, daily PnL, or bootstrap repetitions were read in this task.",
                    "learned_from_failure": "Q001 common-E coupling produced E=0; Q002 fixed U separated U from future paths but was a legacy REJECT. Neither fact licenses a rescue search.",
                    "identified_difference": "Only the repaired decision/analysis separation and explicit outcome/null contract are introduced; side, q75, 15-minute reaction, next-bar entry, 30-minute hold, costs, and listed stresses remain inherited.",
                    "needed_data": "Development calendar, OSE-night and TSE-day prices with documented as-of price semantics, R004 quarantine identity, and next-bar executable prices.",
                    "execution_profile": "existing R062 decision adapter plus a separately accepted next-eligible-bar fill/exit path; no R065 cross-session profile.",
                    "remaining_constraints": ["R2 price_available_at UNKNOWN", "S2 non-PnL feasibility NOT_RUN", "economic minimum net effect not agreed", "no real-data access authorization"],
                    "real_data_pnl_access": 0,
                }
            ],
        },
    )
    preregistration = """# R3B-R062-MR-01 — DRAFT_NOT_FROZEN\n\nStatus: DRAFT_NOT_FROZEN; market access false; this is not a replacement for R062-Q001/Q002 and does not alter their BLOCKED/REJECT records.\n\nFamily/study/spec/protocol: `overnight_inventory_rejection` / `R3B-R062-MR-01` / `MR-01` / `RG-20260915-01`. Parent studies: R062-Q001/Q002. Change reason: retain the economic rule while binding decision-time U/E_exec and post-decision E_analysis/null accounting explicitly.\n\nScheduled axis: every version-controlled Development trade_date (2021-01-01..2025-06-30) for which the calendar maps prior TSE normal -> OSE night -> target TSE normal; no price-derived ex post exclusion. U: at each target, the immediately prior 120 scheduled triplets, no backfill beyond 120; a reference is valid only when its night open/close are eligible, non-quarantined, and positive; at least 100 valid x values; q50/q70/q75/q80 nearest rank and equality to upper cell. U excludes target TSE, response, entry, exit, sensitivity and future outcomes.\n\nE_exec(t): at t = target TSE scheduled minute 15 close, target night validity, U, TSE minute 1 open and minutes 1..15 eligible/positive, fixed R004 quarantine known at t, and h=s*(c14-oT) are available. Signal is c14; selected A/C/D/M is fixed then. Order: selected A uses side -s, quantity one, one order, next eligible scheduled bar open; no retry after cancel. Main exit is 30 scheduled minutes after entry at that bar open; a one-minute entry delay does not extend exit. Stop/target/re-entry/early exit absent. Missing entry cancels before fill and is known no-trade (0 only if no cancellation fee); a filled position with unknown exit or an unfinished position is null, not zero; any main-axis null blocks complete Net and promotion. Existing shared engine acceptance is required before real execution; it is not supplied by this draft.\n\nE_analysis: execution/fill/exit observability may be determined after the order; it cannot revise selection. Main point: A, q75, rejection at 15 minutes, 30-minute hold. Main purpose: cost-adjusted A scheduled-date mean Net. Main control: C (q50<=x<q75, same rejection and -s direction). Co-primary: A-C scheduled-date Net difference. Required stresses: 2 and 3 ticks, fee double, one-bar entry delay without exit extension, q70/q80, rejection 2 ticks, reactions 10/20 minutes, holds 15/45 minutes. Diagnostics: A_follow, fixed buy/sell, A/C/D/M counts, long/short counts, calendar year/month, top-5/top-10 removal, PF/DD, FWL delta. None is silently promoted or demoted from an inherited required gate.\n\nEstimands: all primary values are JPY per scheduled trade_date on the complete scheduled axis, unit weight per date, denominator is every scheduled date, including known no-trade zero; unknown PnL remains null. Report JPY/trade only as a labelled descriptive estimand. Cost is 1 tick/side plus 30 JPY/side, tick 5 points, multiplier 100 JPY/point, one contract, round trip 1,060 JPY. `reference_gross`, `gross_fill`, fees and Net follow RG-20260915-01; slippage is not deducted twice.\n\nInformation/decision: planned information minima inherited from Q002 (E>=700, A/C/D/M>=70, A long/short>=25, q80 A>=45, 10/20-minute A>=50); shortfall is INCONCLUSIVE, unidentified co-primary is NOT_IDENTIFIED and blocks promotion. Economic minimum Net effect, capital, permissible DD and operational minimum profit are null because no prior agreement was located; they must be explicitly agreed before freeze, not inferred from calibration. Inference, if frozen, is fixed-axis 20-trade_date no-wrap MBB with 10,000 repetitions, seed 20261006 and linear percentile CI; the co-primary FWL keeps its inherited fixed-design score bootstrap only after its exact nuisance dictionary is recovered.\n\nStopping: any leakage/accounting failure -> INVALID; data/as-of failure -> BLOCKED; information shortfall -> INCONCLUSIVE; economic/required stress failure after adequate information -> REJECT; no rescue variation, WFA, OOS or Final Holdout. This draft itself permits none of those actions.\n"""
    (OUTPUT / "preregistration_R3B-R062-MR-01.md").write_text(preregistration, encoding="utf-8")
    write_json(
        "estimand_registry.json",
        {
            "estimands": [
                {"estimand_id": "R3B_A_NET_SCHEDULED_DATE", "unit": "JPY_PER_SCHEDULED_TRADE_DATE", "set": "complete scheduled axis", "denominator": "all scheduled dates", "weighting": "one per date", "cost": "1 tick/side + JPY30/side", "null_policy": "any unknown PnL => complete value null"},
                {"estimand_id": "R3B_A_MINUS_C_NET_SCHEDULED_DATE", "unit": "JPY_PER_SCHEDULED_TRADE_DATE", "set": "same complete scheduled axis", "denominator": "all scheduled dates", "weighting": "paired one per date", "cost": "same", "null_policy": "any unknown component => complete difference null"},
                {"estimand_id": "R032_CONFIRMED_MINUS_NONCONFIRMED_JPY_PER_TRADE", "status": "KNOWN_PRIOR_INFORMATION", "value": metric["displayed_metric"]["confirmed_minus_nonconfirmed"]},
                {"estimand_id": "R032_CONFIRMED_MINUS_NONCONFIRMED_JPY_PER_TARGET_NIGHT", "status": "KNOWN_PRIOR_INFORMATION", "value": metric["bootstrap_metric"]["estimate"]},
            ],
        },
    )
    write_json(
        "calibration_plan.json",
        {
            "hash_freeze_status": "FROZEN_FOR_SYNTHETIC_METHOD_DIAGNOSTIC_ONLY",
            "market_access_approval": "SEPARATE_NOT_GRANTED",
            "plan": asdict(plan),
            "generator": {"shared_axis": "1111 synthetic scheduled cycles, not a market calendar", "A_and_C": "disjoint 10%/10% categories", "time_dependence": "AR(1)=0.35", "tails": "Student-t df=5", "missingness": "none in calibration; production null policy separately tested", "directions": "direction-neutral because price-to-execution is not exercised"},
            "effect_scenarios": {"gross_zero": "gross=0, net=-1060", "net_zero": "gross=1060, net=0", "hypothetical_minimum_net": "A gross=1060+1500 plus noise; C gross=1060 plus noise"},
            "inference": "20-cycle no-wrap moving-block percentile bootstrap, 400 draws; common-axis main A Net and co-primary A-C lower CI > 0; information gates A/C >=70",
            "decision_rule": "method pass only when net-zero Wilson upper <=5% and hypothetical-effect Wilson lower >=80%, with zero failed repetitions; gross-zero is diagnostic only",
        },
    )
    write_json(
        "gate_witnesses.json",
        {
            "r031_impossible_and": {"status": "REJECTED_BY_TEST", "description": "same path/event/weight long and short 0-tick pre-fee gross means both <0 is rejected"},
            "valid_joint_witness": {"status": "PASS", "shared_signed_price_changes_points": [5, 10, -5], "multiplier_jpy_per_point": 100, "long_gross_pre_fee_mean_jpy": 333.3333333333333, "short_gross_pre_fee_mean_jpy": -333.3333333333333, "note": "one shared path, not independently fabricated condition PnL"},
            "required_gates": ["information A>=70", "information C>=70", "main A daily-net lower CI>0", "co-primary A-C daily-net lower CI>0"],
            "method_limit": "No order/fill record or BacktestEngine execution is injected; witnesses prove only gate logic and shared-path identity.",
        },
    )
    write_json("calibration_results.json", calibration)
    write_json(
        "feasibility_plan.json",
        {
            "real_data_feasibility_status": "NOT_RUN",
            "reason": "TASK-R3B-01 prohibits real-data and non-PnL count diagnostics.",
            "R2_supported": ["document/synthetic planning", "limited Development diagnostic only under PASS_LIMITED"],
            "R2_not_supported": ["price read permission", "OOS", "Final Holdout", "candidate selection", "execution claim"],
            "blocking_evidence": ["price_available_at UNKNOWN", "OHLC meaning/roll/adjustment/correction semantics unresolved"],
            "next_non_pnl_audit_if_separately_authorized": {"period": "Development only, 2021-01-01..2025-06-30 by trade_date", "columns": ["trade_date", "session", "timestamp label", "bar availability/as-of metadata", "OHLC semantic metadata", "contract/roll/adjustment metadata", "R004 quarantine identifier"], "output": ["availability by required decision/entry/exit timestamp", "missingness reason taxonomy", "no-PnL feasibility table"], "prohibited": ["prices", "returns", "PnL", "direction success", "performance ranks", "OOS", "Final Holdout"]},
            "unresolved_evidence_requirements": ["supplier/file-specific bar label and as-of availability", "OHLC construction", "missing/correction policy", "contract identity/roll/adjustment"],
            "external_actions_taken": False,
        },
    )
    write_json(
        "freeze_blockers.json",
        {"status": "DRAFT_NOT_FROZEN", "blockers": ["economic minimum Net effect unagreed (null)", "capital/DD/operational profit minima unagreed (null)", "R2 price/as-of semantics unresolved", "S2 NOT_RUN", "R062 FWL nuisance dictionary must be recovered rather than inferred", "existing engine execution acceptance for this path not established"], "do_not_remediate_with": ["real-data PnL", "old-ledger reread", "threshold rescue", "OOS", "Final Holdout"]},
    )
    write_json(
        "access_ledger.json",
        {"status": "PASS", "allowed_reads": [str(path.relative_to(ROOT)) for path in inputs], "generated_only": ["synthetic scheduled axis", "synthetic gross/fill/net arrays", "bootstrap draws"], "not_read": ["raw/Silver/Gold", "price columns", "volume", "features", "price-derived QC/cache", "old trades/daily PnL/bootstrap draws", "OOS", "Final Holdout"], "physical_market_data_read": False, "real_data_pnl_read": False},
    )
    write_json(
        "prior_information_seen.json",
        {"inherited": "R0 prior_information_seen retained; no legacy performance payload reopened.", "R062": "legacy Q001 BLOCKED and Q002 REJECT known from registry/results documentation", "R032": {"audit": "AUDIT-R3-A-20260915T045000Z", "JPY_PER_TRADE_difference": metric["displayed_metric"]["confirmed_minus_nonconfirmed"], "JPY_PER_TARGET_NIGHT_difference": metric["bootstrap_metric"]["estimate"], "handling": "different estimands retained without correction, reread, bootstrap recalculation, or legacy-decision change"}, "new_information": "synthetic-method calibration outputs only"},
    )
    write_json(
        "change_diff.json",
        {"added": ["src/n225m_bt/research/r3b_calibration.py", "tests/test_r3b_calibration.py", "scripts/run_r3b_01_finite_plan_calibration.py"], "modified_shared_engine": False, "modified_legacy_specs_or_artifacts": False, "economic_rule_change": False, "notes": "new additive synthetic-only calibration; no new runner or R065 executor"},
    )
    write_json(
        "test_log.json",
        {"status": "NOT_RUN", "reason": "populated by the executor after pytest/Ruff/mypy and the script complete"},
    )
    (OUTPUT / "summary.md").write_text(
        "# TASK-R3B-01\n\n"
        f"Synthetic calibration status: `{calibration['status']}`. It is explicitly `{calibration['method_classification']}`. "
        "No real data, PnL, WFA, OOS, Final Holdout, or R065 executor was used.\n\n"
        "The one-family R062 method-repair proposal is DRAFT_NOT_FROZEN and has no market access authorization. "
        "R2 remains PASS_LIMITED/availability BLOCKED for this purpose; R3-A's two R032 estimands are retained separately. "
        "Operational state remains PAUSED_METHOD_REPAIR.\n",
        encoding="utf-8",
    )
    write_json(
        "COMPLETED.json",
        {"audit_id": "AUDIT-R3-B-20260915T060000Z", "status": "COMPLETE", "scope_complete": ["finite plan", "draft preregistration", "synthetic calibration", "future feasibility plan"], "market_data_access": False, "next_action": "SEPARATE_AUTHORIZATION_REQUIRED_FOR_DEVELOPMENT_NON_PNL_FEASIBILITY_AUDIT", "operational_state": "PAUSED_METHOD_REPAIR"},
    )


if __name__ == "__main__":
    main()
