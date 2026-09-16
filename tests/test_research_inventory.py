"""Access and identity regressions for the metadata-only research inventory."""

from pathlib import Path

import pytest

from scripts.build_research_inventory import identity, read_evidence


@pytest.mark.parametrize(
    "relative",
    [
        "data/gold/year=2026/month=01/bars.parquet",
        "results/research/r065-q001-legacy/decision.json",
        "results/research/r099-q001-oos/decision.json",
        "results/research/r099-q001-legacy/trades.parquet",
        "results/research/r099-q001-legacy/conditions/decision.json",
        "../outside.json",
    ],
)
def test_inventory_denies_price_and_restricted_payloads(relative: str) -> None:
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError):
        read_evidence(root, relative)


def test_same_legacy_id_does_not_merge_different_r066_studies() -> None:
    assert identity("r066-q001-20260915-tse-lower-tail-night-reversal-01") == (
        "R066-LOWER-TAIL/Q001"
    )
    assert identity("task-r066-q001-tse-regular-hours-fixed-long-20260915-02") == (
        "R066-CASH-LONG/Q001"
    )


def test_metadata_selection_never_follows_path_outside_root() -> None:
    # Absolute paths cannot bypass the same root constraint.
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError):
        read_evidence(root, str(root.parent / "external.json"))
