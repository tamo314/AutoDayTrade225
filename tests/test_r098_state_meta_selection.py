from n225m_bt.research.r098_state_meta_selection import (
    CALIBRATION_DAYS,
    mbb_indices,
    routed_daily_net,
    select_state_paths,
)


def test_state_scores_are_strict_prior_and_second_rank_requires_positive_score() -> None:
    daily = {
        "a": [1] * CALIBRATION_DAYS + [99],
        "b": [0] * CALIBRATION_DAYS + [7],
        "c": [-1] * (CALIBRATION_DAYS + 1),
    }
    states = ["L"] * (CALIBRATION_DAYS + 1)
    rows, static_choices, unavailable = select_state_paths(daily, states, state_observations=40)
    assert not unavailable
    assert static_choices == {"L": "a", "M": None, "H": None}
    assert rows[0].selected_strategy_id == "a"
    assert rows[0].rank_two_strategy_id is None
    assert rows[0].matching_prior_indices[-1] == CALIBRATION_DAYS - 1
    a, q, s, u = routed_daily_net(rows, daily)
    assert a == [99] and q == [0] and s == [99] and u == [99]


def test_state_unavailable_is_excluded_but_short_history_routes_a_and_q_to_cash() -> None:
    daily = {"a": [1] * (CALIBRATION_DAYS + 2), "b": [0] * (CALIBRATION_DAYS + 2)}
    states = ["L"] * CALIBRATION_DAYS + [None, "M"]
    rows, _, unavailable = select_state_paths(daily, states, state_observations=40)
    assert len(rows) == 1
    assert rows[0].state == "M"
    assert rows[0].selected_strategy_id is None
    assert rows[0].rank_two_strategy_id is None
    assert rows[0].matching_prior_indices == ()
    assert unavailable[CALIBRATION_DAYS] == "STATE_UNAVAILABLE"


def test_mbb_is_common_nonwrapping_and_tail_truncated() -> None:
    indices = mbb_indices(23, seed=9, repetitions=3, block_length=20)
    assert indices.shape == (3, 23)
    assert (indices[:, 1:] - indices[:, :-1] == 1).sum() >= 19 * 3
    assert indices.min() >= 0 and indices.max() < 23
