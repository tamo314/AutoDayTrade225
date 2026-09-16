# 134. C04 雇用統計発表カレンダー・時刻入場監査

タスク: **TASK-C04-EVENT-CALENDAR-AUDIT-08** / 2026-09-17 JST

## 目的

C04「予定経済指標の発表後継続」の候補について、BLS Employment Situation（雇用統計）という一つの指標の公式発表日時、予定と実際の発表、夏時間、改訂・再発表、OSEとの時刻対応を確認する公開資料監査である。経済指標の実数値、サプライズ、価格反応、方向継続を扱わない。

F04の局所shockとF13/R082の米国開始時刻周辺は既存familyの結果である。このタスクはそれらを再開、再評価、又はC04と同一視しない。C03が`NOT_VERIFIED`で完了したこともC04のカレンダー可用性・経済性の証拠ではない。

## 読み取りと公開調査の範囲

最初に`AGENTS.md`、00、13、20、120、121、123、124、registry、`config/research_execution.json`、およびC03の`input_admission.json`を読む。すべて読み取り専用である。

公開調査はBLS自身のEmployment Situationの発表予定・アーカイブ・時刻・改訂に関する一次資料だけに限定する。最大8検索クエリ、最大8本文取得までとする。検索語、URL、資料の版・日付・参照日、支持する主張、支持しない主張、取得不能を残す。実際の指標値が載る本文、経済カレンダー集約サイト、価格画面、時系列、二次記事、SNSを根拠にしない。

対象はEmployment Situation一種類だけである。2021-01-01..2025-06-30について、公式に裏付けられる発表日時だけを記録できる。資料が対象期間全体、予定と実発表、又はJST変換規則を支持しなければ`NOT_VERIFIED`又は`NOT_USABLE_FOR_C04`として完了する。

## 禁止事項

- `data/raw/`、正規化Parquet、派生バー、価格cache、取引明細、特徴量、実際のUSDJPY又は日経225の価格・volume列を開かない。
- 経済指標の実数値・改訂数値、サプライズ、外部価格・時系列、リターン、PnL、実データ由来件数、OOS、Final Holdoutを取得・計算・表示しない。
- C04のfamily、例外、有限attempt、budget、manifest、ReviewReceipt、grant、engine変更を作成・変更しない。F04、F13、F11を含む既存familyも再開しない。

## 実施内容

1. 次のイベント属性を個別に`VERIFIED`、`UNVERIFIED`、`CONTRADICTED`で記録する。各判断には根拠ID又は未確認理由を付ける。
   - `event_identity_and_scope`: Employment Situationの発行主体・対象・このタスクで扱う一種類であること。
   - `historical_release_timestamp`: 対象期間の公式発表日・時刻を追跡できること。
   - `scheduled_actual_and_change_status`: 予定、実際の発表、延期・臨時変更を区別できること。
   - `timezone_dst_to_jst`: 発表時刻のET定義、夏時間、JSTへの変換規則。
   - `revision_and_republication_semantics`: 値を扱わずに、改訂・再公表がイベント時刻を置き換えない規則を確認できること。
   - `ose_session_trade_date_alignment`: OSEのsession・trade_dateと、発表後に限る将来の照合規則。資料がなければ未確認とする。
2. C04のイベント入力判断を`NOT_VERIFIED`、`NOT_USABLE_FOR_C04`、`CONDITIONALLY_DOCUMENTED`の一つにする。どの判断でも経済値取得、C04 family化、実データ読取り、実行許可を与えない。
3. 合成ケースで予定と実発表、夏時間境界、休日・臨時変更、改訂値、OSE session外、時刻未確認を固定する。価格、経済値、サプライズ、閾値、シグナル、リターン、注文、fill、PnLを含めない。

## 成果物

`docs/strategy/plans/TASK-C04-EVENT-CALENDAR-AUDIT-08/` にだけ次を作る。

- `source_contract.md`: BLS資料台帳、支持・非支持・未確認、F04/F13/C03との区別。
- `event_admission.json`: 属性別判断、C04入力判断、調査使用量、実アクセスなしの記録。
- `synthetic_calendar_cases.json`: 固定した予定・時刻・改訂・session境界ケースと期待処理。経済値・価格値を含めない。
- `verification.md`: validator結果、資料と合成ケースの照合、限界。
- `review.md`: 完了条件ごとの根拠、最終判断、実アクセス・変更・未許可事項の記録。

`event_admission.json`は`task_id`、`event_type`、`current_input_decision`、`attributes`、`research_ledger`、`market_data_accessed`、`economic_values_accessed`、`authorises_c04_family`、`authorises_market_data_access`を持つ。`attributes`は上記6項目を全て含める。`research_ledger`の検索・本文数は各8以下とする。

## 固定検証と終了

```powershell
.venv/Scripts/python.exe scripts/validate_c04_event_calendar_audit.py --artifacts docs/strategy/plans/TASK-C04-EVENT-CALENDAR-AUDIT-08
```

validatorは出力構造、調査上限、属性網羅、非承認境界、固定合成ケースを検査する。構造PASSをBLS発表の完全性、将来の時点可用性、C04実行、C04収益性、外部入力の許可と扱わない。

資料不足・日付時刻の矛盾・対象期間の未裏付けは`NOT_VERIFIED`又は`NOT_USABLE_FOR_C04`として記録してDONEにできる。経済値・価格・実データを読まなければ埋められない条件は、試行履歴と未完了条件を残してBLOCKEDとする。いずれの場合も実データ、OOS、Final Holdout、C04 family化を行わない。
