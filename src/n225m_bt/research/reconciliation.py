"""Metric-lineage contracts for audits that must not reopen result ledgers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReconciliationStatus(StrEnum):
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    COMPARABLE = "COMPARABLE"


@dataclass(frozen=True, slots=True)
class MetricLineage:
    """The non-numeric provenance of a displayed or inferred metric."""

    metric_id: str
    artifact: str
    field: str
    unit: str
    population: str
    aggregation: str
    weighting: str
    center: str


@dataclass(frozen=True, slots=True)
class ComparisonLineage:
    comparison_id: str
    left_metric_id: str
    right_metric_id: str
    output_artifact: str
    output_field: str
    output_unit: str
    output_population: str
    output_aggregation: str
    output_weighting: str
    output_center: str


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    status: ReconciliationStatus
    comparison_id: str
    reasons: tuple[str, ...]


def assess_comparison(
    metrics: tuple[MetricLineage, ...], comparison: ComparisonLineage
) -> ReconciliationFinding:
    """Assess definition compatibility only; no metric values are accepted."""
    by_id = {item.metric_id: item for item in metrics}
    if len(by_id) != len(metrics):
        raise ValueError("metric lineage IDs must be unique")
    try:
        left, right = by_id[comparison.left_metric_id], by_id[comparison.right_metric_id]
    except KeyError as exc:
        raise ValueError("comparison references an unknown metric lineage") from exc
    reasons: list[str] = []
    if left.unit != right.unit or left.unit != comparison.output_unit:
        reasons.append("unit differs")
    if left.population != right.population or left.population != comparison.output_population:
        reasons.append("population differs")
    if left.aggregation != right.aggregation or left.aggregation != comparison.output_aggregation:
        reasons.append("aggregation differs")
    if left.weighting != right.weighting or left.weighting != comparison.output_weighting:
        reasons.append("weighting differs")
    if left.center != right.center or left.center != comparison.output_center:
        reasons.append("estimate center differs")
    return ReconciliationFinding(
        ReconciliationStatus.RECONCILIATION_REQUIRED if reasons else ReconciliationStatus.COMPARABLE,
        comparison.comparison_id,
        tuple(reasons),
    )


R032_DISPLAYED_CONFIRMATION_METRICS: tuple[MetricLineage, ...] = (
    MetricLineage(
        "confirmed_0tick_gross_expectancy",
        "gap_direction_0tick_diagnostic.json",
        "confirmed.gross_expectancy_jpy",
        "JPY_PER_TRADE",
        "filled confirmed events",
        "trade gross pnl / filled trade count",
        "uniform per filled trade",
        "arithmetic mean",
    ),
    MetricLineage(
        "nonconfirmed_0tick_gross_expectancy",
        "gap_direction_0tick_diagnostic.json",
        "nonconfirmed.gross_expectancy_jpy",
        "JPY_PER_TRADE",
        "filled nonconfirmed events",
        "trade gross pnl / filled trade count",
        "uniform per filled trade",
        "arithmetic mean",
    ),
)

R032_STATED_DIFFERENCE = ComparisonLineage(
    "confirmed_minus_nonconfirmed_0tick_gross",
    "confirmed_0tick_gross_expectancy",
    "nonconfirmed_0tick_gross_expectancy",
    "bootstrap.json",
    "confirmed_minus_nonconfirmed_gap_direction_0tick_gross_daily_mean_jpy.estimate",
    "JPY_PER_TARGET_NIGHT",
    "all frozen target nights, including no-trade zero days",
    "paired daily gross pnl difference / frozen target-night count",
    "uniform per frozen target night",
    "arithmetic mean; bootstrap CI center uses same daily estimator",
)
