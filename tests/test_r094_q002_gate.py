from n225m_bt.research.r094_q002_gate import k_order_audit, revised_gate


def _k(status: str, reason: str, day: str, sign: int, percentile: float) -> dict[str, object]:
    return {
        "state": "K",
        "status": status,
        "reason": reason,
        "entry_order_submitted": True,
        "trade_date": day,
        "night_schedule_version": "ose_n225m_from_20241105" if day >= "2024-11-05" else "old",
        "current_x_rolling_percentile": percentile,
        "observation": {"r_sign": sign},
    }


def test_r094_q002_gate_replaces_only_k_minimum() -> None:
    q001 = {
        "K_completed": 243,
        "gate": {
            "unexplained_exclusions_equal_zero": True,
            "K_completed_at_least_250": False,
            "each_x_band_H_and_M_at_least_20": True,
            "passed": False,
        },
    }
    revised = revised_gate(q001)
    assert revised["gate"] == {
        "unexplained_exclusions_equal_zero": True,
        "each_x_band_H_and_M_at_least_20": True,
        "K_completed_at_least_240": True,
        "passed": True,
    }


def test_r094_q002_k_audit_rejects_an_unexplained_cancellation() -> None:
    events = [
        _k("EXECUTABLE", "SCHEDULED_CASH_TO_NIGHT_PATH_COMPLETE", "2024-11-06", 1, 0.8),
        _k("ENTRY_CANCELLED", "UNEXPLAINED", "2024-11-07", -1, 0.6),
    ]
    audit = k_order_audit(events)
    assert audit["K_completed_by_cash_direction"] == {"positive": 1}
    assert audit["K_completed_by_rolling_x_band"] == {"0.50_to_0.75": 0, "0.75_to_1.00": 1}
    assert audit["checks"]["K_cancellation_reasons_exactly_explainable"] is False
    assert audit["passed"] is False
