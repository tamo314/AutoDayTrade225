"""Finite, content-addressed research dispatch with a durable reservation ledger.

This is an accidental-execution guard, not an OS sandbox for arbitrary Python.
OOS and Final Holdout dispatch are deliberately unsupported.
"""

from __future__ import annotations

import json
import runpy
import sqlite3
import sys
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from n225m_bt.research.governance import AccessDeniedError

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")]
T = TypeVar("T")
POLICY_PATH = "config/research_execution.json"
LEDGER_PATH = "results/research_control/ledger.sqlite3"
START, END = date(2021, 1, 1), date(2025, 6, 30)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileSeal(FrozenModel):
    path: str
    sha256: Digest


class RunManifest(FrozenModel):
    schema_version: Literal[1] = 1
    batch_id: Identifier
    family_id: Identifier
    study_id: Identifier
    spec_version: Identifier
    run_id: Identifier
    stage: Literal["S2", "S3", "S4"]
    split: Literal["development"] = "development"
    trade_date_start: date = START
    trade_date_end: date = END
    entrypoint: str
    # kwargs are frozen with the manifest; callers cannot append runtime overrides.
    arguments: dict[str, str] = Field(default_factory=dict)
    conditions: tuple[Identifier, ...] = Field(min_length=1)
    seed: int
    stop_rule: str = Field(min_length=1)
    known_record_keys: tuple[str, ...] = ()
    specification: FileSeal
    evidence: tuple[FileSeal, ...] = Field(min_length=1)
    source_tree_sha256: Digest
    config_files: tuple[FileSeal, ...] = Field(min_length=1)
    input_root: str
    input_files: tuple[FileSeal, ...] = Field(min_length=1)
    output: str

    @model_validator(mode="after")
    def fixed_scope(self) -> RunManifest:
        if (self.trade_date_start, self.trade_date_end) != (START, END):
            raise ValueError("only the fixed Development trade_date interval is supported")
        if len(set(self.conditions)) != len(self.conditions):
            raise ValueError("condition identifiers must be unique")
        for seals in (self.config_files, self.input_files, self.evidence):
            if len({s.path for s in seals}) != len(seals):
                raise ValueError("duplicate sealed paths")
        return self


class FamilyBudget(FrozenModel):
    max_attempts: int = Field(ge=0)
    max_specs: int = Field(ge=0, le=2)
    max_conditions: int = Field(ge=0)


class BatchBudget(FrozenModel):
    max_attempts: int = Field(ge=0)
    max_specs: int = Field(ge=0, le=3)
    max_conditions: int = Field(ge=0)
    stop_reason: str | None = None


class Grant(FrozenModel):
    manifest_sha256: Digest
    review: FileSeal
    # Explicitly reviewed nested entrypoints, e.g. campaign -> schema-v2 preflight.
    entrypoints: tuple[str, ...] = Field(min_length=1)
    reopened_family: bool = False


class ReviewReceipt(FrozenModel):
    manifest_sha256: Digest
    decision: Literal["APPROVED"]
    reason: str = Field(min_length=1)
    specification: Literal["PASS"]
    input_contract: Literal["PASS", "PASS_LIMITED"]
    causality: Literal["PASS"]
    unknown_exit_null: Literal["PASS"]
    synthetic_calibration: Literal["PASS"]
    reopened_family: bool = False


class ExecutionPolicy(FrozenModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    market_execution_enabled: bool = False
    registry: FileSeal
    families: dict[str, FamilyBudget] = Field(default_factory=dict)
    batches: dict[str, BatchBudget] = Field(default_factory=dict)
    grants: tuple[Grant, ...] = ()
    max_total_specs: int = Field(default=3, ge=0, le=3)

    @model_validator(mode="after")
    def bounded_authority(self) -> ExecutionPolicy:
        if len([f for f in self.families.values() if f.max_attempts > 0]) > 3:
            raise ValueError("at most three reopened families per reviewed authority")
        if len({g.manifest_sha256 for g in self.grants}) != len(self.grants):
            raise ValueError("duplicate manifest grants")
        return self


def digest_file(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def canonical_digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def manifest_digest(manifest: RunManifest) -> str:
    return canonical_digest(manifest.model_dump(mode="json"))


def within(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise AccessDeniedError(f"workspace-relative path required: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise AccessDeniedError(f"path escapes workspace: {relative}")
    return resolved


def verify_seal(root: Path, seal: FileSeal) -> None:
    if digest_file(within(root, seal.path)) != seal.sha256:
        raise AccessDeniedError(f"hash changed: {seal.path}")


def source_tree_digest(root: Path) -> str:
    """Include imported helpers and added/deleted source files, not just the entry script."""
    paths = sorted(
        {
            *root.joinpath("src").rglob("*.py"),
            *root.joinpath("scripts").rglob("*.py"),
            *root.joinpath("tests").rglob("*.py"),
            *(
                root / name
                for name in ("orchestrator.py", "pyproject.toml", "uv.lock")
                if (root / name).is_file()
            ),
        }
    )
    if not paths:
        raise AccessDeniedError("empty source tree")
    return canonical_digest({p.relative_to(root).as_posix(): digest_file(p) for p in paths})


def development_files(root: Path) -> list[Path]:
    """Enumerate allowed calendar partitions only; never glob OOS/Holdout namespaces."""
    paths: list[Path] = []
    for year in range(2020, 2026):
        for month in range(1, 13):
            if (2020, 12) <= (year, month) <= (2025, 6):
                paths.extend(
                    sorted((root / f"year={year}" / f"month={month:02d}").glob("*.parquet"))
                )
    return paths


def _verify_inputs(root: Path, manifest: RunManifest) -> None:
    input_root = within(root, manifest.input_root)
    actual = {p.resolve() for p in development_files(input_root)}
    declared = {within(root, s.path) for s in manifest.input_files}
    if not actual or actual != declared or len(declared) != len(manifest.input_files):
        raise AccessDeniedError("Development partition set differs from frozen input manifest")
    for seal in manifest.input_files:
        verify_seal(root, seal)


@dataclass(frozen=True)
class Permit:
    root: Path
    manifest: RunManifest
    grant: Grant
    policy_sha256: str


_ACTIVE: ContextVar[Permit | None] = ContextVar("research_execution_permit", default=None)


def require_entry(entrypoint: str) -> Permit:
    permit = _ACTIVE.get()
    if permit is None or entrypoint not in permit.grant.entrypoints:
        raise AccessDeniedError(f"unreserved research entry: {entrypoint}; use research execute")
    if digest_file(permit.root / POLICY_PATH) != permit.policy_sha256:
        raise AccessDeniedError("execution policy changed during run")
    with sqlite3.connect(permit.root / LEDGER_PATH) as connection:
        row = connection.execute(
            "SELECT status,manifest_hash FROM runs WHERE run_id=?", (permit.manifest.run_id,)
        ).fetchone()
    if row is None or row[0] != "RUNNING" or row[1] != manifest_digest(permit.manifest):
        raise AccessDeniedError("run reservation is no longer active")
    for seal in (
        permit.manifest.specification,
        *permit.manifest.config_files,
        *permit.manifest.evidence,
    ):
        verify_seal(permit.root, seal)
    if source_tree_digest(permit.root) != permit.manifest.source_tree_sha256:
        raise AccessDeniedError("source changed before runner/data access")
    return permit


def require_market_read(root: Path, split: str) -> Permit:
    if split != "development":
        raise AccessDeniedError("OOS and Final Holdout are locked in the shared dispatcher")
    permit = _ACTIVE.get()
    if permit is None:
        raise AccessDeniedError("market read requires a reserved research manifest")
    require_entry(permit.manifest.entrypoint)
    if root.resolve() != within(permit.root, permit.manifest.input_root):
        raise AccessDeniedError("input root differs from reserved manifest")
    _verify_inputs(permit.root, permit.manifest)
    with sqlite3.connect(permit.root / LEDGER_PATH) as connection:
        connection.execute(
            "INSERT INTO access_events VALUES (?, ?, ?, ?)",
            (
                permit.manifest.run_id,
                split,
                str(root.resolve()),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return permit


def require_frozen_file(path: Path) -> None:
    permit = _ACTIVE.get()
    if permit is None:
        raise AccessDeniedError("configuration read requires a reserved manifest")
    seals = (
        permit.manifest.specification,
        *permit.manifest.config_files,
        *permit.manifest.evidence,
    )
    seal = next((s for s in seals if within(permit.root, s.path) == path.resolve()), None)
    if seal is None:
        raise AccessDeniedError(f"unsealed configuration/specification: {path}")
    verify_seal(permit.root, seal)


def require_frozen_config(config_dir: Path) -> None:
    for name in ("instrument", "sessions", "data", "backtest"):
        require_frozen_file(config_dir / f"{name}.yaml")


class ResearchController:
    """One workspace authority and SQLite ledger; no caller-selected alternate ledger."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.ledger = self.root / LEDGER_PATH

    @contextmanager
    def _execution_lock(self) -> Iterator[None]:
        """OS releases this lock on process death; an orphan reservation remains in SQLite."""
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with (self.ledger.parent / "active.lock").open("a+b") as stream:
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
                raise AccessDeniedError(
                    "another research process holds the execution lock"
                ) from exc
            try:
                yield
            finally:
                stream.seek(0)
                if sys.platform == "win32":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def validate(self, manifest: RunManifest, *, inputs: bool = True) -> Permit:
        # Also reject invalid model_copy/model_construct instances supplied by Python callers.
        manifest = RunManifest.model_validate_json(manifest.model_dump_json())
        policy_path = self.root / POLICY_PATH
        policy_bytes = policy_path.read_bytes()
        policy = ExecutionPolicy.model_validate_json(policy_bytes)
        if not policy.market_execution_enabled:
            raise AccessDeniedError("market execution is paused by the workspace policy")
        grant = next(
            (g for g in policy.grants if g.manifest_sha256 == manifest_digest(manifest)), None
        )
        if grant is None:
            raise AccessDeniedError("manifest has no exact reviewed grant")
        if manifest.entrypoint not in grant.entrypoints:
            raise AccessDeniedError("entrypoint is not approved")
        family = policy.families.get(manifest.family_id)
        batch = policy.batches.get(manifest.batch_id)
        if family is None or batch is None or batch.stop_reason:
            raise AccessDeniedError("unknown/closed family or batch")
        if (
            min(
                family.max_attempts,
                family.max_specs,
                family.max_conditions,
                batch.max_attempts,
                batch.max_specs,
                batch.max_conditions,
            )
            <= 0
        ):
            raise AccessDeniedError("closed family/batch has no remaining budget")
        verify_seal(self.root, policy.registry)
        registry = json.loads(within(self.root, policy.registry.path).read_text(encoding="utf-8"))
        records = {r["key"]: r for r in registry["records"]}
        for key in manifest.known_record_keys:
            if key not in records or records[key]["family_id"] != manifest.family_id:
                raise AccessDeniedError("known parent record has a different or unknown family")
        closed = {f["family_id"] for f in registry["families"] if f["status"].startswith("CLOSED")}
        if manifest.family_id in closed and not grant.reopened_family:
            raise AccessDeniedError("closed family requires an explicit reviewed reopening")
        if manifest.family_id in closed and not manifest.known_record_keys:
            raise AccessDeniedError("reopening requires identified prior results")
        for seal in (
            grant.review,
            manifest.specification,
            *manifest.evidence,
            *manifest.config_files,
        ):
            verify_seal(self.root, seal)
        review = ReviewReceipt.model_validate_json(
            within(self.root, grant.review.path).read_bytes()
        )
        if (
            review.manifest_sha256 != manifest_digest(manifest)
            or review.reopened_family != grant.reopened_family
        ):
            raise AccessDeniedError("review receipt does not authorize this manifest/reopening")
        if source_tree_digest(self.root) != manifest.source_tree_sha256:
            raise AccessDeniedError("source tree hash changed")
        output = within(self.root, manifest.output)
        if output.name != manifest.run_id:
            raise AccessDeniedError("output directory name must match run_id")
        if (
            not output.is_relative_to(self.root / "results/research")
            or output == self.root / "results/research"
        ):
            raise AccessDeniedError("run output must be a child of results/research")
        if output.exists():
            raise AccessDeniedError(
                "output already exists; inspect the ledger instead of replaying"
            )
        if inputs:
            _verify_inputs(self.root, manifest)
        return Permit(self.root, manifest, grant, sha256(policy_bytes).hexdigest())

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.ledger, timeout=15)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, manifest_hash TEXT UNIQUE NOT NULL,
                fingerprint TEXT UNIQUE NOT NULL, batch_id TEXT NOT NULL, family_id TEXT NOT NULL,
                spec_hash TEXT NOT NULL, conditions INTEGER NOT NULL, status TEXT NOT NULL,
                manifest_json TEXT NOT NULL, policy_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                finished_at TEXT, detail TEXT, output_hashes TEXT
            )""")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS access_events (run_id TEXT, split TEXT, root TEXT, opened_at TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS policies (hash TEXT PRIMARY KEY, snapshot TEXT NOT NULL)"
            )
            connection.commit()
            yield connection
        finally:
            connection.close()

    def _reserve(self, permit: Permit) -> None:
        m = permit.manifest
        # Identity changes cannot rerun exactly the same technical experiment.
        fingerprint = canonical_digest(
            {
                "spec": m.specification.sha256,
                "source": m.source_tree_sha256,
                "inputs": sorted(s.sha256 for s in m.input_files),
                "stage": m.stage,
                "conditions": m.conditions,
                "seed": m.seed,
            }
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if digest_file(self.root / POLICY_PATH) != permit.policy_sha256:
                raise AccessDeniedError("policy changed before reservation")
            policy = ExecutionPolicy.model_validate_json((self.root / POLICY_PATH).read_bytes())
            connection.execute(
                "INSERT OR IGNORE INTO policies VALUES (?, ?)",
                (permit.policy_sha256, (self.root / POLICY_PATH).read_text(encoding="utf-8")),
            )
            all_specs = {
                row[0] for row in connection.execute("SELECT DISTINCT spec_hash FROM runs")
            } | {m.specification.sha256}
            if len(all_specs) > policy.max_total_specs:
                raise AccessDeniedError("exhausted total specification budget")
            # Budgets are cumulative across policy revisions, run IDs and batch IDs.
            for column, identity, limit in (
                ("family_id", m.family_id, policy.families[m.family_id]),
                ("batch_id", m.batch_id, policy.batches[m.batch_id]),
            ):
                rows = connection.execute(
                    f"SELECT spec_hash, conditions FROM runs WHERE {column}=?", (identity,)
                ).fetchall()
                specs = {r["spec_hash"] for r in rows} | {m.specification.sha256}
                if (
                    len(rows) + 1 > limit.max_attempts
                    or len(specs) > limit.max_specs
                    or sum(r["conditions"] for r in rows) + len(m.conditions) > limit.max_conditions
                ):
                    raise AccessDeniedError(f"exhausted {column} budget: {identity}")
            if connection.execute("SELECT 1 FROM runs WHERE status='RUNNING'").fetchone():
                raise AccessDeniedError(
                    "an active or interrupted run needs reconciliation before dispatch"
                )
            try:
                connection.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, NULL, NULL, NULL)",
                    (
                        m.run_id,
                        manifest_digest(m),
                        fingerprint,
                        m.batch_id,
                        m.family_id,
                        m.specification.sha256,
                        len(m.conditions),
                        m.model_dump_json(),
                        permit.policy_sha256,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AccessDeniedError(
                    "duplicate run/manifest/technical experiment; no automatic retry"
                ) from exc
            connection.commit()

    def _finish(self, manifest: RunManifest, status: str, detail: str) -> None:
        output = within(self.root, manifest.output)
        hashes = (
            {
                p.relative_to(output).as_posix(): digest_file(p)
                for p in sorted(output.rglob("*"))
                if p.is_file()
            }
            if output.is_dir()
            else {}
        )
        with self._connection() as connection:
            connection.execute(
                "UPDATE runs SET status=?, finished_at=?, detail=?, output_hashes=? WHERE run_id=? AND status='RUNNING'",
                (
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    detail,
                    json.dumps(hashes),
                    manifest.run_id,
                ),
            )
            connection.commit()

    def execute(self, manifest: RunManifest, action: Callable[[], T]) -> T:
        """Reserve before I/O; failures consume budget. No automatic restart of partial runs."""
        with self._execution_lock():
            return self._execute(manifest, action)

    def _execute(self, manifest: RunManifest, action: Callable[[], T]) -> T:
        if _ACTIVE.get() is not None:
            raise AccessDeniedError("nested reservations are not allowed")
        permit = self.validate(manifest, inputs=False)
        self._reserve(permit)
        token = _ACTIVE.set(permit)
        try:
            self._snapshot(permit)
            _verify_inputs(self.root, manifest)
            result = action()
            # Detect mutations during execution; do not label such a run successful.
            for seal in (manifest.specification, *manifest.evidence, *manifest.config_files):
                verify_seal(self.root, seal)
            if source_tree_digest(self.root) != manifest.source_tree_sha256:
                raise AccessDeniedError("source changed during execution")
            require_entry(manifest.entrypoint)
            _verify_inputs(self.root, manifest)
            if not within(self.root, manifest.output).is_dir():
                raise AccessDeniedError("runner did not produce its declared output")
        except BaseException as exc:
            self._finish(manifest, "FAILED", f"{type(exc).__name__}: {exc}")
            raise
        else:
            self._finish(
                manifest,
                "COMPLETED",
                "Execution completed; scientific decision remains in runner outputs.",
            )
            return result
        finally:
            _ACTIVE.reset(token)

    def _snapshot(self, permit: Permit) -> None:
        manifest = permit.manifest
        receipt = self.ledger.parent / "receipts" / manifest.run_id
        receipt.mkdir(parents=True, exist_ok=False)
        (receipt / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        (receipt / "policy.json").write_bytes((self.root / POLICY_PATH).read_bytes())
        (receipt / "runtime.json").write_text(
            json.dumps(
                {
                    "python": sys.version,
                    "packages": {
                        name: version(name)
                        for name in ("polars", "pyarrow", "pydantic", "PyYAML", "typer")
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        paths = {
            *self.root.joinpath("src").rglob("*.py"),
            *self.root.joinpath("scripts").rglob("*.py"),
            *self.root.joinpath("tests").rglob("*.py"),
            *(
                within(self.root, seal.path)
                for seal in (
                    manifest.specification,
                    permit.grant.review,
                    *manifest.config_files,
                    *manifest.evidence,
                )
            ),
            *(
                self.root / name
                for name in ("orchestrator.py", "pyproject.toml", "uv.lock")
                if (self.root / name).is_file()
            ),
        }
        with zipfile.ZipFile(receipt / "frozen_sources.zip", "x", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(paths):
                archive.write(path, path.relative_to(self.root).as_posix())

    def status(self) -> list[dict[str, object]]:
        """Inspect metadata without opening market data or creating a fresh ledger."""
        if not self.ledger.exists():
            return []
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT run_id,batch_id,family_id,status,conditions,created_at,finished_at,detail FROM runs ORDER BY created_at"
            ).fetchall()
            return [dict(row) for row in rows]

    def overview(self) -> dict[str, object]:
        policy = ExecutionPolicy.model_validate_json((self.root / POLICY_PATH).read_bytes())
        runs = self.status()
        return {
            "policy_id": policy.policy_id,
            "market_execution_enabled": policy.market_execution_enabled,
            "reviewed_grants": len(policy.grants),
            "family_remaining_attempts": {
                name: max(0, budget.max_attempts - sum(r["family_id"] == name for r in runs))
                for name, budget in policy.families.items()
            },
            "batch_remaining_attempts": {
                name: max(0, budget.max_attempts - sum(r["batch_id"] == name for r in runs))
                for name, budget in policy.batches.items()
            },
            "runs": runs,
        }

    def verify_output(self, run_id: str) -> dict[str, object]:
        """Verify saved artifacts only; this never resumes computation or opens inputs."""
        if not self.ledger.exists():
            raise ValueError("execution ledger does not exist")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if (
            row is None
            or row["status"] not in {"COMPLETED", "FAILED"}
            or row["output_hashes"] is None
        ):
            raise ValueError("run has no sealed output boundary; reconcile interruption first")
        manifest = RunManifest.model_validate_json(row["manifest_json"])
        output = within(self.root, manifest.output)
        expected = json.loads(row["output_hashes"])
        # Reports are appended by the existing report command after completion.
        # New report files do not change the sealed research boundary; existing
        # sealed files (including original reports) must still match exactly.
        actual = (
            {
                p.relative_to(output).as_posix(): digest_file(p)
                for p in output.rglob("*")
                if p.is_file()
            }
            if output.is_dir()
            else {}
        )
        extras = set(actual) - set(expected)
        if any(actual.get(name) != digest for name, digest in expected.items()) or any(
            not name.startswith("reports/") for name in extras
        ):
            raise AccessDeniedError("saved output hashes changed; do not resume or overwrite")
        return {"run_id": run_id, "status": row["status"], "verified_files": len(expected)}

    def close_interrupted(self, run_id: str, reason: str) -> None:
        """Record an externally confirmed dead process; never refund its budget or rerun it."""
        with self._execution_lock():
            self._close_interrupted(run_id, reason)

    def _close_interrupted(self, run_id: str, reason: str) -> None:
        if not reason.strip() or not self.ledger.exists():
            raise ValueError("existing run and explicit reconciliation reason required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "RUNNING":
                raise ValueError("only a RUNNING record can be closed as interrupted")
            connection.execute(
                "UPDATE runs SET status='INTERRUPTED', finished_at=?, detail=? WHERE run_id=?",
                (datetime.now(timezone.utc).isoformat(), reason, run_id),
            )
            connection.commit()


def dispatch(root: Path, manifest: RunManifest) -> Path:
    """Run reviewed adapters only; no shell command or runtime argument overrides."""
    root = root.resolve()
    if Path.cwd().resolve() != root:
        raise AccessDeniedError("execute from the project root")
    output = within(root, manifest.output)
    arguments = manifest.arguments

    def action() -> Path:
        require_entry(manifest.entrypoint)
        if manifest.entrypoint == "s2_availability":
            if manifest.stage != "S2" or set(arguments) != {"config_dir"}:
                raise AccessDeniedError("S2 adapter requires S2 and exactly config_dir")
            from n225m_bt.research.r3b_ohlc_availability import write_s2_diagnostic

            if manifest.conditions != ("ohlc_availability",):
                raise AccessDeniedError("S2 availability condition must be explicit")
            return write_s2_diagnostic(output, within(root, arguments["config_dir"]))
        if manifest.entrypoint == "campaign":
            if manifest.stage != "S3" or set(arguments) != {
                "config_dir",
                "calendar",
                "study_config",
            }:
                raise AccessDeniedError(
                    "campaign adapter requires S3 and frozen config/calendar/study"
                )
            from n225m_bt.research.report import render_campaign
            from n225m_bt.research.runner import run_campaign

            result = run_campaign(
                within(root, arguments["config_dir"]),
                output.parent,
                within(root, arguments["calendar"]),
                output.name,
                study_config=within(root, arguments["study_config"]),
            )
            render_campaign(result)
            return result
        if manifest.entrypoint == "r003_preflight":
            if manifest.stage != "S2" or set(arguments) != {
                "config_dir",
                "calendar",
                "study_config",
            }:
                raise AccessDeniedError(
                    "R003 preflight requires S2 and frozen config/calendar/study"
                )
            from n225m_bt.research.r003 import run_r003_campaign

            return run_r003_campaign(
                within(root, arguments["config_dir"]),
                output.parent,
                within(root, arguments["calendar"]),
                output.name,
                print,
                within(root, arguments["study_config"]),
                "development",
            )
        if manifest.entrypoint == "baseline_backtest":
            if set(arguments) not in ({"config_dir"}, {"config_dir", "calendar"}):
                raise AccessDeniedError("baseline requires frozen config_dir and optional calendar")
            from n225m_bt.cli import run_backtest

            run_backtest(
                within(root, arguments["config_dir"]),
                output.parent,
                manifest.run_id,
                within(root, arguments["calendar"]) if "calendar" in arguments else None,
                "center",
            )
            return output
        if manifest.entrypoint.startswith("script:"):
            path = within(root, manifest.entrypoint.removeprefix("script:"))
            if not path.is_relative_to(root / "scripts") or path.suffix != ".py" or arguments:
                raise AccessDeniedError(
                    "script adapter requires a workspace script and no arguments"
                )
            old_path, old_argv = sys.path[:], sys.argv[:]
            try:
                sys.path.insert(0, str(root / "scripts"))
                sys.argv = [str(path)]
                namespace = runpy.run_path(str(path), run_name="__research_dispatch__")
                # Old wrappers with implicit outputs must first receive an explicit adapter.
                if (
                    namespace.get("RUN_ID") != manifest.run_id
                    or Path(str(namespace.get("OUT", ""))).resolve() != output
                ):
                    raise AccessDeniedError("script RUN_ID/OUT do not match the reserved manifest")
                if namespace.get("RESEARCH_STAGE") != manifest.stage:
                    raise AccessDeniedError("script must declare its reviewed RESEARCH_STAGE")
                if tuple(namespace.get("RESEARCH_CONDITIONS", ())) != manifest.conditions:
                    raise AccessDeniedError("script conditions differ from reserved manifest")
                if namespace.get("RESEARCH_SEED") != manifest.seed:
                    raise AccessDeniedError("script seed differs from reserved manifest")
                main = namespace.get("main")
                if not callable(main):
                    raise AccessDeniedError("script adapter requires callable main")
                main()
                return output
            finally:
                sys.path[:], sys.argv[:] = old_path, old_argv
        raise AccessDeniedError("entrypoint has no accepted adapter")

    return ResearchController(root).execute(manifest, action)
