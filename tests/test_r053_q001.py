from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from n225m_bt.config import JST
from n225m_bt.domain import Bar, Session
from n225m_bt.research.data import partition_paths
from n225m_bt.research.r020 import TSECashMarketCalendar
from n225m_bt.research.r053 import R053QNotIdentifiableError, fwl_delta, r053_event


def rows(day: date, gap: int = 100, future: int = 0) -> list[Bar]:
    stamps = [
        datetime(day.year, day.month, day.day, 9, 0, tzinfo=JST) + timedelta(minutes=i)
        for i in range(150)
    ]
    stamps += [
        datetime(day.year, day.month, day.day, 12, 30, tzinfo=JST) + timedelta(minutes=i)
        for i in range(47)
    ]
    result = []
    for stamp in stamps:
        open_ = (
            10000
            + (gap if stamp.hour == 12 and stamp.minute >= 30 else 0)
            + (future if stamp.hour >= 14 else 0)
        )
        result.append(Bar(stamp, day, day, Session.DAY, "synthetic", open_, open_, open_, open_))
    return result


def calendar() -> TSECashMarketCalendar:
    return TSECashMarketCalendar(frozenset(), date(2020, 1, 1), date(2026, 1, 1))


def history(target: date) -> list[tuple[date, list[Bar], bool]]:
    return [
        (target - timedelta(days=i + 1), rows(target - timedelta(days=i + 1)), False)
        for i in range(120)
    ]


def test_causal_gap_ranks_equality_and_prefix() -> None:
    day = date(2024, 11, 5)
    event = r053_event(day, rows(day), history(day), calendar())
    assert event["status"] == "E"
    assert event["q90"] == 0.01 and event["x"] == 0.01
    assert event["planned_entry_jst"].endswith("12:31:00+09:00")
    assert event == r053_event(day, rows(day, future=999), history(day), calendar())


def test_lock_history_zero_and_holdout() -> None:
    day = date(2024, 11, 5)
    assert (
        r053_event(day, rows(day), history(day)[:-1], calendar())["reason"]
        == "HISTORY_NOT_EXACTLY_120_SCHEDULED_TSE_DAYS"
    )
    assert r053_event(day, rows(day, 0), history(day), calendar())["direction_eligible"] is False
    assert (
        r053_event(date(2025, 7, 1), rows(day), history(day), calendar())["reason"]
        == "OUTSIDE_DEVELOPMENT"
    )
    with pytest.raises(ValueError, match="Final Holdout is locked"):
        partition_paths(Path("unused"), "final_holdout")  # type: ignore[arg-type]


def test_fwl_matches_full_rank_ols_q_coefficient() -> None:
    nuisance = np.array([[1.0, -2.0], [1.0, -1.0], [1.0, 1.0], [1.0, 2.0]])
    q = np.array([0.0, 1.0, 0.0, 1.0])
    y = np.array([4.0, 10.0, 7.0, 13.0])
    expected = np.linalg.lstsq(np.column_stack((nuisance[:, 0], q, nuisance[:, 1])), y, rcond=None)[0][1]
    actual, residual_ss = fwl_delta(
        q, y, nuisance, pinv_rcond=1e-12, residual_ss_tolerance=1e-12
    )
    assert actual == pytest.approx(expected, abs=1e-12)
    assert residual_ss > 1e-12


def test_fwl_handles_missing_year_dummy_in_nuisance() -> None:
    nuisance_with_absent_year = np.array(
        [[1.0, -1.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [1.0, 3.0, 0.0]]
    )
    q = np.array([0.0, 1.0, 0.0, 1.0])
    y = np.array([2.0, 7.0, 3.0, 12.0])
    with_missing, _ = fwl_delta(
        q, y, nuisance_with_absent_year, pinv_rcond=1e-12, residual_ss_tolerance=1e-12
    )
    without_missing, _ = fwl_delta(
        q, y, nuisance_with_absent_year[:, :2], pinv_rcond=1e-12, residual_ss_tolerance=1e-12
    )
    assert with_missing == pytest.approx(without_missing, abs=1e-12)


def test_fwl_stops_when_q_is_in_nuisance_span() -> None:
    nuisance = np.array([[1.0, 0.0], [1.0, 1.0], [1.0, 0.0], [1.0, 1.0]])
    q = nuisance[:, 1].copy()
    with pytest.raises(R053QNotIdentifiableError, match="not identifiable"):
        fwl_delta(
            q,
            np.array([1.0, 2.0, 3.0, 4.0]),
            nuisance,
            pinv_rcond=1e-12,
            residual_ss_tolerance=1e-12,
        )
