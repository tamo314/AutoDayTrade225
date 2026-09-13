"""Read saved experiments and write a new report; never reopen market data."""

import json
import platform
from datetime import date, datetime, timezone
from importlib.metadata import version
from itertools import accumulate
from pathlib import Path
from typing import Any
from uuid import uuid4

import polars as pl


def render_campaign(campaign: Path) -> Path:
    """Create append-only final summaries and daily equity from completed experiment metrics."""
    if not (campaign / "COMPLETED.json").is_file():
        raise ValueError("cannot publish a research report for an incomplete campaign")
    decisions: dict[str, Any] = json.loads(
        (campaign / "family_decisions.json").read_text(encoding="utf-8")
    )
    folder = (
        campaign
        / "reports"
        / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    )
    folder.mkdir(parents=True, exist_ok=False)
    environment = {
        "python": platform.python_version(),
        "packages": {
            name: version(name) for name in ("polars", "pyarrow", "pydantic", "PyYAML", "typer")
        },
    }
    (folder / "report_environment.json").write_text(
        json.dumps(environment, indent=2), encoding="utf-8"
    )
    lines = [
        "# Strategy research report",
        "",
        f"Campaign: {campaign.name}",
        "",
        "Final Holdout: not opened. Final family decisions below supersede provisional per-trial summaries.",
        "",
        "|Family|Decision|OOS|",
        "|---|---|---|",
    ]
    for family, decision in decisions.items():
        lines.append(f"|{family}|{decision['decision']}|{decision['oos_status']}|")
    lines += ["", "## Parameter sensitivity (Development expectancy JPY/trade)", ""]
    sensitivity: dict[str, Any] = json.loads(
        (campaign / "sensitivity.json").read_text(encoding="utf-8")
    )
    for family, grid in sensitivity.items():
        lookbacks = sorted({point["lookback"] for point in grid})
        holdings = sorted({point["holding"] for point in grid})
        lines += [
            f"### {family}",
            "",
            "|Lookback / Holding|" + "|".join(map(str, holdings)) + "|",
            "|---|" + "---:|" * len(holdings),
        ]
        for lookback in lookbacks:
            cells = [
                next(
                    point["expectancy_jpy"]
                    for point in grid
                    if point["lookback"] == lookback and point["holding"] == holding
                )
                for holding in holdings
            ]
            lines.append(
                f"|{lookback}|"
                + "|".join(f"{cell:,.1f}" if cell is not None else "N/A" for cell in cells)
                + "|"
            )
        lines.append("")
    lines += [
        "## Experiments",
        "",
        "|ID|Split|Net JPY|Trades|Expectancy JPY|",
        "|---|---|---:|---:|---:|",
    ]
    for metrics_path in sorted(campaign.glob("*/metrics.json")):
        trial = metrics_path.parent
        metrics: dict[str, Any] = json.loads(metrics_path.read_text(encoding="utf-8"))
        manifest: dict[str, Any] = json.loads(
            (trial / "run_manifest.json").read_text(encoding="utf-8")
        )
        family = manifest["strategy_name"]
        overall = metrics["overall"]
        # Verify serialized economics before promoting the report, without recomputing fills.
        ledger = pl.read_parquet(trial / "trades.parquet")
        for field in ("net_pnl_jpy", "gross_pnl_jpy", "fees_jpy", "slippage_cost_jpy"):
            total = ledger[field].sum() if ledger.height else 0
            if total != overall[field]:
                raise ValueError(f"ledger/metrics mismatch: {trial.name} {field}")
        if ledger.height != overall["trade_count"]:
            raise ValueError(f"ledger/metrics trade count mismatch: {trial.name}")
        daily = metrics["daily_net_pnl_jpy"]
        days = sorted(daily)
        equity = list(accumulate(int(daily[day]) for day in days))
        if (equity[-1] if equity else 0) != overall["net_pnl_jpy"]:
            raise ValueError(f"daily equity/ledger mismatch: {trial.name}")
        pl.DataFrame(
            {
                "trade_date": [date.fromisoformat(day) for day in days],
                "daily_net_pnl_jpy": [daily[day] for day in days],
                "realized_equity_jpy": equity,
            }
        ).write_parquet(folder / f"{trial.name}-daily_equity.parquet")
        summary = [
            f"# {trial.name}",
            "",
            f"Hypothesis: {family}; see original hypothesis.md.",
            "",
            "Implementation: OpeningStrategy v1, existing BacktestEngine.",
            "",
            f"Parameters tested: {manifest['parameters']}",
            "",
            f"Development result: {'metrics below' if manifest['split'] == 'development' else 'see campaign'}.",
            "",
            f"Validation result: {decisions[family]['oos_status']}; Final Holdout unopened.",
            "",
            "Robustness result: see family_decisions.json for stress, WFA, resampling and adverse exit overlay.",
            "",
            f"Problems discovered: {manifest['audit']}; data overlap policy in quality_summary.json.",
            "",
            f"Decision: {decisions[family]['decision']} (family-level, all preregistered evidence).",
            "",
            "Next experiment: see docs/strategy/04_research_results.md; do not retune on OOS.",
            "",
            "## Metrics",
            "",
        ]
        summary.extend(f"- {key}: {result}" for key, result in overall.items())
        summary += ["", "## Concentration", ""]
        summary.extend(f"- {key}: {result}" for key, result in metrics["concentration"].items())
        (folder / f"{trial.name}-summary.md").write_text(
            "\n".join(summary) + "\n", encoding="utf-8"
        )
        lines.append(
            f"|[{trial.name}]({trial.name}-summary.md)|{manifest['split']}|{overall['net_pnl_jpy']:,}|{overall['trade_count']}|{overall['expectancy_jpy']}|"
        )
    lines += [
        "",
        "Daily equity files in this report are reconstructed from saved daily PnL and include zero-trade dates. Close-marked DD remains in each original metrics.json.",
        "",
    ]
    (folder / "research_report.md").write_text("\n".join(lines), encoding="utf-8")
    return folder
