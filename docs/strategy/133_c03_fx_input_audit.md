# 133. C03 ドル円外部入力の出所・時刻入場監査

タスク: **TASK-C03-FX-INPUT-AUDIT-07** / 2026-09-17 JST

## 目的

C03「ドル円変動への日経225の遅行反応」を、外部USDJPY入力の利用可能時刻・時刻意味・改訂・欠損・再現可能性が未確認のまま仮説や実データ研究に進めないための公開資料監査である。C02の出来高監査が`NOT_VERIFIED`で完了したことは、C03の可用性・経済性・新family性の証拠ではない。

このタスクは候補ソースの文書化と入力状態の限定だけを行う。USDJPYの価格・レート・リターン・件数を取得、表示、計算しない。C03を実行、familyとして登録、または既存familyを再開しない。

## 読み取りと公開調査の範囲

最初に`AGENTS.md`、00、13、20、120、121、123、124、registry、`config/research_execution.json`、およびC02の`input_semantics.json`を読む。すべて読み取り専用である。

公開調査は、一つのUSDJPY候補ソースについて、供給者自身または一次的な配布・契約・仕様資料だけに限定する。最大8検索クエリ、最大8本文取得までとする。検索語、URL、資料の版・日付・参照日、支持する主張、支持しない主張、取得不能を残す。価格画面、ダウンロードした時系列、CSV/APIレスポンス、二次記事、SNSを根拠にしない。

候補ソースは、対象期間の履歴と時点可用性を説明できる単一の供給者・商品・配布仕様でなければならない。複数ソースを組み合わせて不足を埋めない。資料だけで候補を一つに絞れなければ`NOT_USABLE_FOR_C03`または`NOT_VERIFIED`として完了する。

## 禁止事項

- `data/raw/`、正規化Parquet、派生バー、価格cache、取引明細、特徴量、実際のUSDJPY又は日経225の価格・volume列を開かない。
- 外部の価格・レート・時系列、API/CSVデータ、リターン、PnL、実データ由来件数、OOS、Final Holdoutを取得・計算・表示しない。
- C03のfamily、例外、有限attempt、budget、manifest、ReviewReceipt、grant、engine変更を作成・変更しない。F11や既存の閉鎖familyも再開しない。

## 実施内容

1. 次の入力属性を個別に`VERIFIED`、`UNVERIFIED`、`CONTRADICTED`で記録する。各判断には根拠IDまたは未確認理由を付ける。
   - `instrument_definition_and_quote_convention`: USDJPYの向き、価格定義、対象市場・供給者・商品。
   - `historical_access_and_license`: 2021-01-01..2025-06-30の履歴取得、保存、再現・利用条件。
   - `timestamp_timezone_and_bar_label`: timezone、時刻がbar開始/終了/quote時刻のどれか、粒度。
   - `availability_latency_finality_revision`: decision時点の利用可能時刻、遅延、速報・確定・訂正、as-of再現。
   - `session_calendar_and_missingness`: 取引時間、休日、停止、欠損値・ゼロ・連続性の意味。
   - `n225_alignment_and_causality`: OSEの判断時刻より前に利用可能だったことを確認するための照合規則。資料がなければ未確認とする。
2. C03の入力判断を`NOT_VERIFIED`、`NOT_USABLE_FOR_C03`、`CONDITIONALLY_DOCUMENTED`の一つにする。どの判断でも外部時系列取得、C03のfamily化、実データ読取り、実行許可を与えない。
3. 合成ケースでas-of時刻、未来の訂正値、timezone/barラベル不明、日経225判断時刻より遅い観測、session・休日欠損、複数ソース不一致を固定する。価格、レート、閾値、シグナル、VWAP、リターン、注文、fill、PnLを含めない。

## 成果物

`docs/strategy/plans/TASK-C03-FX-INPUT-AUDIT-07/` にだけ次を作る。

- `source_contract.md`: 候補ソースの資料台帳、支持・非支持・未確認、C02と既存閉鎖familyとの区別。
- `input_admission.json`: 属性別判断、C03入力判断、調査使用量、実アクセスなしの記録。
- `synthetic_timing_cases.json`: 固定した時刻・改訂・欠損境界ケースと期待処理。価格値を含めない。
- `verification.md`: validator結果、資料と合成ケースの照合、限界。
- `review.md`: 完了条件ごとの根拠、最終判断、実アクセス・変更・未許可事項の記録。

`input_admission.json`は`task_id`、`current_input_decision`、`attributes`、`research_ledger`、`real_data_accessed`、`external_time_series_accessed`、`authorises_c03_family`、`authorises_market_data_access`を持つ。`attributes`は上記6項目を全て含める。`research_ledger`の検索・本文数は各8以下とする。

## 固定検証と終了

```powershell
.venv/Scripts/python.exe scripts/validate_c03_fx_input_audit.py --artifacts docs/strategy/plans/TASK-C03-FX-INPUT-AUDIT-07
```

validatorは出力構造、調査上限、属性網羅、非承認境界、固定合成ケースを検査する。構造PASSをソースの真偽、USDJPYの時点可用性、C03実行、C03収益性、外部入力の許可と扱わない。

資料不足・契約不適合・時刻意味の矛盾は`NOT_VERIFIED`または`NOT_USABLE_FOR_C03`として記録してDONEにできる。外部時系列を読まなければ埋められない条件は、試行履歴と未完了条件を残してBLOCKEDとする。いずれの場合も実データ、OOS、Final Holdout、C03 family化を行わない。
