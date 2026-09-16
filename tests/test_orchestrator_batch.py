from __future__ import annotations

import io
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import orchestrator as app
from orchestrator_batch import (
    PROTECTED,
    Batch,
    BatchError,
    TaskSpec,
    read_json,
    save_json,
    task_lock,
)


@pytest.fixture()
def task_workspace(workspace_tmp: Path) -> tuple[Path, Path]:
    root = (workspace_tmp / uuid.uuid4().hex).resolve()
    root.mkdir(parents=True)
    for name in PROTECTED:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("protected\n", encoding="utf-8")
    (root / "task.md").write_text("Create evidence for one hypothesis", encoding="utf-8")
    (root / "scope.md").write_text("Design only, no market access", encoding="utf-8")
    path = root / "batch.json"
    save_json(
        path,
        {
            "schema_version": 1,
            "task_id": "design-01",
            "mode": "design",
            "task_file": "task.md",
            "scope_file": "scope.md",
            "max_executor_calls": 2,
            "max_planner_calls": 3,
            "required_artifacts": ["docs/strategy/plans/design-01/review.md"],
        },
    )
    return root, path


class FakeTransport:
    def __init__(self, batch: Batch, decisions: list[dict[str, Any]]) -> None:
        self.batch = batch
        self.decisions = decisions
        self.executor_calls = 0
        self.planner_calls = 0
        self.make_artifacts = True
        self.fail_executor = False
        self.fail_planner = False

    def execute(self, task: str, attempt: int, check: Callable[[], None]) -> str:
        check()
        assert self.batch.status()["phase"] == "EXECUTOR_RUNNING"
        assert self.batch.status()["executor_calls"] == attempt
        assert "FINITE TASK" in task
        self.executor_calls += 1
        if self.fail_executor:
            raise RuntimeError("simulated child failure")
        if self.make_artifacts:
            for name in self.batch.spec.required_artifacts:
                output = self.batch.root / name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("HOLD: evidence not sufficient\n", encoding="utf-8")
        return "Design completed; no market data accessed"

    def plan(self, task: str, report: str, attempt: int) -> dict[str, Any]:
        assert self.batch.status()["phase"] == "PLANNER_RUNNING"
        assert self.batch.status()["planner_calls"] == attempt
        self.planner_calls += 1
        if self.fail_planner:
            raise RuntimeError("simulated planner outage")
        return self.decisions.pop(0)


DONE = {"status": "done", "analysis": "HOLD recorded; task complete", "next_task": ""}
MORE = {
    "status": "continue",
    "analysis": "Missing citation",
    "next_task": "Complete citation in the same review",
}


def test_design_review_resume_and_done_are_finite(task_workspace: tuple[Path, Path]) -> None:
    batch = Batch(*task_workspace, {})
    transport = FakeTransport(batch, [MORE, DONE])
    state = batch.run(transport, once=True)
    assert state["phase"] == "EXECUTOR_PENDING"
    assert state["executor_calls"] == 1
    resumed = Batch(*task_workspace, {})
    state = resumed.run(transport)
    assert state["phase"] == "DONE"
    assert state["executor_calls"] == state["planner_calls"] == 2
    assert resumed.run(transport)["phase"] == "DONE"
    assert transport.executor_calls == transport.planner_calls == 2


def test_continuation_cannot_refill_budget(task_workspace: tuple[Path, Path]) -> None:
    batch = Batch(*task_workspace, {})
    transport = FakeTransport(batch, [MORE, MORE])
    state = batch.run(transport)
    assert state["phase"] == "EXHAUSTED"
    assert batch.run(transport)["executor_calls"] == 2
    assert transport.executor_calls == 2


def test_planner_retry_does_not_repeat_executor(task_workspace: tuple[Path, Path]) -> None:
    batch = Batch(*task_workspace, {})
    transport = FakeTransport(batch, [DONE])
    transport.fail_planner = True
    with pytest.raises(BatchError, match="Planner failed"):
        batch.run(transport)
    transport.fail_planner = False
    state = batch.run(transport)
    assert state["phase"] == "DONE"
    assert state["executor_calls"] == 1
    assert state["planner_calls"] == 2


def test_planner_failures_have_a_budget(task_workspace: tuple[Path, Path]) -> None:
    batch = Batch(*task_workspace, {})
    transport = FakeTransport(batch, [])
    transport.fail_planner = True
    for _ in range(3):
        with pytest.raises(BatchError, match="Planner failed"):
            batch.run(transport)
    assert batch.run(transport)["phase"] == "EXHAUSTED"
    assert transport.executor_calls == 1


def test_executor_failure_is_not_replayed(task_workspace: tuple[Path, Path]) -> None:
    batch = Batch(*task_workspace, {})
    transport = FakeTransport(batch, [])
    transport.fail_executor = True
    with pytest.raises(BatchError, match="Executor failed"):
        batch.run(transport)
    assert batch.run(transport)["phase"] == "FAILED"
    assert transport.executor_calls == 1
    assert transport.planner_calls == 0


def test_missing_artifacts_cannot_be_done(task_workspace: tuple[Path, Path]) -> None:
    batch = Batch(*task_workspace, {})
    transport = FakeTransport(batch, [DONE])
    transport.make_artifacts = False
    assert batch.run(transport)["phase"] == "INCOMPLETE"


def test_task_or_policy_change_blocks_next_dispatch(task_workspace: tuple[Path, Path]) -> None:
    root, path = task_workspace
    batch = Batch(root, path, {})
    transport = FakeTransport(batch, [MORE])
    batch.run(transport, once=True)
    (root / "config/research_execution.json").write_text("changed", encoding="utf-8")
    with pytest.raises(BatchError, match="Protected input changed"):
        batch.run(transport)
    with pytest.raises(BatchError, match="Frozen contract"):
        Batch(root, path, {}).run(transport)
    assert transport.executor_calls == 1


def test_new_id_and_deleted_state_do_not_refill(task_workspace: tuple[Path, Path]) -> None:
    root, path = task_workspace
    batch = Batch(root, path, {})
    transport = FakeTransport(batch, [DONE])
    batch.run(transport)
    spec = read_json(path)
    spec["task_id"] = "another-id"
    spec["required_artifacts"] = ["docs/strategy/plans/another-id/review.md"]
    save_json(root / "another.json", spec)
    with pytest.raises(BatchError, match="another task_id"):
        Batch(root, root / "another.json", {}).run(transport)
    batch.state_path.unlink()
    with pytest.raises(BatchError, match="without state"):
        batch.run(transport)


def test_orphan_and_os_lock(task_workspace: tuple[Path, Path]) -> None:
    batch = Batch(*task_workspace, {})
    transport = FakeTransport(batch, [])
    with (
        task_lock(batch.runtime / "active.lock"),
        pytest.raises(BatchError, match="Another bounded task"),
    ):
        batch.run(transport)
    state = batch._initialize()
    state.update(phase="EXECUTOR_RUNNING", executor_calls=1)
    save_json(batch.state_path, state)
    with pytest.raises(BatchError, match="Interrupted dispatch"):
        batch.run(transport)
    state = batch.run(transport, close_reason="Original process stopped; logs inspected")
    assert state["phase"] == "INTERRUPTED"
    assert state["executor_calls"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_file", "../outside.md"),
        ("max_executor_calls", True),
        ("max_executor_calls", 0),
        ("mode", "unbounded"),
        ("required_artifacts", ["data/raw/output.md"]),
        ("manifest_file", "market.json"),
    ],
    ids=["outside", "boolean", "zero", "mode", "artifact", "manifest"],
)
def test_invalid_contracts_fail_before_dispatch(
    task_workspace: tuple[Path, Path], field: str, value: object
) -> None:
    root, path = task_workspace
    spec = read_json(path)
    spec[field] = value
    save_json(path, spec)
    with pytest.raises(BatchError):
        TaskSpec.load(root, path)


def test_completed_artifact_mutation_is_reported(task_workspace: tuple[Path, Path]) -> None:
    batch = Batch(*task_workspace, {})
    batch.run(FakeTransport(batch, [DONE]))
    (batch.root / batch.spec.required_artifacts[0]).write_text("rewritten", encoding="utf-8")
    with pytest.raises(BatchError, match="Completed artifact changed"):
        batch.status()


def test_registered_adapter_uses_only_frozen_cli_commands(
    task_workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path = task_workspace
    spec = read_json(path)
    spec.update(
        mode="registered",
        manifest_file="manifest.json",
        max_executor_calls=1,
        required_artifacts=[],
    )
    save_json(path, spec)
    save_json(root / "manifest.json", {"run_id": "fixed-run"})
    config = {"executor": "codex", "executor_timeout_seconds": 10}
    batch = Batch(root, path, config)
    commands = []

    def command(args: list[str], cwd: Path, timeout: int) -> app.CommandResult:
        assert batch.status()["phase"] == "EXECUTOR_RUNNING"
        commands.append(args)
        return app.CommandResult(args, 0, "verified", "", 0)

    monkeypatch.setattr(app, "run_command", command)
    monkeypatch.setattr(app, "run_planner", lambda *a, **k: (DONE, root / "planner.json"))
    monkeypatch.setattr(
        app, "run_executor", lambda *a, **k: pytest.fail("Market run must not use agent executor")
    )
    assert app.run_bounded_task(config, root, batch)["phase"] == "DONE"
    assert [c[-2] for c in commands] == ["check-execution", "execute", "verify-execution"]
    assert commands[-1][-1] == "fixed-run"


def test_registered_failed_preflight_never_executes(
    task_workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path = task_workspace
    spec = read_json(path)
    spec.update(
        mode="registered",
        manifest_file="manifest.json",
        max_executor_calls=1,
        required_artifacts=[],
    )
    save_json(path, spec)
    save_json(root / "manifest.json", {"run_id": "fixed-run"})
    config = {"executor": "codex", "executor_timeout_seconds": 10}
    batch = Batch(root, path, config)
    commands = []

    def command(args: list[str], cwd: Path, timeout: int) -> app.CommandResult:
        commands.append(args)
        return app.CommandResult(args, 1, "", "no grant", 0)

    monkeypatch.setattr(app, "run_command", command)
    with pytest.raises(BatchError, match="check-execution failed"):
        app.run_bounded_task(config, root, batch)
    assert len(commands) == 1
    assert batch.status()["phase"] == "FAILED"


def test_design_transport_dispatches_executor_and_readonly_planner(
    task_workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path = task_workspace
    config = app.normalize_config(
        {
            "executor": "codex",
            "max_iterations": 20,
            "codex": {"command": "codex"},
            "claude": {"command": "claude"},
            "antigravity": {"command": "agy"},
            "research_execution_paused": True,
        }
    )
    batch = Batch(root, path, config)
    commands = []
    monkeypatch.setattr(app, "APP_DIR", root)
    monkeypatch.setattr(app, "CONTEXT_FILE", root / "AGENTS.md")
    monkeypatch.setattr(app, "PLANNER_PROMPT_FILE", root / "prompts/planner.md")
    monkeypatch.setattr(app, "EXECUTOR_PROMPT_FILE", root / "prompts/executor.md")
    monkeypatch.setattr(app, "PLANNER_SCHEMA", root / "planner_schema.json")

    def command(
        args: list[str], cwd: Path, timeout: int, stdin_text: str | None = None, env: object = None
    ) -> app.CommandResult:
        commands.append(args)
        assert stdin_text and "FINITE TASK" in stdin_text
        final = Path(args[args.index("--output-last-message") + 1])
        if "--output-schema" in args:
            assert args[args.index("--sandbox") + 1] == "read-only"
            final.write_text(json.dumps(DONE), encoding="utf-8")
        else:
            artifact = root / batch.spec.required_artifacts[0]
            artifact.parent.mkdir(parents=True)
            artifact.write_text("HOLD: no evidence", encoding="utf-8")
            final.write_text("Completed document", encoding="utf-8")
        return app.CommandResult(args, 0, "", "", 0)

    monkeypatch.setattr(app, "run_command", command)
    state = app.run_bounded_task(config, root, batch)
    assert state["phase"] == "DONE"
    assert len(commands) == 2
    assert (batch.directory / "runs/0001_executor_codex.json").is_file()
    assert (batch.directory / "history.jsonl").is_file()


def test_main_reset_rejected_before_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    monkeypatch.setattr(
        app,
        "parse_args",
        lambda: argparse.Namespace(
            config=Path("config.json"),
            batch=None,
            reset=True,
            check=False,
            status=False,
            close_interrupted=None,
            once=False,
        ),
    )
    monkeypatch.setattr(
        app, "run_checks", lambda *a: pytest.fail("reset must fail before CLI checks")
    )
    assert app.main() == 1


def test_main_check_handles_windows_console_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp932")
    monkeypatch.setattr(app.sys, "stdout", stream)
    monkeypatch.setattr(
        app,
        "parse_args",
        lambda: argparse.Namespace(
            config=Path("config.json"),
            batch=None,
            reset=False,
            check=True,
            status=False,
            close_interrupted=None,
            once=False,
        ),
    )

    def checks(*args: object) -> bool:
        print("CLI response: \u2014 \U0001f50d")
        return True

    monkeypatch.setattr(app, "run_checks", checks)
    assert app.main() == 0
    stream.flush()
    assert b"\\u2014" in buffer.getvalue()
