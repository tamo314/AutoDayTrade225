from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from n225m_bt.domain import Bar, Session
from n225m_bt.research import r062
from n225m_bt.research.r062 import nearest_rank, r062_event, r062_exec_event


def test_nearest_rank_and_preregistered_reaction_windows() -> None:
    assert nearest_rank([1.0, 2.0, 3.0, 4.0], 75) == 3.0
    with pytest.raises(ValueError, match="nearest-rank"):
        nearest_rank([], 75)
    with pytest.raises(ValueError, match="reaction window"):
        r062_event(None, None, None, None, [], reaction_minutes=11)  # type: ignore[arg-type]


class _Classifier:
    def session_open(self, target: date, session: Session) -> datetime:
        assert session is Session.DAY
        return datetime.combine(target, datetime.min.time()).replace(hour=8, minute=45)


def test_r1_exec_adapter_ignores_future_exit_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    target = date(2024, 11, 5)
    start = _Classifier().session_open(target, Session.DAY)

    def observed(_classifier: object, _day: date, _rows: object) -> dict[str, object]:
        return {"status": "valid", "rN": 0.1, "x": 0.1}

    monkeypatch.setattr(r062, "night_observation", observed)
    target_day = [
        Bar(start + timedelta(minutes=index), target, target, Session.DAY, "synthetic", 100, 100, 90, 90)
        for index in range(66)
        if index != 50
    ]
    history = [
        (
            prior_day := target - timedelta(days=index + 1),
            None,
            [
                Bar(
                    _Classifier().session_open(prior_day, Session.DAY) + timedelta(minutes=minute),
                    prior_day,
                    prior_day,
                    Session.DAY,
                    "synthetic",
                    100,
                    100,
                    90,
                    90,
                )
                for minute in range(66)
            ],
            False,
        )
        for index in range(120)
    ]
    adapter = r062_exec_event(_Classifier(), target, None, target_day, history)  # type: ignore[arg-type]
    legacy = r062_event(_Classifier(), target, None, target_day, history)  # type: ignore[arg-type]
    assert adapter["status"] == "E_EXEC"
    assert adapter["selection_status"] == "A"
    assert legacy["reason"] == "TSE_COMMON_EXECUTION_PATH_INVALID"
