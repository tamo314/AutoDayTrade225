from datetime import date, timedelta

from n225m_bt.research.r075_high_night_range_opening_breakout import select_state
from n225m_bt.research.r075_q001_s2 import binomial_lower_predictive_quantile, feasibility_q001


def test_high_state_is_exclusive_and_degenerate_quantiles_are_not_rescued() -> None:
    assert select_state(100, q25=20, q75=80, high_percentile_value=80) == ("high", None)
    assert select_state(20, q25=20, q75=80, high_percentile_value=80) == ("low", None)
    assert select_state(50, q25=20, q75=80, high_percentile_value=80) == ("middle", None)
    assert select_state(80, q25=80, q75=80, high_percentile_value=80) == (None, "STATE_DEGENERATE_Q25_GTE_Q75")


def _event(*, high: bool, executable: bool, direction: str = "long") -> dict[str, object]:
    return {"status": "EXECUTABLE" if executable else "SKIPPED", "reason": "SELECTED_STATE_FIRST_STRICT_CLOSE_BREAKOUT" if executable else "OUTSIDE_SELECTED_NIGHT_RANGE_STATE", "reference_ranges_points_p1_to_p20": list(range(20)), "current_night_range_points": 90 if high else 50, "q25_points": 20, "q75_points": 80, "breakout_direction": direction}


def test_s2_counts_high_occurrences_against_all_reference_sets() -> None:
    high: dict[date, dict[str, object]] = {}
    middle: dict[date, dict[str, object]] = {}
    for year in range(2021, 2025):
        start = date(year, 1, 1)
        for offset in range(180):
            high[start + timedelta(days=offset)] = _event(high=offset < 60, executable=offset < 60, direction="long" if offset % 2 else "short")
        for offset in range(80):
            middle[start + timedelta(days=offset)] = {"status": "EXECUTABLE", "reason": "SELECTED_STATE_FIRST_STRICT_CLOSE_BREAKOUT"}
    result = feasibility_q001(high, middle)
    assert binomial_lower_predictive_quantile(20) == 2
    assert result["gate"]["passed"] is True
    assert result["gate"]["annual_nominal_state_eligibility"]["2021"]["reference_set_established_n"] == 180
