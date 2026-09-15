from datetime import date, timedelta

from test_r096_q001 import _bars, _classifier

from n225m_bt.research.r096_q002_complement_control import (
    build_q002_events,
    q002_causality_audit,
    q002_state_ledger,
)


def test_kstar_is_exact_non_e_complement_at_or_above_x_cutoff() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=index) for index in range(121)]
    bars = {(day, "DAY"): _bars(day, [1 + index % 3] * 60) for index, day in enumerate(axis)}
    # Use the real Session key through the Q001 fixture's constructed Bar values.
    from n225m_bt.domain import Session

    actual = {(day, Session.DAY): value for (day, _), value in bars.items()}
    actual[(axis[-1], Session.DAY)] = _bars(axis[-1], [5] * 60)
    row = q002_state_ledger(_classifier(axis), axis, actual, set())[-1]
    assert bool(row["accepted_condition"]) != bool(row["kstar_condition"])
    assert bool(row["accepted_condition"])
    assert row["q002_control_definition"] == "x>=qx and z<qz_upper; equality belongs to E"


def test_equality_at_qz_upper_is_checked_only_inside_high_x_partition() -> None:
    axis = [date(2023, 1, 2) + timedelta(days=index) for index in range(121)]
    from n225m_bt.domain import Session

    actual = {(day, Session.DAY): _bars(day, [1 + index % 3] * 60) for index, day in enumerate(axis)}
    events = build_q002_events(_classifier(axis), axis, actual, set())
    assert q002_causality_audit(events, axis)["checks"]["z_qz_upper_equality_belongs_to_e"]
