from datetime import date, timedelta

from n225m_bt.research.r075_q002_s2 import MINIMUM_MIDDLE_OBSERVABLE_TRADES, feasibility_q002


def _event(*, high: bool, executable: bool, direction: str = "long") -> dict[str, object]:
    return {
        "status": "EXECUTABLE" if executable else "SKIPPED",
        "reason": "SELECTED_STATE_FIRST_STRICT_CLOSE_BREAKOUT" if executable else "OUTSIDE_SELECTED_NIGHT_RANGE_STATE",
        "reference_ranges_points_p1_to_p20": list(range(20)),
        "current_night_range_points": 90 if high else 50,
        "q25_points": 20,
        "q75_points": 80,
        "breakout_direction": direction,
    }


def test_q002_only_replaces_the_middle_minimum_with_250() -> None:
    high: dict[date, dict[str, object]] = {}
    middle: dict[date, dict[str, object]] = {}
    for year in range(2021, 2025):
        start = date(year, 1, 1)
        for offset in range(180):
            high[start + timedelta(days=offset)] = _event(
                high=offset < 60, executable=offset < 60, direction="long" if offset % 2 else "short"
            )
    for offset in range(MINIMUM_MIDDLE_OBSERVABLE_TRADES):
        middle[date(2025, 1, 1) + timedelta(days=offset)] = _event(high=False, executable=True)

    result = feasibility_q002(high, middle)

    assert result["gate"]["minimum_middle_observable_trades"] == 250
    assert result["gate"]["middle_at_least_250"] is True
    assert "middle_at_least_300" not in result["gate"]
    assert result["gate"]["passed"] is True
