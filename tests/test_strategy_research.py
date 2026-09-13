from __future__ import annotations

import json
import shutil
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest
import yaml
from test_reports import sample_trade

from n225m_bt.backtest.engine import BacktestEngine
from n225m_bt.calendar.classifier import CalendarClassifier
from n225m_bt.config import JST, load_project_config, load_research_config
from n225m_bt.domain import Bar, ExitReason, Session, Side
from n225m_bt.research.data import ResearchData, load_split, partition_paths, validate_splits
from n225m_bt.research.metrics import drawdown, ledger_metrics, research_metrics
from n225m_bt.research.robustness import resample, walk_forward
from n225m_bt.research.runner import evaluate, reserve_directory
from n225m_bt.strategies.opening import OpeningStrategy


def opening_bars(day: date = date(2024, 11, 5)) -> list[Bar]:
    start = datetime.combine(day, datetime.min.time().replace(hour=8, minute=45), JST)
    bars = []
    for minute in range(12):
        price = 40000 + minute * 5
        bars.append(
            Bar(
                start + timedelta(minutes=minute),
                day,
                day,
                Session.DAY,
                "test",
                price,
                price + 5,
                price - 5,
                price + 5,
                is_session_open=minute == 0,
            )
        )
    return bars


def test_opening_signal_next_open_and_exact_fees() -> None:
    instrument, sessions, _, config = load_project_config(Path("config"))
    bars = opening_bars()
    result = BacktestEngine(
        instrument.instrument.to_spec(), config, CalendarClassifier(sessions)
    ).run(bars, OpeningStrategy("opening_momentum", 3, 2, bars[0].ts_jst))
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_signal_ts == bars[2].ts_jst
    assert trade.entry_ts == bars[3].ts_jst
    assert trade.exit_ts == bars[5].ts_jst
    assert trade.exit_reason is ExitReason.SIGNAL
    assert trade.entry_fill_price == 40020
    assert trade.exit_fill_price == 40020
    assert trade.gross_pnl_jpy == 0
    assert trade.fees_jpy == 60
    assert trade.net_pnl_jpy == -60
    assert trade.slippage_cost_jpy == 1000


def test_future_prices_do_not_change_opening_signal_and_missing_opening_abstains() -> None:
    instrument, sessions, _, config = load_project_config(Path("config"))
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    bars = opening_bars()
    changed = bars[:3] + [
        replace(b, open=30000, high=30005, low=29995, close=30000) for b in bars[3:]
    ]
    original = engine.run(bars, OpeningStrategy("opening_momentum", 3, 2, bars[0].ts_jst))
    future_changed = engine.run(changed, OpeningStrategy("opening_momentum", 3, 2, bars[0].ts_jst))
    assert original.trades[0].entry_signal_ts == future_changed.trades[0].entry_signal_ts
    assert original.trades[0].side is future_changed.trades[0].side is Side.LONG
    missing = engine.run(
        bars[:1] + bars[2:], OpeningStrategy("opening_momentum", 3, 2, bars[0].ts_jst)
    )
    assert not missing.trades
    ineligible = [replace(b, is_eligible=False) if i == 1 else b for i, b in enumerate(bars)]
    assert not engine.run(
        ineligible, OpeningStrategy("opening_momentum", 3, 2, bars[0].ts_jst)
    ).trades


def test_delay_and_session_reset_are_economically_equivalent_to_one_engine_run() -> None:
    instrument, sessions, _, config = load_project_config(Path("config"))
    bars = opening_bars()
    classifier = CalendarClassifier(sessions)
    baseline = BacktestEngine(instrument.instrument.to_spec(), config, classifier).run(
        bars, OpeningStrategy("opening_reversal", 3, 2, bars[0].ts_jst, 1, 1)
    )
    evaluated, audit = evaluate(
        ResearchData(bars, "synthetic", {}),
        "opening_reversal",
        3,
        2,
        instrument.instrument.to_spec(),
        config,
        classifier,
        1,
        1,
    )
    assert replace(evaluated[0], parameter_hash="") == baseline.trades[0]
    assert evaluated[0].entry_ts == bars[4].ts_jst
    assert evaluated[0].exit_ts == bars[7].ts_jst
    assert audit["end_of_data_exits"] == 0


def test_breakout_uses_frozen_range_and_fills_after_breakout_close() -> None:
    instrument, sessions, _, config = load_project_config(Path("config"))
    bars = opening_bars()
    # A large high on the breakout candle must not move the already frozen opening range.
    bars[3] = replace(bars[3], high=41000)
    engine = BacktestEngine(instrument.instrument.to_spec(), config, CalendarClassifier(sessions))
    result = engine.run(bars, OpeningStrategy("opening_breakout", 3, 2, bars[0].ts_jst))
    trade = result.trades[0]
    assert trade.entry_signal_ts == bars[3].ts_jst
    assert trade.entry_ts == bars[4].ts_jst
    assert trade.exit_ts == bars[6].ts_jst
    assert trade.side is Side.LONG
    assert trade.net_pnl_jpy == -60
    no_break = bars[:3] + [
        replace(b, open=40000, high=40015, low=39995, close=40015) for b in bars[3:]
    ]
    assert not engine.run(
        no_break, OpeningStrategy("opening_breakout", 3, 2, bars[0].ts_jst)
    ).trades
    lower_break = bars[:3] + [
        replace(b, open=39990, high=39995, low=39980, close=39985) for b in bars[3:]
    ]
    short = engine.run(lower_break, OpeningStrategy("opening_breakout", 3, 2, bars[0].ts_jst))
    assert short.trades[0].side is Side.SHORT


def test_holdout_partition_never_read_and_trade_date_boundary(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid4().hex / "gold"
    safe = root / "year=2025" / "month=06"
    safe.mkdir(parents=True)
    bars = opening_bars(date(2025, 6, 30))
    rows = [asdict(bars[0]), asdict(replace(bars[1], trade_date=date(2025, 7, 1)))]
    for row in rows:
        row.update(instrument="N225M", series_type="center_continuous", source_file="synthetic")
    pl.DataFrame(rows).write_parquet(safe / "bars.parquet")
    holdout = root / "year=2026" / "month=01"
    holdout.mkdir(parents=True)
    (holdout / "bars.parquet").write_bytes(b"UNREADABLE HOLDOUT SENTINEL")
    forward = root / "forward" / "year=2025" / "month=06"
    forward.mkdir(parents=True)
    (forward / "bars.parquet").write_bytes(b"UNREADABLE FORWARD SENTINEL")
    loaded = load_split(root, "development")
    assert len(loaded.bars) == 1
    assert loaded.bars[0].trade_date == date(2025, 6, 30)
    assert load_split(root, "out_of_sample").bars[0].trade_date == date(2025, 7, 1)
    with pytest.raises(ValueError, match="locked"):
        partition_paths(root, "final_holdout")  # type: ignore[arg-type]
    validate_splits(load_research_config(Path("config")))


def test_identical_overlap_is_reported_but_conflicting_duplicate_fails(workspace_tmp: Path) -> None:
    root = workspace_tmp / uuid4().hex
    partition = root / "year=2024" / "month=11"
    partition.mkdir(parents=True)
    row = asdict(opening_bars()[0]) | {
        "instrument": "N225M",
        "series_type": "center_continuous",
        "source_file": "annual_a",
    }
    duplicate = row | {"source_file": "annual_b"}
    pl.DataFrame([row, duplicate]).write_parquet(partition / "bars.parquet")
    loaded = load_split(root, "development")
    assert len(loaded.bars) == 1
    assert loaded.quality["identical_duplicate_rows_collapsed"] == 1
    pl.DataFrame([row, duplicate | {"close": row["close"] + 5}]).write_parquet(
        partition / "bars.parquet"
    )
    with pytest.raises(ValueError, match="conflicting canonical"):
        load_split(root, "development")


def test_drawdown_and_nontrading_days_and_resampling() -> None:
    assert drawdown([-100, 200, -300]) == 300
    trade = sample_trade()
    all_win = replace(trade, net_pnl_jpy=100)
    assert ledger_metrics((all_win,))["profit_factor"] is None
    bars = opening_bars() + opening_bars(date(2024, 11, 6))
    metrics = research_metrics((trade,), bars)
    assert metrics["daily_net_pnl_jpy"] == {"2024-11-05": -1200, "2024-11-06": 0}
    result = resample((trade, all_win), 225, 100)
    assert result == resample((trade, all_win), 225, 100)
    assert result["shuffle"]["net_pnl_jpy"]["p05"] == -1100


def test_walk_forward_selection_cannot_see_test_profit() -> None:
    trade = sample_trade()
    training = replace(trade, trade_date=date(2021, 6, 1), net_pnl_jpy=-100)
    future_winner = replace(trade, trade_date=date(2022, 2, 1), net_pnl_jpy=1000000)
    result = walk_forward(
        {(30, 60): (training, future_winner)}, (30, 60), 1, date(2021, 1, 1), date(2022, 3, 31)
    )
    assert result["active_folds"] == 0
    assert result["overall"]["trade_count"] == 0
    positive = replace(training, net_pnl_jpy=100)
    result = walk_forward(
        {(30, 60): (positive, future_winner)}, (30, 60), 1, date(2021, 1, 1), date(2022, 3, 31)
    )
    assert result["active_folds"] == 1
    assert result["overall"]["net_pnl_jpy"] == 1000000


def test_existing_experiment_is_never_overwritten(workspace_tmp: Path) -> None:
    tmp_path = workspace_tmp / uuid4().hex
    path = reserve_directory(tmp_path, "experiment")
    (path / "sentinel").write_text("keep")
    with pytest.raises(FileExistsError):
        reserve_directory(tmp_path, "experiment")
    assert (path / "sentinel").read_text() == "keep"
    with pytest.raises(ValueError):
        reserve_directory(tmp_path, "../escape")


def test_complete_campaign_rejects_without_opening_oos_and_reports(
    workspace_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from n225m_bt.research import runner
    from n225m_bt.research.report import render_campaign

    root = workspace_tmp / uuid4().hex
    config_dir = root / "config"
    config_dir.mkdir(parents=True)
    for name in ("instrument", "sessions", "data", "backtest", "research"):
        shutil.copyfile(Path("config") / f"{name}.yaml", config_dir / f"{name}.yaml")
    settings = yaml.safe_load(Path("config/strategy_research.yaml").read_text())
    settings.update(
        lookback_minutes=[3],
        holding_minutes=[2],
        representative_lookback=3,
        representative_holding=2,
        minimum_positive_neighbors=1,
        resamples=100,
    )
    (config_dir / "strategy_research.yaml").write_text(yaml.safe_dump(settings))
    calendar = root / "calendar.yaml"
    calendar.write_text(
        yaml.safe_dump(
            {
                "trading_days": [
                    {
                        "trade_date": "2024-11-05",
                        "previous_trade_date": "2024-11-04",
                        "next_trade_date": "2024-11-06",
                        "night_calendar_start_date": "2024-11-04",
                        "is_holiday_trading_day": False,
                        "schedule_version": "test",
                    }
                ]
            }
        )
    )
    loaded = []

    def synthetic_split(path: Path, split: str) -> ResearchData:
        loaded.append(split)
        assert split == "development", "OOS was opened despite insufficient Development evidence"
        return ResearchData(opening_bars(), "synthetic", {"rows": 12})

    monkeypatch.setattr(runner, "load_split", synthetic_split)
    monkeypatch.setattr(
        runner, "snapshot_source", lambda *args: {"git_commit": "test", "source_hash": "test"}
    )
    output = runner.run_campaign(
        config_dir, root / "results", calendar, "synthetic", lambda _: None
    )
    assert loaded == ["development"]
    completion = json.loads((output / "COMPLETED.json").read_text())
    assert completion["experiments"] == 14
    assert set(completion["decisions"].values()) == {"REJECT"}
    report = render_campaign(output)
    assert (report / "research_report.md").is_file()
    assert len(list(report.glob("*-daily_equity.parquet"))) == 14
    with pytest.raises(FileExistsError):
        runner.run_campaign(config_dir, root / "results", calendar, "synthetic", lambda _: None)
