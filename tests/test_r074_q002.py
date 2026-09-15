from datetime import date, timedelta

from n225m_bt.research.r074_q002_s2 import (
    binomial_lower_predictive_quantile,
    feasibility_q002,
)


def _event(*, a: bool, executable: bool, direction: str = "long") -> dict[str, object]:
    return {
        "status": "EXECUTABLE" if executable else "SKIPPED",
        "reason": "SELECTED_STATE_FIRST_STRICT_CLOSE_BREAKOUT" if executable else "OUTSIDE_SELECTED_NIGHT_RANGE_STATE",
        "state_group": "compression",
        "reference_ranges_points_p1_to_p20": list(range(20)),
        "current_night_range_points": 1 if a else 10,
        "q25_points": 5,
        "breakout_direction": direction,
    }


def test_binomial_lower_predictive_quantile_is_frozen_to_inverse_cdf_definition() -> None:
    assert binomial_lower_predictive_quantile(0) == 0
    assert binomial_lower_predictive_quantile(20) == 2


def test_q002_s2_uses_reference_set_denominator_and_a_execution_rate() -> None:
    primary: dict[date, dict[str, object]] = {}
    middle: dict[date, dict[str, object]] = {}
    for year in range(2021, 2025):
        start = date(year, 1, 1)
        for offset in range(180):
            primary[start + timedelta(days=offset)] = _event(
                a=offset < 45, executable=offset < 45, direction="long" if offset % 2 else "short"
            )
        for offset in range(75):
            target = start + timedelta(days=offset)
            middle[target] = {
                "status": "EXECUTABLE",
                "reason": "SELECTED_STATE_FIRST_STRICT_CLOSE_BREAKOUT",
                "state_group": "middle",
            }
    result = feasibility_q002(primary, middle)
    gate = result["gate"]
    assert gate["passed"] is True
    annual = gate["annual_nominal_state_eligibility"]
    assert annual["2021"]["reference_set_established_n"] == 180
    assert annual["2021"]["a_occurrences"] == 45
    assert annual["2021"]["a_executable_rate"] == 1.0
