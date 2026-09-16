# 09. 実装済みCLIと出力

## データの取り込み

```powershell
.venv/Scripts/python.exe -m n225m_bt.cli validate
.venv/Scripts/python.exe -m n225m_bt.cli data inspect data/raw/225labo/center/N225minif_2024.xlsx
.venv/Scripts/python.exe -m n225m_bt.cli data build-calendar --source-root data/raw/225labo/center --source-root data/raw/225labo/forward --output config/local_calendar.yaml
.venv/Scripts/python.exe -m n225m_bt.cli data ingest data/raw/225labo/center/N225minif_2024.xlsx --calendar-override config/local_calendar.yaml
```

build-calendarは元ファイルの日付列からローカルカレンダーを作る。rawは変更しない。新規データの投入・カレンダー変更は既存実験とは別のデータ版として扱う。Gold研究入力の年次重複処理は [データ重複記録](../strategy/05_data_overlap.md) を参照。

forwardを取り込む場合は data ingest に --dataset forward --series-type next_continuous を加えてcenterと分離する。

## 戦略研究

```powershell
.venv/Scripts/python.exe -m n225m_bt.cli research run --calendar-override config/local_calendar.yaml
```

研究の仕様・結果は [docs/strategy](../strategy/01_research_protocol.md)。旧 backtest run はalways-flatによる基盤確認用で、任意戦略選択や期間制限を備えない。研究には専用コマンドを使う。旧文書の config validate / data quality / backtest sweep / report build は実装済みCLIではないため、例から削除した。

## 保存物

基盤のwrite_resultsは trades/orders/fills/equity Parquet、metrics JSON/Markdown、run_manifestを出力する。研究では別の排他的ディレクトリ作成と詳細集計を重ねる。ローカルの結果と実データはGit除外。

## RG-20260915-01: 実行前の運用制限

上記CLIは元文書が「実装済み」と記録した例であり、この改訂で実コード・`--help`・オプションを検証したものではない。**新規研究を開始する手順として上記`research run`をそのまま実行しない。** 現在はPAUSED_METHOD_REPAIRで、段階ゲートの実装・実測確認が必要である。

`build-calendar`が元ファイルの日付から作るcalendarは、観測カバレッジと候補mappingの材料である。公式予定表、TSE営業日、休業、欠落sessionの不存在の証明には使わない。Development監査で年次全価格や2026価格へアクセスする実装になっていないかを先に確認する。

### 追加予定の能力（コマンド名は未確定）

|能力|入力と出力|実装状態|
|---|---|---|
|仕様監査|完全なpreregistration→論理・単位・集合・予算監査|本改訂では仕様のみ|
|入力意味監査|供給仕様・許可Development来歴→用途別quality gate|実コード要確認|
|件数・可用性診断|因果的候補→件数／欠損理由。将来returnとPnLは返さない|本改訂では仕様のみ|
|段階別研究実行|明示stage＋許可manifest→台帳・多軸判定|本改訂では仕様のみ|
|旧結果の読取り専用照合|既存台帳→差分・集計式・影響監査|利用可能APIを確認して実装|
|OOS開封管理|凍結候補＋期間・許可→アクセス台帳|自動開封禁止。Final Holdoutは引き続き禁止|

上記の名前を架空の実行可能CLIとして配布しない。実装後は`--help`、段階未指定拒否、未知設定拒否、価格範囲・cache境界、旧CLI回帰を実測する。根拠のない`audit-*`や`--stage`を動くものとして記載しない。

### R1で実装した価格非読取り仕様監査

`research audit-spec SPECIFICATION.json --output RESULTS_DIRECTORY` は、S0/S1の新規仕様JSONだけを読み、排他的な出力先へ`spec_audit.json`、`condition_resolution.json`、`gate_witnesses.json`、`causality_audit.json`、`access_ledger.json`を書き出す。市場データ、既存run、OOS、Final Holdoutは開かない。

入力にはfamily/study/spec/run/protocol識別子、事前登録・source snapshot hash、seed、nuisance宣言、判定条件、明示side/quantile/集合関係、gateを全て含める。暗黙default、R031型の同一経路・反対side・0tick費用前双方負AND、S2以降の指定は拒否する。`gate_witnesses.json`と`causality_audit.json`が`NOT_RUN`の場合、それは次の合成・pipeline監査待ちであり、PASSではない。

### 追加出力契約

`study_registry.json`、`run_registry.json`、`condition_resolution.json`、`spec_audit.json`、`causality_audit.json`、`scheduled_axis`、`rolling_u_ledger`、`exec_eligibility_ledger`、`analysis_eligibility_ledger`、`outcome_missingness`、`decision.json`、`access_ledger`を必要責務として追加する。名称・形式は実装時にschemaと合わせ、既存成果物と混同しない。

日次損益は単一の固定軸を共有し、metric・bootstrap間で同じaxis hashを要求する。既知無取引0と不明損益nullを分ける。原文で記載された取引台帳などは本配布物に含まれず、今回生成したのは文書と雛形・整合検査だけである。
