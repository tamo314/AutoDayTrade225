from __future__ import annotations

from datetime import date

from n225m_bt.research.r090_lunch_rejection import feasibility
from n225m_bt.research.r090_q002 import feasibility_q002


def _event(day: date, *, lr: bool, la: bool, band: str, up: bool = True) -> dict[str, object]:
    return {
        "trade_date": day.isoformat(),
        "status": "EXECUTABLE",
        "reason": "LUNCH_DISPLACEMENT_REJECTION",
        "lr_condition": lr,
        "la_condition": la,
        "m_band": band,
        "displacement_points": 1.0 if up else -1.0,
    }


def test_q002_changes_only_the_2025_h1_lr_minimum() -> None:
    lr_years = [2021] * 7 + [2022] * 20 + [2023] * 20 + [2024] * 36 + [2025] * 7
    lr = [
        _event(date(year, 1, 2), lr=True, la=False, band="B1" if i < 45 else "B2", up=i % 2 == 0)
        for i, year in enumerate(lr_years)
    ]
    la = [
        _event(date(2023, 2, 1), lr=False, la=True, band="B1" if i < 45 else "B2")
        for i in range(90)
    ]
    placebo = [
        _event(date(2023, 3, 1), lr=True, la=False, band="B1" if i < 45 else "B2")
        for i in range(90)
    ]

    q001 = feasibility(lr + la, placebo)
    q002 = feasibility_q002(lr + la, placebo)

    assert q001["gate"]["lr_2025_h1_at_least_8"] is False
    assert q001["gate"]["passed"] is False
    assert "lr_2025_h1_at_least_8" not in q002["gate"]
    assert q002["gate"]["lr_2025_h1_at_least_6"] is True
    assert q002["gate"]["passed"] is True
