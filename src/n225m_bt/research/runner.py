"""Append-only, bounded experiments using the unchanged BacktestEngine."""

import json
import subprocess
import zipfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from itertools import accumulate
from pathlib import Path
from typing import cast
from uuid import uuid4

import polars as pl
import yaml

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.calendar.model import ExchangeCalendar
from n225m_bt.config import (
    BacktestConfig,
    load_project_config,
    load_research_config,
    load_yaml_model,
)
from n225m_bt.domain import Bar, ExitReason, InstrumentSpec, Session, Trade
from n225m_bt.io.manifest import canonical_hash
from n225m_bt.reports.writer import write_results
from n225m_bt.research.config import CampaignConfig, Family
from n225m_bt.research.data import SPLITS, ResearchData, Split, load_split, validate_splits
from n225m_bt.research.metrics import Metrics, adverse_exit_overlay, research_metrics
from n225m_bt.research.robustness import resample, walk_forward
from n225m_bt.strategies.opening import OpeningStrategy


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, default=str, allow_nan=False
        ),
        encoding="utf-8",
    )


def reserve_directory(root: Path, name: str) -> Path:
    if not name or name in {".", ".."} or any(c in name for c in "/\\:"):
        raise ValueError("experiment identifier must be one directory name")
    path = root / name
    path.mkdir(parents=True, exist_ok=False)
    return path


def snapshot_source(output: Path, config_dir: Path, calendar_path: Path) -> dict[str, object]:
    paths = sorted(
        {
            *Path("src").rglob("*.py"),
            *Path("docs/strategy").glob("*.md"),
            *[p for p in config_dir.glob("*.yaml") if not p.name.startswith("local")],
            Path("pyproject.toml"),
            Path("uv.lock"),
            Path("AGENTS.md"),
            Path("DECISIONS.md"),
        }
    )
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(output / "source_snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            content = path.read_bytes()
            hashes[path.as_posix()] = sha256(content).hexdigest()
            archive.writestr(path.as_posix(), content)
        calendar_content = calendar_path.read_bytes()
        hashes["calendar_snapshot.yaml"] = sha256(calendar_content).hexdigest()
        archive.writestr("calendar_snapshot.yaml", calendar_content)
    commit = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
        or "unavailable"
    )
    return {
        "git_commit": commit,
        "source_hash": canonical_hash(hashes),
        "file_hashes": hashes,
        "package_versions": {
            name: version(name) for name in ("polars", "pyarrow", "pydantic", "PyYAML", "typer")
        },
    }


@dataclass(frozen=True)
class Trial:
    trades: tuple[Trade, ...]
    metrics: dict[str, object]
    audit: dict[str, int]
    experiment_id: str


def overall(trial: Trial) -> Metrics:
    return cast(Metrics, trial.metrics["overall"])


def value(metrics: Metrics, key: str) -> float:
    result = metrics[key]
    return float(result) if result is not None else float("-inf")


def evaluate(
    data: ResearchData,
    family: Family,
    lookback: int,
    holding: int,
    spec: InstrumentSpec,
    config: BacktestConfig,
    classifier: CalendarClassifier,
    entry_delay: int = 0,
    exit_delay: int = 0,
) -> tuple[tuple[Trade, ...], dict[str, int]]:
    """Session-independent hypotheses; reset state only at predefined session boundaries.

    This bounds the existing engine's cumulative-ledger equity bookkeeping cost.
    Any residual END_OF_DATA trade is reported and prevents candidate promotion.
    """
    groups: dict[tuple[date, Session], list[Bar]] = defaultdict(list)
    for bar in data.bars:
        groups[(bar.trade_date, bar.session)].append(bar)
    trades: list[Trade] = []
    audit = dict.fromkeys(
        (
            "sessions",
            "incomplete_opening_sessions",
            "zero_direction_sessions",
            "canceled_orders",
            "end_of_data_exits",
            "force_flat_exits",
        ),
        0,
    )
    parameter_hash = canonical_hash(
        {
            "lookback": lookback,
            "holding": holding,
            "entry_delay": entry_delay,
            "exit_delay": exit_delay,
        }
    )
    engine = BacktestEngine(spec, config, classifier)
    for (trade_date, session), bars in groups.items():
        strategy = OpeningStrategy(
            family,
            lookback,
            holding,
            classifier.session_open(trade_date, session),
            entry_delay,
            exit_delay,
        )
        result = engine.run(bars, strategy, parameter_hash)
        audit["sessions"] += 1
        audit["incomplete_opening_sessions"] += int(
            strategy.invalid_opening or strategy.count != lookback
        )
        audit["zero_direction_sessions"] += int(
            not strategy.invalid_opening and strategy.count == lookback and strategy.direction == 0
        )
        audit["canceled_orders"] += result.canceled_orders
        for trade in result.trades:
            audit["end_of_data_exits"] += int(trade.exit_reason is ExitReason.END_OF_DATA)
            audit["force_flat_exits"] += int(trade.exit_reason is ExitReason.FORCE_FLAT)
            trades.append(trade)
    trades.sort(key=lambda t: t.entry_ts)
    return tuple(replace(t, trade_id=f"trade-{i:06d}") for i, t in enumerate(trades, 1)), audit


def run_campaign(
    config_dir: Path,
    results_root: Path,
    calendar_path: Path,
    campaign_id: str | None = None,
    progress: Callable[[str], None] = print,
    study_config: Path | None = None,
    stage: str | None = None,
) -> Path:
    selected_study_config = study_config or config_dir / "strategy_research.yaml"
    study_payload = yaml.safe_load(selected_study_config.read_text(encoding="utf-8"))
    if isinstance(study_payload, dict) and study_payload.get("schema_version") == 2:
        from n225m_bt.research.r003 import run_r003_campaign

        return run_r003_campaign(
            config_dir,
            results_root,
            calendar_path,
            campaign_id,
            progress,
            selected_study_config,
            stage,
        )
    if stage is not None:
        raise ValueError("--stage is only supported by the explicit R003 study schema")
    instrument, sessions, data_config, baseline = load_project_config(config_dir)
    settings = load_yaml_model(
        study_config or config_dir / "strategy_research.yaml", CampaignConfig
    )
    split_config = load_research_config(config_dir)
    validate_splits(split_config)
    if baseline.fees.jpy_per_side_per_contract <= 0:
        raise ValueError("research ranking requires a positive explicit fee")
    if baseline.mode != "full_session" or not baseline.risk.force_flat:
        raise ValueError("R001 preregisters full_session with force_flat")
    if baseline.execution.slippage_ticks != 1:
        raise ValueError("baseline research ranking requires one tick per execution")
    if (
        split_config.walk_forward.train_months,
        split_config.walk_forward.test_months,
        split_config.walk_forward.step_months,
    ) != (12, 3, 3) or not split_config.walk_forward.enabled:
        raise ValueError("R001 preregisters 12/3/3 walk forward")
    identifier = (
        campaign_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    )
    output = reserve_directory(results_root, identifier)
    source = snapshot_source(output, config_dir, calendar_path)
    hypothesis = settings.hypothesis_document.read_text(encoding="utf-8-sig")
    write_json(
        output / "campaign_manifest.json",
        {
            "campaign_id": identifier,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **source,
            "settings": settings.model_dump(mode="json"),
            "splits": split_config.model_dump(mode="json"),
            "status": "running",
            "holdout_access": "locked",
            "hypothesis_hash": canonical_hash(hypothesis),
        },
    )
    classifier = CalendarClassifier(sessions, ExchangeCalendar.from_path(calendar_path))
    spec = instrument.instrument.to_spec()
    progress("Loading Development only (2021-01-01..2025-06-30).")
    development = load_split(data_config.gold_root, "development")
    write_json(output / "development_quality.json", development.quality)
    counter = 0

    def trial(
        data: ResearchData,
        split: Split,
        family: Family,
        lookback: int,
        holding: int,
        label: str,
        ticks: int = 1,
        fee_multiplier: int = 1,
        entry_delay: int = 0,
        exit_delay: int = 0,
    ) -> Trial:
        nonlocal counter
        counter += 1
        experiment_id = f"{identifier}-{counter:03d}-{family}-L{lookback}-H{holding}-{label}"
        folder = reserve_directory(output, experiment_id)
        cost = baseline.model_copy(
            update={
                "execution": baseline.execution.model_copy(update={"slippage_ticks": ticks}),
                "fees": baseline.fees.model_copy(
                    update={
                        "jpy_per_side_per_contract": baseline.fees.jpy_per_side_per_contract
                        * fee_multiplier
                    }
                ),
            }
        )
        parameters = {
            "lookback_minutes": lookback,
            "holding_minutes": holding,
            "entry_delay_minutes": entry_delay,
            "exit_delay_minutes": exit_delay,
        }
        manifest = {
            "experiment_id": experiment_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_commit": source["git_commit"],
            "source_hash": source["source_hash"],
            "source_snapshot": "../source_snapshot.zip",
            "data_version": data.data_version,
            "strategy_name": family,
            "strategy_version": OpeningStrategy.strategy_version,
            "parameters": parameters,
            "cost_model": cost.model_dump(mode="json"),
            "data_range": SPLITS[split].model_dump(mode="json"),
            "split": split,
            "seed": settings.seed,
            "status": "running",
        }
        write_json(folder / "run_manifest.json", manifest)
        (folder / "hypothesis.md").write_text(hypothesis, encoding="utf-8")
        (folder / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "parameters": parameters,
                    "backtest": cost.model_dump(mode="json"),
                    "research": settings.model_dump(mode="json"),
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        progress(f"{counter:02d} {split} {family} L={lookback} H={holding} {label}")
        trades, audit = evaluate(
            data, family, lookback, holding, spec, cost, classifier, entry_delay, exit_delay
        )
        metrics = research_metrics(trades, data.bars)
        # Keep the established ledger serializer. Research metrics add risk/statistical fields.
        write_results(folder, trades, (), manifest | {"status": "complete", "audit": audit})
        write_json(folder / "metrics.json", metrics)
        daily = cast(dict[str, int], metrics["daily_net_pnl_jpy"])
        days = sorted(daily)
        pl.DataFrame(
            {
                "trade_date": [date.fromisoformat(day) for day in days],
                "daily_net_pnl_jpy": [daily[day] for day in days],
                "realized_equity_jpy": list(accumulate(daily[day] for day in days)),
            }
        ).write_parquet(folder / "equity.parquet")
        write_json(folder / "quality_summary.json", data.quality | {"execution_audit": audit})
        summary = (
            f"# {experiment_id}\n\nHypothesis: {family}; see hypothesis.md.\n\n"
            f"Implementation: unchanged engine, session-independent opening/time exit.\n\n"
            f"Parameters tested: {parameters}; {ticks} tick/side, "
            f"{cost.fees.jpy_per_side_per_contract} JPY/side.\n\n"
            f"Development result: {'see metrics.json' if split == 'development' else 'see campaign'}.\n\n"
            f"Validation result: {'see metrics.json' if split == 'out_of_sample' else 'OOS gated; holdout locked'}.\n\n"
            "Robustness result: see campaign family_decisions.json and related stress runs.\n\n"
            f"Problems discovered: {audit}.\n\n"
            "Decision: INVESTIGATE pending campaign aggregation; final decision in family_decisions.json.\n\n"
            "Next experiment: follow the preregistered campaign gates.\n"
        )
        (folder / "summary.md").write_text(summary, encoding="utf-8")
        return Trial(trades, metrics, audit, experiment_id)

    representative = (settings.representative_lookback, settings.representative_holding)
    grids: dict[Family, dict[tuple[int, int], Trial]] = {}
    for family in settings.families:
        grids[family] = {
            (lookback, holding): trial(
                development, "development", family, lookback, holding, "base"
            )
            for lookback in settings.lookback_minutes
            for holding in settings.holding_minutes
        }
    sensitivity = {
        family: [
            {
                "lookback": key[0],
                "holding": key[1],
                "experiment_id": run.experiment_id,
                **overall(run),
            }
            for key, run in grid.items()
        ]
        for family, grid in grids.items()
    }
    write_json(output / "sensitivity.json", sensitivity)
    decisions: dict[str, dict[str, object]] = {}
    eligible: list[Family] = []
    for family, grid in grids.items():
        base = grid[representative]
        stresses: dict[str, Trial] = {"1tick": base}
        for ticks in (0, 2, 3):
            stresses[f"{ticks}tick"] = trial(
                development, "development", family, *representative, f"{ticks}tick", ticks=ticks
            )
        for label, kwargs in (
            ("double_fee", {"fee_multiplier": 2}),
            ("entry_delay_1m", {"entry_delay": 1}),
            ("exit_delay_1m", {"exit_delay": 1}),
        ):
            stresses[label] = trial(
                development, "development", family, *representative, label, **kwargs
            )
        wfa = walk_forward(
            {key: run.trades for key, run in grid.items()},
            representative,
            settings.minimum_positive_neighbors,
            SPLITS["development"].start,
            SPLITS["development"].end,
        )
        concentrated = cast(Metrics, base.metrics["concentration"])
        overlay = adverse_exit_overlay(base.trades, development.bars)
        checks = {
            "minimum_trades": len(base.trades) >= settings.minimum_development_trades,
            "positive_baseline": value(overall(base), "expectancy_jpy") > 0,
            "positive_2tick": value(overall(stresses["2tick"]), "expectancy_jpy") > 0,
            "parameter_stability": sum(
                value(overall(run), "expectancy_jpy") > 0 for run in grid.values()
            )
            >= settings.minimum_positive_neighbors,
            "positive_months": value(concentrated, "positive_month_fraction") >= 0.5,
            "without_top10": value(concentrated, "net_excluding_top10_jpy") > 0,
            "walk_forward": bool(wfa["passes"]),
            "fee_and_delay_stress": all(
                value(overall(stresses[key]), "expectancy_jpy") > 0
                for key in ("double_fee", "entry_delay_1m", "exit_delay_1m")
            ),
            "no_truncated_positions": all(
                run.audit["end_of_data_exits"] == 0 for run in stresses.values()
            ),
            "adverse_exit_overlay": value(overlay, "expectancy_jpy_matched_only") > 0
            and overlay["unavailable_trades"] == 0,
        }
        decision: dict[str, object] = {
            "representative": representative,
            "development_checks": checks,
            "decision": "INVESTIGATE" if all(checks.values()) else "REJECT",
            "stress": {
                label: {
                    "experiment_id": run.experiment_id,
                    "metrics": overall(run),
                    "audit": run.audit,
                }
                for label, run in stresses.items()
            },
            "walk_forward": wfa,
            "adverse_exit_overlay": overlay,
            "resampling": resample(base.trades, settings.seed, settings.resamples),
            "oos_status": "frozen_pending" if all(checks.values()) else "not_opened",
            "holdout_status": "not_opened",
        }
        decisions[family] = decision
        if all(checks.values()):
            eligible.append(family)
    # Concrete candidate selection is persisted BEFORE the OOS loader can run.
    write_json(
        output / "development_freeze.json",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_hash": source["source_hash"],
            "eligible_families": eligible,
            "representative": representative,
            "decisions": decisions,
        },
    )
    if eligible:
        oos = load_split(data_config.gold_root, "out_of_sample")
        write_json(output / "oos_quality.json", oos.quality)
        for family in eligible:
            runs = {
                str(tick): trial(
                    oos, "out_of_sample", family, *representative, f"oos-{tick}tick", ticks=tick
                )
                for tick in (0, 1, 2, 3)
            }
            base = runs["1"]
            for label, kwargs in (
                ("double_fee", {"fee_multiplier": 2}),
                ("entry_delay_1m", {"entry_delay": 1}),
                ("exit_delay_1m", {"exit_delay": 1}),
            ):
                runs[label] = trial(
                    oos, "out_of_sample", family, *representative, f"oos-{label}", **kwargs
                )
            concentrated = cast(Metrics, base.metrics["concentration"])
            oos_checks = {
                "minimum_trades": len(base.trades) >= settings.minimum_oos_trades,
                "positive_1_and_2tick": all(
                    value(overall(runs[key]), "expectancy_jpy") > 0 for key in ("1", "2")
                ),
                "without_top5": value(concentrated, "net_excluding_top5_jpy") > 0,
                "no_truncated_positions": all(
                    run.audit["end_of_data_exits"] == 0 for run in runs.values()
                ),
                "fee_and_delay_stress": all(
                    value(overall(runs[key]), "expectancy_jpy") > 0
                    for key in ("double_fee", "entry_delay_1m", "exit_delay_1m")
                ),
            }
            decisions[family].update(
                {
                    "oos_status": "evaluated_once",
                    "oos_checks": oos_checks,
                    "oos_runs": {
                        key: {"experiment_id": run.experiment_id, "metrics": overall(run)}
                        for key, run in runs.items()
                    },
                    "oos_resampling": resample(base.trades, settings.seed, settings.resamples),
                    "oos_adverse_exit_overlay": adverse_exit_overlay(base.trades, oos.bars),
                    "decision": "CANDIDATE" if all(oos_checks.values()) else "REJECT",
                }
            )
    write_json(output / "family_decisions.json", decisions)
    write_json(
        output / "COMPLETED.json",
        {
            "campaign_id": identifier,
            "experiments": counter,
            "decisions": {key: d["decision"] for key, d in decisions.items()},
            "holdout_read": False,
        },
    )
    progress(f"Completed {counter} experiments: {output}")
    return output
