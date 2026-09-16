# 07. Data Quality and Validation

> **文書改訂: RG-20260915-01 / 2026-09-15 JST**
> 本改訂は仕様の更新です。追加機能・テスト・実データ監査が実装／完了したことは意味しません。
> 新しい研究の共通正本は [研究統治・因果性・段階別ゲート](../strategy/13_research_governance.md)、移行順序は [修復・移行計画](../strategy/14_research_repair_plan.md) を参照してください。

## 1. Quality philosophy

Do not silently repair market data. Detect, classify, report, and only exclude according to explicit policy.

## 2. Required checks

### Structural
- file readable
- required columns resolvable
- numeric parse success
- timestamps parseable
- timestamps monotonic after sort
- duplicate natural keys

### OHLC
- `high >= open`
- `high >= close`
- `low <= open`
- `low <= close`
- `high >= low`
- prices positive
- expected 5-JPY grid for N225M

### Volume
- null allowed if source lacks volume
- otherwise integer and >=0

### Session/calendar
- bar timestamp belongs to a configured session
- trade date mapping is possible
- schedule version resolved
- no unexplained bars inside known exchange breaks

### Continuity
- missing expected minutes
- unusually long gaps inside a session
- duplicated minutes
- jump/outlier diagnostics

## 3. Severity

Suggested codes:
- INFO
- WARN
- ERROR
- FATAL

Examples:
- missing low-activity minute: WARN
- duplicate timestamp with conflicting prices: ERROR/FATAL
- impossible OHLC: ERROR
- unresolvable timestamp/trade date: FATAL

## 4. Quality flags on bars

Use stable codes, e.g.:
- `TICK_GRID_VIOLATION`
- `OHLC_INVARIANT`
- `MISSING_PREV_EXPECTED`
- `UNEXPECTED_SESSION_TIME`
- `DUPLICATE_TIMESTAMP`
- `SOURCE_PARSE_WARNING`
- `CALENDAR_UNCERTAIN`
- `ROLL_RISK`

## 5. Outliers

Large returns are not automatically bad data. Flag statistical outliers for review but do not drop them automatically.

## 6. Quality report outputs

For every ingest build:
- `quality_summary.json`
- `quality_issues.parquet`
- `quality_report.md`

Include counts by year/month/session/flag and sample lineage references.

## 7. Gate to backtesting

Gold dataset generation should fail if FATAL issues exist.

For ERROR issues, behavior is configurable:
- strict: fail
- permissive: mark `is_eligible=false` and proceed with explicit warning

Default: strict.

## 8. 用途別の意味・因果性ゲート — RG-20260915-01

構造検査と意味の確認を分ける。整数・5刻み・非負volume・単調timestampだけでは、価格種別、実限月、roll、adjustment、出来高の非累積性、当時の観測可能時刻は証明されない。

|品質|許可範囲|新規昇格の制約|
|---|---|---|
|BLOCKED|文書・合成・原因調査。許可範囲を固定した読み取り専用の入力監査|重大な時刻・価格解釈・相違重複・因果性問題を残してPnLへ進まない|
|PASS_LIMITED|説明可能な範囲のDevelopment限定診断|実限月執行、OOS、CANDIDATEの根拠にしない|
|PASS_RESEARCH|用途に必要な時刻・価格・系列構成を証拠で説明できる研究入力|他の全ゲートと凍結手順を通って初めてOOS審査。実売買保証ではない|

未実施はNOT_EVALUATEDとし、既定でPASSにしない。旧R004のwhole-session隔離を再現できることは、as-of取引可能性を証明しない。scope/period、evidence、unknowns、allowed_use、prohibited_useをquality_gateへ保存する。

### Causal QC

`quality_available_at`と`quality_detected_at`を持つ。現在までのデータから検出できる欠損・異常は現在以後の新規取引を止められるが、後半の異常を理由に前半の注文を遡及除外しない。全期間分布を使うoutlierラベルは分析専用とする。

E_execの判断に必要な過去だけを検査し、将来exitや全感度の足が揃うことをentryの条件にしない。入力不明で未実行と、既約定後に価格が失われた状態を分け、後者を無取引0円へ置換しない。

### Data semantics evidence

時刻ラベル、regular/auction記録、連続系列と実限月、roll選択時点、調整方式、volume単位・期間・zero/missing/訂正方針を用途別に照合する。根拠の不足を、公開制度ページを見たことだけで解消しない。必要資料が得られない場合は別データ版の検討へ進み、元データを修復したことにしない。

追加出力: `source_semantics_evidence.json`、`causal_quality_audit.json`、`availability_audit.json`、`outcome_missingness.json`、`quality_gate.json`。既存のquality_summary/issues/reportを置き換えず、監査と本文の全件数を照合する。
