from pathlib import Path

import pytest

import orchestrator


@pytest.mark.parametrize("config", [{}, {"research_execution_paused": True}])
def test_pause_precedes_reset_and_state_load(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, object]
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("A paused invocation must not load/reset state or dispatch a task")

    monkeypatch.setattr(orchestrator, "load_state", unexpected)
    monkeypatch.setattr(orchestrator, "run_executor", unexpected)
    with pytest.raises(orchestrator.OrchestratorError, match="paused"):
        orchestrator.orchestrate(config, Path.cwd(), reset=True, once=False)


def test_direct_executor_dispatch_is_also_paused() -> None:
    with pytest.raises(orchestrator.OrchestratorError, match="paused"):
        orchestrator.run_executor({}, Path.cwd(), "Old pending research task", 1)


def test_flipping_pause_flag_cannot_bypass_registered_dispatch() -> None:
    with pytest.raises(orchestrator.OrchestratorError, match="retired"):
        orchestrator.run_executor({"research_execution_paused": False}, Path.cwd(), "Unregistered", 1)


def test_minimum_iteration_cannot_force_a_new_experiment(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("A finite stopping decision must not cause another planner invocation")

    monkeypatch.setattr(orchestrator, "run_planner", unexpected)
    stop = {"status": "done", "analysis": "Registered budget exhausted", "next_task": ""}
    result = orchestrator.CommandResult([], 0, "", "", 0)
    decision, attempts = orchestrator.apply_done_policy(
        {"min_iterations_before_done": 20, "done_confirmation_required": False},
        Path.cwd(), 1, "codex", "bounded task", result, "completed", stop,
        orchestrator.APP_DIR / "synthetic_planner.json",
    )
    assert decision == stop
    assert len(attempts) == 1
