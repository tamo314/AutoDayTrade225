"""PnL-free S2 eligibility gate for the separately frozen R074-Q002 run."""

from __future__ import annotations

from collections import Counter
from datetime import date
from math import comb

BINOMIAL_PROBABILITY = 0.25
LOWER_TAIL_ALPHA = 0.05


def binomial_lower_predictive_quantile(
    trials: int, *, probability: float = BINOMIAL_PROBABILITY, alpha: float = LOWER_TAIL_ALPHA
) -> int:
    """Return min k such that Pr[Binomial(trials, probability) <= k] >= alpha."""
    if trials < 0 or not 0 < probability < 1 or not 0 < alpha < 1:
        raise ValueError("invalid binomial predictive-quantile parameters")
    cumulative = 0.0
    for successes in range(trials + 1):
        cumulative += comb(trials, successes) * probability**successes * (1 - probability) ** (
            trials - successes
        )
        if cumulative >= alpha:
            return successes
    raise AssertionError("binomial CDF did not reach one")


def _reference_set_established(event: dict[str, object]) -> bool:
    ranges = event.get("reference_ranges_points_p1_to_p20")
    return isinstance(ranges, list) and len(ranges) == 20


def _a_occurs(event: dict[str, object]) -> bool:
    current = event.get("current_night_range_points")
    q25 = event.get("q25_points")
    return isinstance(current, int) and isinstance(q25, int) and current <= q25


def _unexplained_dates(events: list[tuple[date, dict[str, object]]]) -> list[str]:
    known_prefixes = (
        "INSUFFICIENT_",
        "REFERENCE_",
        "CURRENT_",
        "OUTSIDE_",
        "R004_",
        "OPENING_",
        "BREAKOUT_",
        "NO_STRICT_",
        "DELAY_",
        "NO_NEXT_",
        "FIXED_EXIT_",
        "SELECTED_",
    )
    return [
        target.isoformat()
        for target, event in events
        if not str(event.get("reason", "")).startswith(known_prefixes)
    ]


def feasibility_q002(
    compression_events: dict[date, dict[str, object]], middle_events: dict[date, dict[str, object]]
) -> dict[str, object]:
    """Calculate only the pre-registered R074-Q002 S2 availability criteria."""
    executable_a = [
        target
        for target, event in compression_events.items()
        if event.get("status") == "EXECUTABLE" and event.get("state_group") == "compression"
    ]
    executable_d = [
        target
        for target, event in middle_events.items()
        if event.get("status") == "EXECUTABLE" and event.get("state_group") == "middle"
    ]
    by_side = Counter(str(compression_events[target]["breakout_direction"]) for target in executable_a)
    annual: dict[str, dict[str, int | float | bool | None]] = {}
    for year in range(2021, 2025):
        year_events = [
            event for target, event in compression_events.items() if target.year == year and _reference_set_established(event)
        ]
        a_events = [event for event in year_events if _a_occurs(event)]
        executable = sum(event.get("status") == "EXECUTABLE" for event in a_events)
        n = len(year_events)
        lower_quantile = binomial_lower_predictive_quantile(n)
        rate = executable / len(a_events) if a_events else None
        annual[str(year)] = {
            "reference_set_established_n": n,
            "a_occurrences": len(a_events),
            "binomial_probability": BINOMIAL_PROBABILITY,
            "binomial_lower_tail_alpha": LOWER_TAIL_ALPHA,
            "binomial_lower_5pct_predictive_quantile": lower_quantile,
            "a_occurrences_at_least_lower_quantile": len(a_events) >= lower_quantile,
            "a_executable": executable,
            "a_executable_rate": rate,
            "minimum_a_executable_rate": 0.75,
            "a_executable_rate_at_least_75pct": rate is not None and rate >= 0.75,
        }
    unexplained = _unexplained_dates([*compression_events.items(), *middle_events.items()])
    gate = {
        "compression_observable_trades": len(executable_a),
        "minimum_compression_observable_trades": 150,
        "compression_at_least_150": len(executable_a) >= 150,
        "compression_direction_counts": dict(sorted(by_side.items())),
        "minimum_compression_long_and_short_each": 45,
        "compression_direction_minima_passed": by_side["long"] >= 45 and by_side["short"] >= 45,
        "middle_state_observable_trades": len(executable_d),
        "minimum_middle_state_observable_trades": 300,
        "middle_state_at_least_300": len(executable_d) >= 300,
        "unexplained_exclusion_trade_dates": unexplained,
        "unexplained_exclusions_equal_zero": len(unexplained) == 0,
        "annual_nominal_state_eligibility": annual,
        "annual_2021_2024_binomial_and_execution_rate_passed": all(
            bool(item["a_occurrences_at_least_lower_quantile"])
            and bool(item["a_executable_rate_at_least_75pct"])
            for item in annual.values()
        ),
    }
    gate["passed"] = all(
        bool(gate[key])
        for key in (
            "compression_at_least_150",
            "compression_direction_minima_passed",
            "middle_state_at_least_300",
            "unexplained_exclusions_equal_zero",
            "annual_2021_2024_binomial_and_execution_rate_passed",
        )
    )
    return {
        "s2_scope": "PnL-free availability only; no return, win/loss, PF, ranking, or performance statistic.",
        "compression_status_counts": dict(
            sorted(Counter(str(event.get("reason", event["status"])) for event in compression_events.values()).items())
        ),
        "middle_status_counts": dict(
            sorted(Counter(str(event.get("reason", event["status"])) for event in middle_events.values()).items())
        ),
        "gate": gate,
    }
