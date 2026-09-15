"""PnL-free S2 eligibility gate for frozen R075-Q001."""

from __future__ import annotations

from collections import Counter
from datetime import date
from math import comb

BINOMIAL_PROBABILITY = 0.25
LOWER_TAIL_ALPHA = 0.05


def binomial_lower_predictive_quantile(trials: int) -> int:
    """Return min k such that the frozen Binomial CDF is at least five percent."""
    if trials < 0:
        raise ValueError("trials must be non-negative")
    cumulative = 0.0
    for successes in range(trials + 1):
        cumulative += comb(trials, successes) * BINOMIAL_PROBABILITY**successes * (1 - BINOMIAL_PROBABILITY) ** (trials - successes)
        if cumulative >= LOWER_TAIL_ALPHA:
            return successes
    raise AssertionError("binomial CDF did not reach one")


def _reference_set_established(event: dict[str, object]) -> bool:
    ranges = event.get("reference_ranges_points_p1_to_p20")
    return isinstance(ranges, list) and len(ranges) == 20


def _high_occurs(event: dict[str, object]) -> bool:
    current, q25, q75 = event.get("current_night_range_points"), event.get("q25_points"), event.get("q75_points")
    return isinstance(current, int) and isinstance(q25, int) and isinstance(q75, int) and q25 < q75 and current >= q75


def feasibility_q001(high_events: dict[date, dict[str, object]], middle_events: dict[date, dict[str, object]]) -> dict[str, object]:
    """Calculate only the pre-registered availability criteria, never PnL statistics."""
    executable_h = [target for target, event in high_events.items() if event.get("status") == "EXECUTABLE"]
    executable_m = [target for target, event in middle_events.items() if event.get("status") == "EXECUTABLE"]
    sides = Counter(str(high_events[target]["breakout_direction"]) for target in executable_h)
    annual: dict[str, dict[str, int | float | bool | None]] = {}
    for year in range(2021, 2025):
        year_events = [event for target, event in high_events.items() if target.year == year and _reference_set_established(event)]
        occurrences = [event for event in year_events if _high_occurs(event)]
        executable = sum(event.get("status") == "EXECUTABLE" for event in occurrences)
        n = len(year_events)
        rate = executable / len(occurrences) if occurrences else None
        threshold = binomial_lower_predictive_quantile(n)
        annual[str(year)] = {
            "reference_set_established_n": n,
            "h_occurrences": len(occurrences),
            "binomial_probability": BINOMIAL_PROBABILITY,
            "binomial_lower_tail_alpha": LOWER_TAIL_ALPHA,
            "binomial_lower_5pct_predictive_quantile": threshold,
            "h_occurrences_at_least_lower_quantile": len(occurrences) >= threshold,
            "h_executable": executable,
            "h_executable_rate": rate,
            "minimum_h_executable_rate": 0.75,
            "h_executable_rate_at_least_75pct": rate is not None and rate >= 0.75,
        }
    known_prefixes = ("INSUFFICIENT_", "REFERENCE_", "CURRENT_", "STATE_", "OUTSIDE_", "R004_", "OPENING_", "BREAKOUT_", "NO_STRICT_", "DELAY_", "NO_NEXT_", "FIXED_EXIT_", "SELECTED_")
    unexplained = [
        target.isoformat()
        for target, event in [*high_events.items(), *middle_events.items()]
        if not str(event.get("reason", "")).startswith(known_prefixes)
    ]
    gate = {
        "high_observable_trades": len(executable_h),
        "minimum_high_observable_trades": 150,
        "high_at_least_150": len(executable_h) >= 150,
        "high_direction_counts": dict(sorted(sides.items())),
        "minimum_high_long_and_short_each": 45,
        "high_direction_minima_passed": sides["long"] >= 45 and sides["short"] >= 45,
        "middle_observable_trades": len(executable_m),
        "minimum_middle_observable_trades": 300,
        "middle_at_least_300": len(executable_m) >= 300,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": len(unexplained) == 0,
        "annual_nominal_state_eligibility": annual,
        "annual_2021_2024_binomial_and_execution_rate_passed": all(bool(item["h_occurrences_at_least_lower_quantile"]) and bool(item["h_executable_rate_at_least_75pct"]) for item in annual.values()),
    }
    gate["passed"] = all(bool(gate[key]) for key in ("high_at_least_150", "high_direction_minima_passed", "middle_at_least_300", "unexplained_exclusions_equal_zero", "annual_2021_2024_binomial_and_execution_rate_passed"))
    return {"s2_scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.", "high_status_counts": dict(sorted(Counter(str(event.get("reason", event["status"])) for event in high_events.values()).items())), "middle_status_counts": dict(sorted(Counter(str(event.get("reason", event["status"])) for event in middle_events.values()).items())), "gate": gate}
