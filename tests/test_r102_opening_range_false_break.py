from n225m_bt.research.r102_opening_range_false_break import FalseBreakMeasure, nearest_rank, route


def test_registered_quantile_and_routes() -> None:
    assert nearest_rank([1.0, 2.0, 3.0, 4.0]) == 2.0
    assert route("Q", "S") == "reverse"
    assert route("Q", "B") == "break"
    assert route("T", "U") == "reverse"
    assert route("Z", "S") is None


def test_measure_x_is_immutable() -> None:
    item = FalseBreakMeasure(1, "Q", 10.0, 5.0, -1.0)
    assert item.x == 0.5
