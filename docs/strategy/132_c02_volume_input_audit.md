# 132. C02 分足出来高入力の意味・時刻監査

タスク: **TASK-C02-VOLUME-INPUT-AUDIT-06** / 2026-09-16 JST

## 目的

C02「午前の出来高加重基準からの乖離修正」の前提となる分足出来高について、値の意味、確定時点、時刻・trade_date、訂正、session境界を一次資料で確認する。R010/R052の停止理由は入力意味不足であり、経済的な不採用ではない。このタスクは入力の使用可否を限定して記録するだけで、C02を実行・再開しない。

## 読み取りと外部調査の範囲

最初に`AGENTS.md`、00、13、120、121、123、124、registry、R010/R052の索引・結果記録、`config/research_execution.json`を読む。これらは読み取り専用である。

公開調査は、データ提供者・取引所・配布仕様の一次資料だけに限定し、最大8検索クエリ・8本文取得までとする。検索語、URL、資料の版・日付・参照日、支持する主張と支持しない主張、取得不能を残す。二次記事、価格画面、SNS、実データファイルは根拠にしない。

## 禁止事項

- `data/raw/`、正規化Parquet、派生バー、価格cache、取引明細、特徴量、実際のvolume列を開かない。
- 価格、リターン、PnL、実データ由来件数、OOS、Final Holdout、外部時系列を取得・計算・表示しない。
- F11を再開せず、例外、有限attempt、budget、manifest、ReviewReceipt、grant、engine変更を作成・変更しない。

## 実施内容

1. 次の入力属性を個別に `VERIFIED` / `UNVERIFIED` / `CONTRADICTED` と記録する。各判断には根拠IDまたは未確認理由を付ける。
   - 1分値が各分の確定約定数量か、累積値か、別の集計値か
   - バー終端で確定する時点と、速報・訂正の扱い
   - 単位、取引所・商品・限月との対応、JST timestamp
   - calendar_date / trade_date / day・night session 境界
   - 欠損、ゼロ、session再開時の意味
2. C02の入力判断を `NOT_VERIFIED`、`NOT_USABLE_FOR_C02`、`CONDITIONALLY_DOCUMENTED` の一つにする。`CONDITIONALLY_DOCUMENTED`でも実データアクセス、F11再開、経済評価を許可しない。
3. 合成ケースで、各分値、累積値、未確定バー、訂正、session跨ぎ、欠損・ゼロを固定する。根拠が曖昧・矛盾するケースは`FAIL_CLOSED`とし、VWAPや価格シグナルを計算しない。

## 成果物

`docs/strategy/plans/TASK-C02-VOLUME-INPUT-AUDIT-06/` にだけ次を作る。

- `source_contract.md`: 資料台帳、根拠と未確認、R010/R052との対応。
- `input_semantics.json`: 属性別判定、C02入力判断、調査使用量、実アクセスなしの記録。
- `synthetic_volume_cases.json`: 固定境界ケースと期待処理。価格・VWAP・PnLを含めない。
- `verification.md`: validator結果、資料と合成ケースの照合、限界。
- `review.md`: 完了条件ごとの根拠、最終判断、実アクセス・変更、F11が未再開である記録。

`input_semantics.json` は `task_id`、`current_input_decision`、`attributes`、`research_ledger`、`real_data_accessed`、`authorises_f11_reopen` を持つ。`attributes`は `per_minute_quantity`、`bar_finality_and_corrections`、`timestamp_timezone`、`trade_date_and_session_boundary`、`missing_zero_semantics` を全て含める。`research_ledger`の検索・本文数は各8以下とする。

## 固定検証と終了

```powershell
.venv/Scripts/python.exe scripts/validate_c02_volume_input_audit.py --artifacts docs/strategy/plans/TASK-C02-VOLUME-INPUT-AUDIT-06
```

validatorは出力構造、調査上限、属性網羅、非承認境界、固定合成ケースを検査する。構造PASSを入力真偽、F11再開、実データアクセス、C02収益性のPASSと扱わない。

資料不足や矛盾は`NOT_VERIFIED`または`NOT_USABLE_FOR_C02`として記録してDONEにできる。資料の不足を埋めるために実データを読む必要がある場合だけ、試行履歴と未完了条件を残してBLOCKEDとする。いずれの場合もF11、OOS、Final Holdoutを開かない。
