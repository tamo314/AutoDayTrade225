from __future__ import annotations

from n225m_bt.research.r017 import (
    RULES,
    alternative_theta,
    centered_errors,
    detection_rates,
    rule_detected,
    scenario_theta,
)


def _means() -> dict[str, float]:
    return {
        f"{rule}_{suffix}": 0.0
        for rule in RULES
        for suffix in ("P0_A", "P1_A", "P0_A_minus_B", "P0_A_minus_C")
    }


def test_centered_errors_preserve_the_full_twenty_series_correspondence() -> None:
    means = _means()
    first = {name: float(index) for index, name in enumerate(means)}
    second = {name: float(index + 10) for index, name in enumerate(means)}
    errors = centered_errors(means, [first, second])
    assert len(errors) == 2
    assert errors[0]["R011_P0_A"] == 0.0
    assert errors[1]["R015_P0_A_minus_C"] == 29.0


def test_null_and_one_rule_injection_keep_controls_at_zero_and_apply_cost_difference() -> None:
    c1 = {rule: 10.0 for rule in RULES}
    c2 = {rule: 30.0 for rule in RULES}
    null = scenario_theta(c1, None, None)
    injected = alternative_theta(c1, c2, "R013", 1.0)
    assert null["R013_P0_A"] == 0.0
    assert null["R013_P1_A"] == -10.0
    assert injected["R013_P0_A"] == 30.0
    assert injected["R013_P1_A"] == 20.0
    assert injected["R012_P0_A"] == 0.0
    assert injected["R012_P1_A"] == -10.0


def test_detection_uses_strict_positive_lower_bounds_and_is_monotone_for_fixed_errors() -> None:
    c1 = {rule: 1.0 for rule in RULES}
    c2 = {rule: 10.0 for rule in RULES}
    errors = [_means(), {name: 5.0 for name in _means()}]
    exact_zero = {name: 0.0 for name in _means()}
    exact_zero["R011_P0_A"] = 10.0
    exact_zero["R011_P0_A_minus_B"] = 10.0
    exact_zero["R011_P0_A_minus_C"] = 10.0
    assert not rule_detected(exact_zero, 10.0, "R011")
    low = detection_rates(alternative_theta(c1, c2, "R011", 0.5), errors, 1.0)["R011"]
    high = detection_rates(alternative_theta(c1, c2, "R011", 2.0), errors, 1.0)["R011"]
    assert low <= high
