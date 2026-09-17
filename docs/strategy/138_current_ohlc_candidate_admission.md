# 138. 現有225Labo OHLC候補の入場判定

運用改訂: **POLICY-20260917-01** / 2026-09-17 JST  
実行設定: `config/orchestration/current_ohlc_candidate_admission.json`

## 目的

現有225Laboの1分`日時・OHLC`、版管理済みcalendar、行内適格性だけで将来のS0を設計できる候補を、閉鎖台帳と照合して有限に選別する。これは[137](137_current_data_scope_gap_audit.md)で確認した、対象外入力の監査が既定キューを占有していた不整合の修正である。

このタスクは候補選定のためのdesignであり、価格、Parquet、PnL、OOS、Final Holdoutを読まない。したがって、ここでの`ADMISSIBLE_FOR_S0`は実データ実行の許可でも収益性の主張でもない。

## 入力境界とキュー

|区分|扱い|
|---|---|
|`CURRENT_225LABO_OHLC`|日時、OHLC、`trade_date`/`calendar_date`、session、版管理済みcalendar、行内適格性。既定キューに載せられる唯一の研究入力プロファイル。|
|`REQUIRES_VOLUME_SEMANTICS`|出来高/VWAP。入力拡張キューのみ。C02の結果を現有OHLC研究の停止理由にしない。|
|`REQUIRES_EXTERNAL_SERIES`|ドル円などの外部価格。入力拡張キューのみ。|
|`REQUIRES_EVENT_TIMESTAMP_LEDGER`|実発表時刻を伴うイベント。入力拡張キューのみ。|

`INPUT_EXPANSION`のタスクは`config.json`の既定`bounded_task`に設定できない。実行する場合は、入力範囲を明示的に拡張した上で`--batch`を指定する。`--check`と`--status`は履歴監査のために利用できる。

## 固定した作業範囲

読むものは現方針、225Labo限定スコープ、統治規約、閉鎖台帳、既存結果の索引、[122](122_project_policy_review.md)、[123](123_registered_execution_control.md)、[124](124_next_research_orchestrator_handoff.md)、[137](137_current_data_scope_gap_audit.md)である。

次を変更・作成・実行してはならない。

- `data/raw/`、正規化済みParquet、派生バー、取引明細、価格cache、外部時系列
- 実データPnL、OOS、Final Holdout、engine、既存結果、registry
- manifest、ReviewReceipt、grant、`market_execution_enabled`、実データ予算
- 閉鎖familyの再開、旧仕様の件数下限・時刻・対照の事後変更

外部検索は不要であり、外部データの取得・問い合わせ・購入を行わない。

## 作業契約

1. `input_admission.json`に、許可する列と禁止する入力分類、`PASS_LIMITED`の適用範囲、今回の市場データ/PnLアクセスが0件であることを記録する。
2. `candidate_screen.json`で既存13群、C01/C05、C02〜C04を区別し、新規OHLC-only family候補を**0又は1件**だけ判定する。候補は機構、既存familyとの差分、必要な過去情報、利用入力、反証予測を持つ。既存の閉鎖familyへの近縁救済なら候補にしない。
3. 候補がなければ`NO_ADMISSIBLE_CANDIDATE`で終了する。これは有限探索の結論であり、データ列の意味・外部資料・価格アクセスが不足したという結論に置き換えない。
4. 候補がある場合だけ`s0_feasibility_contract.json`に、PnL前の一回限りの可用性校正を記す。warm-up部分期、必要な共同セル、最小効果・精度、対象日、非PnL出力、停止条件を候補に合わせて固定する。既知の4対8、41対45を再発見するだけの反復は契約に入れない。
5. `verification.md`と`review.md`で、入力境界、近縁性、有限性、未変更範囲、validatorの結果を監査する。次の自動dispatchを作らない。

## 成果物の形式

|成果物|必須キー又は内容|
|---|---|
|`input_admission.json`|`input_profile`=`CURRENT_225LABO_OHLC`、`allowed_columns`、`forbidden_input_categories`、`pass_limited_scope`、`market_data_read`=`false`、`pnl_access`=`false`、`decision`=`CURRENT_OHLC_ONLY`|
|`candidate_screen.json`|`decision`、`candidate`。候補ありなら`mechanism`、`family_difference`、`prior_information`、`allowed_inputs_only`、`falsification_prediction`を含める。|
|`s0_feasibility_contract.json`|`decision`、`requires_registered_s0`。候補ありなら`warmup_policy`、`precision_target`、`minimum_effect`、`non_pnl_outputs`、`stop_conditions`を含める。|
|`verification.md`|固定validator、相対リンク、差分、実アクセス0件、保護対象の不変を確認する。|
|`review.md`|最終判定、候補又は候補なしの根拠、次の人手判断、実データ実行を許可しないことを記す。|

## 次工程の境界

`ADMISSIBLE_FOR_S0`が出ても、実データ研究は自動で開始しない。人手で候補を確認した後、[123](123_registered_execution_control.md)に従って、完全仕様、S0の有限予算、manifest、ReviewReceipt、一致grantを一つの登録パッケージとして作る。その後もPnLはS0可用性の通過だけで許可されない。

候補がなければ、既存familyを別名で再試行せず、経済仮説を追加する人手判断を待つ。対象外入力の調査を再開する場合は、現有OHLC研究とは別の`INPUT_EXPANSION`として範囲変更を記録する。
