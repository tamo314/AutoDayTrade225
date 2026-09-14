from __future__ import annotations

import pytest

from n225m_bt.research.r062 import nearest_rank, r062_event


def test_nearest_rank_and_preregistered_reaction_windows() -> None:
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 75) == 3.0
    with pytest.raises(ValueError, match="nearest-rank"):
        nearest_rank([], 75)
    with pytest.raises(ValueError, match="reaction window"):
        r062_event(None, None, None, None, [], reaction_minutes=11)  # type: ignore[arg-type]
