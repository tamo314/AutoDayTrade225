# 10. Implementation Plan

Implement phases in order. Do not jump to strategy optimization before data/time semantics are tested.

## Phase 0 — Project skeleton

Deliver:
- `pyproject.toml`
- package layout
- CLI entry point
- typed config loader
- lint/test/type-check commands

Tests:
- load provided YAML configs
- reject malformed configs

## Phase 1 — Domain model and calendar/session engine

Deliver:
- enums/domain dataclasses or Pydantic models
- instrument spec
- versioned schedule rules
- session classifier
- trade/calendar date model

Tests:
- regime boundaries around 2021-09-21 and 2024-11-05
- day/night classification
- timestamp awareness
- invalid time detection

Do not yet ingest real proprietary data.

## Phase 2 — Source inspection and 225Labo adapter

Deliver:
- file hashing
- encoding/delimiter/header detection
- explicit/auto column mapping
- Bronze writer
- source lineage

Tests use synthetic CSV fixtures in CP932 and UTF-8.

## Phase 3 — Silver normalization and quality pipeline

Deliver:
- canonical bar schema
- timestamp reconstruction
- session/trade-date metadata
- Parquet output
- expected-minute grid
- quality checks/reports
- dataset manifest/id

Integration test: synthetic source -> Silver -> QC.

## Phase 4 — Gold dataset

Deliver:
- eligibility flag
- session minute indices
- minimal derived fields
- partition filtering

Avoid strategy-specific indicators here.

## Phase 5 — Backtest engine core

Deliver:
- order/fill models
- portfolio state
- next-bar market fills
- stop/target
- conservative intrabar resolution
- gap-through stop behavior
- slippage
- fees
- forced flat
- day/night/full modes

Regression tests for every economic rule.

## Phase 6 — Strategy API and baseline strategies

Deliver at least two trivial test strategies, not alpha claims:
- `AlwaysFlatStrategy`
- deterministic synthetic-test strategy (e.g. enter on configured timestamp)

Optional demonstration indicator strategy may be included but must be labeled example only.

## Phase 7 — Ledger, metrics, reports

Deliver:
- orders/fills/trades/equity Parquet
- metrics JSON/Markdown
- segmented stats
- slippage sweep

## Phase 8 — Reproducibility and hardening

Deliver:
- run manifest hashes
- idempotency checks
- logging
- performance sanity benchmark
- end-to-end CLI test
- documentation synchronization

## Phase 9 — User real-data onboarding

Once user places actual 225Labo files locally:
- run `data inspect`
- capture resolved source mapping in local config
- ingest one year first
- inspect quality report
- then ingest full history

Never add real downloaded files to Git.

## Phase R — Research repair before further PnL exploration

### R0 — 文書・run registry

旧Phases 0～9の実装が完了しているかは本改訂で再確認していない。新しい戦略最適化より先に、研究ID・仕様版・run・親子・無効化・既知情報・利用制限を整理する。原文や成果物を上書きしない。R031/R032などの監査注記を現行状態として追跡する。

### R1 — 仕様・因果性の共通部品

追加責務はSpecAudit、ConditionResolver、AsOfDataView、UniverseBuilder、OutcomeLedger、StageGate。既存コードへ適合させ、責務だけ共通化する。side/thresholdの暗黙default、Eの未来依存、QCの事後除外、event statusの上書きを合成テストで拒否する。

### R2 — データ意味の解決

barラベル・OHLC種別・volume・実限月・roll・調整を用途別に証拠化する。必要な実ファイル監査はDevelopmentのみで、監査仕様とアクセス範囲を先に固定する。未知をguess/round/fillで修復しない。新データ版を作る場合は原版と差分を保持する。

### R3 — 集計・検出能力・段階実行

固定予定軸、未知損益null、多軸decision、完全なpreregistrationとfamily予算を実装する。合成共有価格でゲート成立可能性を検査し、手順全体の検出感度を校正する。runtime制限対応はchunk/checkpointのhash整合で行い、成績を見て手順を省略しない。

### R4 — 限定再開

[共通規約](13_research_governance.md)の最大3仕様版のバッチだけを扱う。R065はcross-sessionと時刻注文の専用監査を前提にし、セッション独立runを流用しない。S3通過後にS4時間安定性、品質と候補を凍結後にOOS審査へ進む。新規実行は文書改訂と別の依頼として行う。

### Implementation acceptance

[11_acceptance_criteria.md](11_acceptance_criteria.md)の新規項目を実測する。コード・config・schema・CLIが未提供のため、今回のパッケージ作成ではプロジェクトpytest/Ruff/mypyを実行していない。テスト件数・PASS・実装済みAPIを先に記入しない。

詳細な依存・完了証拠は [修復・移行計画](14_research_repair_plan.md) を正本とする。元Phase番号を削除して過去の実装進捗を捏造しない。
