"""PnL-free K* complement control for TASK-R096-Q002.

Q001's opening-range observation, strict-prior quantiles, E definition, and
execution timing are imported unchanged.  This module changes only the control
membership from the old lower-tail K to the non-E complement within x >= qx50.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date, time
from typing import cast

from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.domain import Bar, Session
from n225m_bt.research.r096_opening_range_acceptance import (
    causality_audit,
    nearest_rank,
    r096_event,
    state_ledger,
)


def q002_state_ledger(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    *,
    window_minutes: int = 60,
    x_cutoff: int = 50,
    z_upper: int = 75,
) -> list[dict[str, object]]:
    """Return Q001 state rows with unchanged E and complement K* labels."""
    rows = state_ledger(
        classifier,
        axis,
        bars,
        isolated,
        window_minutes=window_minutes,
        x_cutoff=x_cutoff,
        z_upper=z_upper,
    )
    for row in rows:
        row["q002_control_definition"] = "x>=qx and z<qz_upper; equality belongs to E"
        row["kstar_condition"] = False
        row["qz50"] = None
        if "qx_cutoff" not in row or not bool(row.get("observation_valid")):
            continue
        references = cast(list[str], row["reference_valid_trade_dates"])
        # The source ledger already establishes the strict-prior membership.  Reconstruct
        # qz50 solely from its saved prior state values, never from the current row.
        by_date = {cast(str, item["trade_date"]): item for item in rows}
        zs = [
            cast(float, by_date[target]["z_directional_close_position"])
            for target in references
        ]
        qz50 = nearest_rank(zs, 50)
        x, z = (
            cast(float, row["x_abs_displacement_fraction"]),
            cast(float, row["z_directional_close_position"]),
        )
        qx, qzhigh = cast(float, row["qx_cutoff"]), cast(float, row["qz_high"])
        accepted, kstar = x >= qx and z >= qzhigh, x >= qx and z < qzhigh
        row.update(
            qz50=qz50,
            accepted_condition=accepted,
            control_condition=kstar,
            high_efficiency_condition=accepted,
            low_efficiency_condition=kstar,
            kstar_condition=kstar,
        )
        if accepted:
            row.update(condition="E", reason="HIGH_X_DIRECTIONAL_RANGE_END_ACCEPTANCE")
        elif kstar:
            row.update(condition="KSTAR", reason="HIGH_X_NON_ACCEPTANCE_COMPLEMENT_CONTROL")
        else:
            row.update(condition="NONE", reason="OUTSIDE_REGISTERED_E_KSTAR_CONDITIONS")
    return cast(list[dict[str, object]], rows)


def build_q002_events(
    classifier: CalendarClassifier,
    axis: Iterable[date],
    bars: dict[tuple[date, Session], list[Bar]],
    isolated: set[tuple[date, Session]],
    **kwargs: int | object,
) -> list[dict[str, object]]:
    """Add the unmodified Q001 next-open/T-minus-exit event path to E/K*."""
    state_keys = {
        key: cast(int, value)
        for key, value in kwargs.items()
        if key in {"window_minutes", "x_cutoff", "z_upper"}
    }
    return [
        r096_event(
            row,
            bars,
            entry_extra_bars=cast(int, kwargs.get("entry_extra_bars", 0)),
            exit_minus_minutes=cast(int, kwargs.get("exit_minus_minutes", 5)),
            fixed_exit_time=cast(time | None, kwargs.get("fixed_exit_time")),
        )
        for row in q002_state_ledger(classifier, axis, bars, isolated, **state_keys)
    ]


def q002_feasibility(events: Iterable[dict[str, object]]) -> dict[str, object]:
    """Evaluate the pre-registered, PnL-free Q002 availability gate."""
    rows = list(events)
    e = [row for row in rows if bool(row.get("accepted_condition")) and row["status"] == "EXECUTABLE"]
    k = [row for row in rows if bool(row.get("kstar_condition")) and row["status"] == "EXECUTABLE"]
    ready = [row for row in rows if "qx_cutoff" in row and bool(row.get("observation_valid"))]

    def counts(items: list[dict[str, object]], field: str) -> Counter[str]:
        if field == "direction":
            return Counter(
                "up" if cast(float, row["displacement_points"]) > 0 else "down" for row in items
            )
        if field == "year":
            return Counter(str(date.fromisoformat(cast(str, row["trade_date"])).year) for row in items)
        return Counter(cast(str, row.get(field, "")) for row in items)

    e_direction, k_direction = counts(e, "direction"), counts(k, "direction")
    e_year, k_year = counts(e, "year"), counts(k, "year")
    e_regime, k_regime = counts(e, "tse_close_regime"), counts(k, "tse_close_regime")
    bands = {
        band: {
            "E": sum(row.get("x_band") == band for row in e),
            "KSTAR": sum(row.get("x_band") == band for row in k),
        }
        for band in ("X50_75", "X75_100")
    }
    unknown = sorted(
        cast(str, row["trade_date"]) for row in rows if row["status"] == "ENTRY_FILLED_EXIT_UNKNOWN"
    )
    known = (
        "NO_", "R004_", "WINDOW_", "P0_", "ZERO_", "NONPOSITIVE_", "VALID_", "INSUFFICIENT_",
        "DEGENERATE_", "OUTSIDE_", "HIGH_", "E_", "KSTAR_", "ENTRY_", "NEXT_", "FIXED_",
    )
    unexplained = [
        cast(str, row["trade_date"])
        for row in rows
        if not str(row.get("reason", "")).startswith(known)
    ]
    gate: dict[str, object] = {
        "q_ready_nonzero_days": len(ready),
        "q_ready_nonzero_at_least_800": len(ready) >= 800,
        "e_completed": len(e), "kstar_completed": len(k),
        "e_completed_at_least_100": len(e) >= 100,
        "kstar_completed_at_least_200": len(k) >= 200,
        "e_direction_counts": dict(sorted(e_direction.items())),
        "kstar_direction_counts": dict(sorted(k_direction.items())),
        "e_up_down_at_least_35_each": e_direction["up"] >= 35 and e_direction["down"] >= 35,
        "kstar_up_down_at_least_50_each": k_direction["up"] >= 50 and k_direction["down"] >= 50,
        "e_by_year": {str(year): e_year[str(year)] for year in range(2021, 2026)},
        "kstar_by_year": {str(year): k_year[str(year)] for year in range(2021, 2026)},
        "e_2021_initialization_at_least_8": e_year["2021"] >= 8,
        "e_2022_2024_at_least_18_each": all(e_year[str(year)] >= 18 for year in range(2022, 2025)),
        "e_2025_h1_at_least_8": e_year["2025"] >= 8,
        "kstar_2021_initialization_at_least_10": k_year["2021"] >= 10,
        "kstar_2022_2024_at_least_35_each": all(k_year[str(year)] >= 35 for year in range(2022, 2025)),
        "kstar_2025_h1_at_least_10": k_year["2025"] >= 10,
        "e_tse_regime_counts": dict(sorted(e_regime.items())),
        "kstar_tse_regime_counts": dict(sorted(k_regime.items())),
        "e_old_new_at_least_85_8": e_regime["old"] >= 85 and e_regime["new"] >= 8,
        "kstar_old_new_at_least_170_15": k_regime["old"] >= 170 and k_regime["new"] >= 15,
        "x_band_counts": bands,
        "each_x_band_e_kstar_at_least_20": all(
            values["E"] >= 20 and values["KSTAR"] >= 20 for values in bands.values()
        ),
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": not unexplained,
        "entry_filled_exit_unknown_trade_dates": unknown,
        "entry_filled_exit_unknown_equal_zero": not unknown,
    }
    required = (
        "q_ready_nonzero_at_least_800", "e_completed_at_least_100", "kstar_completed_at_least_200",
        "e_up_down_at_least_35_each", "kstar_up_down_at_least_50_each",
        "e_2021_initialization_at_least_8", "e_2022_2024_at_least_18_each", "e_2025_h1_at_least_8",
        "kstar_2021_initialization_at_least_10", "kstar_2022_2024_at_least_35_each",
        "kstar_2025_h1_at_least_10", "e_old_new_at_least_85_8", "kstar_old_new_at_least_170_15",
        "each_x_band_e_kstar_at_least_20", "unexplained_exclusions_equal_zero",
        "entry_filled_exit_unknown_equal_zero",
    )
    gate["passed"] = all(bool(gate[name]) for name in required)
    return {
        "scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "status_counts": dict(sorted(Counter(str(row.get("reason", row["status"])) for row in rows).items())),
        "condition_counts": {"E": len(e), "KSTAR": len(k)}, "gate": gate,
    }


def q002_causality_audit(events: list[dict[str, object]], axis: list[date]) -> dict[str, object]:
    """Extend Q001's audit with complement partition and equality ownership."""
    audit = causality_audit(events, axis)
    labelled = [row for row in events if "qx_cutoff" in row and bool(row.get("observation_valid"))]
    checks = cast(dict[str, bool], audit["checks"])
    checks.update(
        e_kstar_partition_all_high_x=all(
            bool(row["accepted_condition"]) != bool(row["kstar_condition"])
            if cast(float, row["x_abs_displacement_fraction"]) >= cast(float, row["qx_cutoff"])
            else not bool(row["accepted_condition"]) and not bool(row["kstar_condition"])
            for row in labelled
        ),
        z_qz_upper_equality_belongs_to_e=all(
            cast(float, row["x_abs_displacement_fraction"]) < cast(float, row["qx_cutoff"])
            or cast(float, row["z_directional_close_position"]) != cast(float, row["qz_high"])
            or bool(row["accepted_condition"])
            for row in labelled
        ),
        kstar_selected_without_entry_or_exit_availability=all(
            bool(row.get("kstar_condition")) or bool(row.get("accepted_condition")) or row["status"] == "SKIPPED"
            for row in events
        ),
    )
    audit["passed"] = all(checks.values())
    return cast(dict[str, object], audit)
