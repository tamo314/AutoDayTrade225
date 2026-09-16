#!/usr/bin/env python3
"""Minimal Codex -> (Claude Code | Antigravity | Codex) research orchestration loop.

The planner is Codex CLI. The implementation executor is selected in config.json.
No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator_batch import Batch, BatchError, DispatchUncertainError

APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_DIR / "config.json"
PLANNER_SCHEMA = APP_DIR / "planner_schema.json"
PLANNER_PROMPT_FILE = APP_DIR / "prompts" / "planner.md"
EXECUTOR_PROMPT_FILE = APP_DIR / "prompts" / "executor.md"
CONTEXT_FILE = APP_DIR / "AGENTS.md"
INITIAL_TASK_FILE = APP_DIR / "research" / "INITIAL_TASK.md"
RUNTIME_DIR = APP_DIR / ".orchestrator"
RUNS_DIR = RUNTIME_DIR / "runs"
CODEX_SCRATCH_DIR = RUNTIME_DIR / "codex_scratch"
STATE_FILE = RUNTIME_DIR / "state.json"
HISTORY_FILE = RUNTIME_DIR / "history.jsonl"


class OrchestratorError(RuntimeError):
    pass


@dataclass
class CommandResult:
    command: list[str]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


@dataclass
class State:
    iteration: int
    phase: str
    current_task: str
    executor: str
    last_executor_report_path: str | None = None
    done_reason: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise OrchestratorError(f"Expected JSON object: {path}")
        return value
    except FileNotFoundError as exc:
        raise OrchestratorError(f"Required file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise OrchestratorError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False) + "\n")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise OrchestratorError(f"Required file not found: {path}") from exc


def resolve_project_dir(config: dict[str, Any], config_path: Path) -> Path:
    raw = config.get("project_dir", "..")
    project_dir = Path(raw)
    if not project_dir.is_absolute():
        project_dir = (config_path.parent / project_dir).resolve()
    else:
        project_dir = project_dir.resolve()
    if not project_dir.is_dir():
        raise OrchestratorError(f"project_dir does not exist or is not a directory: {project_dir}")
    return project_dir


def require_automation_enabled(config: dict[str, Any]) -> None:
    """Pause automation before state reset or any planner/executor dispatch."""
    if config.get("research_execution_paused", True) is not False:
        raise OrchestratorError(
            "Research automation is paused. See docs/strategy/00_current_research_policy.md; "
            "a new loop or --reset does not reopen a closed research batch."
        )
    # Free-form planner tasks cannot carry a reservation across child processes.
    # Finite registered runs use the deterministic research execute command instead.
    raise OrchestratorError(
        "Unbounded planner/executor dispatch is retired; use research execute with a reviewed manifest."
    )


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    required_top = ["executor", "max_iterations", "codex", "claude", "antigravity"]
    missing = [key for key in required_top if key not in config]
    if missing:
        raise OrchestratorError(f"Missing config keys: {', '.join(missing)}")

    executor = config["executor"]
    if executor not in {"claude", "antigravity", "codex"}:
        raise OrchestratorError('config.executor must be "claude", "antigravity", or "codex"')

    if int(config["max_iterations"]) < 1:
        raise OrchestratorError("max_iterations must be >= 1")

    config.setdefault("executor_timeout_seconds", 7200)
    config.setdefault("planner_timeout_seconds", 600)
    config.setdefault("max_result_chars_for_planner", 60000)
    config.setdefault("recent_history_items_for_planner", 4)
    config.setdefault("stop_on_executor_error", True)
    if isinstance(config.get("codex"), dict):
        config["codex"].setdefault("planner_remove_windowsapps_from_path", True)

    # Research-completion guardrails. These defaults intentionally make an
    # early `done` decision difficult without forcing meaningless busywork.
    # Existing config.json files do not need to be edited unless you want to
    # change these values.
    config.setdefault("min_iterations_before_done", 5)
    config.setdefault("done_confirmation_required", True)

    min_iterations_before_done = int(config["min_iterations_before_done"])
    if min_iterations_before_done < 0:
        raise OrchestratorError("min_iterations_before_done must be >= 0")
    if min_iterations_before_done > int(config["max_iterations"]):
        raise OrchestratorError(
            "min_iterations_before_done must be <= max_iterations "
            f"({config['max_iterations']})"
        )
    if not isinstance(config["done_confirmation_required"], bool):
        raise OrchestratorError("done_confirmation_required must be true or false")

    for tool in ("codex", "claude", "antigravity"):
        if not isinstance(config[tool], dict) or not config[tool].get("command"):
            raise OrchestratorError(f"config.{tool}.command is required")
        config[tool].setdefault("extra_args", [])
        config[tool].setdefault("model", "")
        if not isinstance(config[tool]["extra_args"], list):
            raise OrchestratorError(f"config.{tool}.extra_args must be an array")
        if config[tool]["model"] is None:
            config[tool]["model"] = ""
        if not isinstance(config[tool]["model"], str):
            raise OrchestratorError(f"config.{tool}.model must be a string")

    config["antigravity"].setdefault("dangerously_skip_permissions", False)
    if not isinstance(config["antigravity"]["dangerously_skip_permissions"], bool):
        raise OrchestratorError("config.antigravity.dangerously_skip_permissions must be true or false")

    # Claude Code can end a healthy long-running non-interactive task with
    # error_max_turns.  By default, resume the same session a bounded number of
    # times instead of treating that condition as an immediate executor failure.
    config["claude"].setdefault("auto_resume_on_max_turns", True)
    config["claude"].setdefault("max_turn_resumes", 2)
    if not isinstance(config["claude"]["auto_resume_on_max_turns"], bool):
        raise OrchestratorError("config.claude.auto_resume_on_max_turns must be true or false")
    try:
        max_turn_resumes = int(config["claude"]["max_turn_resumes"])
    except (TypeError, ValueError) as exc:
        raise OrchestratorError("config.claude.max_turn_resumes must be an integer") from exc
    if max_turn_resumes < 0:
        raise OrchestratorError("config.claude.max_turn_resumes must be >= 0")
    config["claude"]["max_turn_resumes"] = max_turn_resumes

    # Codex is always the planner, and can optionally also be the implementation
    # executor. Keep executor-specific policy separate from the planner's
    # read-only invocation.
    config["codex"].setdefault("executor_model", "")
    config["codex"].setdefault("executor_sandbox", "workspace-write")
    config["codex"].setdefault("executor_approval_policy", "never")
    config["codex"].setdefault("executor_extra_args", [])
    if not isinstance(config["codex"]["executor_model"], str):
        raise OrchestratorError("config.codex.executor_model must be a string")
    if config["codex"]["executor_sandbox"] not in {"read-only", "workspace-write", "danger-full-access"}:
        raise OrchestratorError(
            'config.codex.executor_sandbox must be "read-only", "workspace-write", or "danger-full-access"'
        )
    if config["codex"]["executor_approval_policy"] not in {"untrusted", "on-request", "never"}:
        raise OrchestratorError(
            'config.codex.executor_approval_policy must be "untrusted", "on-request", or "never"'
        )
    if not isinstance(config["codex"]["executor_extra_args"], list):
        raise OrchestratorError("config.codex.executor_extra_args must be an array")

    return config


def resolve_cli_command(command: str) -> str:
    """Resolve a CLI name to the actual executable/shim path.

    This is especially important on Windows: PowerShell and shutil.which()
    can resolve commands such as ``codex`` to ``codex.CMD``, while
    CreateProcess (used by subprocess) may fail when given only the bare
    command name. Passing the fully resolved shim path avoids WinError 2.
    """
    command_path = Path(command)
    if command_path.is_absolute() or command_path.parent != Path("."):
        return str(command_path)
    resolved = shutil.which(command)
    return resolved or command


def resolved_command(command: list[str]) -> list[str]:
    if not command:
        raise OrchestratorError("Cannot run an empty command")
    return [resolve_cli_command(command[0]), *command[1:]]


def _decode_subprocess_bytes(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        if os.name == "nt":
            try:
                return value.decode("cp932")
            except UnicodeDecodeError:
                pass
        return value.decode("utf-8", errors="replace")


def run_command(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    stdin_text: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    command = resolved_command(command)
    start = time.monotonic()
    stdin_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
    process_env = os.environ.copy() if env is None else env.copy()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            input=stdin_bytes,
            capture_output=True,
            text=False,
            timeout=timeout_seconds,
            env=process_env,
        )
        return CommandResult(
            command=command,
            return_code=proc.returncode,
            stdout=_decode_subprocess_bytes(proc.stdout),
            stderr=_decode_subprocess_bytes(proc.stderr),
            duration_seconds=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_subprocess_bytes(exc.stdout)
        stderr = _decode_subprocess_bytes(exc.stderr)
        return CommandResult(
            command=command,
            return_code=124,
            stdout=stdout,
            stderr=stderr + f"\nTimed out after {timeout_seconds} seconds.",
            duration_seconds=time.monotonic() - start,
            timed_out=True,
        )


def _planner_codex_env(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Build the environment used only for Codex planner subprocesses.

    On Windows, Microsoft Store/MSIX PowerShell can resolve through a
    ``WindowsApps`` PATH entry but fail when Codex tries to create it inside its
    sandbox with CreateProcessAsUserW. Removing only those PATH entries for the
    planner subprocess lets Codex fall back to another shell without mutating
    the user's global environment.
    """
    env = os.environ.copy()
    codex_cfg = config.get("codex", {})
    remove_windowsapps = bool(codex_cfg.get("planner_remove_windowsapps_from_path", True))
    removed: list[str] = []
    if os.name == "nt" and remove_windowsapps:
        kept: list[str] = []
        for item in env.get("PATH", "").split(os.pathsep):
            normalized = item.replace("/", "\\").lower()
            if "\\windowsapps" in normalized:
                removed.append(item)
            else:
                kept.append(item)
        env["PATH"] = os.pathsep.join(kept)
    return env, {
        "windowsapps_path_filter_enabled": os.name == "nt" and remove_windowsapps,
        "windowsapps_path_entries_removed": removed,
    }

def _truncate_text_middle(text: str, max_chars: int) -> tuple[str, bool]:
    """Keep a bounded diagnostic excerpt while preserving both ends."""
    if max_chars <= 0:
        return "", bool(text)
    if len(text) <= max_chars:
        return text, False
    marker = f"\n... <{len(text) - max_chars} chars omitted> ...\n"
    usable = max(0, max_chars - len(marker))
    head = usable // 3
    tail = usable - head
    return text[:head] + marker + text[-tail:], True


def _codex_stderr_excerpt(stderr: str, max_chars: int) -> tuple[str, bool]:
    """Compact Codex human-progress stderr for structured run JSON.

    Codex exec intentionally streams human-readable progress/tool traces to
    stderr while stdout/--output-last-message carries the final answer.  The
    full progress stream can be megabytes, so retain only the runtime banner,
    error/warning lines, and a bounded tail.
    """
    if len(stderr) <= max_chars:
        return stderr, False

    lines = stderr.splitlines()
    banner: list[str] = []
    for line in lines:
        if line.strip().lower() == "user":
            break
        banner.append(line)
        if len(banner) >= 40:
            break

    # Skip the echoed user prompt when looking for diagnostics. In default
    # human-output mode Codex writes the full prompt to stderr between the
    # `user` marker and the first subsequent `codex` marker.
    trace_start = 0
    try:
        user_index = next(i for i, line in enumerate(lines) if line.strip().lower() == "user")
        trace_start = next(
            i for i in range(user_index + 1, len(lines))
            if lines[i].strip().lower() == "codex"
        )
    except StopIteration:
        trace_start = 0
    trace_lines = lines[trace_start:]

    interesting: list[str] = []
    pattern = re.compile(
        r"(?i)(\berror\b|\bwarn(?:ing)?\b|failed|rejected|timed?\s*out|panic|exception)"
    )
    for line in trace_lines:
        if pattern.search(line):
            # One tool error can itself contain a very long command.
            interesting.append(line[:2000])
            if len(interesting) >= 80:
                break

    tail_lines = trace_lines[-120:]
    combined_parts = ["\n".join(banner).strip()]
    if interesting:
        combined_parts.append("[selected Codex diagnostics]\n" + "\n".join(interesting))
    if tail_lines:
        combined_parts.append("[Codex stderr tail]\n" + "\n".join(tail_lines))
    combined = "\n\n".join(part for part in combined_parts if part)
    excerpt, _ = _truncate_text_middle(combined, max_chars)
    return excerpt, True


def _prepare_codex_stderr_for_log(
    config: dict[str, Any],
    stderr: str,
    *,
    iteration: int,
    log_stem: str,
    success: bool,
) -> tuple[str, dict[str, Any]]:
    """Return compact stderr plus metadata; persist full trace only when requested.

    Successful Codex runs default to compact-only storage. Failed runs always
    preserve the full stderr sidecar because it may be needed for diagnosis.
    Set codex.save_full_stderr=true to keep full sidecars for successful runs too.
    """
    codex_cfg = config.get("codex", {})
    max_chars = int(codex_cfg.get("stderr_excerpt_chars", 20000))
    save_full = bool(codex_cfg.get("save_full_stderr", False)) or not success

    full_path: Path | None = None
    if stderr and save_full:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        full_path = RUNS_DIR / f"{iteration:04d}_{log_stem}.stderr.log"
        full_path.write_text(stderr, encoding="utf-8", errors="replace")

    excerpt, truncated = _codex_stderr_excerpt(stderr, max_chars)
    meta: dict[str, Any] = {
        "stderr_chars": len(stderr),
        "stderr_truncated": truncated,
        "stderr_excerpt_limit_chars": max_chars,
        "stderr_full_log": (
            str(full_path.relative_to(APP_DIR)) if full_path is not None else None
        ),
    }
    return excerpt, meta


def clean_cli_name(command: str) -> str:
    return Path(command).name


def command_exists(command: str) -> bool:
    command_path = Path(command)
    if command_path.is_absolute() or command_path.parent != Path("."):
        return command_path.exists()
    return shutil.which(command) is not None


def version_probe(command: str) -> str:
    try:
        actual_command = resolve_cli_command(command)
        result = subprocess.run(
            [actual_command, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        text = (result.stdout or result.stderr).strip().splitlines()
        return text[0] if text else f"exit={result.returncode}"
    except Exception as exc:  # diagnostic only
        return f"version check failed: {exc}"


def _read_antigravity_settings_model() -> str | None:
    path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    model = data.get("model") if isinstance(data, dict) else None
    return model.strip() if isinstance(model, str) and model.strip() else None


def _read_codex_config_model() -> str | None:
    path = Path.home() / ".codex" / "config.toml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # Only consider a top-level model assignment before the first TOML table.
    pattern = re.compile(r"model\s*=\s*[\"']([^\"']+)[\"']")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            break
        match = pattern.match(stripped)
        if match:
            return match.group(1).strip()
    return None


def configured_model(
    config: dict[str, Any], tool: str, *, role: str = "default"
) -> tuple[str, str]:
    if tool == "codex" and role == "executor":
        executor_model = str(config["codex"].get("executor_model", "")).strip()
        if executor_model:
            return executor_model, "config.json codex.executor_model"

    explicit = str(config[tool].get("model", "")).strip()
    if explicit:
        return explicit, "config.json"
    if tool == "antigravity":
        detected = _read_antigravity_settings_model()
        if detected:
            return detected, "Antigravity settings.json"
    if tool == "codex":
        detected = _read_codex_config_model()
        if detected:
            return detected, "Codex config.toml"
    return "CLI default (not pinned)", "default"


def _model_args(config: dict[str, Any], tool: str, *, role: str = "default") -> list[str]:
    if tool == "codex" and role == "executor":
        model = str(config["codex"].get("executor_model", "")).strip()
        if not model:
            model = str(config["codex"].get("model", "")).strip()
    else:
        model = str(config[tool].get("model", "")).strip()
    return ["--model", model] if model else []


def _parse_codex_runtime_model(stdout: str, stderr: str) -> str | None:
    text = f"{stderr}\n{stdout}"
    match = re.search(r"(?mi)^\s*model\s*:\s*([^\r\n]+)", text)
    return match.group(1).strip() if match else None


def run_checks(config: dict[str, Any], project_dir: Path, task_file: Path = INITIAL_TASK_FILE) -> bool:
    executor = config["executor"]
    required_commands = list(dict.fromkeys([config["codex"]["command"], config[executor]["command"]]))
    ok = True

    planner_model, planner_model_source = configured_model(config, "codex")
    executor_model, executor_model_source = configured_model(
        config, executor, role="executor" if executor == "codex" else "default"
    )

    print(f"Project directory : {project_dir}")
    print(f"Executor          : {executor}")
    print(f"Planner model     : {planner_model} [{planner_model_source}]")
    print(f"Executor model    : {executor_model} [{executor_model_source}]")
    print(f"Legacy max iterations (bounded mode ignores): {config['max_iterations']}")
    print(f"Legacy min (ignored): {config['min_iterations_before_done']}")
    print(f"Legacy loop paused: {config.get('research_execution_paused', True)}")
    print(f"Done confirmation : {config['done_confirmation_required']}")
    print(f"Planner workdir    : {project_dir}")
    if os.name == "nt":
        print(
            "Planner WinApps filter: "
            f"{bool(config.get('codex', {}).get('planner_remove_windowsapps_from_path', True))}"
        )
    print()

    for command in required_commands:
        exists = command_exists(command)
        status = "OK" if exists else "MISSING"
        print(f"[{status}] {clean_cli_name(command)}", end="")
        if exists:
            print(f" - {version_probe(command)}")
        else:
            print()
            ok = False

    for path in [PLANNER_SCHEMA, PLANNER_PROMPT_FILE, EXECUTOR_PROMPT_FILE, CONTEXT_FILE, task_file]:
        exists = path.is_file()
        print(f"[{'OK' if exists else 'MISSING'}] {path}")
        ok = ok and exists

    if executor == "antigravity":
        print()
        if config["antigravity"].get("dangerously_skip_permissions"):
            print("[WARNING] Antigravity auto-approval is ENABLED (--dangerously-skip-permissions).")
            print("          The agent can run commands and modify files without confirmation.")
        else:
            print("Antigravity note: agy -p is non-interactive. For host-side code changes, set")
            print("Tool Permission to 'always-proceed' in /permissions or /config, OR explicitly")
            print("set antigravity.dangerously_skip_permissions=true in config.json.")

    if executor == "claude":
        print()
        print(
            "Claude max-turn auto-resume: "
            f"enabled={config['claude']['auto_resume_on_max_turns']}, "
            f"max_resumes={config['claude']['max_turn_resumes']}"
        )
        print(
            "Note: executor_timeout_seconds is the total wall-clock budget across "
            "the initial Claude run and all automatic resumes."
        )

    if executor == "codex":
        print()
        print(
            "Codex executor policy: "
            f"sandbox={config['codex']['executor_sandbox']}, "
            f"approval={config['codex']['executor_approval_policy']}"
        )
        if config["codex"]["executor_sandbox"] == "danger-full-access":
            print("[WARNING] Codex executor has danger-full-access to the host environment.")
        print("Note: planner and executor are separate Codex invocations; planner remains read-only.")

    return ok


def build_executor_prompt(task: str, context: str) -> str:
    base = read_text(EXECUTOR_PROMPT_FILE)
    return (
        f"{base}\n\n"
        "=== RESEARCH CONTEXT ===\n"
        f"{context}\n\n"
        "=== CURRENT TASK ===\n"
        f"{task}\n"
    )


def normalize_executor_output(executor: str, raw_stdout: str, raw_stderr: str) -> str:
    if executor == "claude":
        try:
            parsed = json.loads(raw_stdout)
            if isinstance(parsed, dict):
                for key in ("structured_output", "result", "content"):
                    value = parsed.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                    if value is not None and key == "structured_output":
                        return json.dumps(value, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass

    output = raw_stdout.strip()
    if not output and raw_stderr.strip():
        output = raw_stderr.strip()
    return output


def _parse_claude_result(stdout: str) -> dict[str, Any] | None:
    """Parse Claude Code --output-format json output when available."""
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _claude_max_turns_session(result: CommandResult) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Return whether this is error_max_turns and the resumable session id.

    Claude Code intentionally exits non-zero when --max-turns is exhausted.
    That is different from a crash/permission error: the JSON result normally
    contains subtype=error_max_turns, terminal_reason=max_turns and session_id.
    """
    parsed = _parse_claude_result(result.stdout)
    if not parsed:
        return False, None, None
    is_max_turns = (
        parsed.get("subtype") == "error_max_turns"
        or parsed.get("terminal_reason") == "max_turns"
        or any("maximum number of turns" in str(x).lower() for x in parsed.get("errors", []) if x)
    )
    session_id = parsed.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = None
    return is_max_turns, session_id, parsed


def _masked_command(command: list[str], prompt_values: set[str]) -> list[str]:
    return [
        "<prompt>" if arg in prompt_values else (clean_cli_name(arg) if i == 0 else arg)
        for i, arg in enumerate(command)
    ]


def _run_claude_executor_with_resume(
    config: dict[str, Any],
    project_dir: Path,
    prompt: str,
) -> tuple[CommandResult, list[dict[str, Any]], int]:
    """Run Claude and automatically resume only error_max_turns sessions.

    The configured executor_timeout_seconds is a TOTAL wall-clock budget across
    the initial invocation and all resume invocations.  max_turn_resumes bounds
    the number of resumes, so this cannot loop forever.
    """
    claude_cfg = config["claude"]
    model_args = _model_args(config, "claude")
    auto_resume = bool(claude_cfg.get("auto_resume_on_max_turns", True))
    max_resumes = int(claude_cfg.get("max_turn_resumes", 2))
    total_timeout = int(config["executor_timeout_seconds"])
    start_total = time.monotonic()

    continuation_prompt = (
        "Continue the SAME CURRENT TASK from where the previous Claude Code "
        "invocation stopped because it reached --max-turns. Inspect git status "
        "and existing partial artifacts first. Preserve valid completed work; "
        "do not restart, duplicate, or overwrite completed work unnecessarily. "
        "Finish only the remaining requirements and verification checks from "
        "the original task, then return the final executor report. This is a "
        "continuation of the existing task, not a new task."
    )

    attempts: list[dict[str, Any]] = []
    session_id: str | None = None
    resume_count = 0
    current_prompt = prompt

    while True:
        elapsed = time.monotonic() - start_total
        remaining = max(1, int(total_timeout - elapsed))
        if elapsed >= total_timeout:
            synthetic = CommandResult(
                command=[],
                return_code=124,
                stdout=attempts[-1].get("stdout", "") if attempts else "",
                stderr=f"Total Claude executor timeout reached after {total_timeout} seconds.",
                duration_seconds=elapsed,
                timed_out=True,
            )
            return synthetic, attempts, resume_count

        if session_id is None:
            command = [
                claude_cfg["command"],
                *model_args,
                *claude_cfg["extra_args"],
                "-p",
                current_prompt,
            ]
            kind = "initial"
        else:
            # Anthropic documents `claude -p --resume <session-id> "..."` for
            # non-interactive multi-turn continuation. Reusing extra_args keeps
            # the same model/permission/max-turn policy for each chunk.
            command = [
                claude_cfg["command"],
                *model_args,
                *claude_cfg["extra_args"],
                "--resume",
                session_id,
                "-p",
                continuation_prompt,
            ]
            kind = "resume"

        result = run_command(command, project_dir, remaining)
        hit_max_turns, parsed_session_id, parsed = _claude_max_turns_session(result)
        if parsed_session_id:
            session_id = parsed_session_id

        attempts.append(
            {
                "kind": kind,
                "resume_index": resume_count if kind == "resume" else 0,
                "session_id": session_id,
                "command": _masked_command(command, {prompt, continuation_prompt}),
                "return_code": result.return_code,
                "timed_out": result.timed_out,
                "duration_seconds": round(result.duration_seconds, 3),
                "terminal_reason": parsed.get("terminal_reason") if parsed else None,
                "subtype": parsed.get("subtype") if parsed else None,
                "num_turns": parsed.get("num_turns") if parsed else None,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )

        if result.return_code == 0:
            result.duration_seconds = time.monotonic() - start_total
            return result, attempts, resume_count

        if result.timed_out:
            result.duration_seconds = time.monotonic() - start_total
            return result, attempts, resume_count

        if not hit_max_turns:
            result.duration_seconds = time.monotonic() - start_total
            return result, attempts, resume_count

        if not auto_resume:
            result.duration_seconds = time.monotonic() - start_total
            return result, attempts, resume_count

        if session_id is None:
            result.stderr = (
                result.stderr
                + "\nClaude hit max_turns but no session_id was present; cannot auto-resume."
            ).strip()
            result.duration_seconds = time.monotonic() - start_total
            return result, attempts, resume_count

        if resume_count >= max_resumes:
            result.stderr = (
                result.stderr
                + f"\nClaude hit max_turns after {resume_count} automatic resume(s); "
                  f"max_turn_resumes={max_resumes} reached."
            ).strip()
            result.duration_seconds = time.monotonic() - start_total
            return result, attempts, resume_count

        resume_count += 1
        print(
            f"Claude reached --max-turns; automatically resuming session "
            f"{session_id} ({resume_count}/{max_resumes})..."
        )


def run_executor(
    config: dict[str, Any],
    project_dir: Path,
    task: str,
    iteration: int,
    *,
    dispatch_check: Callable[[], None] | None = None,
) -> tuple[CommandResult, str, Path]:
    if dispatch_check is None:
        require_automation_enabled(config)
    else:
        dispatch_check()
    executor = config["executor"]
    tool_config = config[executor]
    context = read_text(CONTEXT_FILE)
    prompt = build_executor_prompt(task, context)

    codex_final_path: Path | None = None
    stdin_text: str | None = None
    claude_attempts: list[dict[str, Any]] | None = None
    claude_resume_count = 0

    if executor == "claude":
        model_name, model_source = configured_model(config, executor)
        command: list[str] = []  # actual commands are captured per Claude attempt
    elif executor == "antigravity":
        model_args = _model_args(config, executor)
        permission_args = (
            ["--dangerously-skip-permissions"]
            if tool_config.get("dangerously_skip_permissions")
            else []
        )
        command = [
            tool_config["command"],
            *permission_args,
            *model_args,
            *tool_config["extra_args"],
            "-p",
            prompt,
        ]
        model_name, model_source = configured_model(config, executor)
    else:  # codex executor
        CODEX_SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        codex_final_path = CODEX_SCRATCH_DIR / f"executor_{iteration:04d}_final.txt"
        if codex_final_path.exists():
            codex_final_path.unlink()

        approval = str(tool_config["executor_approval_policy"])
        sandbox = str(tool_config["executor_sandbox"])
        command = [
            tool_config["command"],
            "--ask-for-approval",
            approval,
            "exec",
            *_model_args(config, "codex", role="executor"),
            "--sandbox",
            sandbox,
            "--skip-git-repo-check",
            "--output-last-message",
            str(codex_final_path),
            *tool_config["executor_extra_args"],
            "-",
        ]
        stdin_text = prompt
        model_name, model_source = configured_model(config, "codex", role="executor")

    print(f"\n[{iteration}] Executor: {executor}")
    print(f"[{iteration}] Executor model: {model_name} [{model_source}]")
    if executor == "claude":
        print(
            f"[{iteration}] Claude max-turn auto-resume: "
            f"{tool_config.get('auto_resume_on_max_turns', True)} "
            f"(max resumes={tool_config.get('max_turn_resumes', 2)})"
        )
    if executor == "codex":
        print(
            f"[{iteration}] Codex policy: sandbox={tool_config['executor_sandbox']}, "
            f"approval={tool_config['executor_approval_policy']}"
        )
    print(f"[{iteration}] Running implementation task...")

    if executor == "claude":
        result, claude_attempts, claude_resume_count = _run_claude_executor_with_resume(
            config, project_dir, prompt
        )
    else:
        result = run_command(
            command,
            project_dir,
            int(config["executor_timeout_seconds"]),
            stdin_text=stdin_text,
        )

    if executor == "codex" and codex_final_path is not None and codex_final_path.is_file():
        normalized = codex_final_path.read_text(encoding="utf-8").strip()
        if not normalized:
            normalized = normalize_executor_output(executor, result.stdout, result.stderr)
    else:
        normalized = normalize_executor_output(executor, result.stdout, result.stderr)

    runtime_model = (
        _parse_codex_runtime_model(result.stdout, result.stderr)
        if executor == "codex"
        else None
    )
    effective_model = runtime_model or model_name
    effective_model_source = "Codex runtime banner" if runtime_model else model_source

    if executor == "claude" and claude_attempts:
        logged_command = claude_attempts[-1]["command"]
        prompt_transport = "argv"
    else:
        logged_command = _masked_command(command, {prompt})
        prompt_transport = "stdin" if stdin_text is not None else "argv"

    if executor == "codex":
        logged_stderr, stderr_meta = _prepare_codex_stderr_for_log(
            config,
            result.stderr,
            iteration=iteration,
            log_stem="executor_codex",
            success=result.return_code == 0 and not result.timed_out,
        )
    else:
        logged_stderr = result.stderr
        stderr_meta = {}

    report_payload: dict[str, Any] = {
        "timestamp": utc_now(),
        "iteration": iteration,
        "executor": executor,
        "model": effective_model,
        "model_source": effective_model_source,
        "task": task,
        "command": logged_command,
        "prompt_transport": prompt_transport,
        "return_code": result.return_code,
        "timed_out": result.timed_out,
        "duration_seconds": round(result.duration_seconds, 3),
        "stdout": result.stdout,
        "stderr": logged_stderr,
        **stderr_meta,
        "normalized_report": normalized,
    }
    if executor == "claude":
        report_payload["claude_auto_resume"] = {
            "enabled": bool(tool_config.get("auto_resume_on_max_turns", True)),
            "max_turn_resumes": int(tool_config.get("max_turn_resumes", 2)),
            "resume_count": claude_resume_count,
            "attempt_count": len(claude_attempts or []),
        }
        report_payload["claude_attempts"] = claude_attempts or []

    report_path = RUNS_DIR / f"{iteration:04d}_executor_{executor}.json"
    write_json(report_path, report_payload)

    if runtime_model and runtime_model != model_name:
        print(f"[{iteration}] Executor runtime model: {runtime_model}")
    if executor == "claude" and claude_resume_count:
        print(f"[{iteration}] Claude automatic resumes used: {claude_resume_count}")
    print(f"[{iteration}] Executor exit={result.return_code}, {result.duration_seconds:.1f}s")
    print(f"[{iteration}] Saved: {report_path.relative_to(APP_DIR)}")
    return result, normalized, report_path


def load_recent_history(limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not HISTORY_FILE.is_file():
        return []
    lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                items.append(item)
        except json.JSONDecodeError:
            continue
    return items


def make_planner_prompt(
    config: dict[str, Any],
    iteration: int,
    executor: str,
    task: str,
    executor_result: CommandResult,
    executor_report: str,
    extra_instruction: str | None = None,
) -> str:
    planner_rules = read_text(PLANNER_PROMPT_FILE)
    context = read_text(CONTEXT_FILE)
    max_chars = int(config["max_result_chars_for_planner"])
    truncated_report = executor_report[-max_chars:]
    history = load_recent_history(int(config["recent_history_items_for_planner"]))

    history_for_prompt = [
        {
            "iteration": item.get("iteration"),
            "executor": item.get("executor"),
            "task": item.get("task"),
            "executor_return_code": item.get("executor_return_code"),
            "planner_status": item.get("planner", {}).get("status") if isinstance(item.get("planner"), dict) else None,
            "planner_analysis": item.get("planner", {}).get("analysis") if isinstance(item.get("planner"), dict) else None,
            "next_task": item.get("planner", {}).get("next_task") if isinstance(item.get("planner"), dict) else None,
        }
        for item in history
    ]

    continuation_policy = f"""
=== FINITE RESEARCH COMPLETION POLICY ===
Read docs/strategy/00_current_research_policy.md and the closure registry before proposing work.
Stop when the registered batch reaches its verdict, budget, or a blocking gate.
Uncertainty, a missing profitable strategy, and an unopened OOS are not reasons to extend a batch.
Use status="done" for the bounded batch, with unresolved questions and unavailable evidence recorded.
Use status="continue" only for ONE task within an explicitly registered remaining scope and budget.
Do not replenish budgets with a new ID, a new family label, or another loop invocation.
S2 information is already known information; a revised count gate is a new specification.
Never request OOS or Final Holdout merely to satisfy a completion checklist.
Current iteration: {iteration}; iteration counts are ceilings, never minimum research quotas.
""".strip()

    extra = ""
    if extra_instruction and extra_instruction.strip():
        extra = (
            "\n\n=== ADDITIONAL ORCHESTRATOR INSTRUCTION ===\n"
            + extra_instruction.strip()
        )

    return (
        f"{planner_rules}\n\n"
        f"{continuation_policy}{extra}\n\n"
        "=== RESEARCH CONTEXT ===\n"
        f"{context}\n\n"
        "=== RECENT HISTORY ===\n"
        f"{json.dumps(history_for_prompt, ensure_ascii=False, indent=2)}\n\n"
        "=== LATEST ITERATION ===\n"
        f"Iteration: {iteration}\n"
        f"Executor: {executor}\n"
        f"Executor return code: {executor_result.return_code}\n"
        f"Executor timed out: {executor_result.timed_out}\n"
        "Task sent to executor:\n"
        f"{task}\n\n"
        "Executor report:\n"
        f"{truncated_report}\n"
    )


def run_planner(
    config: dict[str, Any],
    project_dir: Path,
    iteration: int,
    executor: str,
    task: str,
    executor_result: CommandResult,
    executor_report: str,
    *,
    run_kind: str = "planner",
    extra_instruction: str | None = None,
    schema_path: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    CODEX_SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    safe_kind = re.sub(r"[^a-zA-Z0-9_-]+", "_", run_kind).strip("_") or "planner"
    final_path = CODEX_SCRATCH_DIR / f"planner_{iteration:04d}_{safe_kind}_final.json"
    if final_path.exists():
        final_path.unlink()

    prompt = make_planner_prompt(
        config,
        iteration,
        executor,
        task,
        executor_result,
        executor_report,
        extra_instruction=extra_instruction,
    )
    codex_cfg = config["codex"]
    planner_model, planner_model_source = configured_model(config, "codex")
    command = [
        codex_cfg["command"],
        "exec",
        *_model_args(config, "codex"),
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--output-schema",
        str(schema_path or PLANNER_SCHEMA),
        "--output-last-message",
        str(final_path),
        *codex_cfg["extra_args"],
        "-",
    ]

    labels = {
        "planner": "Planner",
        "done_guard": "Premature-done guard",
        "done_confirmation": "Done confirmation",
    }
    label = labels.get(run_kind, run_kind.replace("_", " ").title())
    print(f"[{iteration}] {label}: codex")
    print(f"[{iteration}] {label} model: {planner_model} [{planner_model_source}]")
    # Feed the planner prompt through UTF-8 stdin instead of argv. This avoids
    # Windows .cmd/cmd.exe command-line length limits for large executor reports.
    planner_env, planner_env_meta = _planner_codex_env(config)
    result = run_command(
        command,
        project_dir,
        int(config["planner_timeout_seconds"]),
        stdin_text=prompt,
        env=planner_env,
    )
    logged_stderr, stderr_meta = _prepare_codex_stderr_for_log(
        config,
        result.stderr,
        iteration=iteration,
        log_stem=f"{safe_kind}_codex",
        success=result.return_code == 0 and not result.timed_out,
    )
    # Preserve failed calls and malformed final responses before validation.
    write_json(
        RUNS_DIR / f"{iteration:04d}_{safe_kind}_response.json",
        {
            "return_code": result.return_code,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
            "stdout": result.stdout,
            "stderr": logged_stderr,
            **stderr_meta,
            "final_text": final_path.read_text(encoding="utf-8") if final_path.is_file() else None,
        },
    )
    if result.return_code != 0:
        diagnostic = (logged_stderr or result.stdout).strip()
        raise OrchestratorError(
            f"Codex {label.lower()} failed with exit code {result.return_code}.\n{diagnostic}"
        )
    if not final_path.is_file():
        raise OrchestratorError(
            "Codex completed but --output-last-message file was not created. "
            "Run `codex exec --help` and verify your Codex CLI is current."
        )

    final_text = final_path.read_text(encoding="utf-8").strip()
    try:
        decision = json.loads(final_text)
    except json.JSONDecodeError as exc:
        raise OrchestratorError(
            f"Codex final message was not valid JSON despite --output-schema: {exc}\n{final_text}"
        ) from exc

    if decision.get("status") not in ({"continue", "done", "blocked"} if schema_path else {"continue", "done"}):
        raise OrchestratorError(f"Invalid planner status: {decision.get('status')!r}")
    if not isinstance(decision.get("analysis"), str):
        raise OrchestratorError("Planner response missing string field: analysis")
    if not isinstance(decision.get("next_task"), str):
        raise OrchestratorError("Planner response missing string field: next_task")
    if decision["status"] == "continue" and not decision["next_task"].strip():
        raise OrchestratorError("Planner returned continue with an empty next_task")

    runtime_model = _parse_codex_runtime_model(result.stdout, result.stderr)
    effective_model = runtime_model or planner_model

    if run_kind == "planner":
        planner_log = RUNS_DIR / f"{iteration:04d}_planner_codex.json"
    else:
        planner_log = RUNS_DIR / f"{iteration:04d}_{safe_kind}_codex.json"
    write_json(
        planner_log,
        {
            "timestamp": utc_now(),
            "iteration": iteration,
            "run_kind": run_kind,
            "model": effective_model,
            "model_source": "Codex runtime banner" if runtime_model else planner_model_source,
            "return_code": result.return_code,
            "duration_seconds": round(result.duration_seconds, 3),
            "planner_workdir": str(project_dir),
            "planner_environment": planner_env_meta,
            "stdout": result.stdout,
            "stderr": logged_stderr,
            **stderr_meta,
            "decision": decision,
        },
    )

    if runtime_model and runtime_model != planner_model:
        print(f"[{iteration}] {label} runtime model: {runtime_model}")
    print(f"[{iteration}] {label} decision: {decision['status']}")
    print(f"[{iteration}] {label} analysis: {decision['analysis']}")
    if decision["status"] == "continue":
        print(f"[{iteration}] {label} next task: {decision['next_task']}")
    return decision, planner_log


def apply_done_policy(
    config: dict[str, Any],
    project_dir: Path,
    iteration: int,
    executor: str,
    task: str,
    executor_result: CommandResult,
    executor_report: str,
    initial_decision: dict[str, Any],
    initial_log: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retain the initial decision and optionally review bounded batch completion.

    Minimum iterations never override a valid stop. Any proposed continuation
    must remain inside the registered scope and budget.
    """
    attempts: list[dict[str, Any]] = [
        {
            "kind": "planner",
            "log_path": str(initial_log.relative_to(APP_DIR)),
            "decision": initial_decision,
        }
    ]
    decision = initial_decision
    if decision["status"] != "done":
        return decision, attempts

    # A bounded research stop is valid at any iteration. Legacy minimum-iteration
    # settings remain parseable but cannot force another experiment.
    if bool(config["done_confirmation_required"]):
        print(f"[{iteration}] Planner proposed done; running adversarial completion review.")
        instruction = f"""
The primary planner proposed terminating the research with this decision:
{json.dumps(decision, ensure_ascii=False, indent=2)}

Review completion of the REGISTERED BATCH, not whether all scientific uncertainty is gone.
Check evidence, stopping rules and remaining budget in docs/strategy/00_current_research_policy.md.
A closed family, exhausted budget or failed gate must remain stopped.
Return status="continue" only if a concrete required task remains inside the frozen scope and budget.
An unopened OOS, lack of a profitable strategy or a possible new hypothesis cannot force continuation.
Otherwise return status="done", preserve unresolved evidence, and explain the batch outcome.
""".strip()
        decision, confirm_log = run_planner(
            config,
            project_dir,
            iteration,
            executor,
            task,
            executor_result,
            executor_report,
            run_kind="done_confirmation",
            extra_instruction=instruction,
        )
        attempts.append(
            {
                "kind": "done_confirmation",
                "log_path": str(confirm_log.relative_to(APP_DIR)),
                "decision": decision,
            }
        )

    return decision, attempts


def save_state(state: State) -> None:
    write_json(STATE_FILE, asdict(state))


def load_state(config: dict[str, Any], reset: bool) -> State:
    if reset and RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    CODEX_SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    if STATE_FILE.is_file():
        raw = load_json(STATE_FILE)
        try:
            state = State(**raw)
        except TypeError as exc:
            raise OrchestratorError(f"Invalid state file {STATE_FILE}: {exc}") from exc
        if state.executor != config["executor"]:
            raise OrchestratorError(
                "The existing run was started with executor "
                f"{state.executor!r}, but config now selects {config['executor']!r}. "
                "Use --reset to start a new run after switching executors."
            )
        return state

    initial_task = read_text(INITIAL_TASK_FILE)
    state = State(
        iteration=1,
        phase="executor_pending",
        current_task=initial_task,
        executor=config["executor"],
    )
    save_state(state)
    return state


def load_saved_executor_report(path_text: str) -> tuple[CommandResult, str]:
    path = Path(path_text)
    if not path.is_absolute():
        path = (APP_DIR / path).resolve()
    data = load_json(path)
    result = CommandResult(
        command=[],
        return_code=int(data.get("return_code", 1)),
        stdout=str(data.get("stdout", "")),
        stderr=str(data.get("stderr", "")),
        duration_seconds=float(data.get("duration_seconds", 0)),
        timed_out=bool(data.get("timed_out", False)),
    )
    report = str(data.get("normalized_report", ""))
    return result, report


def orchestrate(config: dict[str, Any], project_dir: Path, reset: bool, once: bool) -> None:
    require_automation_enabled(config)
    state = load_state(config, reset=reset)
    max_iterations = int(config["max_iterations"])

    if state.phase == "done":
        print(f"Run already completed: {state.done_reason or 'done'}")
        print("Use --reset to start over.")
        return

    while state.iteration <= max_iterations:
        iteration = state.iteration
        task = state.current_task

        if state.phase == "executor_pending":
            result, report, report_path = run_executor(config, project_dir, task, iteration)
            state.last_executor_report_path = str(report_path.relative_to(APP_DIR))

            if result.return_code != 0 and bool(config["stop_on_executor_error"]):
                # Keep the task pending so a rerun retries the same task after the
                # user fixes the underlying problem. The failed report remains on disk.
                state.phase = "executor_pending"
                save_state(state)
                raise OrchestratorError(
                    f"Executor failed at iteration {iteration} (exit={result.return_code}). "
                    f"Inspect {report_path}. After fixing the cause, rerun to retry the same task."
                )

            state.phase = "planner_pending"
            save_state(state)
        elif state.phase == "planner_pending":
            if not state.last_executor_report_path:
                raise OrchestratorError("State is planner_pending but no executor report is recorded")
            result, report = load_saved_executor_report(state.last_executor_report_path)
        else:
            raise OrchestratorError(f"Unknown state phase: {state.phase}")

        initial_decision, planner_log = run_planner(
            config,
            project_dir,
            iteration,
            state.executor,
            task,
            result,
            report,
        )
        decision, planner_attempts = apply_done_policy(
            config,
            project_dir,
            iteration,
            state.executor,
            task,
            result,
            report,
            initial_decision,
            planner_log,
        )

        append_jsonl(
            HISTORY_FILE,
            {
                "timestamp": utc_now(),
                "iteration": iteration,
                "executor": state.executor,
                "task": task,
                "executor_return_code": result.return_code,
                "executor_report_path": state.last_executor_report_path,
                "planner_log_path": str(planner_log.relative_to(APP_DIR)),
                "planner_attempts": planner_attempts,
                "planner": decision,
            },
        )

        if decision["status"] == "done":
            state.phase = "done"
            state.done_reason = decision["analysis"]
            save_state(state)
            print("\nOrchestration completed by planner decision.")
            return

        state.iteration += 1
        state.current_task = decision["next_task"].strip()
        state.phase = "executor_pending"
        state.last_executor_report_path = None
        save_state(state)

        if once:
            print("\n--once: completed one executor + planner cycle.")
            print(f"Next task is saved in {STATE_FILE.relative_to(APP_DIR)}")
            return

    state.phase = "done"
    state.done_reason = f"Reached max_iterations={max_iterations}"
    save_state(state)
    print(f"\nStopped: reached max_iterations={max_iterations}.")


def run_bounded_task(
    config: dict[str, Any], project_dir: Path, batch: Batch,
    *, once: bool = False, close_reason: str | None = None,
) -> dict[str, Any]:
    """Reuse CLI transports inside a reserved finite task and isolated log directory."""
    global RUNS_DIR, CODEX_SCRATCH_DIR, HISTORY_FILE
    old_paths = RUNS_DIR, CODEX_SCRATCH_DIR, HISTORY_FILE
    RUNS_DIR = batch.directory / "runs"
    CODEX_SCRATCH_DIR = batch.directory / "codex_scratch"
    HISTORY_FILE = batch.directory / "history.jsonl"

    class BoundedTransport:
        def validate(self, attempt: int) -> dict[str, Any]:
            details = []
            passed = True
            for number, name in enumerate(batch.spec.validation_scripts):
                batch.verify()
                result = run_command(
                    [sys.executable, str(project_dir / name), "--artifacts",
                     str(project_dir / "docs/strategy/plans" / batch.spec.task_id)],
                    project_dir, min(120, int(config["executor_timeout_seconds"])),
                )
                write_json(RUNS_DIR / f"{attempt:04d}_validation_{number}.json", asdict(result))
                passed = passed and result.return_code == 0 and not result.timed_out
                details.append({"script": name, "exit": result.return_code,
                                "stdout": result.stdout, "stderr": result.stderr})
                batch.verify()
            return {"passed": passed, "details": details}

        def execute(self, task: str, attempt: int, check: Callable[[], None]) -> str:
            check()
            if batch.spec.mode == "design":
                result, report, _ = run_executor(
                    config, project_dir, task, attempt, dispatch_check=check,
                )
                if result.timed_out:
                    raise DispatchUncertainError(f"Executor timeout; inspect {RUNS_DIR} and child processes")
                if result.return_code != 0 or result.timed_out:
                    raise BatchError(f"Executor exit={result.return_code}; see {RUNS_DIR}")
                return report
            # No agent receives authority to invent a market command or carry a reservation.
            # The registered controller validates and reserves the frozen manifest itself.
            manifest = project_dir / str(batch.spec.manifest_file)
            run_id = load_json(manifest).get("run_id")
            if not isinstance(run_id, str):
                raise BatchError("Registered manifest has no run_id")
            outputs = []
            for verb, argument in (("check-execution", str(manifest)),
                                   ("execute", str(manifest)), ("verify-execution", run_id)):
                check()
                result = run_command(
                    [sys.executable, "-m", "n225m_bt.cli", "research", verb, argument],
                    project_dir, int(config["executor_timeout_seconds"]),
                )
                write_json(RUNS_DIR / f"{attempt:04d}_{verb}.json", asdict(result))
                if result.return_code != 0 or result.timed_out:
                    raise BatchError(f"Registered {verb} failed; inspect execution-status and {RUNS_DIR}")
                outputs.append(result.stdout)
            return "\n".join(outputs)

        def plan(self, task: str, report: str, attempt: int) -> dict[str, Any]:
            result = CommandResult([], 0, "", "", 0)
            auto_instruction = ""
            if batch.spec.schema_version == 2:
                auto_instruction = (
                    " AUTONOMOUS v2: HOLD is NOT a reason to stop while scoped work remains. "
                    "Return every completion criterion with artifact evidence, and remaining_work. "
                    "For every complete criterion, criteria[].evidence MUST be a nonempty list of "
                    "exact declared artifact paths (for example, docs/strategy/plans/TASK/.../design.md); "
                    "put explanatory prose in analysis, never in evidence. "
                    "Repairable defects, incomplete calendar documents, missing stage-irrelevant "
                    "capital/DD values, and failed validation must continue automatically. "
                    "Only use blocked for an evidenced major obstacle with attempted remedies. "
                    "Only use done when every criterion is complete and validation passed. "
                    "The controller performs a second completion audit. "
                    "No fixed call cap applies when the limit is null; research scope stays fixed."
                )
            decision, _ = run_planner(
                config, project_dir, attempt, config["executor"], task, result, report,
                run_kind=f"bounded_review_{attempt:04d}",
                extra_instruction=(
                    "This is a reserved bounded task, not the retired legacy loop. "
                    "Review the declared artifacts and frozen scope. HOLD/CLOSE with complete "
                    "records can finish. Continue only to complete this same task; no new "
                    "hypothesis, market access, scope/budget changes, or task dispatch. "
                    f"Mode={batch.spec.mode}; limits executor={batch.spec.max_executor_calls}, "
                    f"planner={batch.spec.max_planner_calls}. In registered mode review only "
                    "the frozen run outputs; do not open unrelated results or market data."
                    + auto_instruction
                ),
                schema_path=project_dir / "planner_auto_schema.json" if batch.spec.schema_version == 2 else None,
            )
            append_jsonl(HISTORY_FILE, {
                "iteration": attempt, "executor": config["executor"], "task": task,
                "executor_return_code": 0, "planner": decision,
            })
            return decision

    try:
        return batch.run(BoundedTransport(), once=once, close_reason=close_reason)
    finally:
        RUNS_DIR, CODEX_SCRATCH_DIR, HISTORY_FILE = old_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Codex planner + configurable Claude Code / Antigravity / Codex executor orchestrator"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to config.json (default: ./config.json next to this script)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check configuration and CLI availability, then exit",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete orchestration state/logs and start a new run",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one executor + planner cycle and save the next task",
    )
    parser.add_argument("--batch", type=Path, help="Frozen bounded task JSON (defaults to config.bounded_task)")
    parser.add_argument("--status", action="store_true", help="Read bounded task state without dispatch")
    parser.add_argument("--close-interrupted", metavar="REASON", help="Close an orphaned bounded dispatch without refunding its budget")
    parser.add_argument(
        "--recover-interrupted",
        action="store_true",
        help="After inspecting child processes, revalidate an interrupted v2 task and resume Planner review without replaying its Executor",
    )
    parser.add_argument(
        "--recover-planner-block",
        action="store_true",
        help="After resolving an external Planner failure, revalidate and retry Planner only; research BLOCKED decisions cannot be reopened",
    )
    return parser.parse_args()


def main() -> int:
    # Windows redirected consoles may use cp932; arbitrary agent output must not
    # abort a persisted task merely because one character is not representable.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")
    args = parse_args()
    config_path = args.config.resolve()
    try:
        config = normalize_config(load_json(config_path))
        project_dir = resolve_project_dir(config, config_path)

        batch_name = args.batch or config.get("bounded_task")
        if batch_name:
            if args.reset:
                raise BatchError("--reset is disabled for bounded tasks; budgets and logs must be retained")
            if sum((bool(args.check), bool(args.status), args.close_interrupted is not None, bool(getattr(args, "recover_interrupted", False)), bool(getattr(args, "recover_planner_block", False)))) > 1:
                raise BatchError("Use only one recovery/status/check action at a time")
            # One Executor process per reservation; no hidden Claude auto-resumes.
            config["claude"]["auto_resume_on_max_turns"] = False
            batch_path = Path(batch_name)
            if not batch_path.is_absolute():
                batch_path = project_dir / batch_path
            batch = Batch(project_dir, batch_path, config)
            limit_text = (f"executor={batch.spec.max_executor_calls or 'scope completion'}; "
                          f"planner={batch.spec.max_planner_calls or 'scope completion'}")
            if args.status:
                print(json.dumps({k: v for k, v in batch.status(historical=True).items() if k not in {"report", "feedback"}}, ensure_ascii=False, indent=2))
                return 0
            if args.check:
                batch.verify()
                # A diagnostic check must remain available after a controller
                # upgrade so an interrupted task can be explicitly recovered.
                state = batch.status(historical=True)
                print(f"Bounded task: {batch.spec.task_id}; mode={batch.spec.mode}; phase={state['phase']}; {limit_text}")
                return 0 if run_checks(config, project_dir, project_dir / batch.spec.task_file) else 2
            if getattr(args, "recover_interrupted", False):
                recovered = batch.recover_interrupted()
                print(
                    "Recovered interrupted task without replaying Executor: "
                    f"executor={recovered['executor_calls']}; planner={recovered['planner_calls']}"
                )
            if getattr(args, "recover_planner_block", False):
                recovered = batch.recover_planner_block()
                print(
                    "Recovered Planner infrastructure block without replaying Executor: "
                    f"executor={recovered['executor_calls']}; planner={recovered['planner_calls']}"
                )
            if args.close_interrupted is None and not run_checks(config, project_dir, project_dir / batch.spec.task_file):
                return 2
            print(f"Bounded task: {batch.spec.task_id}; mode={batch.spec.mode}; {limit_text}")
            state = run_bounded_task(config, project_dir, batch, once=args.once, close_reason=args.close_interrupted)
            print(json.dumps({k: v for k, v in state.items() if k not in {"report", "feedback"}}, ensure_ascii=False, indent=2))
            return 0 if state["phase"] in {"DONE", "EXECUTOR_PENDING", "PLANNER_PENDING"} else 1

        if args.status or args.close_interrupted is not None:
            raise BatchError("--status/--close-interrupted requires a bounded task")

        if args.check:
            return 0 if run_checks(config, project_dir) else 2

        if not run_checks(config, project_dir):
            print("\nFix the missing prerequisites above before running.", file=sys.stderr)
            return 2

        orchestrate(config, project_dir, reset=args.reset, once=args.once)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted. State was preserved; inspect research execution-status before further work.", file=sys.stderr)
        return 130
    except (OrchestratorError, BatchError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
