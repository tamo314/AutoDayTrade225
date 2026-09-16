# 123. 有限バッチの共通実行管理

実装改訂: **EXECUTION-20260916-01** / 2026-09-16 JST

## 結論と適用範囲

[122の再開条件](122_project_policy_review.md)に対し、登録した研究だけを予約・実行し、失敗を含めて枠と証拠を残す共通入口を実装した。**実行管理の合成受入と、次の経済仮説の適格性は別である。** 現在の運用設定は停止、登録grantは0件。今回、新しい戦略・PnL・OOS評価は実行していない。

売買エンジン、戦略シグナル、約定・コスト・PnL算式は変更していない。旧結果も再実行・上書きしていない。119本の既存スクリプトには冒頭の実行確認だけを追加した。コード変更後のhashは当然変わるが、過去runのsnapshotが当時の正本である。

## 1. 正本と保存場所

|対象|場所・役割|
|---|---|
|実行可能な範囲|`config/research_execution.json`。現在は`market_execution_enabled=false`、family/batch/grantは空|
|過去の閉鎖群|`docs/strategy/registry/20260916_review.json`。運用設定が内容hashを固定して参照|
|一回の完全な計画|`RunManifest`。batch/family/study/spec/run、段階、全条件、seed、停止規則、親結果、仕様・証拠・設定・入力のhash、出力先を固定|
|対象計画の受入記録|`ReviewReceipt`。manifestのhash、仕様、入力契約、因果性、未知exitのnull保持、合成校正、再開理由を記録。grantがこの記録のhashを固定|
|枠・実行状態・アクセス要求|`results/research_control/ledger.sqlite3`。workspaceごとに一つ。呼出しごとに別台帳を選べない|
|再現用snapshot|`results/research_control/receipts/<run_id>/`。manifest、運用設定、ソース・テスト・仕様・設定・証拠のZIP、Python/ライブラリ版|
|研究本体|manifestで指定した`results/research/<run>/`。既存出力への再実行を拒否|

ReviewReceiptは試験の代用品ではない。`evidence`に実際の監査・試験結果を内容hashで結び、根拠がないPASSを書かない。入力の`PASS_LIMITED`は既に認められた限定Development用途にだけ使う。これらはローカルの研究記録であり、毎回ユーザーへの承認質問を要求する仕組みではない。

## 2. 実行順序

1. manifestを厳格に検証する。現在対応する段階はS2/S3/S4、期間は固定Developmentだけ。Pythonから不正に組み立てたモデルも再検証する。
2. 完全一致するgrant、閉鎖群の再開記録、既知親結果のfamily、仕様・設定・証拠・source treeのhashを確認する。ID変更で親familyを付け替えない。
3. OSの排他ロックとSQLiteのトランザクションで枠を予約し、`RUNNING`を永続化する。これが価格ファイルのhash確認・ローダー・計算より前の境界となる。
4. snapshotを保存し、Developmentの許可月だけから列挙したParquet集合と各hashを照合する。追加・削除・変更を検出する。実際のローダーでも入力root・period・運用設定・実行状態を再確認する。
5. 指定したrunnerを実行し、仕様等の再照合と出力hashの保存後に`COMPLETED`または`FAILED`を記録する。`COMPLETED`は実行完了であり、CANDIDATEや利益の判定ではない。

## 3. 予算と終了

- **1 attemptは凍結manifest全体の1回のdispatch**。全条件を予約時に消費する。結果を見て不利な条件だけ台帳から落とすことはできない。
- familyとbatchの双方に、attempt・異なるspecの内容hash・条件数の上限を持つ。失敗・中断も消費に含む。
- 現行の例外枠は全体最大3spec、最大3family、同family最大2spec。`max_total_specs`は同じ台帳全体に適用する。一律に3件を実行する許可ではない。
- policyの改訂や新しいbatch/run/study IDでも、台帳の消費済み分は差し引く。全体上限を使い切った後は終了する。この版に台帳reset・予算返却・自動補充コマンドはない。
- 同じspec・source・input内容・段階・条件・seedの技術的な再実行も拒否する。未修復の失敗をID変更で繰り返さない。経済的に近縁かどうかの判断は閉鎖台帳とレビューで行い、文章の同義性まで自動判定できるとはしない。
- 条件名や件数だけで研究内容の妥当性は保証できない。campaign adapterは全grid/stressとseedを設定から照合し、script adapterは明示した条件表・段階・seedを照合する。個別runnerの完全仕様とS0～S2受入は引き続き必要。

## 4. 実行と確認

プロジェクトrootで実行する。実装と同期したJSON schemaは次で取得できる。

```powershell
.venv/Scripts/python.exe -m n225m_bt.cli research execution-schema manifest
.venv/Scripts/python.exe -m n225m_bt.cli research execution-schema policy
.venv/Scripts/python.exe -m n225m_bt.cli research execution-schema review
.venv/Scripts/python.exe -m n225m_bt.cli research execution-status
```

次の研究では、閉鎖台帳に照らした再開根拠と完全仕様、合成受入を先に揃える。manifestの入力hashは、対象Developmentだけの既存保存記録から引き継ぐか、許可された対象で確認する。現在の停止を解除する目的でOOS/全期間を走査しない。

根拠が揃った計画だけを、具体的な上限と完全一致するgrantとして運用設定に登録する。`FileSeal`のhashはファイルの生bytesのSHA-256、manifest hashは`execution.manifest_digest()`、source treeは`execution.source_tree_digest()`で計算する。異なるJSON整形のhashを混同しない。

```powershell
# PLAN.jsonは仕様・証拠・入力・条件を固定したRunManifest。
.venv/Scripts/python.exe -m n225m_bt.cli research check-execution PLAN.json
.venv/Scripts/python.exe -m n225m_bt.cli research execute PLAN.json
.venv/Scripts/python.exe -m n225m_bt.cli research verify-execution RUN_ID
```

`check-execution`は価格を開かず、枠も消費しない。入力集合・input hashと残枠の確定は実行予約時に行う。checkの成功は予約ではなく、その後のファイル変更や他runによる枠消費でexecuteが拒否される場合がある。

`execution-status`は実行状態と残attemptを表示する。`verify-execution`は保存済み出力を確認するだけで、入力価格や計算を再開しない。後から`reports/`へ追加した派生レポートは元の計算境界を変えないが、既に封印されたファイルの変更・削除やそれ以外の追加は検出する。

## 5. runner接続

|入口|扱い|
|---|---|
|`campaign`|旧schema-v1のS3。全grid/stress、seed、設定、出力先を照合。Developmentとレポートだけを生成|
|`baseline_backtest`|AlwaysFlatのS3基盤確認。条件`always_flat`・seed 0・凍結configと任意calendar。全期間scanを廃し、共通Development readerと一意出力を使う|
|`r003_preflight`|schema-v2の非PnL S2監査。条件は`r003_preflight`、引数は`config_dir/calendar/study_config`|
|`s2_availability`|非PnL S2。条件は`ohlc_availability`、引数は`config_dir`|
|`script:scripts/<name>.py`|レビュー済みの専用runner。引数なし。`RUN_ID`、`OUT`、`RESEARCH_STAGE`、`RESEARCH_CONDITIONS`、`RESEARCH_SEED`をmanifestと一致させ、mainで`require_entry()`を呼ぶ|
|既存script・関数の直接起動|119本のscript、campaign、R003、R057、専用S2、baseline CLIは未予約なら冒頭で停止。純粋な合成計算helperは引き続き利用できる|
|`load_split` / `partition_paths` / 専用S2 partition列挙|予約のないDevelopmentも、OOSも、価格scan前に拒否。Final Holdoutは閉鎖を維持|
|旧Planner/Executorループ|自由文タスクから価格実行の予約を引き継がないため停止。`research_execution_paused=false`や`--reset`では復活しない。別経路の[126 有限バッチ](126_bounded_orchestration.md)はdesign作業、または固定manifestを上記登録入口へ渡すことだけを許可する|

campaign/scriptの仕様は、S4など別工程へ勝手に引き継がない。旧scriptに冒頭確認を付けたことは、その旧案を再開してよいという意味ではない。RESEARCH_*定義や固定出力契約を持たない旧scriptは、専用adapterの受入まで実行不可。子プロセスへの予約の暗黙継承もない。

共通の入力readerを使わない独自I/O、ingest・汎用Parquet API、既にメモリにあるデータを扱うエンジンそのものをOSレベルで封じる実装ではない。新runnerはソースレビューとこの接続契約の試験が必要で、任意のPythonを実行できる人に対するセキュリティ境界ではない。

## 6. 中断・障害時

実行中のプロセスはOSロックを保持するため、二重起動も、稼働中runへの`close-interrupted`も拒否する。通常例外・KeyboardInterruptは`FAILED`を残す。プロセス強制終了で`RUNNING`が残った場合は後続実行を止める。

```powershell
.venv/Scripts/python.exe -m n225m_bt.cli research execution-status
# 元プロセスの停止と部分成果物を確認した後だけ実行する。
.venv/Scripts/python.exe -m n225m_bt.cli research close-interrupted RUN_ID --reason "停止確認・残存成果物・次の扱いを具体的に記録"
```

この操作は`INTERRUPTED`を記録するだけで、枠や実験IDを返却しない。今回の実装はrun単位の停止・照合を保証し、計算途中のchunk再開は実装しない。部分成果物から有利な条件だけ再計算しない。必要な修復は、原因・code変更・既知情報・新しい出力先を明示して残枠内で別manifestを登録する。同じ技術状態の無条件再試行は不可。

台帳とreceiptsはローカル研究記録として保持する。削除・初期化を再開手段にしない。破損時は実行を止め、バックアップと保存runを照合する。

## 7. 合成受入と影響

受入対象は`tests/test_research_execution.py`、`tests/test_strategy_research.py`、`tests/test_orchestrator_research_stop.py`と既存の因果性・unknown-exit・S2・期間境界試験。実際のCLIからの予約→実行→status→出力照合も、隔離した合成workspaceで確認する。

検証対象: 閉鎖群、未登録grant、親family不一致、予算超過、batch/IDによる補充、入力集合・hash変更、OOS、直接呼出し、二重起動、途中失敗、orphan reservation、live processの誤閉鎖、保存結果改変、既知未来参照、unknownの0円化。既存キャンペーンの合成14条件・会計・全PASSでもOOS未読も維持する。

変更した旧scriptについて、追加した冒頭確認を除いたASTがGit基準版と同一であることも確認する。全体の検証結果・file hashes・変更前後はローカルの`results/research_audit/AUDIT-EXECUTION-CONTROL-20260916-01/`に保存する。

最初の全体pytestでは466 passed、1件は既存`.pytest_tmp`のWindows権限エラーでsetup不可だった。新しいbasetempでも同じWindows権限問題が出たため、該当1テストの一時ファイルを既存の`workspace_tmp` fixtureへ揃えた。検証内容は不変で、既存領域の削除・権限変更はしていない。関連46テストは通過した。その後、無予約baseline CLIを前提としていた既存E2E 2件を登録実行へ移行し、合成CSV ingest→凍結manifest→共通loader→エンジン→結果保存・照合まで通過した。最終の全体pytestは **471 passed（188.98秒）**。変更した共通コード・CLIのmypy（6ファイル）、変更した主要コード・テストのRuff、設定validate、文書リンク39件も通過した。コマンド出力とfile hashesを監査記録に保存した。
