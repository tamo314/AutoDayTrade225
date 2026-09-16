# 131. C01 F05/F06例外レビュー

タスク: **TASK-C01-EXCEPTION-REVIEW-05** / 2026-09-16 JST

## 目的

C01がF05/F06に近縁であることを前提に、完了済みの準備、S1、S2成果物と既知のR087/R101結果を対応付ける。現在の例外判断を根拠付きで固定し、将来に例外を再考するための条件を限定する。これは例外を発行するタスクではない。

## 読み取り専用の入力

`AGENTS.md`、00、13、120、121、123、128、129、130、閉鎖台帳、実行設定、R087-Q002結果、R101-Q001結果、およびC01準備・S1・S2の全成果物を読む。既存の結果、registry、設定、grant、RunManifest、ReviewReceipt、engine、カレンダー契約を変更しない。

## 固定範囲

- C01の単一代表ルート、Development境界、S1ラベル、S2入場gateを継承する。休日類型、時刻、方向、対照、exit、費用、経済仮説を追加・変更しない。
- 市場データ、価格、出来高、特徴量、取引明細、PnL、実データ由来件数、OOS、Final Holdout、外部時系列を読まない。新規の公開調査も行わず、保存済みの一次資料・結果だけを使う。
- F05/F06は閉鎖中で、現在の実行設定は停止、grant 0件である。例外、有限attempt、予算、manifest、ReviewReceipt、grantを作成・予約・変更しない。

## 実施内容

1. C01、R087-Q002、R101-Q001について、利用情報、局面、判断・entry・exit、方向、対照、既知結果を比較する。C01の制度カレンダー差分が、R101の経済的REJECTとR087の情報不足を自動で解消しない理由を記録する。
2. 現時点の判断を **`NO_EXCEPTION_RECOMMENDED`** とする。理由は、カレンダー制度・ラベル監査はあるが、新しい直接の経済的証拠、F05/F06を限定して再開する人間判断、有限S2 attemptと残枠、ReviewReceipt、一致grantがないためである。これはC01が経済的にREJECTと判明したという意味ではない。
3. 将来の再考条件を有限に列挙する。少なくとも、C01の固定ルートに関係する新しい直接経済根拠、限定したF05/F06例外の人間判断、S2用の有限attempt、完全な非PnL manifest/ReviewReceipt、一致grant、S1 hashとDevelopment範囲の再照合を要求する。時刻・窓・閾値の変更、カレンダーだけの新規性、既存結果の再解釈は根拠に数えない。
4. 合成例を固定する。カレンダーだけの新規性と旧ルートの小変更は`DENY`、将来の未検証主張は`REQUIRE_SEPARATE_REGISTERED_REVIEW`とする。いかなる合成例も実アクセスやfamily再開を許可しない。

## 成果物

`docs/strategy/plans/TASK-C01-EXCEPTION-REVIEW-05/` にだけ次を作る。

- `exception_review.md`: 比較、現在判断、再考条件、C01が`NOT_EVALUATED`である理由。
- `comparability_matrix.json`: registryとS2 matrixのSHA-256、R087/R101/C01の比較、現在判断、阻害条件、再考条件。
- `synthetic_exception_cases.json`: 固定判断例と、それが実行許可ではない宣言。
- `verification.md`: validator結果、入力hash、構造監査、限界。
- `review.md`: 完了条件ごとの根拠、アクセス・変更、最終監査。

`comparability_matrix.json` は少なくとも `task_id`、`registry_sha256`、`s2_admission_matrix_sha256`、`current_decision`、`prior_records`、`blocking_conditions`、`reconsideration_requirements` を持つ。`current_decision` は`NO_EXCEPTION_RECOMMENDED`とし、`prior_records`はR087/Q002とR101/Q001を含める。`blocking_conditions`には、`f05_f06_closed`、`no_new_direct_economic_evidence`、`no_finite_s2_attempt_or_budget`、`no_review_receipt`、`no_matching_grant`を含める。

## 固定検証

```powershell
.venv/Scripts/python.exe scripts/validate_c01_exception_review.py --artifacts docs/strategy/plans/TASK-C01-EXCEPTION-REVIEW-05
```

validatorは親入力hash、現在の非承認判断、必要な比較record・阻害条件、固定合成例を検査する。構造PASSを例外、grant、実データアクセス、経済性・因果性・実行可能性のPASSと扱わない。

## 終了条件と停止条件

全完了条件、固定validator、最終監査が通ればDONEとする。`NO_EXCEPTION_RECOMMENDED`は、この固定レビュー範囲の完了でありBLOCKEDではない。比較に必要な保存済み入力が矛盾し、今回の専用成果物内で整理できない場合だけ、証拠、対処履歴、未完了条件を残してBLOCKEDとする。

S2実データ、S3 PnL、family再開、例外、grant/manifest/ReviewReceipt、OOS、Final Holdoutは今回作成・変更・実行しない。
