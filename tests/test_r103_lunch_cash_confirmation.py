from n225m_bt.research.r103_lunch_cash_confirmation import (
    LunchConfirmationMeasure,
    nearest_rank,
    route,
)


def test_registered_quantiles_and_routes() -> None:
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert route("K", "A") == "c"
    assert route("D", "A") is None
    assert route("D", "D_control") == "c"
    assert route("K", "R") == "reverse"


def test_measure_is_immutable_value_object() -> None:
    assert LunchConfirmationMeasure(10.0, -5.0).p_points == 10.0
