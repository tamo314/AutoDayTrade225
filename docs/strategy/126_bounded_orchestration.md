# 126. 有限Planner／Executor連携と開始方法

実装改訂: **ORCHESTRATION-20260916-01** / 2026-09-16 JST

追補: 本書はschema v1の仕様・初回受入記録。現在の既定は [128 自律継続](128_autonomous_orchestration.md) のschema v2と [127 C01準備](127_c01_autonomous_preparation.md)。以下の「現在」「既定」や3/5回上限はv1実装時点の記録として保持する。実データの登録入口とv1の保護は維持する。

ユーザーの自動連携実装依頼に基づき、固定タスクの作成・レビューを自動で進める入口を追加した。旧自由文ループの無条件停止を解除する方式ではない。現在の既定タスクはC01の設計で、実データ研究の運用設定・閉鎖台帳・grant 0件は維持する。

## 1. 正しい開始方法

`C:\Work\AutoDayTrade225` で次を実行する。

```powershell
# 契約・保存状態とCLIの存在／versionを確認。モデル呼出し・価格アクセスなし。
.venv/Scripts/python.exe orchestrator.py --check

# 固定タスクを開始。途中状態があればその続きから実行。
.venv/Scripts/python.exe orchestrator.py

# 状態だけを確認。Planner／Executorを起動しない。
.venv/Scripts/python.exe orchestrator.py --status
```

明示指定もできる。

```powershell
.venv/Scripts/python.exe orchestrator.py --batch config/orchestration/c01_design.json
```

1回のExecutor＋Plannerで区切る場合は `--once` を付ける。その後同じ開始コマンドで続行する。Plannerだけの再試行待ちならExecutorを再実行せずレビューから再開する。`--check`はCLIの存在・versionと契約確認で、認証・モデル利用可否・ネットワーク到達性を保証しない。

`config.json` の `bounded_task` が既定バッチを選ぶ。既存のPlanner/Executorモデル、CLIコマンド、timeout、sandbox設定を利用する。`research_execution_paused=true` は旧ループの停止を表し、有限designバッチの起動を妨げない。値をfalseに変える必要はない。

## 2. 今回の固定範囲

正本は [バッチ設定](../../config/orchestration/c01_design.json)、[124 引継ぎ](124_next_research_orchestrator_handoff.md)、[125 初期指示文](125_next_research_initial_task.md)。Git対象外の `research/INITIAL_TASK.md` や旧 `.orchestrator/state.json` は新経路の入力に使わない。

- task_id: `TASK-NEXT-C01-DESIGN-01`
- mode: `design`
- Executor最大3回、Planner最大5回。最低回数はなく、1回でも完了できる。
- 初回は125の固定タスクをExecutorへ渡し、Plannerが成果物をレビューする。continueのときだけ同じ固定タスクに修正指摘を付け、残回数内でExecutorへ戻す。
- 成果物: `docs/strategy/plans/TASK-NEXT-C01-DESIGN-01/` の `evidence.md`、`design.md`、`review.md`。
- C01一案、外部調査の累積上限は124を継承。別候補・別仮説・実データ診断へ自動で移らない。
- HOLD/CLOSEでも記録が完成していればdone。成果物の存在・非空を機械確認し、内容の妥当性はPlannerがレビューする。ファイルの存在だけを研究PASSとしない。

## 3. 保存と終了

`.orchestrator/bounded/<task_id>/` に契約hash、参照ファイルhash、状態、消費回数、Executor／Plannerログ、各レビュー決定を保存する。旧ループのログを上書きしない。共通OSロックで有限タスクの二重起動を防ぐ。

各子プロセス起動前に回数を永続化する。失敗・中断でも返却しない。再起動しても上限は増えず、同じタスク・範囲内容を別IDへ振り替えることも拒否する。自然言語の意味的な同一性を完全に機械判定するものではなく、近縁性の審査は引き続き120を適用する。

入力タスク、範囲、コード、主要規約、運用設定、閉鎖台帳、モデル等の実効configを初回に固定する。途中の変更は拒否する。変更された契約で続けたい場合、元の記録・消費を保全して終了レビューと新しい明示範囲が必要。hashを手で合わせたり、台帳を削除して再実行しない。

|状態|意味と操作|
|---|---|
|NOT_STARTED|まだ呼出していない。開始できる|
|EXECUTOR_PENDING|同じタスクの作業待ち。同じ開始コマンドで続行|
|PLANNER_PENDING|作業結果は保存済み。Planner失敗後もこの状態からレビューだけ再試行できる。試行ごとにPlanner回数を消費|
|EXECUTOR_RUNNING / PLANNER_RUNNING|呼出し予約済み。別の稼働プロセスがあればロックで拒否。プロセス不在でも自動再実行しない|
|DONE|Plannerが完了と判断し、必要成果物が揃った。再起動しても再実行しない。保存成果物のhash変更を検出|
|FAILED|Executorの異常終了等。ログと残存出力を保全し終了。無条件再試行しない|
|INCOMPLETE|doneでも成果物不足、または登録実行後に追加作業が必要。終了し不足を記録|
|EXHAUSTED|回数上限到達。候補なしでも終了|
|INTERRUPTED|中断を確認して閉じた。消費回数を保持して終了|

元の子プロセスが終了したこととログ・部分成果物を確認した後だけ、残ったRUNNINGを閉じる。

```powershell
.venv/Scripts/python.exe orchestrator.py --close-interrupted "元プロセスの停止、ログ、残存成果物を確認した内容"
```

これは再開・枠返却ではない。親だけが異常終了して子プロセスが残っている場合、先に子の終了を確認する。`--reset`は有限バッチで拒否する。旧状態・ログの削除も不要。

## 4. 将来の実データ研究との接続

`mode="registered"` では、`manifest_file` に完全仕様・証拠・grantと一致する既存RunManifestを指定し、`max_executor_calls=1` とする。タスク・範囲文書とPlanner上限も固定する。実データ用grantをこのバッチが作ることはない。

Executor部分はAIへの自由文指示ではなく、同じPython環境で次を順に実行する。

1. `research check-execution <固定manifest>`
2. `research execute <同じmanifest>`
3. `research verify-execution <manifestのrun_id>`

各段階の失敗で停止する。期間、閉鎖群、source/input/spec hash、ReviewReceipt、残枠、価格I/O前予約は123の管理に従う。Plannerはその固定runの結果だけをレビューし、continueでも別実験をdispatchしない。登録実行後のPlanner再試行で研究本体は繰り返さない。

実データ研究の残枠は `results/research_control/ledger.sqlite3` が正本で、オーケストレーターの呼出回数とは別。現在はgrant 0件のため、実データ用バッチは起動可能な研究を増やさない。OOS／Final Holdoutの入口も追加しない。

## 5. 保護の限界と検証

これは作業範囲、呼出回数、契約変更、登録入口を管理する仕組みで、エージェントの全ツールに対するOSレベルのアクセス制御ではない。既存CLIのsandbox設定を引き継ぐ。自由文feedbackの意味や検索件数を完全に機械検証せず、凍結タスクの再提示、証拠記録、Plannerレビューで管理する。任意のPythonや設定を書き換えられる利用者を隔離する仕組みとは主張しない。

今回の検証は `tests/test_orchestrator_batch.py` と既存停止・登録実行テストで行う。偽のCLI応答で実際のExecutor／Plannerプロンプト・ログ経路を通し、残回数・再開・障害・出力不足・設定変更・二重起動・登録コマンドへの受渡しを確認する。実モデルによるC01研究や実データPnLは実装テストでは開始しない。

2026-09-16の検証結果：全体pytest **491 passed（216.31秒）**。全体実行開始後に追加したWindows文字コード回帰と最後のログ保存修正を含め、関連テストを再実行して **26 passed**。変更したPythonのRuff・mypy、新規モジュール／テストのformat確認、ローカル文書リンク66件、`git diff --check`も通過した。実機の`--check`はcodex-cli 0.154.0を検出して成功、`--status`は`NOT_STARTED`。実データの運用設定・閉鎖台帳と登録hashは不変、grantは0件を確認した。
