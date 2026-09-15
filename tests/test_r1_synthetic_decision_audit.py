"""R1 synthetic runner boundary tests; these never open market-data artifacts."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from n225m_bt.cli import app
from n225m_bt.research.conditions import shared_path_gross_witness
from n225m_bt.research.decision_audit import (
    ADAPTER_BINDINGS,
    ProcessingProfile,
    SyntheticAccessDeniedError,
    UnknownProcessingProfileError,
    bind_adapter_event,
    bind_adapter_events,
    freeze_events,
    inject_execution,
    resolve_profile,
)
from n225m_bt.research.execution_ledger import ExecutionStatus
from n225m_bt.research.outcomes import OutcomeState, ScheduledOutcome, summarize_scheduled_axis


def _event(*, entry: str = "2025-06-30T09:31:00+09:00") -> dict[str, object]:
    return {
        "status": "E_EXEC",
        "trade_date": "2025-06-30",
        "selection_status": "A",
        "execution_status": "scheduled",
        "planned_entry_jst": entry,
    }


def test_ri10_and_ri11_frozen_selection_execution_and_null_outcome_are_separate() -> None:
    selections = freeze_events("R1-SYNTH", [_event()])
    executions = inject_execution(
        selections, [(0, ExecutionStatus.FILLED, "synthetic_next_open")]
    )
    assert executions[0].reason.startswith("INJECTED_TEST_EVENT:")
    assert selections[0].selection_status == executions[0].selection.selection_status == "A"
    axis = (date(2025, 6, 30),)
    open_position = summarize_scheduled_axis(
        "synthetic-axis", axis, (ScheduledOutcome(axis[0], OutcomeState.OPEN_POSITION, None),)
    )
    no_trade = summarize_scheduled_axis(
        "synthetic-axis", axis, (ScheduledOutcome(axis[0], OutcomeState.KNOWN_NO_TRADE, 0),)
    )
    assert open_position.complete_net_jpy is None and no_trade.complete_net_jpy == 0
    with pytest.raises(ValueError, match="duplicate"):
        freeze_events("R1-SYNTH", [_event(), _event()])


def test_ri02_shared_synthetic_price_path_preserves_opposite_side_identity() -> None:
    witness = shared_path_gross_witness((5, 10, -5), 100)
    assert witness["long_gross_pre_fee_mean_jpy"] == -witness["short_gross_pre_fee_mean_jpy"]
    assert witness["long_gross_pre_fee_mean_jpy"] > 0
    with pytest.raises(ValueError, match="positive multiplier"):
        shared_path_gross_witness((5,), 0)


def test_ri12_and_ri13_are_fail_closed_without_cross_profile_fallback() -> None:
    from n225m_bt.research.decision_audit import assert_synthetic_input

    with pytest.raises(SyntheticAccessDeniedError):
        assert_synthetic_input("MARKET", "gold/development.parquet")
    assert resolve_profile(ProcessingProfile.R1_SYNTHETIC_DECISION)["status"] == (
        "SUPPORTED_SYNTHETIC_DECISION_ONLY"
    )
    assert resolve_profile(ProcessingProfile.R065_SHARED_ENGINE)["status"] == (
        "BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED"
    )
    with pytest.raises(UnknownProcessingProfileError):
        resolve_profile("unregistered")


def test_adapter_binding_matrix_references_the_seven_additive_execution_adapters() -> None:
    assert set(ADAPTER_BINDINGS) == {"R046", "R049", "R060", "R061", "R062", "R063", "R064"}
    modules = {
        "R046": "n225m_bt.research.r046",
        "R049": "n225m_bt.research.r049",
        "R060": "n225m_bt.research.r060",
        "R061": "n225m_bt.research.r061",
        "R062": "n225m_bt.research.r062",
        "R063": "n225m_bt.research.r063",
        "R064": "n225m_bt.research.r064",
    }
    for study, module_name in modules.items():
        module = __import__(module_name, fromlist=[str(ADAPTER_BINDINGS[study]["callable"])])
        assert callable(getattr(module, str(ADAPTER_BINDINGS[study]["callable"])))


def test_r046_adapter_reaches_freeze_selection_on_synthetic_bars() -> None:
    """Exercise adapter -> signal/order intent -> immutable ledger, not a mock event."""
    from n225m_bt.calendar.classifier import CalendarClassifier
    from n225m_bt.calendar.model import ExchangeCalendar
    from n225m_bt.config import load_project_config
    from n225m_bt.domain import Bar, Session
    from n225m_bt.research.r046 import r046_exec_event

    _, sessions, _, _ = load_project_config(Path("config"))
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )
    target = date(2024, 11, 5)

    def bars(day: date) -> list[Bar]:
        start = classifier.session_open(day, Session.DAY)
        return [
            Bar(
                start + timedelta(minutes=index),
                day,
                day,
                Session.DAY,
                "AUDIT_SYNTHETIC",
                100,
                101,
                99,
                101 if index % 2 == 0 else 99,
            )
            for index in range(30)
        ]

    history = [
        (target - timedelta(days=index + 1), bars(target - timedelta(days=index + 1)), False)
        for index in range(60)
    ]
    event = r046_exec_event(classifier, target, bars(target), history)
    assert event["status"] == "E_EXEC"
    selection = bind_adapter_event("R046-SYNTH", "R046", event)
    assert selection.execution_status_at_decision is ExecutionStatus.SCHEDULED
    with pytest.raises(ValueError, match="unregistered"):
        bind_adapter_event("R046-SYNTH", "R065", event)


def test_all_registered_adapters_require_one_e_exec_record_at_the_ledger_boundary() -> None:
    events = {adapter_id: _event(entry=f"2025-06-30T09:{index + 10:02d}:00+09:00") for index, adapter_id in enumerate(ADAPTER_BINDINGS)}
    selections = bind_adapter_events("R1-BATCH-SYNTH", events)
    assert set(selections) == set(ADAPTER_BINDINGS)
    with pytest.raises(ValueError, match="exactly"):
        bind_adapter_events("R1-BATCH-SYNTH", {"R046": _event()})


def test_r062_real_night_observation_reaches_the_ledger_without_a_mock() -> None:
    """Build night U from synthetic bars; do not monkeypatch night_observation."""
    from n225m_bt.calendar.classifier import CalendarClassifier
    from n225m_bt.calendar.model import ExchangeCalendar
    from n225m_bt.config import load_project_config
    from n225m_bt.domain import Bar, Session
    from n225m_bt.research.r055 import normal_night_end
    from n225m_bt.research.r062 import r062_exec_event

    _, sessions, _, _ = load_project_config(Path("config"))
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )
    target = date(2024, 11, 5)

    def night_rows(day: date) -> list[Bar]:
        start = classifier.session_open(day, Session.NIGHT)
        final, _, _, _ = normal_night_end(classifier, day)
        count = int((final - start).total_seconds() // 60) + 1
        return [
            Bar(
                start + timedelta(minutes=index),
                day,
                (start + timedelta(minutes=index)).date(),
                Session.NIGHT,
                "AUDIT_SYNTHETIC",
                100,
                110 if index == count - 1 else 100,
                90,
                110 if index == count - 1 else 100,
            )
            for index in range(count)
        ]

    start = classifier.session_open(target, Session.DAY)

    def day_rows(future_close: int = 100) -> list[Bar]:
        return [
            Bar(
                start + timedelta(minutes=index),
                target,
                target,
                Session.DAY,
                "AUDIT_SYNTHETIC",
                100,
                future_close if index >= 15 else 100,
                90,
                future_close if index >= 15 else 90,
            )
            for index in range(66)
        ]
    history: list[tuple[date, list[Bar] | None, list[Bar] | None, bool]] = []
    candidate = target - timedelta(days=1)
    while len(history) < 120:
        try:
            rows = night_rows(candidate)
        except ValueError:
            candidate -= timedelta(days=1)
            continue
        history.append((candidate, rows, None, False))
        candidate -= timedelta(days=1)
    event = r062_exec_event(classifier, target, night_rows(target), day_rows(), history)
    assert event["status"] == "E_EXEC" and event["selection_status"] == "A"
    assert bind_adapter_event("R062-SYNTH", "R062", event).selection_status == "A"
    changed_future = r062_exec_event(
        classifier, target, night_rows(target), day_rows(999), history
    )
    assert changed_future == event


def test_r063_and_r064_build_rolling_u_from_synthetic_history_before_binding() -> None:
    """Use candidate_rows/rolling ledgers rather than a frozen-U test fixture."""
    from datetime import datetime

    from n225m_bt.domain import Bar, Session
    from n225m_bt.research import r063, r064

    class DayClassifier:
        def session_open(self, target: date, session: Session) -> datetime:
            assert session is Session.DAY
            return datetime.combine(target, datetime.min.time()).replace(hour=8, minute=45)

    classifier = DayClassifier()
    history_days = [date(2024, 1, 1) + timedelta(days=index) for index in range(101)]
    target = date(2024, 11, 5)

    def r063_rows(day: date, *, target_shape: bool) -> list[Bar]:
        start = classifier.session_open(day, Session.DAY)
        return [
            Bar(
                start + timedelta(minutes=index),
                day,
                day,
                Session.DAY,
                "AUDIT_SYNTHETIC",
                100,
                120 if target_shape and index == 34 else 110,
                90,
                120 if target_shape and index == 34 else 110 if target_shape and 35 <= index <= 39 else 100,
            )
            for index in range(150)
        ]

    r063_bars = {day: r063_rows(day, target_shape=False) for day in history_days}
    r063_bars[target] = r063_rows(target, target_shape=True)
    r063_u = r063.candidate_rows(classifier, [*history_days, target], r063_bars, set())[-1]
    r063_event = r063.r063_exec_event(classifier, target, r063_bars[target], set(), r063_u)
    assert r063_event["status"] == "E_EXEC" and r063_event["selection_status"] == "A"
    assert bind_adapter_event("R063-SYNTH", "R063", r063_event).selection_status == "A"

    def r064_rows(day: date, *, target_shape: bool) -> list[Bar]:
        start = classifier.session_open(day, Session.DAY)
        return [
            Bar(
                start + timedelta(minutes=index),
                day,
                day,
                Session.DAY,
                "AUDIT_SYNTHETIC",
                100,
                120 if target_shape and index == 59 else 116 if target_shape and index >= 60 else 100,
                90,
                120 if target_shape and index == 59 else 116 if target_shape and index >= 60 else 100,
            )
            for index in range(75)
        ]

    r064_bars = {day: r064_rows(day, target_shape=False) for day in history_days}
    r064_bars[target] = r064_rows(target, target_shape=True)
    r064_u = r064.candidate_rows(classifier, [*history_days, target], r064_bars, set())[-1]
    r064_event = r064.r064_exec_event(classifier, target, r064_bars[target], set(), r064_u)
    assert r064_event["status"] == "E_EXEC" and r064_event["selection_status"] == "A"
    assert bind_adapter_event("R064-SYNTH", "R064", r064_event).selection_status == "A"


def test_r060_adapter_exposes_the_common_ledger_planned_entry_field() -> None:
    from n225m_bt.calendar.classifier import CalendarClassifier
    from n225m_bt.calendar.model import ExchangeCalendar
    from n225m_bt.config import load_project_config
    from n225m_bt.domain import Bar, Session
    from n225m_bt.research.r020 import TSECashMarketCalendar
    from n225m_bt.research.r022 import normal_session_end
    from n225m_bt.research.r060 import r060_exec_event

    _, sessions, _, _ = load_project_config(Path("config"))
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )
    target, prior = date(2024, 11, 5), date(2024, 11, 1)
    cash = TSECashMarketCalendar(
        frozenset({date(2024, 11, 4)}), date(2020, 1, 1), date(2026, 12, 31)
    )
    prior_start = classifier.session_open(prior, Session.DAY)
    prior_end = normal_session_end(classifier, prior, Session.DAY)
    prior_rows = [
        Bar(
            prior_start + timedelta(minutes=index),
            prior,
            prior,
            Session.DAY,
            "AUDIT_SYNTHETIC",
            100,
            110,
            90,
            100,
        )
        for index in range(int((prior_end - prior_start).total_seconds() // 60))
    ]
    start = classifier.session_open(target, Session.DAY)
    target_rows = [
        Bar(
            start + timedelta(minutes=index),
            target,
            target,
            Session.DAY,
            "AUDIT_SYNTHETIC",
            100,
            115 if index == 0 else 110,
            90,
            105 if index == 29 else 100,
        )
        for index in range(30)
    ]
    event = r060_exec_event(classifier, cash, target, target_rows, prior_rows)
    assert event["status"] == "E_EXEC" and event["selection_status"] == "A"
    assert event["planned_entry_jst"] == event["E_planned_entry_jst"]
    assert bind_adapter_event("R060-SYNTH", "R060", event).selection_status == "A"


def test_r049_and_r061_real_adapter_outputs_reach_the_ledger() -> None:
    from n225m_bt.calendar.classifier import CalendarClassifier
    from n225m_bt.calendar.model import ExchangeCalendar
    from n225m_bt.config import load_project_config
    from n225m_bt.domain import Bar, Session
    from n225m_bt.research.r020 import TSECashMarketCalendar
    from n225m_bt.research.r022 import normal_session_end
    from n225m_bt.research.r049 import r049_exec_candidate
    from n225m_bt.research.r061 import r061_exec_event

    _, sessions, _, _ = load_project_config(Path("config"))
    classifier = CalendarClassifier(
        sessions, ExchangeCalendar.from_path(Path("config/local_calendar.yaml"))
    )
    target = date(2024, 11, 5)
    cash = TSECashMarketCalendar(frozenset(), date(2020, 1, 1), date(2026, 12, 31))

    def r049_rows(day: date) -> list[Bar]:
        day_start = classifier.session_open(day, Session.DAY)
        return [
            Bar(
                day_start + timedelta(minutes=index),
                day,
                day,
                Session.DAY,
                "AUDIT_SYNTHETIC",
                100,
                110 if index == 44 else 100,
                90,
                110 if index == 44 else 100,
            )
            for index in range(300)
        ]

    r049_history = [
        (target - timedelta(days=index + 1), r049_rows(target - timedelta(days=index + 1)), False)
        for index in range(120)
    ]
    r049_event = r049_exec_candidate(target, "mS+30", r049_rows(target), r049_history, cash)
    assert r049_event["status"] == "E_EXEC"
    assert bind_adapter_event("R049-SYNTH", "R049", r049_event).selection_status == "eligible"

    def r061_rows(day: date, *, breakout: bool) -> list[Bar]:
        day_start = classifier.session_open(day, Session.DAY)
        return [
            Bar(
                day_start + timedelta(minutes=index),
                day,
                day,
                Session.DAY,
                "AUDIT_SYNTHETIC",
                100,
                107 if breakout and index == 90 else 102 if index == 60 else 101,
                98 if index == 61 else 99,
                107 if breakout and index == 90 else 100,
            )
            for index in range(120)
        ]

    prior = date(2024, 11, 4)
    prior_start = classifier.session_open(prior, Session.DAY)
    prior_end = normal_session_end(classifier, prior, Session.DAY)
    prior_rows = [
        Bar(
            prior_start + timedelta(minutes=index),
            prior,
            prior,
            Session.DAY,
            "AUDIT_SYNTHETIC",
            100,
            101,
            99,
            100,
        )
        for index in range(int((prior_end - prior_start).total_seconds() // 60))
    ]
    r061_history = [
        (target - timedelta(days=index + 1), r061_rows(target - timedelta(days=index + 1), breakout=False), False)
        for index in range(120)
    ]
    r061_event = r061_exec_event(
        classifier, cash, target, r061_rows(target, breakout=True), r061_history, prior_rows
    )
    assert r061_event["status"] == "E_EXEC" and r061_event["execution_status"] == "scheduled"
    assert bind_adapter_event("R061-SYNTH", "R061", r061_event).selection_status == "eligible"


def test_ri14_cli_creates_exclusive_not_run_audit_shell(workspace_tmp: Path) -> None:
    output = workspace_tmp / f"r1-audit-{uuid4().hex}"
    fixture = Path(__file__).resolve()
    result = CliRunner().invoke(
        app,
        [
            "research",
            "audit-synthetic-decision",
            "--audit-id",
            "AUDIT-SYNTH-TEST",
            "--output",
            str(output),
            "--fixture",
            str(fixture),
        ],
    )
    assert result.exit_code == 0, result.stdout
    manifest = json.loads((output / "audit_manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_type"] == "AUDIT_SYNTHETIC"
    assert manifest["market_data_access"] is False
    assert len(manifest["source_sha256"]) >= 10
    assert json.loads((output / "test_results.json").read_text())["status"] == "NOT_RUN"
    duplicate = CliRunner().invoke(
        app,
        [
            "research",
            "audit-synthetic-decision",
            "--audit-id",
            "AUDIT-SYNTH-TEST",
            "--output",
            str(output),
            "--fixture",
            str(fixture),
        ],
    )
    assert duplicate.exit_code != 0


def test_cli_never_reaches_market_loader_or_engine(workspace_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read-spy proves the audit shell has no hidden production data route."""
    import n225m_bt.backtest.engine as engine
    import n225m_bt.research.data as data

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("market route was called")

    monkeypatch.setattr(data, "load_split", forbidden)
    monkeypatch.setattr(engine.BacktestEngine, "run", forbidden)
    output = workspace_tmp / f"r1-no-market-{uuid4().hex}"
    result = CliRunner().invoke(
        app,
        [
            "research",
            "audit-synthetic-decision",
            "--audit-id",
            "AUDIT-SYNTH-NO-MARKET",
            "--output",
            str(output),
            "--fixture",
            str(Path(__file__).resolve()),
        ],
    )
    assert result.exit_code == 0, result.stdout
