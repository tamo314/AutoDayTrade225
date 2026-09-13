"""R003 preregistration validation and Development-only quality gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import polars as pl
import yaml

from n225m_bt.config import InstrumentConfig, load_project_config
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.research.data import ResearchData, load_split

QualityStatus = Literal["BLOCKED", "PASS_LIMITED", "PASS_RESEARCH"]


@dataclass(frozen=True, slots=True)
class R003Study:
    path: Path
    hypothesis_document: Path
    development_start: str
    development_end: str
    gold_root: Path
    threshold_values: tuple[Fraction, ...]
    holding_minutes: tuple[int, ...]
    representative_threshold: Fraction
    representative_holding: int
    baseline_sessions: int
    fee_per_side: int
    seed: int


def _expect_keys(value: object, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    unknown = set(value) - keys
    missing = keys - set(value)
    if unknown or missing:
        raise ValueError(f"{where} keys mismatch: missing={sorted(missing)} unknown={sorted(unknown)}")
    return value


def load_r003_study(path: Path) -> R003Study:
    """Load the fixed R003 v1 schema and reject semantic expansion."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    root = _expect_keys(
        payload,
        {
            "schema_version", "study_id", "study_kind", "strategy_version", "hypothesis_document",
            "periods", "inputs", "strategy", "control", "costs", "sensitivity", "walk_forward",
            "robustness", "gates", "execution",
        },
        "R003 root",
    )
    if (root["schema_version"], root["study_id"], root["study_kind"], root["strategy_version"]) != (
        2, "R003", "state_conditioned_opening", "1.0.0"
    ):
        raise ValueError("only preregistered R003 v1 is accepted")
    periods = _expect_keys(
        root["periods"], {"development", "oos", "final_holdout_start", "final_holdout_enabled", "split_key"}, "periods"
    )
    development = _expect_keys(periods["development"], {"start", "end"}, "periods.development")
    if development != {"start": "2021-01-01", "end": "2025-06-30"} or periods["final_holdout_enabled"] is not False or periods["split_key"] != "trade_date":
        raise ValueError("R003 period boundary or Final Holdout lock changed")
    inputs = _expect_keys(
        root["inputs"], {"gold_root", "series_type", "backtest_config", "warmup_policy", "allow_pre_development_prices", "instrument_assertions"}, "inputs"
    )
    assertions = _expect_keys(inputs["instrument_assertions"], {"tick_size_points", "multiplier_jpy_per_point"}, "instrument_assertions")
    if inputs["series_type"] != "center_continuous" or inputs["warmup_policy"] != "previous_observed_only" or inputs["allow_pre_development_prices"] is not False or assertions != {"tick_size_points": 5, "multiplier_jpy_per_point": 100}:
        raise ValueError("R003 input assertions changed")
    strategy = _expect_keys(
        root["strategy"],
        {"family", "sessions", "opening_minutes", "baseline_sessions", "baseline_group_by", "baseline_selection", "require_all_baseline_members_valid", "include_valid_zero_ranges_in_history", "compression_thresholds", "comparison", "holding_minutes", "representative", "signal_rule", "signal_bar_start_elapsed_min_inclusive", "signal_bar_start_elapsed_max_exclusive", "breakout_buffer_ticks", "entry_rule", "exit_rule", "max_entry_attempts_per_session", "quantity", "max_positions", "stop_loss", "take_profit"},
        "strategy",
    )
    representative = _expect_keys(strategy["representative"], {"compression_threshold", "holding_minutes"}, "strategy.representative")
    thresholds = tuple(Fraction(str(value)) for value in strategy["compression_thresholds"])
    if thresholds != (Fraction(3, 5), Fraction(3, 4), Fraction(9, 10)) or tuple(strategy["holding_minutes"]) != (30, 60, 90):
        raise ValueError("R003 grid changed")
    if strategy["family"] != "opening_compression_breakout" or tuple(strategy["sessions"]) != ("day", "night") or strategy["opening_minutes"] != 30 or strategy["baseline_sessions"] != 20 or strategy["baseline_group_by"] != "session" or strategy["baseline_selection"] != "previous_scheduled_sessions" or strategy["require_all_baseline_members_valid"] is not True or strategy["include_valid_zero_ranges_in_history"] is not True or strategy["comparison"] != "less_than_or_equal" or strategy["signal_bar_start_elapsed_min_inclusive"] != 30 or strategy["signal_bar_start_elapsed_max_exclusive"] != 120 or strategy["breakout_buffer_ticks"] != 0 or strategy["entry_rule"] != "next_eligible_bar_open" or strategy["exit_rule"] != "elapsed_minutes_from_actual_fill" or strategy["max_entry_attempts_per_session"] != 1 or strategy["quantity"] != 1 or strategy["max_positions"] != 1 or strategy["stop_loss"] is not None or strategy["take_profit"] is not None:
        raise ValueError("R003 strategy contract changed")
    if Fraction(str(representative["compression_threshold"])) != Fraction(3, 4) or representative["holding_minutes"] != 60:
        raise ValueError("R003 representative changed")
    costs = _expect_keys(root["costs"], {"fee_jpy_per_contract_per_side", "baseline_slippage_ticks_per_side", "representative_extra_development_runs", "double_fee_jpy_per_contract_per_side", "entry_delay_minutes", "exit_delay_minutes", "oos_runs"}, "costs")
    if costs["fee_jpy_per_contract_per_side"] != 30 or costs["baseline_slippage_ticks_per_side"] != 1 or costs["double_fee_jpy_per_contract_per_side"] != 60 or costs["entry_delay_minutes"] != 1 or costs["exit_delay_minutes"] != 1:
        raise ValueError("R003 cost contract changed")
    robustness = _expect_keys(root["robustness"], {"seed", "repetitions", "primary_block_length_trade_dates", "diagnostic_block_length_trade_dates", "block_sampling", "block_truncate_to_original_length", "keep_day_night_together", "quantile_method", "reported_quantiles", "independent_rng_per_method", "require_defined_primary_gate_statistics", "trade_drop_fraction", "trade_drop_count_rounding", "trade_shuffle_for_drawdown_only", "adverse_exit_overlay_minutes", "adverse_exit_overlay_is_engine_replay"}, "robustness")
    if robustness["seed"] != 225 or robustness["repetitions"] != 1000:
        raise ValueError("R003 robustness contract changed")
    _expect_keys(periods["oos"], {"start", "end"}, "periods.oos")
    _expect_keys(root["control"], {"family", "holding_minutes", "require_same_history_and_opening_validity", "diagnostic_complement_threshold", "can_be_promoted"}, "control")
    _expect_keys(root["sensitivity"], {"diagnostic_baseline_sessions", "diagnostic_threshold", "diagnostic_holding_minutes", "diagnostic_can_replace_representative", "max_unique_full_development_engine_conditions", "max_oos_engine_conditions"}, "sensitivity")
    walk_forward = _expect_keys(root["walk_forward"], {"train_months", "test_months", "step_months", "expected_folds", "parameter_selection", "train_gate", "test_gate"}, "walk_forward")
    _expect_keys(walk_forward["train_gate"], {"min_representative_trades", "positive_expectancy_tick_levels", "min_positive_grid_points_at_one_tick"}, "walk_forward.train_gate")
    _expect_keys(walk_forward["test_gate"], {"min_active_folds", "positive_active_fraction_strictly_greater_than", "pooled_positive_expectancy_tick_levels"}, "walk_forward.test_gate")
    gates = _expect_keys(root["gates"], {"quality", "development", "oos", "insufficient_sample_decision", "invalid_measurement_decision", "economic_failure_decision"}, "gates")
    _expect_keys(gates["quality"], {"development_allowed_statuses", "oos_required_status", "allow_unknown_roll_evidence_for_oos"}, "gates.quality")
    _expect_keys(gates["development"], {"min_representative_trades", "positive_expectancy_tick_levels", "min_positive_grid_points_at_one_tick", "positive_month_fraction_min", "month_denominator", "net_excluding_top_winners_count", "net_excluding_top_winners_strictly_positive", "min_noncompression_control_trades", "compression_minus_noncompression_expectancy_strictly_positive", "positive_net_stresses", "primary_block_bootstrap_net_p05_strictly_positive", "require_walk_forward_pass", "max_end_of_data_exits", "max_unexpected_forced_exits"}, "gates.development")
    _expect_keys(gates["oos"], {"min_representative_trades", "positive_expectancy_tick_levels", "net_excluding_top_winners_count", "net_excluding_top_winners_strictly_positive", "positive_net_stresses", "max_end_of_data_exits", "max_unexpected_forced_exits"}, "gates.oos")
    _expect_keys(root["execution"], {"require_explicit_stage_for_r003", "automatic_oos_after_development", "require_preregistration_before_pnl", "require_frozen_candidate_before_oos", "record_oos_access_before_loading_prices", "allow_final_holdout", "overwrite_existing_results", "real_trading_enabled"}, "execution")
    return R003Study(
        path, Path(str(root["hypothesis_document"])), str(development["start"]), str(development["end"]),
        Path(str(inputs["gold_root"])), thresholds, tuple(strategy["holding_minutes"]), Fraction(3, 4), 60, 20, 30, 225,
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def run_preflight(
    data: ResearchData, study: R003Study, instrument: InstrumentConfig, calendar_path: Path, output: Path
) -> dict[str, object]:
    """Audit only Development bars; a known grid warning is never rounded away."""
    output.mkdir(parents=True, exist_ok=True)
    tick = instrument.instrument.tick_size
    rows: list[dict[str, object]] = []
    flagged = 0
    for bar in data.bars:
        cells = {name: getattr(bar, name) % tick for name in ("open", "high", "low", "close")}
        violation = any(value != 0 for value in cells.values()) or "TICK_GRID_VIOLATION" in bar.quality_flags
        if violation:
            flagged += 1
            rows.append({"ts_jst": bar.ts_jst, "trade_date": bar.trade_date, "session": bar.session.value, "remainders": cells, "quality_flags": list(bar.quality_flags), "is_eligible": bar.is_eligible, "cause": "unresolved_in_Gold"})
    pl.DataFrame(rows or {"ts_jst": [], "trade_date": [], "session": [], "cause": []}).write_parquet(output / "tick_grid_audit.parquet")
    roll_rows = [{"trade_date": bar.trade_date, "session": bar.session.value, "roll_observation_status": "unknown", "observed_contract_change": None, "contract_id": None, "evidence_source": None, "available_at": None, "within_session_switch": None, "scheduled_roll_window": None} for bar in data.bars if bar.is_session_open]
    pl.DataFrame(roll_rows or {"trade_date": []}).write_parquet(output / "roll_audit.parquet")
    coverage = [{"trade_date": bar.trade_date, "session": bar.session.value, "ts_jst": bar.ts_jst, "is_eligible": bar.is_eligible} for bar in data.bars]
    pl.DataFrame(coverage).write_parquet(output / "session_coverage.parquet")
    status: QualityStatus = "BLOCKED" if flagged else "PASS_LIMITED"
    checks = {"development_only": True, "final_holdout_read": False, "tick_grid_explained": flagged == 0, "roll_evidence_known": False}
    gate: dict[str, object] = {"quality_status": status, "checks": checks, "unresolved": (["tick_grid_violation_cause_unknown"] if flagged else []) + ["contract_roll_evidence_not_supplied"], "data_version": data.data_version, "calendar_hash": sha256(calendar_path.read_bytes()).hexdigest(), "returned_trade_date_range": [study.development_start, study.development_end], "tick_grid_violation_bars": flagged}
    _write_json(output / "quality_gate.json", gate)
    _write_json(output / "provenance.json", {"data_quality": data.quality, "data_version": data.data_version, "calendar": str(calendar_path), "accessed_at": datetime.now(timezone.utc).isoformat(), "source_prices_read": "development Gold only"})
    (output / "quality_report.md").write_text(f"# R003 Development preflight\n\nStatus: **{status}**\n\nTick-grid violation bars: {flagged}\n\nNo raw data, OOS, or Final Holdout prices were read.\n", encoding="utf-8")
    return gate


def run_r003_campaign(config_dir: Path, results_root: Path, calendar_path: Path, campaign_id: str | None, progress: Any, study_config: Path, stage: str | None) -> Path:
    """Freeze R003, audit Development, and stop before PnL when quality is blocked."""
    if stage != "development":
        raise ValueError("R003 requires explicit --stage development; OOS is separately gated")
    study = load_r003_study(study_config)
    instrument, _, data_config, backtest = load_project_config(config_dir)
    if study.gold_root != data_config.gold_root or study.fee_per_side != backtest.fees.jpy_per_side_per_contract or backtest.execution.slippage_ticks != 1:
        raise ValueError("R003 study assertions disagree with project input/cost configuration")
    from n225m_bt.research.runner import reserve_directory, snapshot_source
    identifier = campaign_id or f"r003-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output = reserve_directory(results_root, identifier)
    source = snapshot_source(output, config_dir, calendar_path)
    hypothesis = study.hypothesis_document.read_text(encoding="utf-8")
    preregistration = {"study_id": "R003", "strategy_version": "1.0.0", "stage": "development", "status": "frozen_before_pnl", "settings_hash": canonical_hash(yaml.safe_load(study_config.read_text(encoding="utf-8"))), "hypothesis_hash": canonical_hash(hypothesis), "source": source, "oos_access": "not_requested", "final_holdout_access": "not_accessed"}
    _write_json(output / "preregistration.json", preregistration)
    (output / "hypothesis.md").write_text(hypothesis, encoding="utf-8")
    (output / "config.yaml").write_bytes(study_config.read_bytes())
    progress("R003: loading Development only (2021-01-01..2025-06-30) for preflight.")
    development = load_split(data_config.gold_root, "development")
    gate = run_preflight(development, study, instrument, calendar_path, output / "preflight")
    decision = "INVESTIGATE" if gate["quality_status"] != "PASS_RESEARCH" else "NOT_RUN"
    completion = {"campaign_id": identifier, "status": "blocked_before_pnl" if gate["quality_status"] == "BLOCKED" else "preflight_complete", "decision": decision, "quality_status": gate["quality_status"], "development_pnl": "NOT_RUN", "oos": "NOT_EVALUATED", "final_holdout": "NOT_ACCESSED", "reason": gate["unresolved"]}
    _write_json(output / "DEVELOPMENT_COMPLETED.json", completion)
    (output / "summary.md").write_text(f"# R003 / Decision: {decision}\n\n## Development result\n\nPnL was not computed. Quality gate: **{gate['quality_status']}**.\n\n## Validation result\n\nOOS: NOT_EVALUATED. Final Holdout: NOT_ACCESSED.\n", encoding="utf-8")
    return output


