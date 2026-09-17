"""Finite Planner/Executor tasks; market work delegates to the registered runner.

This is a workflow guard, not an OS sandbox around agent tools.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


class BatchError(RuntimeError):
    """A bounded task cannot proceed without changing its recorded contract."""


class DispatchUncertainError(BatchError):
    """Child process termination needs inspection before another dispatch."""


class ReviewProtocolError(BatchError):
    """The Planner response needs a review-only correction, not new execution."""


PROTECTED = (
    "AGENTS.md",
    "config.json",
    "config/research_execution.json",
    "docs/strategy/registry/20260916_review.json",
    "docs/strategy/00_current_research_policy.md",
    "docs/strategy/13_research_governance.md",
    "docs/strategy/123_registered_execution_control.md",
    "orchestrator.py",
    "orchestrator_batch.py",
    "planner_schema.json",
    "prompts/planner.md",
    "prompts/executor.md",
)
TERMINAL = {"DONE", "FAILED", "INCOMPLETE", "EXHAUSTED", "INTERRUPTED", "BLOCKED"}

# A task's lane is part of its frozen contract.  It prevents an audit for data
# that is outside the current 225Labo OHLC scope from silently becoming the
# project's default autonomous task.
INPUT_PROFILES = frozenset(
    {
        "DOCUMENTATION_ONLY",
        "CURRENT_225LABO_OHLC",
        "REQUIRES_VOLUME_SEMANTICS",
        "REQUIRES_EXTERNAL_SERIES",
        "REQUIRES_EVENT_TIMESTAMP_LEDGER",
    }
)
QUEUE_LANES = frozenset({"GOVERNANCE", "CURRENT_DATA_RESEARCH", "INPUT_EXPANSION"})
INPUT_EXPANSION_PROFILES = frozenset(
    {
        "REQUIRES_VOLUME_SEMANTICS",
        "REQUIRES_EXTERNAL_SERIES",
        "REQUIRES_EVENT_TIMESTAMP_LEDGER",
    }
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_digest(value: object) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def within(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root) or path == root:
        raise BatchError(f"Path must remain inside project: {name}")
    return path


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise BatchError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BatchError(f"Expected JSON object: {path}")
    return value


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def task_lock(path: Path) -> Iterator[None]:
    """OS-owned lock: a crash releases the lock, never the persisted attempt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BatchError("Another bounded task is active; do not reset its state.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class TaskSpec:
    schema_version: int
    task_id: str
    mode: str
    task_file: str
    scope_file: str
    max_executor_calls: int | None
    max_planner_calls: int | None
    required_artifacts: tuple[str, ...]
    manifest_file: str | None = None
    completion_criteria: dict[str, str] | None = None
    read_only_inputs: tuple[str, ...] = ()
    validation_scripts: tuple[str, ...] = ()
    max_stalled_cycles: int = 3
    max_consecutive_errors: int = 3
    retry_backoff_seconds: int = 2
    input_profile: str = "DOCUMENTATION_ONLY"
    queue_lane: str = "GOVERNANCE"

    @classmethod
    def load(cls, root: Path, path: Path) -> TaskSpec:
        value = read_json(path)
        if set(value) - set(cls.__dataclass_fields__):
            raise BatchError("Unknown bounded task fields")
        try:
            artifacts = value["required_artifacts"]
            if not isinstance(artifacts, list) or not all(isinstance(x, str) for x in artifacts):
                raise ValueError("required_artifacts must be an array of paths")
            value["required_artifacts"] = tuple(artifacts)
            for field in ("read_only_inputs", "validation_scripts"):
                items = value.get(field, [])
                if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
                    raise ValueError(f"{field} must be an array of paths")
                value[field] = tuple(items)
            spec = cls(**value)
        except (KeyError, TypeError, ValueError) as exc:
            raise BatchError(f"Invalid bounded task: {exc}") from exc
        if type(spec.schema_version) is not int or spec.schema_version not in {1, 2}:
            raise BatchError("Unsupported bounded task schema")
        if not isinstance(spec.task_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", spec.task_id
        ):
            raise BatchError("Invalid task_id")
        if not isinstance(spec.mode, str) or spec.mode not in {"design", "registered"}:
            raise BatchError("mode must be design or registered")
        for limit in (spec.max_executor_calls, spec.max_planner_calls):
            if limit is None and spec.schema_version == 2:
                continue
            if type(limit) is not int or not 1 <= limit <= 10:
                raise BatchError("Dispatch limits must be integers in 1..10")
        if spec.schema_version == 2:
            if spec.mode != "design":
                raise BatchError(
                    "Autonomous redesign is design-only; market manifests remain single-dispatch"
                )
            if (
                not isinstance(spec.completion_criteria, dict)
                or not spec.completion_criteria
                or not all(
                    isinstance(k, str) and k and isinstance(v, str) and v.strip()
                    for k, v in spec.completion_criteria.items()
                )
            ):
                raise BatchError("Autonomous tasks require explicit completion criteria")
            for limit in (spec.max_stalled_cycles, spec.max_consecutive_errors):
                if type(limit) is not int or not 2 <= limit <= 10:
                    raise BatchError("Failure/stall limits must be integers in 2..10")
            if (
                type(spec.retry_backoff_seconds) is not int
                or not 0 <= spec.retry_backoff_seconds <= 10
            ):
                raise BatchError("Retry backoff must be in 0..10 seconds")
            for name in (*spec.read_only_inputs, *spec.validation_scripts):
                if not within(root, name).is_file():
                    raise BatchError(f"Missing sealed input: {name}")
            for name in spec.validation_scripts:
                if not name.startswith("scripts/") or Path(name).suffix != ".py":
                    raise BatchError("Validators must be reviewed Python scripts in scripts/")
            if spec.input_profile not in INPUT_PROFILES:
                raise BatchError("Unknown input_profile")
            if spec.queue_lane not in QUEUE_LANES:
                raise BatchError("Unknown queue_lane")
            if (
                spec.input_profile == "CURRENT_225LABO_OHLC"
                and spec.queue_lane != "CURRENT_DATA_RESEARCH"
            ):
                raise BatchError("CURRENT_225LABO_OHLC requires CURRENT_DATA_RESEARCH")
            if (
                spec.input_profile in INPUT_EXPANSION_PROFILES
                and spec.queue_lane != "INPUT_EXPANSION"
            ):
                raise BatchError("External or semantic inputs require INPUT_EXPANSION")
            if (
                spec.queue_lane == "INPUT_EXPANSION"
                and spec.input_profile not in INPUT_EXPANSION_PROFILES
            ):
                raise BatchError("INPUT_EXPANSION requires an out-of-scope input profile")
            if (
                spec.queue_lane == "GOVERNANCE"
                and spec.input_profile != "DOCUMENTATION_ONLY"
            ):
                raise BatchError("GOVERNANCE requires DOCUMENTATION_ONLY")
        elif (
            spec.completion_criteria
            or spec.read_only_inputs
            or spec.validation_scripts
            or spec.input_profile != "DOCUMENTATION_ONLY"
            or spec.queue_lane != "GOVERNANCE"
        ):
            raise BatchError("Autonomous fields require schema_version=2")
        for name in (spec.task_file, spec.scope_file):
            if not isinstance(name, str) or not within(root, name).is_file():
                raise BatchError("Task and scope files must exist")
        if len(set(spec.required_artifacts)) != len(spec.required_artifacts):
            raise BatchError("Duplicate required artifact")
        output_root = root / "docs/strategy/plans" / spec.task_id
        for name in spec.required_artifacts:
            path = within(root, name)
            suffixes = {".md"} if spec.schema_version == 1 else {".md", ".json", ".py"}
            if not path.is_relative_to(output_root) or path.suffix not in suffixes:
                raise BatchError("Artifacts must be documents/code in this task's plans directory")
            if name in spec.read_only_inputs or name in spec.validation_scripts:
                raise BatchError("An artifact cannot also be a sealed input")
        if spec.mode == "design":
            if spec.manifest_file is not None or not spec.required_artifacts:
                raise BatchError("Design requires artifacts and cannot carry a market manifest")
        elif spec.max_executor_calls != 1 or not isinstance(spec.manifest_file, str):
            raise BatchError("Registered tasks dispatch one fixed manifest, once")
        elif not within(root, spec.manifest_file).is_file():
            raise BatchError("Registered manifest is missing")
        return spec


def require_explicit_input_expansion(spec: TaskSpec, *, selected_explicitly: bool) -> None:
    """Keep input-expansion audits out of the default autonomous queue.

    The caller may still inspect such a task with --check/--status, and may
    dispatch it with an explicit --batch after its scope extension is chosen.
    """

    if spec.queue_lane == "INPUT_EXPANSION" and not selected_explicitly:
        raise BatchError(
            "Input-expansion tasks cannot be the default bounded task. "
            "Set a CURRENT_225LABO_OHLC task in config.json, or select this "
            "audit explicitly with --batch after approving its input scope."
        )


class Transport(Protocol):
    def execute(self, task: str, attempt: int, check: Callable[[], None]) -> str: ...

    def plan(self, task: str, report: str, attempt: int) -> dict[str, Any]: ...

    def validate(self, attempt: int) -> dict[str, Any]: ...


class Batch:
    def __init__(self, root: Path, path: Path, config: dict[str, Any]) -> None:
        self.root = root.resolve()
        self.path = path.resolve()
        self.spec = TaskSpec.load(self.root, self.path)
        self.runtime = self.root / ".orchestrator/bounded"
        self.directory = self.runtime / self.spec.task_id
        self.state_path = self.directory / "state.json"
        names: tuple[str, ...] = (*PROTECTED, self.spec.task_file, self.spec.scope_file)
        if self.spec.schema_version == 2:
            names = (
                *names,
                "planner_auto_schema.json",
                *self.spec.read_only_inputs,
                *self.spec.validation_scripts,
            )
        if self.spec.manifest_file:
            names = (*names, self.spec.manifest_file)
        try:
            self.seals = {name: digest(within(self.root, name).read_bytes()) for name in names}
        except OSError as exc:
            raise BatchError(f"Protected input missing: {exc}") from exc
        spec_record = asdict(self.spec)
        if self.spec.schema_version == 1:
            # Preserve the original v1 digest layout when running unchanged v1 contracts.
            for name in (
                "completion_criteria",
                "read_only_inputs",
                "validation_scripts",
                "max_stalled_cycles",
                "max_consecutive_errors",
                "retry_backoff_seconds",
            ):
                spec_record.pop(name)
        self.contract = json_digest({"spec": spec_record, "seals": self.seals, "config": config})
        self.identity = json_digest(
            [
                self.spec.mode,
                self.seals[self.spec.task_file],
                self.seals[self.spec.scope_file],
                self.seals.get(self.spec.manifest_file or ""),
            ]
        )

    def verify(self) -> None:
        if TaskSpec.load(self.root, self.path) != self.spec:
            raise BatchError("Task contract changed during execution")
        for name, expected in self.seals.items():
            path = within(self.root, name)
            if not path.is_file() or digest(path.read_bytes()) != expected:
                raise BatchError(f"Protected input changed: {name}")

    def status(self, *, historical: bool = False) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"task_id": self.spec.task_id, "phase": "NOT_STARTED"}
        state = read_json(self.state_path)
        if state.get("contract") != self.contract and not (
            historical and state.get("phase") in TERMINAL
        ):
            raise BatchError("Frozen contract/config changed; restore it, do not reset budgets.")
        if state.get("phase") not in TERMINAL | {
            "EXECUTOR_PENDING",
            "EXECUTOR_RUNNING",
            "VALIDATION_PENDING",
            "PLANNER_PENDING",
            "PLANNER_RUNNING",
        }:
            raise BatchError("Invalid saved phase")
        for key, limit in (
            ("executor_calls", self.spec.max_executor_calls),
            ("planner_calls", self.spec.max_planner_calls),
        ):
            if (
                type(state.get(key)) is not int
                or state[key] < 0
                or (limit is not None and state[key] > limit)
            ):
                raise BatchError(f"Invalid saved counter: {key}")
        if state["phase"] == "DONE":
            for name, expected in state.get("artifacts", {}).items():
                path = within(self.root, name)
                if not path.is_file() or digest(path.read_bytes()) != expected:
                    raise BatchError(f"Completed artifact changed: {name}")
        if historical:
            state["contract_matches_current"] = state.get("contract") == self.contract
        return state

    def recover_interrupted(self) -> dict[str, Any]:
        """Explicitly reconcile a stopped v2 Executor without refunding its reservation.

        This is deliberately separate from ``run``.  The operator must first
        inspect that no child remains, then request recovery.  Recovery never
        dispatches an Executor: it re-runs the fixed validator and resumes at
        Planner review with the existing artifacts and cumulative counters.
        """
        with task_lock(self.runtime / "active.lock"):
            self.verify()
            if not self.state_path.is_file():
                raise BatchError("No interrupted task state to recover")
            state = read_json(self.state_path)
            if self.spec.schema_version != 2 or state.get("phase") != "INTERRUPTED":
                raise BatchError("Recovery requires an explicitly interrupted autonomous task")
            previous_contract = state.get("contract")
            if not isinstance(previous_contract, str) or not previous_contract:
                raise BatchError("Interrupted task has no recoverable contract")
            prior_record = read_json(self.directory / "contract.json")
            prior_spec = prior_record.get("spec")
            if not isinstance(prior_spec, dict) or prior_spec.get("task_id") != self.spec.task_id:
                raise BatchError("Interrupted state belongs to a different task")
            if type(state.get("executor_calls")) is not int or state["executor_calls"] < 1:
                raise BatchError("Interrupted task has no Executor reservation to preserve")
            recovery_number = int(state.get("recovery_count", 0)) + 1
            save_json(
                self.directory / f"recovery_{recovery_number:04d}.json",
                {
                    "previous_contract": previous_contract,
                    "current_contract": self.contract,
                    "previous_phase": state["phase"],
                    "previous_reason": state.get("reason", ""),
                    "executor_calls_preserved": state["executor_calls"],
                    "planner_calls_preserved": state.get("planner_calls", 0),
                    "action": "validator_then_planner_review_only",
                },
            )
            state.update(
                contract=self.contract,
                phase="VALIDATION_PENDING",
                reason=(
                    "Explicit interrupted-task recovery: Executor reservation retained; "
                    "re-run validation and Planner review without Executor replay"
                ),
                recovery_count=recovery_number,
                completion_review=False,
                review_error="",
            )
            self._save(state)
            return state

    def recover_planner_block(self) -> dict[str, Any]:
        """Retry only a v2 review that stopped because its Planner was unavailable.

        A BLOCKED research conclusion is never reopened here.  This narrow
        recovery accepts only the controller's own repeated-Planner-failure
        state, retains all counters and artifacts, and starts with validation.
        """
        with task_lock(self.runtime / "active.lock"):
            self.verify()
            if not self.state_path.is_file():
                raise BatchError("No blocked task state to recover")
            state = read_json(self.state_path)
            if (
                self.spec.schema_version != 2
                or state.get("phase") != "BLOCKED"
                or state.get("executor_errors", 0) != 0
                or not isinstance(state.get("planner_errors"), int)
                or state["planner_errors"] < self.spec.max_consecutive_errors
                or not str(state.get("reason", "")).startswith("Repeated Planner failure:")
            ):
                raise BatchError("Recovery is limited to a repeated Planner infrastructure failure")
            prior_record = read_json(self.directory / "contract.json")
            prior_spec = prior_record.get("spec")
            if not isinstance(prior_spec, dict) or prior_spec.get("task_id") != self.spec.task_id:
                raise BatchError("Blocked state belongs to a different task")
            previous_contract = state.get("contract")
            if not isinstance(previous_contract, str) or not previous_contract:
                raise BatchError("Blocked task has no recoverable contract")
            recovery_number = int(state.get("recovery_count", 0)) + 1
            save_json(
                self.directory / f"recovery_{recovery_number:04d}.json",
                {
                    "previous_contract": previous_contract,
                    "current_contract": self.contract,
                    "previous_phase": state["phase"],
                    "previous_reason": state.get("reason", ""),
                    "executor_calls_preserved": state.get("executor_calls", 0),
                    "planner_calls_preserved": state.get("planner_calls", 0),
                    "action": "planner_infrastructure_retry_after_validation",
                },
            )
            state.update(
                contract=self.contract,
                phase="VALIDATION_PENDING",
                reason=(
                    "Explicit recovery of repeated Planner infrastructure failure: "
                    "validate and retry Planner without Executor replay"
                ),
                recovery_count=recovery_number,
                planner_errors=0,
                completion_review=False,
                review_error="",
            )
            self._save(state)
            return state

    def _save(self, state: dict[str, Any]) -> None:
        save_json(self.state_path, state)

    def _initialize(self) -> dict[str, Any]:
        index_path = self.runtime / "identities.json"
        index = read_json(index_path) if index_path.exists() else {}
        previous = index.get(self.identity)
        if previous is not None and previous != self.spec.task_id:
            raise BatchError("This task content already has a budget under another task_id")
        state = self.status()
        if state["phase"] != "NOT_STARTED":
            return state
        if previous is not None:
            raise BatchError(
                "Saved identity exists without state; restore the ledger, do not reset it"
            )
        # A stale directory is evidence of a lost ledger, not a new allowance.
        if self.directory.exists():
            raise BatchError(
                "Task directory exists without state; reconcile it instead of resetting"
            )
        index[self.identity] = self.spec.task_id
        save_json(index_path, index)
        state = {
            "task_id": self.spec.task_id,
            "contract": self.contract,
            "phase": "EXECUTOR_PENDING",
            "executor_calls": 0,
            "planner_calls": 0,
            "feedback": "",
            "report": "",
            "reason": "",
        }
        self._save(state)
        save_json(
            self.directory / "contract.json", {"spec": asdict(self.spec), "seals": self.seals}
        )
        return state

    def _task(self, feedback: str) -> str:
        task = within(self.root, self.spec.task_file).read_text(encoding="utf-8-sig")
        scope = within(self.root, self.spec.scope_file).read_text(encoding="utf-8-sig")
        autonomous = ""
        if self.spec.schema_version == 2:
            autonomous = (
                "\nAUTONOMOUS COMPLETION CONTRACT: redesign, repair, run permitted validation, "
                "and finish ALL criteria. HOLD is a research label, not a completion reason. "
                "Do not stop for fixable defects, missing documents, or owner values irrelevant "
                "to the present stage. Record concrete progress and attempts in artifacts each turn. "
                "Preserve sealed source outcomes; put corrections in this task's new artifacts.\n"
                f"Criteria: {json.dumps(self.spec.completion_criteria, ensure_ascii=False)}\n"
                f"Read-only inputs: {json.dumps(self.spec.read_only_inputs)}\n"
            )
        return (
            f"FINITE TASK {self.spec.task_id}; MODE={self.spec.mode}\n"
            "This frozen task is the entire scope. Planner feedback only identifies unfinished work; "
            "it never authorizes another hypothesis, input, period, market run, or budget. "
            "Do not edit task/config/protected files or orchestration state. "
            "Design mode permits documents/public sources only; no market data access. "
            "Write only the declared artifacts. Maintain cumulative research/search limits across calls.\n"
            f"Required artifacts: {json.dumps(self.spec.required_artifacts)}\n"
            f"{autonomous}"
            f"=== FROZEN SCOPE ===\n{scope}\n=== INITIAL TASK ===\n{task}\n"
            f"=== REVIEW FEEDBACK (WITHIN SCOPE ONLY) ===\n{feedback}"
        )

    def run(
        self, transport: Transport, *, once: bool = False, close_reason: str | None = None
    ) -> dict[str, Any]:
        with task_lock(self.runtime / "active.lock"):
            self.verify()
            state = self._initialize()
            if close_reason is not None:
                if not close_reason.strip() or state["phase"] not in {
                    "EXECUTOR_RUNNING",
                    "PLANNER_RUNNING",
                }:
                    raise BatchError("Close only an interrupted dispatch, with a nonempty reason")
                state.update(phase="INTERRUPTED", reason=close_reason)
                self._save(state)
                return state
            if state["phase"] in {"EXECUTOR_RUNNING", "PLANNER_RUNNING"}:
                raise BatchError(
                    "Interrupted dispatch: inspect logs, then --close-interrupted REASON. No automatic replay."
                )
            if state["phase"] in TERMINAL:
                return state
            if self.spec.schema_version == 2:
                return self._run_autonomous(transport, state, once=once)
            while True:
                self.verify()
                task = self._task(state["feedback"])
                if state["phase"] == "EXECUTOR_PENDING":
                    if (
                        self.spec.max_executor_calls is not None
                        and state["executor_calls"] >= self.spec.max_executor_calls
                    ):
                        state.update(phase="EXHAUSTED", reason="Executor budget exhausted")
                        self._save(state)
                        return state
                    state["executor_calls"] += 1
                    state["phase"] = "EXECUTOR_RUNNING"
                    self._save(state)  # Reserve before any child process, including failures.

                    def reservation_check() -> None:
                        self.verify()
                        saved = self.status()
                        if (
                            saved["phase"] != "EXECUTOR_RUNNING"
                            or saved["executor_calls"] != state["executor_calls"]
                        ):
                            raise BatchError("Executor reservation does not match")

                    try:
                        state["report"] = transport.execute(
                            task, state["executor_calls"], reservation_check
                        )
                        self.verify()
                    except (Exception, KeyboardInterrupt) as exc:
                        state.update(phase="FAILED", reason=f"Executor failed: {exc}")
                        self._save(state)
                        raise BatchError(state["reason"]) from exc
                    state["phase"] = "PLANNER_PENDING"
                    self._save(state)
                if (
                    self.spec.max_planner_calls is not None
                    and state["planner_calls"] >= self.spec.max_planner_calls
                ):
                    state.update(phase="EXHAUSTED", reason="Planner budget exhausted")
                    self._save(state)
                    return state
                state["planner_calls"] += 1
                state["phase"] = "PLANNER_RUNNING"
                self._save(state)
                try:
                    decision = transport.plan(task, state["report"], state["planner_calls"])
                    self.verify()
                    if (
                        not isinstance(decision, dict)
                        or decision.get("status") not in {"continue", "done"}
                        or not isinstance(decision.get("analysis"), str)
                        or not isinstance(decision.get("next_task"), str)
                        or (decision["status"] == "continue" and not decision["next_task"].strip())
                    ):
                        raise BatchError("Invalid planner response")
                except (Exception, KeyboardInterrupt) as exc:
                    # Retry only the reviewer on a later invocation; never replay the executor.
                    state.update(phase="PLANNER_PENDING", reason=f"Planner failed: {exc}")
                    self._save(state)
                    raise BatchError(state["reason"]) from exc
                save_json(self.directory / f"decision_{state['planner_calls']:04d}.json", decision)
                if decision["status"] == "done":
                    missing = [
                        name
                        for name in self.spec.required_artifacts
                        if not within(self.root, name).is_file()
                        or not within(self.root, name).read_text(encoding="utf-8-sig").strip()
                    ]
                    state.update(
                        phase="INCOMPLETE" if missing else "DONE",
                        reason=f"Missing artifacts: {missing}" if missing else decision["analysis"],
                    )
                    if not missing:
                        state["artifacts"] = {
                            name: digest(within(self.root, name).read_bytes())
                            for name in self.spec.required_artifacts
                        }
                    self._save(state)
                    return state
                if self.spec.mode == "registered":
                    state.update(
                        phase="INCOMPLETE",
                        reason="Registered run finished; further work needs its own reviewed plan",
                    )
                    self._save(state)
                    return state
                state.update(
                    phase="EXECUTOR_PENDING",
                    feedback=decision["next_task"],
                    reason=decision["analysis"],
                )
                if (
                    self.spec.max_executor_calls is not None
                    and state["executor_calls"] >= self.spec.max_executor_calls
                ):
                    state.update(phase="EXHAUSTED", reason="Executor budget exhausted")
                self._save(state)
                if once or state["phase"] in TERMINAL:
                    return state

    def _artifact_hashes(self) -> dict[str, str]:
        return {
            name: digest(within(self.root, name).read_bytes())
            for name in self.spec.required_artifacts
            if within(self.root, name).is_file()
        }

    def _block(self, state: dict[str, Any], reason: str) -> dict[str, Any]:
        state.update(phase="BLOCKED", reason=reason)
        self._save(state)
        return state

    def _review_issues(self, decision: dict[str, Any]) -> list[str]:
        """Check completion evidence independently of a prose 'done'."""
        if (
            decision.get("status") not in {"continue", "done", "blocked"}
            or not isinstance(decision.get("analysis"), str)
            or not isinstance(decision.get("next_task"), str)
            or not isinstance(decision.get("criteria"), list)
            or not isinstance(decision.get("remaining_work"), list)
            or not all(isinstance(x, str) for x in decision["remaining_work"])
        ):
            raise BatchError("Malformed autonomous review")
        criteria = decision["criteria"]
        if (
            not all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in criteria)
            or len({row["id"] for row in criteria}) != len(criteria)
            or {row["id"] for row in criteria} != set(self.spec.completion_criteria or {})
        ):
            raise BatchError(
                "Review must account for every fixed completion criterion exactly once"
            )
        issues = []
        for row in criteria:
            if row.get("status") not in {"complete", "pending"} or not isinstance(
                row.get("evidence"), list
            ):
                raise BatchError("Invalid completion criterion")
            if row["status"] != "complete":
                issues.append(f"Pending criterion: {row['id']}")
            if row["status"] == "complete":
                evidence = row["evidence"]
                if not evidence or not all(isinstance(name, str) for name in evidence):
                    raise ReviewProtocolError(
                        f"Criterion {row['id']} needs one or more declared artifact paths in evidence"
                    )
                invalid = [
                    name
                    for name in evidence
                    if name not in self.spec.required_artifacts
                    or not within(self.root, name).is_file()
                    or not within(self.root, name).stat().st_size > 0
                ]
                if invalid:
                    raise ReviewProtocolError(
                        f"Criterion {row['id']} evidence must contain only declared artifact paths; "
                        f"invalid values: {invalid}"
                    )
        issues.extend(
            f"Missing artifact: {name}"
            for name in self.spec.required_artifacts
            if not within(self.root, name).is_file()
            or not within(self.root, name).read_text(encoding="utf-8-sig").strip()
        )
        if decision["status"] == "done" and decision["remaining_work"]:
            issues.append("Remaining work is not empty")
        if decision["status"] == "continue" and not decision["next_task"].strip():
            raise BatchError("Continue requires a concrete repair task")
        if decision["status"] == "blocked":
            blocker = decision.get("blocker")
            if (
                not isinstance(blocker, dict)
                or blocker.get("kind")
                not in {
                    "external_dependency",
                    "scope_boundary",
                    "permission",
                    "resource",
                    "contradictory_requirements",
                }
                or not isinstance(blocker.get("explanation"), str)
                or not blocker["explanation"].strip()
                or not isinstance(blocker.get("evidence"), list)
                or not blocker["evidence"]
                or not all(
                    isinstance(name, str)
                    and name in self.spec.required_artifacts
                    and within(self.root, name).is_file()
                    for name in blocker["evidence"]
                )
                or not isinstance(blocker.get("attempted_remedies"), list)
                or not blocker["attempted_remedies"]
                or not all(isinstance(x, str) and x.strip() for x in blocker["attempted_remedies"])
                or not decision["remaining_work"]
            ):
                raise BatchError("Blocked requires evidence, attempted remedies and remaining work")
        return issues

    def _run_autonomous(
        self, transport: Transport, state: dict[str, Any], *, once: bool
    ) -> dict[str, Any]:
        for key, value in {
            "executor_errors": 0,
            "planner_errors": 0,
            "stalled_cycles": 0,
            "progress_hash": "",
            "completion_review": False,
        }.items():
            state.setdefault(key, value)
        while True:
            try:
                self.verify()
            except BatchError as exc:
                return self._block(state, f"Frozen boundary changed: {exc}")
            if state["phase"] == "VALIDATION_PENDING":
                try:
                    state["validation"] = transport.validate(state["executor_calls"])
                except Exception as exc:
                    state["validation"] = {"passed": False, "details": f"Validation failed: {exc}"}
                state["validated_hashes"] = self._artifact_hashes()
                state["phase"] = "PLANNER_PENDING"
                self._save(state)
            task = self._task(state["feedback"])
            if state["phase"] == "EXECUTOR_PENDING":
                if (
                    self.spec.max_executor_calls is not None
                    and state["executor_calls"] >= self.spec.max_executor_calls
                ):
                    return self._block(
                        state, "Explicit Executor resource limit reached; work remains"
                    )
                state["executor_calls"] += 1
                state["phase"] = "EXECUTOR_RUNNING"
                self._save(state)

                def check() -> None:
                    self.verify()
                    saved = self.status()
                    if (
                        saved["phase"] != "EXECUTOR_RUNNING"
                        or saved["executor_calls"] != state["executor_calls"]
                    ):
                        raise BatchError("Executor reservation does not match")

                try:
                    state["report"] = transport.execute(task, state["executor_calls"], check)
                    state["executor_errors"] = 0
                except KeyboardInterrupt:
                    state.update(
                        phase="INTERRUPTED",
                        reason="User interrupted Executor; inspect child processes before further work",
                    )
                    self._save(state)
                    raise
                except DispatchUncertainError as exc:
                    return self._block(
                        state, f"Executor termination is uncertain; inspect child processes: {exc}"
                    )
                except Exception as exc:
                    state["executor_errors"] += 1
                    state["report"] = (
                        f"Executor failed: {exc}. Inspect partial artifacts and repair within scope."
                    )
                try:
                    self.verify()
                except BatchError as exc:
                    return self._block(state, f"Protected input changed: {exc}")
                if state["executor_errors"] >= self.spec.max_consecutive_errors:
                    return self._block(
                        state, f"Repeated Executor failure after repair attempts: {state['report']}"
                    )
                try:
                    state["validation"] = transport.validate(state["executor_calls"])
                except Exception as exc:
                    state["validation"] = {"passed": False, "details": f"Validation failed: {exc}"}
                state["validated_hashes"] = self._artifact_hashes()
                state["phase"] = "PLANNER_PENDING"
                self._save(state)
            try:
                self.verify()
            except BatchError as exc:
                return self._block(state, f"Protected input changed during validation: {exc}")
            if (
                self.spec.max_planner_calls is not None
                and state["planner_calls"] >= self.spec.max_planner_calls
            ):
                return self._block(state, "Explicit Planner resource limit reached; review remains")
            state["planner_calls"] += 1
            state["phase"] = "PLANNER_RUNNING"
            self._save(state)
            report = (
                state["report"]
                + "\nAUTOMATED VALIDATION:\n"
                + json.dumps(state.get("validation"), ensure_ascii=False)
            )
            if state.get("review_error"):
                report += "\nCorrect the previous review protocol error: " + state["review_error"]
            if state["completion_review"]:
                report += "\nINDEPENDENT COMPLETION AUDIT: re-read all artifacts and criteria; actively look for contradictions and incomplete work. Do not repeat the previous verdict without checking."
            try:
                decision = transport.plan(task, report, state["planner_calls"])
                issues = self._review_issues(decision)
                state["planner_errors"] = 0
                state["review_error"] = ""
            except KeyboardInterrupt:
                state.update(
                    phase="PLANNER_PENDING",
                    reason="User interrupted review; Executor result retained",
                )
                self._save(state)
                raise
            except Exception as exc:
                state["planner_errors"] += 1
                state.update(
                    phase="PLANNER_PENDING", reason=f"Planner error: {exc}", review_error=str(exc)
                )
                self._save(state)
                if state["planner_errors"] >= self.spec.max_consecutive_errors:
                    return self._block(state, f"Repeated Planner failure: {exc}")
                if once:
                    return state
                time.sleep(self.spec.retry_backoff_seconds)
                continue
            try:
                self.verify()
            except BatchError as exc:
                return self._block(state, f"Protected input changed: {exc}")
            save_json(self.directory / f"decision_{state['planner_calls']:04d}.json", decision)
            if decision["status"] == "blocked":
                state["blocker"] = decision["blocker"]
                state["remaining_work"] = decision["remaining_work"]
                return self._block(state, decision["analysis"])
            if state.get("executor_errors"):
                issues.append("Last Executor call failed; repair and rerun are required")
            if state.get("validation", {}).get("passed") is not True:
                issues.append("Automated validation has not passed")
            current_hashes = self._artifact_hashes()
            if current_hashes != state.get("validated_hashes"):
                issues.append(
                    "Artifacts changed after validation; repair and revalidate before completion"
                )
            if decision["status"] == "done" and not issues:
                if state["completion_review"] and current_hashes == state.get("completion_hashes"):
                    state.update(
                        phase="DONE",
                        reason=decision["analysis"],
                        artifacts=current_hashes,
                        criteria=decision["criteria"],
                        remaining_work=[],
                    )
                    self._save(state)
                    return state
                state.update(
                    phase="PLANNER_PENDING",
                    completion_review=True,
                    completion_hashes=current_hashes,
                )
                self._save(state)
                if once:
                    return state
                continue
            state["completion_review"] = False
            progress = json_digest(current_hashes)
            state["stalled_cycles"] = (
                state["stalled_cycles"] + 1 if progress == state["progress_hash"] else 0
            )
            state["progress_hash"] = progress
            state["remaining_work"] = [*decision["remaining_work"], *issues]
            state.update(
                phase="EXECUTOR_PENDING",
                reason=decision["analysis"],
                feedback=(
                    decision["next_task"]
                    + "\nREQUIRED REPAIR:\n"
                    + "\n".join(issues)
                    + "\nValidation: "
                    + json.dumps(state.get("validation"), ensure_ascii=False)
                ),
            )
            self._save(state)
            if state["stalled_cycles"] >= self.spec.max_stalled_cycles:
                return self._block(
                    state,
                    "Repeated repair cycles made no artifact progress; inspect preserved attempts",
                )
            if once:
                return state
            if state["executor_errors"]:
                time.sleep(self.spec.retry_backoff_seconds)
