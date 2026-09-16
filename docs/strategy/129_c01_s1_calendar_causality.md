# 129. C01 S1 カレンダーラベル・時点利用可能性検証

タスク: **TASK-C01-S1-CALENDAR-CAUSALITY-03** / 2026-09-16 JST

## 目的

完了済みのC01準備成果物にある `calendar_contract.json` を唯一のカレンダー入力にして、C01の `treatment / control / excluded` のラベルと、それぞれのラベル根拠が決定時点で利用できたかを価格なしで検証する。これはS1のカレンダー・因果性検証であり、C01の採否、収益性、実行可能性を評価しない。

## 最初に読むもの

`AGENTS.md`、00、13、120、121、123、128、ならびに次のC01準備成果物を読む。

- `docs/strategy/plans/TASK-C01-PREPARATION-02/evidence.md`
- `docs/strategy/plans/TASK-C01-PREPARATION-02/design.md`
- `docs/strategy/plans/TASK-C01-PREPARATION-02/calendar_contract.json`
- `docs/strategy/plans/TASK-C01-PREPARATION-02/review.md`

これらは読み取り専用である。訂正・追加説明は今回の専用成果物だけに残し、過去のDONE成果物、registry、実行設定、grant、engineを変更しない。

## 固定範囲

- 対象はDevelopmentの `2021-01-01`～`2025-06-30` のカレンダーだけ。
- C01の観測窓、方向、約定、退出、費用、1枚・最大1ポジション、既存のtreatment/control/excluded定義を変えない。
- `cash_open`、`ose_holiday_trading`、`ose_trade_date`、公式資料の公開日・改訂履歴だけを使う。価格、バー、出来高、注文、取引明細、PnL、実データ由来の件数、OOS、Final Holdoutを読まない。
- 新しい休日類型、曜日・月別分割、対照、戦略、経済仮説を追加しない。

## 実施内容

1. カレンダー契約の各cash-open日を、次の三つへ決定的に分類する。

   - `TREATMENT_HOLIDAY_REOPEN`: 2022-09-23以後で、直前の最大休場区間に確認済みOSE祝日取引がある再開日。
   - `ORDINARY_CASH_OPEN_CONTROL`: 2022-09-23以後で、直前のcalendar_dateもcash openである通常の開場日。
   - `OUT_OF_REGIME_OR_EXCLUDED`: それ以外。週末だけの再開、祝日取引のない休場後、年末年始、制度開始前、例外・不明はここへ含める。

   treatmentを常に優先し、各cash-open日は一度だけ記録する。各行には直前の連続休場日、除外理由または通常開場理由、利用した公式HTTPS根拠を残す。

2. 次の価格なし合成ケースを固定する。

   - 2022-09-23祝日取引と週末を含む2022-09-26再開はtreatment。
   - 週末だけの再開はexcluded。
   - 2023-11-03と2024-08-12の祝日取引非実施後はtreatmentにしない。
   - 年末年始の再開はtreatmentにしない。
   - カレンダー入力が欠落・矛盾する場合は取引せずexcludedにする。

3. ラベルに使う資料の公開時点を記録する。決定日に利用できた予定・通知と、後日公表された最終リストを区別する。後日の確定情報は履歴照合に使えても、過去の執行時点で利用可能だった情報に遡及させない。

4. 全cash-open日のラベル、休場区間、重複、trade_date整合性を検証する。カレンダー日数の集計は許可するが、価格由来の値や市場成績を出力しない。

## 成果物

`docs/strategy/plans/TASK-C01-S1-CALENDAR-CAUSALITY-03/` にだけ次を作る。

- `labeling_spec.md`: 決定的な分類規則、時点利用可能性規則、例外時の除外規則。
- `label_audit.json`: 入力calendar契約のSHA-256、全cash-open日のラベル、直前の休場区間、理由、決定日、公式HTTPS根拠、集計値。
- `synthetic_cases.json`: 境界例の入力、期待ラベル、期待trade_date、除外理由。
- `fixture_results.md`: 固定検証結果、集計、検証限界。市場結果を含めない。
- `review.md`: 完了条件ごとの根拠、実アクセス、未解決事項、C01が`NOT_EVALUATED`である結論。

`label_audit.json` は、入力calendar契約のSHA-256、各cash-open日のdate・label・直前休場日・理由、`decision_date`・`basis`・HTTPS source URLsを持つ`availability`、label別の`counts`を持つ。

## 固定検証

```powershell
.venv/Scripts/python.exe scripts/validate_c01_s1_calendar_causality.py --artifacts docs/strategy/plans/TASK-C01-S1-CALENDAR-CAUSALITY-03
```

validatorはラベル、日付、休場区間、根拠URL、契約hash、合成ケースの構造を検査する。公式資料の真偽や公開時点の実質的妥当性はPlannerが資料と照合する。構造PASSを経済性・因果性・実データ実行のPASSとして扱わない。validatorまたは完了条件を緩めて合格させない。

## 終了条件と停止条件

全完了条件、固定validator、最終監査が通った時だけDONEとする。修正可能なラベル不整合、欠落根拠、合成ケース不足は同じ範囲で修正・再検証する。

必須の公式根拠が複数の取得方法でも回収不能、または固定カレンダー内の矛盾を価格・範囲外情報なしに解消できない場合だけ、証拠、試行履歴、未完了条件を残してBLOCKEDとする。

RunManifest、ReviewReceipt、grant、`research execute`、実データattempt、F05/F06の再開、S2/S3、OOS、Final Holdoutは今回作成・変更・実行しない。
