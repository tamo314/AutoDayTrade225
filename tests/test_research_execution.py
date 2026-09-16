"""Synthetic dispatch acceptance: no proprietary market-data files or child agents."""

import ast
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from pydantic import ValidationError
from research_execution_fixtures import grant, provision, seal

from n225m_bt.research.data import load_split
from n225m_bt.research.execution import (
    LEDGER_PATH,
    AccessDeniedError,
    ResearchController,
    RunManifest,
    require_entry,
    require_market_read,
    source_tree_digest,
)


@pytest.fixture
def root(workspace_tmp: Path) -> Path:
    # Avoid Polars glob metacharacters in parametrized pytest names.
    path = workspace_tmp.parent / "execution-acceptance" / uuid4().hex
    path.mkdir(parents=True)
    return path.resolve()


def complete(root: Path, m: RunManifest) -> str:
    require_entry(m.entrypoint)
    output = root / m.output
    output.mkdir(parents=True)
    (output / "result.json").write_text('{"decision":"INCONCLUSIVE","net":null}')
    return "done"


def policy_update(root: Path, update: object) -> None:
    path = root / "config/research_execution.json"
    policy = json.loads(path.read_text())
    assert callable(update)
    update(policy)
    path.write_text(json.dumps(policy))


def test_success_saves_snapshots_null_and_pre_read_access_log(root: Path) -> None:
    controller, m = provision(root)

    def action() -> str:
        require_market_read(root / "gold", "development")
        with sqlite3.connect(root / LEDGER_PATH) as db:
            assert db.execute("SELECT status FROM runs").fetchone()[0] == "RUNNING"
            assert db.execute("SELECT split FROM access_events").fetchone()[0] == "development"
        return complete(root, m)

    assert controller.execute(m, action) == "done"
    assert controller.status()[0]["status"] == "COMPLETED"
    assert (root / "results/research_control/receipts/test-001/frozen_sources.zip").is_file()
    with sqlite3.connect(root / LEDGER_PATH) as db:
        hashes = json.loads(db.execute("SELECT output_hashes FROM runs").fetchone()[0])
        assert "result.json" in hashes
    with pytest.raises(AccessDeniedError):
        require_entry("fixture")


def test_saved_boundary_verification_allows_new_reports_but_detects_changed_results(
    root: Path,
) -> None:
    controller, m = provision(root)
    controller.execute(m, lambda: complete(root, m))
    assert controller.verify_output(m.run_id)["verified_files"] == 1
    report = root / m.output / "reports/new-report"
    report.mkdir(parents=True)
    (report / "summary.md").write_text("Derived report")
    assert controller.verify_output(m.run_id)["verified_files"] == 1
    (root / m.output / "result.json").write_text('{"net":0}')
    with pytest.raises(AccessDeniedError, match="output hashes"):
        controller.verify_output(m.run_id)


def test_python_model_copy_cannot_bypass_period_schema(root: Path) -> None:
    controller, m = provision(root)
    unsafe = m.model_copy(update={"stage": "S6", "split": "out_of_sample"})
    with pytest.raises(ValidationError):
        controller.execute(unsafe, lambda: pytest.fail("invalid Python model cannot run"))


def test_wrong_input_root_and_oos_are_rejected_inside_an_active_reservation(root: Path) -> None:
    controller, m = provision(root)

    def action() -> str:
        with pytest.raises(AccessDeniedError, match="root differs"):
            require_market_read(root / "elsewhere", "development")
        with pytest.raises(AccessDeniedError, match="locked"):
            require_market_read(root / "gold", "out_of_sample")
        return complete(root, m)

    controller.execute(m, action)


@pytest.mark.parametrize(
    "change", ["no_grant", "closed", "zero_budget", "stage", "oos", "holdout", "parent"]
)
def test_rejection_precedes_input_hash_read(
    root: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    import n225m_bt.research.execution as execution

    controller, m = provision(root)
    if change == "no_grant":
        policy_update(root, lambda p: p.update(grants=[]))
    elif change == "closed":
        policy_update(root, lambda p: p.update(grants=[]))
        grant(root, m, reopened=False)
    elif change == "zero_budget":
        policy_update(root, lambda p: p["families"]["F01"].update(max_attempts=0))
    elif change == "parent":
        m = m.model_copy(update={"known_record_keys": ("UNKNOWN",)})
        grant(root, m)
    else:
        payload = m.model_dump(mode="json")
        payload.update(
            {"stage": "S6"}
            if change == "stage"
            else {"split": "out_of_sample" if change == "oos" else "final_holdout"}
        )
        with pytest.raises(ValidationError):
            RunManifest.model_validate(payload)
        return
    monkeypatch.setattr(
        execution,
        "_verify_inputs",
        lambda *_: pytest.fail("no input hash access before authorization"),
    )
    with pytest.raises(AccessDeniedError):
        controller.execute(m, lambda: pytest.fail("must not dispatch"))
    assert controller.status() == []


@pytest.mark.parametrize(
    "target", ["spec.md", "config/fixture.yaml", "src/fixture.py", "evidence.json", "registry.json"]
)
def test_frozen_metadata_mutation_prevents_dispatch(root: Path, target: str) -> None:
    controller, m = provision(root)
    (root / target).write_text("changed")
    with pytest.raises(AccessDeniedError, match="hash"):
        controller.execute(m, lambda: pytest.fail("changed file cannot run"))
    assert controller.status() == []


def test_input_mutation_and_new_partition_are_recorded_failed_before_loader(root: Path) -> None:
    controller, m = provision(root)
    (root / m.input_files[0].path).write_bytes(b"changed")
    with pytest.raises(AccessDeniedError, match="hash changed"):
        controller.execute(m, lambda: pytest.fail("changed price input cannot run"))
    assert controller.status()[0]["status"] == "FAILED"


def test_partition_addition_is_not_silently_included(root: Path) -> None:
    controller, m = provision(root)
    (root / m.input_files[0].path).with_name("extra.parquet").write_bytes(b"unregistered")
    with pytest.raises(AccessDeniedError, match="partition set"):
        controller.execute(m, lambda: pytest.fail("unexpected partition cannot run"))


def test_failure_consumes_budget_and_identity_changes_do_not_retry(root: Path) -> None:
    controller, m = provision(root)
    with pytest.raises(RuntimeError, match="technical failure"):
        controller.execute(m, lambda: (_ for _ in ()).throw(RuntimeError("technical failure")))
    second = m.model_copy(
        update={"run_id": "new-id", "study_id": "new-study", "output": "results/research/new-id"}
    )
    grant(root, second)
    with pytest.raises(AccessDeniedError, match="duplicate"):
        controller.execute(second, lambda: pytest.fail("new ID is not new evidence"))
    assert len(controller.status()) == 1
    assert controller.status()[0]["status"] == "FAILED"


def test_family_budget_survives_batch_and_policy_changes(root: Path) -> None:
    controller, m = provision(root)
    policy_update(root, lambda p: p["families"]["F01"].update(max_attempts=1))
    controller.execute(m, lambda: complete(root, m))
    (root / "spec2.md").write_text("distinct specification")
    second = m.model_copy(
        update={
            "batch_id": "B2",
            "run_id": "new-id",
            "output": "results/research/new-id",
            "specification": seal(root, root / "spec2.md"),
        }
    )
    policy_update(root, lambda p: p["batches"].update(B2=p["batches"]["B1"]))
    grant(root, second)
    with pytest.raises(AccessDeniedError, match="exhausted family"):
        ResearchController(root).execute(
            second, lambda: pytest.fail("batch ID cannot reset family")
        )


def test_parallel_dispatch_and_closure_of_live_process_are_blocked(root: Path) -> None:
    controller, m = provision(root)
    started, finish = Event(), Event()

    def action() -> str:
        started.set()
        assert finish.wait(10)
        return complete(root, m)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(controller.execute, m, action)
        assert started.wait(10)
        try:
            with pytest.raises(AccessDeniedError, match="lock"):
                ResearchController(root).execute(m, lambda: pytest.fail("parallel dispatch"))
            with pytest.raises(AccessDeniedError, match="lock"):
                ResearchController(root).close_interrupted(m.run_id, "cannot close live process")
        finally:
            finish.set()
        assert future.result(timeout=10) == "done"


def test_orphan_reservation_blocks_resume_and_closure_does_not_refund(root: Path) -> None:
    controller, m = provision(root)
    policy_update(root, lambda p: p["families"]["F01"].update(max_attempts=1))
    controller._reserve(
        controller.validate(m, inputs=False)
    )  # process died immediately after reserve
    with pytest.raises(AccessDeniedError):
        ResearchController(root).execute(m, lambda: pytest.fail("cannot replay interrupted run"))
    ResearchController(root).close_interrupted(
        m.run_id, "Synthetic process death; no market access"
    )
    assert controller.status()[0]["status"] == "INTERRUPTED"
    with pytest.raises(AccessDeniedError, match="exhausted"):
        controller.execute(m, lambda: pytest.fail("interruption must not refund"))


def test_unregistered_loaders_stop_before_polars_or_file_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polars as pl

    from n225m_bt.research.r3b_ohlc_availability import development_paths

    monkeypatch.setattr(pl, "scan_parquet", lambda *_args, **_kwargs: pytest.fail("no scan"))
    for split in ("development", "out_of_sample"):
        with pytest.raises(AccessDeniedError):
            load_split(Path("nonexistent"), split)  # type: ignore[arg-type]
    with pytest.raises(AccessDeniedError):
        development_paths(Path("nonexistent"))


def test_script_adapter_binds_identity_stage_conditions_seed_and_output(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from n225m_bt.cli import app

    controller, m = provision(root)
    script = root / "scripts/run_fixture.py"
    script.parent.mkdir()
    script.write_text(
        "from pathlib import Path\n"
        "from n225m_bt.research.execution import require_entry\n"
        "RUN_ID='test-001'\nRESEARCH_STAGE='S3'\nRESEARCH_CONDITIONS=('A',)\nRESEARCH_SEED=42\n"
        "OUT=Path('results/research/test-001').resolve()\n"
        "def main():\n"
        "    require_entry('script:scripts/run_fixture.py')\n"
        "    OUT.mkdir(parents=True)\n"
        "    (OUT/'result.json').write_text('{\"net\":null}')\n",
        encoding="utf-8",
    )
    m = m.model_copy(
        update={
            "entrypoint": "script:scripts/run_fixture.py",
            "source_tree_sha256": source_tree_digest(root),
        }
    )
    grant(root, m)
    monkeypatch.chdir(root)
    path = root / "manifest.json"
    path.write_text(m.model_dump_json())
    cli = CliRunner()
    checked = cli.invoke(app, ["research", "check-execution", str(path)])
    assert checked.exit_code == 0, checked.output
    assert not (root / LEDGER_PATH).exists()
    executed = cli.invoke(app, ["research", "execute", str(path)])
    assert executed.exit_code == 0, executed.output
    assert controller.status()[0]["status"] == "COMPLETED"
    verified = cli.invoke(app, ["research", "verify-execution", m.run_id])
    assert verified.exit_code == 0, verified.output
    status = cli.invoke(app, ["research", "execution-status"])
    assert json.loads(status.output)["family_remaining_attempts"]["F01"] == 2
    replay = cli.invoke(app, ["research", "execute", str(path)])
    assert replay.exit_code != 0


def test_manifest_condition_budget_cannot_be_oversubscribed(root: Path) -> None:
    controller, m = provision(root, conditions=("A", "B"))
    policy_update(root, lambda p: p["batches"]["B1"].update(max_conditions=1))
    with pytest.raises(AccessDeniedError, match="exhausted batch"):
        controller.execute(m, lambda: pytest.fail("unbudgeted extra condition"))


def test_all_historical_market_script_entries_have_early_guard() -> None:
    excluded = {
        "run_r3b_01_finite_plan_calibration.py",
        "run_r3c_02_source_primary_audit.py",
        "run_r016_d001.py",
        "run_r017_d001.py",
        "run_r018_d001.py",
    }
    checked = 0
    for path in Path("scripts").glob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        if (
            not path.name.startswith(("run_", "finalize_")) and "load_split(" not in source
        ) or path.name in excluded:
            continue
        tree = ast.parse(source)
        main = next(
            (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"), None
        )
        if main is None:
            main = next(
                n for n in tree.body if isinstance(n, ast.If) and "__name__" in ast.unparse(n.test)
            )
        body = main.body
        if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        assert isinstance(body[0], ast.ImportFrom), path
        assert body[0].module == "n225m_bt.research.execution", path
        assert "require_entry(" in ast.unparse(body[1]), path
        checked += 1
    assert checked >= 100


def test_core_entrypoints_reject_before_config_or_output_access(root: Path) -> None:
    from n225m_bt.cli import run_backtest
    from n225m_bt.research.r003 import run_r003_campaign
    from n225m_bt.research.r3b_ohlc_availability import write_s2_diagnostic
    from n225m_bt.research.runner import run_campaign

    with pytest.raises(AccessDeniedError):
        run_campaign(root, root / "never-created", root / "missing.yaml")
    with pytest.raises(AccessDeniedError):
        run_r003_campaign(
            root,
            root / "never-created",
            root / "missing.yaml",
            None,
            print,
            root / "study.yaml",
            "development",
        )
    with pytest.raises(AccessDeniedError):
        write_s2_diagnostic(root / "never-created", root)
    with pytest.raises(AccessDeniedError):
        run_backtest(root, root / "never-created", "blocked", None, "center")
    assert not (root / "never-created").exists()
