from n225m_bt.research.r100_morning_efficiency_switch import MorningMeasure, route, state_rows


def test_states_use_only_the_exact_prior_scheduled_window() -> None:
    measures = [MorningMeasure(float(i + 1), 0.1 + i / 1000, 1) for i in range(120)]
    measures.extend([None, MorningMeasure(999.0, 0.9, 1)])
    rows = state_rows(measures)
    assert rows[120].reason == "CURRENT_MORNING_UNAVAILABLE"
    assert len(rows[121].references) == 119
    assert rows[121].reason == "STATE_AVAILABLE"
    assert all(item < 121 for item in rows[121].references)


def test_fixed_routes_have_no_middle_or_unavailable_trade() -> None:
    assert route("HE", "A") == "continuation"
    assert route("LE", "A") == "reversal"
    assert route("HE", "I") == "reversal"
    assert route("NONE", "C") is None
    assert route("LE", "N") is None


def test_v_event_uses_only_prior_range_quantiles() -> None:
    measures = [MorningMeasure(float(i + 1), 0.1 + i / 1000, 1, float(i + 10)) for i in range(120)]
    rows = state_rows([*measures, MorningMeasure(999.0, 0.9, 1, 999.0)], event_metric="v")
    assert rows[-1].reason == "STATE_AVAILABLE"
    assert rows[-1].state == "HE"
    assert all(item < len(measures) for item in rows[-1].references)
