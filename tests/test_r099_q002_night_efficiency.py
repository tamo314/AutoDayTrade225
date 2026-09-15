from n225m_bt.research.r099_q002_night_efficiency import NightMeasure, route, state_rows


def test_latest_complete_history_is_current_excluded_and_not_backfilled() -> None:
    measures = [NightMeasure(float(index), index + 10) for index in range(180)]
    measures[179] = None
    measures.append(NightMeasure(250.0, 250))
    row = state_rows(measures)[180]
    assert row.reason == "STATE_AVAILABLE"
    assert len(row.references) == 100
    assert row.references[-1] == 178
    assert all(index < row.index and row.index - index <= 180 for index in row.references)


def test_registered_profiles_and_routes_are_bounded() -> None:
    measures = [NightMeasure(float(index), index + 10) for index in range(181)]
    assert state_rows(measures, history_count=80)[180].state is not None
    assert route("HE", "A") == "R079-A"
    assert route("LE", "A") == "R078-A"
    assert route("ME", "C") is None
