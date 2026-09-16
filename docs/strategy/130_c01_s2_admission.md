# 130. C01 S2 入場判定・非PnL観測可能性契約

タスク: **TASK-C01-S2-ADMISSION-04** / 2026-09-16 JST

## 目的

完了済みのC01準備とS1カレンダー監査を読み取り専用で引き継ぎ、将来のS2非PnL可用性診断を開始できる条件と、開始後に出してよい観測可能性情報を固定する。今回作るのは入場判定と合成検証だけである。実データ、価格、方向、件数、注文、約定、PnLを読まず、C01の採否や実行可能性を判断しない。

## 最初に読むもの

`AGENTS.md`、00、13、120、121、123、128、129、閉鎖台帳、`config/research_execution.json`、及び次の完了済み成果物を読む。

- `docs/strategy/plans/TASK-C01-PREPARATION-02/{evidence.md,design.md,calendar_contract.json,review.md}`
- `docs/strategy/plans/TASK-C01-S1-CALENDAR-CAUSALITY-03/{labeling_spec.md,label_audit.json,synthetic_cases.json,fixture_results.md,review.md}`

これらは読み取り専用である。既存成果物、registry、実行設定、grant、RunManifest、ReviewReceipt、engineを訂正・作成・変更しない。

## 固定範囲

- C01の一つの代表ルート、Developmentの境界、S1のラベルとカレンダー契約hashを継承する。新しい休日類型、観測窓、方向、対照、exit、ストレス、経済仮説は追加しない。
- S2の将来目的は、許可済みDevelopment入力に対する **ラベル・バー存在・欠損理由の診断だけ** である。価格水準、OHLC値、リターン、方向、注文、約定価格、損益、成績、実データ由来の件数は今回も将来S2の出力にも含めない。
- 現在は `research_execution_paused=true`、grant 0件、F05/F06例外なし、実データ/PnL attempt 0件である。この状態を実行許可なしとして記録する。空欄を仮のgrant、manifest、ReviewReceipt、予算、family再開根拠で埋めない。
- OOS 2025H2、Final Holdout 2026+、`data/raw/`、派生バー、価格cache、取引明細、外部市場データを読まない。

## 実施内容

1. S2実データアクセスの入場条件を一つの順序で固定する。少なくとも、(a) S1 calendar hash一致、(b) Development範囲固定、(c) F05/F06を対象にした例外レビュー、(d) 有限S2 attemptと残枠、(e) 完全な非PnL RunManifest と ReviewReceipt、(f) 全項目一致grant、を列挙する。いずれか一つでもない場合は拒否する。
2. 将来のS2出力スキーマを固定する。出力は `calendar_date`、`trade_date`、S1 label、09:00/09:14/09:15/10:30の各必要バーの存在フラグ、entry/exitの可用性フラグ、欠損コード、予定日単位の非PnL集計だけとする。バー値・価格・方向・注文・約定・損益は出力しない。S2の可用性はC01の採否、因果性、収益性、運用実行可能性を示さない。
3. 現在の入場状態を `NOT_GRANTED` として記録し、どの具体的な未充足条件が次の人間レビューで必要かを残す。将来のgrantが作られた場合にも、このタスクを再開せず、そのgrantの新しい登録タスクで照合する。
4. 合成の入場ケースを固定する。grantなし、family例外なし、S1 hash不一致、期間外、禁止出力フィールドを必ずrejectとする。すべての前提が揃い、許可出力だけの **仮想** S2契約を一件だけacceptとするが、これは実データアクセスを許可しないことを明記する。

## 成果物

`docs/strategy/plans/TASK-C01-S2-ADMISSION-04/` にだけ次を作る。

- `s2_admission_spec.md`: 入場順序、将来の入力・出力列、拒否規則、S2とS3の境界。
- `admission_matrix.json`: C01準備calendarとS1監査のSHA-256、現在の未許可状態、将来必要な入場条件、許可出力列・禁止出力列。
- `synthetic_admission_cases.json`: 固定したaccept/reject入力と期待決定。仮想acceptに実行権限がないことを含める。
- `verification.md`: validator結果、合成ケースの対応、現在の許可状態、限界。
- `review.md`: 完了条件ごとの根拠、アクセスと変更、未充足条件、C01の`NOT_EVALUATED`を記録する最終監査。

`admission_matrix.json` は少なくとも次のキーを持つ。

```text
task_id, calendar_contract_sha256, s1_label_audit_sha256,
current_authorisation, required_admission_gates,
allowed_output_fields, prohibited_output_fields
```

`current_authorisation` は `real_market_input_access: "NOT_GRANTED"`、`grant_count: 0`、`family_exception: "NOT_GRANTED"`、`real_data_pnl_attempts: 0` を明記する。`allowed_output_fields` は上記の観測可能性列だけ、`prohibited_output_fields` は少なくとも `open`、`high`、`low`、`close`、`price`、`return`、`direction`、`order`、`fill_price`、`pnl`、`gross`、`net` を含める。

## 固定検証

```powershell
.venv/Scripts/python.exe scripts/validate_c01_s2_admission.py --artifacts docs/strategy/plans/TASK-C01-S2-ADMISSION-04
```

validatorは、凍結C01/S1入力hash、現在の未許可状態、必要入場gate、出力の禁止境界、固定合成ケースの構造を検査する。固定validatorを緩めて合格させない。構造PASS、仮想accept、または入場契約の完成を、grant、実データアクセス、family再開、C01の因果性・収益性・実行可能性のPASSと扱わない。

## 終了条件と停止条件

全完了条件、固定validator、最終監査が通ったときだけDONEとする。不足したgate、出力境界、hash、合成拒否は、今回の成果物内で修正し再検証する。

人間による例外レビュー、有限枠、ReviewReceipt、一致grantが現時点でないことは、入場判定を`NOT_GRANTED`で完成させる理由でありBLOCKEDではない。固定C01/S1入力の矛盾を成果物だけでは解消できない場合だけ、矛盾証拠、対処履歴、未完了条件を残してBLOCKEDとする。

実データS2、S3 PnL、grant/manifest/ReviewReceiptの作成、F05/F06再開、OOS、Final Holdoutは今回作成・変更・実行しない。
