# 02. Architecture

## 1. Data flow

```text
User-owned source files
        |
        v
 data/raw/225labo/              immutable
        |
        v
 Ingest Adapter
  - decode
  - source mapping
  - provenance
        |
        v
 Bronze
  source-shaped parsed records
        |
        v
 Normalizer + Calendar Classifier
        |
        v
 Silver canonical minute bars
        |
        +--> Quality checks/reports
        |
        v
 Gold backtest dataset
        |
        v
 Feature pipeline
        |
        v
 Strategy --> Signal/Intent
        |
        v
 Execution Engine
        |
        +--> Slippage / Fees / Risk gates
        |
        v
 Portfolio state / Trade ledger
        |
        v
 Metrics + Reports
```

## 2. Python modules

```text
src/n225m_bt/
  __init__.py
  cli.py
  config.py
  domain.py

  ingest/
    base.py
    source_detect.py
    labo225.py

  normalize/
    bars.py
    timestamps.py

  calendar/
    model.py
    session_rules.py
    classifier.py

  quality/
    checks.py
    report.py

  io/
    parquet.py
    manifest.py

  features/
    base.py
    returns.py

  strategies/
    base.py
    examples.py

  backtest/
    engine.py
    execution.py
    orders.py
    portfolio.py
    costs.py
    intrabar.py

  reports/
    metrics.py
    segmentation.py
    writer.py
```

## 3. Interfaces

### DataSourceAdapter

Responsibilities:
- discover candidate files
- decode source
- resolve/match source columns
- emit typed source records
- attach provenance (`source_file`, `source_row`, source hash)

Must NOT:
- invent missing prices
- silently change dates
- assign fabricated contract months

### CalendarClassifier

Input: timestamp/date/time and configured session regimes.

Output:
- actual `ts_jst`
- `calendar_date`
- `trade_date`
- `session`
- `schedule_version`
- session open/close flags where determinable

### Strategy

Input: historical information available up to current bar only.

Output: intent/signal, not a fill.

Recommended minimal interface:

```python
class Strategy(Protocol):
    def on_bar(self, ctx: StrategyContext, bar: Bar) -> Signal | None: ...
```

### ExecutionModel

Input:
- pending order
- current bar
- instrument spec
- slippage/cost config

Output:
- fill or no fill

### Portfolio

Owns position state and realized/unrealized accounting. It must not compute technical indicators.

## 4. Layer boundaries

- Source parsing knows source quirks but not trading logic.
- Calendar logic knows exchange schedules but not strategy logic.
- Strategy knows market history but not file formats.
- Execution knows orders/fills but not indicator formulas.
- Reports consume ledgers/results but must not change PnL.

## 5. Storage layout

Suggested:

```text
data/
  raw/225labo/center/YYYY/
  raw/225labo/next/YYYY/
  bronze/source=225labo/series=center/year=YYYY/*.parquet
  silver/instrument=N225M/series=center/year=YYYY/month=MM/*.parquet
  gold/instrument=N225M/series=center/year=YYYY/month=MM/*.parquet

results/<run_id>/
  run_manifest.json
  config_snapshot/
  trades.parquet
  equity.parquet
  metrics.json
  metrics.md
  quality_summary.json
```

## 6. Dataset identity

Generate a dataset manifest containing:
- normalized schema version
- source file hashes
- source adapter version
- normalization config hash
- earliest/latest timestamps
- row count
- quality summary

Use a stable hash over canonical manifest content as `dataset_id`.

## 7. Research control plane — RG-20260915-01

以下は追加設計であり、同名モジュールが既存コードに存在すると仮定しない。実装担当は責務を保ち、実際のpackageへ統合する。

|論理部品|責務|禁止事項|
|---|---|---|
|StudyRegistry / Preregistration|family・仕様・試行・予算・既知情報・親子関係|再登録で既読成績を未使用に戻す|
|SpecAudit|ゲートの単位・依存・矛盾・共同成立可能性|任意の整合しない条件別PnLを証人にする|
|AsOfDataView|時刻ごとの利用可能な価格・QC・証拠を供給|未来テーブルを丸ごとStrategyへ渡す|
|UniverseBuilder|scheduled_axis、U、E_execを時刻付きで作る|将来のexit・placeboで主集合をfilterする|
|AnalysisEligibility|E_analysis・対応率・欠測理由を保持|主発注集合を遡及更新する|
|ConditionResolver|side・quantile・時刻・費用を明示解決|root条件のdefaultを別対照へ暗黙流用する|
|OutcomeLedger / Metrics|既知0・既知損益・未完了nullと固定軸の集計|回帰用集合や観測バーだけで分母を変える|
|StageGate / AccessLedger|段階別の許可範囲と開封履歴|凍結fileの存在だけでOOSを許可する|

データ経路は `source evidence -> versioned data -> as-of view -> U/E_exec -> intent -> execution -> outcomes`、解析経路は `outcomes -> E_analysis -> diagnostics` とする。解析経路から過去の取引判断へ戻す辺を持たせない。

カレンダー、品質、条件表、コード、データ、利用可能時刻の仮定はmanifestに含める。キャッシュは実行入力hashとas-of方針をキーにし、全期間で作った将来ラベルを売買用cacheへ混在させない。

### Shared immutable event definition

`event_definition`、`selection_status`、`order_status`、`fill_status`を分離する。event抽出後にstatusを上書きして回帰行が0件になる経路を防ぐ。U台帳は独立保存し、各感度eventへ全rolling履歴を複製しない。参照先の内容hashを検査する。

### Audit before optimization

同じ合成例でloader→QC→universe→strategyまでの未来変更テストを通す。engineの経済契約修正は別変更として影響範囲を保存する。専用のcross-session profileが必要な研究を、session独立runの単なる結合で代用しない。
