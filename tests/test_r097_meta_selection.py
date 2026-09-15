from n225m_bt.research.r097_meta_selection import mbb_indices, routed_daily_net, select_paths


def test_selection_is_current_excluded_strict_positive_and_id_tiebroken() -> None:
    daily = {"a": [1] * 252 + [99, 1], "b": [1] * 252 + [0, 2], "c": [-1] * 254}
    rows, static_id = select_paths(daily)
    assert static_id == "a"
    assert rows[0].selected_strategy_id == "a"  # a/b tie is resolved before date 252 is read.
    assert rows[0].rank_two_strategy_id == "b"
    assert rows[1].selected_strategy_id == "a"
    a, q, s = routed_daily_net(rows, daily, static_id=static_id)
    assert a == [99, 1]
    assert q == [0, 2]
    assert s == [99, 1]


def test_selection_stays_cash_when_all_scores_are_nonpositive() -> None:
    daily = {"a": [-1] * 254, "b": [0] * 254}
    rows, _ = select_paths(daily)
    assert all(
        row.selected_strategy_id is None and row.rank_two_strategy_id is None for row in rows
    )


def test_mbb_is_common_nonwrapping_and_tail_truncated() -> None:
    indices = mbb_indices(23, seed=9, repetitions=3, block_length=20)
    assert indices.shape == (3, 23)
    assert (indices[:, 1:] - indices[:, :-1] == 1).sum() >= 19 * 3
    assert indices.min() >= 0 and indices.max() < 23
