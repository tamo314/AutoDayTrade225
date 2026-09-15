"""Command line interface for inspection, ingest, and backtests."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer
import yaml

from n225m_bt.config import load_project_config, load_research_config
from n225m_bt.domain import SeriesType

app = typer.Typer(help="N225 mini reproducible backtesting platform.", no_args_is_help=True)
data_app = typer.Typer(help="Inspect and ingest source data.")
backtest_app = typer.Typer(help="Run a strategy against an existing Gold Parquet dataset.")
app.add_typer(data_app, name="data")
app.add_typer(backtest_app, name="backtest")
research_app = typer.Typer(help="Run preregistered strategy research with locked Final Holdout.")
app.add_typer(research_app, name="research")


@research_app.command("audit-spec")
def audit_research_specification(
    specification: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path = typer.Option(..., file_okay=False),
) -> None:
    """Validate a frozen S0/S1 specification without opening market data."""
    from n225m_bt.research.spec_audit import SpecificationAuditError, write_specification_audit

    try:
        result = write_specification_audit(specification, output)
    except (OSError, SpecificationAuditError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Specification audit: {result}")


@research_app.command("audit-synthetic-decision")
def audit_synthetic_decision_pipeline(
    audit_id: str = typer.Option(..., help="Unique audit identifier."),
    output: Path = typer.Option(..., file_okay=False),
    fixture: Path = typer.Option(..., exists=True, dir_okay=False),
    run_tests: bool = typer.Option(False, help="Execute the dedicated synthetic acceptance nodes."),
) -> None:
    """Create an exclusive R1 synthetic-decision audit shell without market I/O.

    This is intentionally separate from ``research run`` and never invokes a
    loader, cache, market data, or the shared execution engine.  The dedicated
    synthetic pytest nodes provide the measured acceptance results.
    """
    from n225m_bt.research.decision_audit import write_synthetic_decision_audit

    root = Path(__file__).resolve().parent / "research"
    sources = [
        Path(__file__).resolve(),
        root / "decision_audit.py",
        root / "conditions.py",
        root / "causality.py",
        root / "execution_ledger.py",
        *(root / f"r{number}.py" for number in ("046", "049", "060", "061", "062", "063", "064")),
    ]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        result = write_synthetic_decision_audit(
            output, audit_id=audit_id, source_files=sources, fixture_files=[fixture], commit=commit
        )
    except (OSError, ValueError, PermissionError, subprocess.SubprocessError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if run_tests:
        nodes = [
            "tests/test_r1_synthetic_decision_audit.py",
            "tests/test_r1_governance.py",
            "tests/test_r1_spec_audit.py",
            "tests/test_r046_q001.py",
            "tests/test_r049_q001.py",
            "tests/test_r060_q001.py",
            "tests/test_r061_q001.py",
            "tests/test_r062_q001.py",
            "tests/test_r063_q001.py",
            "tests/test_r064_q001.py",
        ]
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", *nodes],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        (result / "test_results.json").write_text(
            json.dumps(
                {
                    "status": "PASS" if completed.returncode == 0 else "FAIL",
                    "returncode": completed.returncode,
                    "node_ids": nodes,
                    "stdout_file": "test_stdout.txt",
                    "stderr_file": "test_stderr.txt",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (result / "test_stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (result / "test_stderr.txt").write_text(completed.stderr, encoding="utf-8")
        (result / "summary.md").write_text(
            "# R1 synthetic decision audit\n\n"
            f"Measured synthetic test status: {'PASS' if completed.returncode == 0 else 'FAIL'}. "
            "This is not a Development run, an engine-integration pass, or an R1-wide acceptance. "
            "No market data, OOS, or Final Holdout input was opened.\n",
            encoding="utf-8",
        )
        if completed.returncode:
            raise typer.Exit(completed.returncode)
    typer.echo(f"Synthetic decision audit: {result}")


@research_app.command("run")
def run_strategy_research(
    config_dir: Path = typer.Option(Path("config")),
    results_root: Path = typer.Option(Path("results/research")),
    calendar_override: Path = typer.Option(..., exists=True, dir_okay=False),
    campaign_id: str | None = typer.Option(None),
    study_config: Path | None = typer.Option(None, exists=True, dir_okay=False),
    stage: str | None = typer.Option(None, help="Required as 'development' for R003."),
) -> None:
    """Evaluate a bounded Development grid; open OOS only after its selection gates pass."""
    from n225m_bt.research.runner import run_campaign

    output = run_campaign(
        config_dir, results_root, calendar_override, campaign_id, typer.echo, study_config, stage
    )
    typer.echo(f"Research results: {output}")
    if (output / "DEVELOPMENT_COMPLETED.json").is_file():
        typer.echo(f"R003 summary: {output / 'summary.md'}")
        return
    from n225m_bt.research.report import render_campaign

    typer.echo(f"Research report: {render_campaign(output)}")


@research_app.command("report")
def report_strategy_research(
    campaign: Path = typer.Argument(..., exists=True, file_okay=False),
) -> None:
    """Rebuild an append-only research report from saved results without reading market data."""
    from n225m_bt.research.report import render_campaign

    typer.echo(f"Research report: {render_campaign(campaign)}")


@app.command()
def validate(config_dir: Path = typer.Option(Path("config"))) -> None:
    """Validate the project YAML configuration."""
    load_project_config(config_dir)
    load_research_config(config_dir)
    from n225m_bt.config import load_yaml_model
    from n225m_bt.research.config import CampaignConfig
    from n225m_bt.research.data import validate_splits

    validate_splits(load_research_config(config_dir))
    load_yaml_model(config_dir / "strategy_research.yaml", CampaignConfig)
    typer.echo("Configuration is valid.")


@data_app.command("inspect")
def inspect_source(path: Path, config_dir: Path = typer.Option(Path("config"))) -> None:
    """Detect source encoding, delimiter, header, and column candidates."""
    from n225m_bt.ingest.labo225 import inspect_source_file

    data_config = load_project_config(config_dir)[2]
    inspection = inspect_source_file(path, data_config.source_format)
    typer.echo(inspection.to_json())


@data_app.command("build-calendar")
def build_calendar(
    source_root: list[Path] = typer.Option(..., exists=True, file_okay=False),
    output: Path = typer.Option(Path("config/local_calendar.yaml")),
    config_dir: Path = typer.Option(Path("config")),
) -> None:
    """Build a local explicit calendar from every available source trade-date column."""
    from n225m_bt.calendar.inference import infer_calendar_from_trade_dates
    from n225m_bt.ingest.labo225 import read_source_trade_dates

    _, sessions, data, _ = load_project_config(config_dir)
    suffixes = {".csv", ".txt", ".zip", ".xlsx"}
    files = sorted(
        {
            path
            for root in source_root
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in suffixes
        }
    )
    if not files:
        raise typer.BadParameter("no supported source files found under --source-root")
    trade_dates = {
        trade_date
        for path in files
        for trade_date in read_source_trade_dates(path, data.source_format)
    }
    calendar = infer_calendar_from_trade_dates(trade_dates, sessions)
    calendar.to_yaml(output)
    typer.echo(f"Wrote {output} from {len(files)} file(s), {len(trade_dates)} trade dates.")


@data_app.command("ingest")
def ingest_source(
    path: Path,
    config_dir: Path = typer.Option(Path("config")),
    calendar_override: Path | None = typer.Option(None),
    dataset: str = typer.Option(
        "center", help="Dataset namespace below Silver and Gold roots (for example: forward)."
    ),
    series_type: SeriesType | None = typer.Option(
        None, help="Override the configured series type for a separate continuous series."
    ),
) -> None:
    """Ingest one source file through Bronze, Silver, quality, and Gold layers."""
    from n225m_bt.calendar.model import ExchangeCalendar
    from n225m_bt.ingest.pipeline import ingest_file

    instrument, sessions, data, _ = load_project_config(config_dir)
    if Path(dataset).name != dataset or dataset in {"", ".", ".."}:
        raise typer.BadParameter("dataset must be a single directory name")
    if dataset != "center":
        data = data.model_copy(
            update={
                "silver_root": data.silver_root / dataset,
                "gold_root": data.gold_root / dataset,
            }
        )
    if series_type is not None:
        data = data.model_copy(update={"series_type": series_type})
    calendar = ExchangeCalendar.from_path(calendar_override) if calendar_override else None
    manifest = ingest_file(path, instrument, sessions, data, calendar)
    typer.echo(f"Built dataset {manifest.dataset_id} with {manifest.row_count} bars.")


@backtest_app.command("run")
def run_backtest(
    config_dir: Path = typer.Option(Path("config")),
    results_root: Path = typer.Option(Path("results")),
    run_id: str | None = typer.Option(None),
    calendar_override: Path | None = typer.Option(None),
    dataset: str = typer.Option(
        "center", help="Dataset namespace used during ingestion (for example: forward)."
    ),
) -> None:
    """Run the baseline always-flat strategy from Gold Parquet, never source CSV."""
    from n225m_bt.backtest.engine import BacktestEngine
    from n225m_bt.calendar.classifier import CalendarClassifier
    from n225m_bt.calendar.model import ExchangeCalendar
    from n225m_bt.domain import Bar, Session
    from n225m_bt.io.parquet import scan_bars
    from n225m_bt.reports.writer import write_results
    from n225m_bt.strategies.examples import AlwaysFlatStrategy

    instrument, sessions, data, backtest = load_project_config(config_dir)
    if Path(dataset).name != dataset or dataset in {"", ".", ".."}:
        raise typer.BadParameter("dataset must be a single directory name")
    if dataset != "center":
        data = data.model_copy(
            update={
                "silver_root": data.silver_root / dataset,
                "gold_root": data.gold_root / dataset,
            }
        )
    rows = scan_bars(data.gold_root).sort("ts_jst").collect().to_dicts()
    bars = [
        Bar(
            ts_jst=row["ts_jst"],
            trade_date=row["trade_date"],
            calendar_date=row["calendar_date"],
            session=Session(row["session"]),
            schedule_version=row["schedule_version"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            is_session_open=row["is_session_open"],
            is_session_close=row["is_session_close"],
            is_missing_prev_expected=row["is_missing_prev_expected"],
            roll_risk=row["roll_risk"],
            is_eligible=row["is_eligible"],
            quality_flags=tuple(row["quality_flags"]),
        )
        for row in rows
    ]
    strategy = AlwaysFlatStrategy()
    calendar = ExchangeCalendar.from_path(calendar_override) if calendar_override else None
    result = BacktestEngine(
        instrument.instrument.to_spec(), backtest, CalendarClassifier(sessions, calendar)
    ).run(bars, strategy)
    resolved_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = results_root / resolved_run_id
    dataset_manifest_path = data.silver_root / "dataset_manifest.json"
    dataset_manifest = json.loads(dataset_manifest_path.read_text(encoding="utf-8"))
    manifest = {
        "run_id": resolved_run_id,
        "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": dataset_manifest["dataset_id"],
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.strategy_version,
        "parameter_hash": "",
        "code_version": __import__("n225m_bt").__version__,
        "config": backtest.model_dump(mode="json"),
    }
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "config_snapshot"
    snapshot.mkdir(exist_ok=True)
    for name, config in {
        "instrument": instrument,
        "sessions": sessions,
        "data": data,
        "backtest": backtest,
    }.items():
        (snapshot / f"{name}.yaml").write_text(
            yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
    write_results(output, result.trades, result.equity, manifest)
    typer.echo(f"Wrote {output}")


@app.command("benchmark-parquet")
def benchmark_parquet(root: Path) -> None:
    """Report a canonical Parquet scan rate without touching raw source data."""
    from n225m_bt.performance import benchmark_parquet_scan

    result = benchmark_parquet_scan(root)
    typer.echo(
        f"rows={result.row_count} elapsed_seconds={result.elapsed_seconds:.3f} "
        f"rows_per_second={result.rows_per_second:.0f}"
    )


if __name__ == "__main__":
    app()
