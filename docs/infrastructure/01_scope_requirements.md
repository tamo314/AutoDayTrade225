# 01. Scope and Requirements

## 1. Target instrument

- Product: Nikkei 225 mini futures
- Internal symbol: `N225M`
- Currency: JPY
- Time zone: `Asia/Tokyo`
- Contract unit: Nikkei 225 × 100 JPY
- Tick size: 5 JPY
- Tick value: 500 JPY per contract

## 2. Initial data source

225Labo, 1-minute center-contract continuous series, user-downloaded.

Target initial history: 2021 through latest available year. The implementation must support older years without structural changes.

Optional second series: 225Labo 3-month-ahead series. It is stored independently and is not required for the first backtest.

## 3. In scope for V1

- Ingest local CSV/ZIP/text files supplied by the user.
- Detect/validate source encoding and column mapping.
- Normalize to a canonical minute-bar model.
- Trade-date/calendar-date/session classification.
- Historical session schedule support.
- Data quality report.
- Parquet storage partitioned for efficient reads.
- Strategy plug-in interface.
- One-position event-driven backtest.
- Market entry/exit.
- Stop loss and take profit.
- Conservative same-bar stop/target handling.
- Configurable tick slippage.
- Configurable fees.
- Day-only, night-only, full-session modes.
- End-of-session forced exit option.
- Trade ledger and equity curve.
- Performance metrics and segmented reports.
- Reproducibility metadata.

## 4. Explicitly out of scope for V1

- Live broker connectivity.
- Order-book/Level 2 simulation.
- Tick-by-tick path reconstruction.
- Limit-order queue modeling.
- Partial fills.
- Multi-contract position scaling.
- Portfolio/multi-asset trading.
- Options.
- Margin/SPAN/VaR simulation.
- Machine learning pipeline.
- Automatic download/login from 225Labo.
- Redistribution of third-party data.

## 5. Functional requirements

### FR-01 Ingestion

The CLI can ingest one or more source files, map source columns, and write normalized Parquet without mutating source files.

### FR-02 Idempotency

Re-running ingestion with unchanged input/config produces the same normalized records and content hashes.

### FR-03 Time semantics

Every bar has an aware JST timestamp, exchange trade date, calendar date, session classification, and schedule version.

### FR-04 Data quality

The system produces a machine-readable and human-readable quality report including duplicates, missing expected minutes, tick violations, OHLC invariants, non-monotonic timestamps, and unexpected session records.

### FR-05 Backtest causality

No strategy may use future bars. A close-based signal fills no earlier than the next eligible bar.

### FR-06 Cost accounting

Gross PnL, fees, slippage and net PnL are separately visible per trade and in aggregate.

### FR-07 Reproducibility

Each run stores configuration snapshot, input dataset identifier/hash, code/version information if available, run timestamp, and strategy parameters.

## 6. Non-functional requirements

- A 5-year 1-minute dataset should be practical on a normal desktop.
- Normalized dataset load/filter should use columnar Parquet, not repeated CSV parsing.
- Core execution calculations must be deterministic.
- Error messages must identify the file/row/field involved when feasible.
- Unit tests must run without proprietary market data.

## 7. Research scope amendment — RG-20260915-01

最終目標は日経225miniの自動デイトレードによる収益システムである。V1はデータ整備・検証基盤であり、本改訂でもライブ証券接続、板・キュー、部分約定、証拠金シミュレーション等を実装済み範囲へ追加しない。

デフォルト研究は1枚・最大1ポジション・セッション内フラットを基本とする。夜間0時跨ぎと取引session終了後の休場跨ぎは区別する。R065のような持越し研究は [実行前レビュー](../strategy/15_r065_execution_review.md) の例外profileを別途設計・検証し、全戦略のデフォルトを変えない。収益が正でも実売買承認にはならない。

### FR-08 — Decision-time causality

価格・出来高・品質・カレンダー更新・roll情報について利用可能時刻を管理する。E_exec、特徴量、signal、orderは意思決定時刻までの情報だけで決める。将来placebo・exit・感度の可用性を主戦略の発注可否に使わない。

### FR-09 — Research governance and bounded exploration

family/study/spec/runを別IDで記録し、完全な条件表、予算、停止規則、既知情報を凍結する。単に異なる窓・閾値だから独立仮説とは扱わない。仕様の論理整合、情報量、経済性、機構診断、頑健性、OOS開封を段階分離する。

### FR-10 — Unknown outcomes and immutable history

既知無取引0円と、未完了建玉・不明損益nullを分ける。原文・raw・旧Gold・旧runを上書きせず、技術訂正・新仕様・新データ版を追跡する。変更後の未実施テストをPASSと初期化しない。

### FR-11 — Promotion boundary

PASS_LIMITEDのDevelopment結果をそのままCANDIDATEへ昇格させない。PASS_RESEARCH、実行・因果性の監査、凍結した経済・頑健性条件、許容リスクと開封計画が必要。資本・DD等の未指定値は勝手に埋めない。

進捗は新規実験数だけでなく、重大な未解決点・再発不備・完全な仕様・独立検証へ進める候補を指標にする。
