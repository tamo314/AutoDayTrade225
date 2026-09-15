from n225m_bt.research.r099_night_efficiency_switch import NightMeasure, route, state_rows


def test_strict_prior_state_and_no_backfill() -> None:
    measures = [NightMeasure(float(index), index + 10) for index in range(120)]
    measures.extend([None, NightMeasure(200.0, 200)])
    rows = state_rows(measures)
    assert rows[120].reason == "CURRENT_NIGHT_UNAVAILABLE"
    assert len(rows[121].references) == 119
    assert rows[121].reason == "STATE_AVAILABLE"
    assert all(prior < 121 for prior in rows[121].references)


def test_route_is_fixed_and_middle_is_cash() -> None:
    assert route("HE", "A") == "R079-A"
    assert route("LE", "A") == "R078-A"
    assert route("HE", "I") == "R078-A"
    assert route("ME", "C") is None
