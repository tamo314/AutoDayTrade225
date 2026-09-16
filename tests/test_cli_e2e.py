from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from research_execution_fixtures import grant, provision, seal
from typer.testing import CliRunner

from n225m_bt.cli import app
from n225m_bt.performance import benchmark_parquet_scan


def write_e2e_config(config_dir: Path, workspace_tmp: Path) -> None:
    source = Path("config")
    config_dir.mkdir(exist_ok=True)
    for name in ["instrument.yaml", "sessions.yaml", "backtest.yaml", "research.yaml"]:
        (config_dir / name).write_text(
            (source / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    data = yaml.safe_load((source / "data.yaml").read_text(encoding="utf-8"))
    data.update(
        {
            "raw_root": str(workspace_tmp / "raw"),
            "bronze_root": str(workspace_tmp / "bronze"),
            "silver_root": str(workspace_tmp / "silver"),
            "gold_root": str(workspace_tmp / "gold"),
        }
    )
    data["source_format"]["source_mapping"] = "auto"
    (config_dir / "data.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def test_cli_ingest_and_backtest_are_end_to_end_and_reproducible(
    workspace_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_tmp = (workspace_tmp / uuid4().hex).resolve()
    workspace_tmp.mkdir(parents=True)
    config_dir = workspace_tmp / "config"
    write_e2e_config(config_dir, workspace_tmp)
    source = workspace_tmp / "source.csv"
    source.write_text(
        "date,time,open,high,low,close,volume\n2024-11-05,10:00,40000,40005,39995,40000,1\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    first = runner.invoke(app, ["data", "ingest", str(source), "--config-dir", str(config_dir)])
    second = runner.invoke(app, ["data", "ingest", str(source), "--config-dir", str(config_dir)])
    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout
    controller, plan = provision(
        workspace_tmp,
        input_root=workspace_tmp / "gold",
        entry="baseline_backtest",
        conditions=("always_flat",),
        seed=0,
    )
    plan = plan.model_copy(
        update={
            "arguments": {"config_dir": "config"},
            "evidence": (
                *plan.evidence,
                seal(workspace_tmp, workspace_tmp / "silver/dataset_manifest.json"),
            ),
        }
    )
    grant(workspace_tmp, plan)
    frozen = workspace_tmp / "execution.json"
    frozen.write_text(plan.model_dump_json())
    monkeypatch.chdir(workspace_tmp)
    run = runner.invoke(app, ["research", "execute", str(frozen)])
    assert run.exit_code == 0, run.stdout
    manifest = json.loads((workspace_tmp / plan.output / "run_manifest.json").read_text())
    assert manifest["dataset_id"]
    assert manifest["code_version"] == "0.1.0"
    scan = benchmark_parquet_scan(workspace_tmp / "gold")
    assert scan.row_count == 1
    assert controller.verify_output(plan.run_id)["status"] == "COMPLETED"


def test_backtest_cli_requires_calendar_for_night_data(
    workspace_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_tmp = (workspace_tmp / uuid4().hex).resolve()
    workspace_tmp.mkdir(parents=True)
    config_dir = workspace_tmp / "config-night"
    write_e2e_config(config_dir, workspace_tmp / "night")
    source = workspace_tmp / "night-source.csv"
    source.write_text(
        "date,time,open,high,low,close,volume\n2024-11-05,10:00,40000,40005,39995,40000,1\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    ingested = runner.invoke(app, ["data", "ingest", str(source), "--config-dir", str(config_dir)])
    assert ingested.exit_code == 0, ingested.stdout
    calendar = workspace_tmp / "calendar.yaml"
    calendar.write_text(
        "trading_days:\n"
        "  - trade_date: '2024-11-05'\n"
        "    previous_trade_date: '2024-11-04'\n"
        "    next_trade_date: ''\n"
        "    night_calendar_start_date: '2024-11-04'\n"
        "    is_holiday_trading_day: false\n"
        "    schedule_version: ose_n225m_from_20241105\n",
        encoding="utf-8",
    )
    controller, plan = provision(
        workspace_tmp,
        input_root=workspace_tmp / "night/gold",
        entry="baseline_backtest",
        conditions=("always_flat",),
        seed=0,
    )
    plan = plan.model_copy(
        update={
            "arguments": {"config_dir": "config-night", "calendar": "calendar.yaml"},
            "evidence": (
                *plan.evidence,
                seal(workspace_tmp, workspace_tmp / "night/silver/dataset_manifest.json"),
            ),
        }
    )
    grant(workspace_tmp, plan)
    frozen = workspace_tmp / "execution.json"
    frozen.write_text(plan.model_dump_json())
    monkeypatch.chdir(workspace_tmp)
    result = runner.invoke(app, ["research", "execute", str(frozen)])
    assert result.exit_code == 0, result.stdout
    assert controller.verify_output(plan.run_id)["status"] == "COMPLETED"
