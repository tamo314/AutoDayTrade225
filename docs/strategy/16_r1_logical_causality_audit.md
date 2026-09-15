# 16. R1 論理・因果性監査（開始記録）

改訂ID: **RG-20260915-01**  
状態: **PAUSED_METHOD_REPAIR / R1限定実装中**

本記録は、R0で固定したR1の最初の実装範囲である。市場価格、出来高、特徴量、注文、約定、取引、日次PnL、OOS、Final Holdoutを読まず、旧runを再実行しない。

追加する新規研究用の責務は、明示的な条件行列とR031型gate成立可能性、決定時点のU/E_exec/signal/order prefix不変性、段階別アクセス拒否、固定予定軸での0/null分離である。既存studyの条件辞書・runner・legacy判定は変更しない。

最初の合成テストはM01の同一経路・反対side・0tick費用前双方負ANDの拒否、M02--M04の将来analysis/outcome変異による決定prefix不変性、Final Holdout拒否、OOS fail-closed、未知損益null保持を対象とする。実行結果とcode hashは次節に保存する。

## 初回実測記録（2026-09-15 JST）

新規追加は `research/conditions.py`、`causality.py`、`governance.py`、`outcomes.py` と `tests/test_r1_governance.py` だけである。既存study runner、戦略、設定、価格データ、既存成果物には変更を加えていない。

- `pytest tests/test_r1_governance.py tests/test_r031_q001.py tests/test_r046_q001.py tests/test_r049_q001.py`: **16 passed**。全て合成fixtureであり、価格ファイルを読まない。
- `ruff check`（上記4モジュールと新規test）: **PASS**。
- `mypy`（上記4モジュール）: **PASS**。
- `git diff --check`: **PASS**。

対象SHA-256は、`conditions.py=5aaa0f80…04fc5`、`causality.py=c52b3426…7df21`、`governance.py=65041a6f…c1796`、`outcomes.py=8f123900…330b1`、`test_r1_governance.py=bc56d193…96409` である。

この通過はR1全体、既存R046/R049 pipelineの因果性、R032再照合、R065 cross-session実装、データ品質、OOS開封のPASSを意味しない。次のR1作業は、新しい契約を実際の新規stage runnerへ接続するための設計・影響監査であり、旧runの再実行は含まない。

## S0/S1仕様監査パス（2026-09-15 JST）

`research audit-spec SPECIFICATION.json --output DIRECTORY` を実装した。仕様JSONのみを読取り、既存directoryを上書きせず、`spec_audit.json`、`condition_resolution.json`、`gate_witnesses.json`、`causality_audit.json`、`access_ledger.json`を出力する。S0/S1以外、未収録のfamily/study/spec/run/protocol識別子、preregistration/source snapshot hash、seed、nuisance宣言、判定条件、明示条件行列、またはgateの不足は拒否する。

`tests/test_r1_spec_audit.py`で排他的出力、物理市場データread=false、未実施gate witness/causalityが`NOT_RUN`のまま保存されること、M09不足、M01矛盾拒否を確認した。既存CLIのsynthetic E2Eを含む追加検証は **20 passed**、CLI help、Ruff、対象5モジュールのmypy、`git diff --check` はPASSである。新規 `spec_audit.py` SHA-256は`956d5091…7eea5`、testは`6a851eaa…f3daf`、変更したCLIは`7e7d418f…9fd2f`。

このパスはS0/S1の構文・論理監査であり、gate witnessやM02--M04の実pipeline mutationを成功と表示しない。`gate_witnesses.json`／`causality_audit.json`は意図的に`NOT_RUN`であり、R1の次段階で合成入力と新stage runnerを結合して初めて実測対象になる。

## M02/M03 decision-time adapter（2026-09-15 JST）

旧結果用の`r046_event`／`r049_event`は変更せず、別関数として`r046_exec_event`と`r049_exec_candidate`を追加した。前者は09:14時点のW0と過去W0だけ、後者は各anchorの5分観測と同anchorの過去観測だけを用いる。いずれも後刻placebo、後続anchor、entry/exit pathの可用性をE_execの前提にしない。

R046では09:45--10:14相当のfuture placeboを欠損化しても新adapterのE_execが不変で、保存されたcommon-E関数は従来どおりskipになることを確認した。R049では後続anchorを欠損化しても先行anchor candidateが不変で、保存されたall-six-anchor関数は従来どおりskipになることを確認した。これは旧成績への新adapter適用や影響件数／PnLの推定ではない。

R1追加・旧回帰を合わせた対象検証は **22 passed**、変更対象8 sourceのRuffとmypy、`git diff --check` はPASS。adapter SHA-256は`r046.py=fc13fc33…6a5f7`、`r049.py=e4c892c6…bb522`である。M04のR060--R064、実runnerへの接続、R032台帳照合、R065 profileは依然未実施である。

## M04 R061 decision-time adapter（2026-09-15 JST）

R061についても、保存済みの`r061_event`（全感度窓・ordinal 166までのcommon-Eを要求する旧報告契約）を変更せず、`r061_exec_event`を追加した。新adapterは選択された圧縮窓の過去120営業日W、当日ordinal 1--90、そして最初の91--120 close breakoutまでだけを読む。最初のsignalを見つけた時点で停止し、entry bar、固定exit bar、signal後のbreakout探索、及び20/30/40分の未選択感度窓を`E_EXEC`判定に戻さない。予定entry／exit時刻は価格を参照せずsignal時刻から算出する。

合成fixtureでordinal 151を欠損化した。R1 adapterは同一の`E_EXEC`・signal予定を返し、旧`r061_event`は従来どおりcommon-E path不足でskipとなることを確認した。これはlegacy成績・対象件数・損益への適用ではない。対象検証（R046/R049/R061、R1仕様・governance、CLI回帰）は **27 passed**、R061およびtestのRuff、R061のmypyはPASS、`git diff --check`はPASS（GitのLF/CRLF警告のみ）。SHA-256は`r061.py=28848882…ac776`、`test_r061_q001.py=7081e203…3cdd`である。

M04の残りR060/R062/R063/R064、R1 runnerへの結合、R032の元台帳・集計関数照合、R065のcross-session profileとaccess記録の照合は未実施である。運用状態は引き続き`PAUSED_METHOD_REPAIR`であり、OOS／Final Holdout／価格由来データは未読である。

## M04 R060/R062 decision-time adapter（2026-09-15 JST）

`r060_exec_event`は、前TSE通常session全体と選択された20/30/40分のopening観測prefixだけで、A/Dの選択状態と予定注文時刻を返す。未選択観測窓、entry/exit bar、15/30/45分の固定exitは読まない。`r062_exec_event`は、target night observation、night-onlyの過去120参照、選択された10/15/20分reaction prefixだけでcellを返す。旧`r060_event`／`r062_event`のcommon-E（全感度・固定exit経路）契約は変更していない。

R060ではfuture exit相当のordinal 71、R062ではreaction後のordinal 51を欠損化した合成入力を用いた。各R1 adapterの`E_EXEC`とselectionは不変で、対応するlegacy関数だけがcommon-E不足でskipすることを確認した。R062のnight observationはunit境界で明示的にモックし、価格ファイルは読んでいない。R060 adapter SHA-256は`5b4e38ab…afe93`、R062 adapterは`87194e69…f602`、testは各々`6ee2d6a1…44311`、`f032eb02…0139e`である。

R060単体は **3 passed**、残るR1対象群は **29 passed**。変更対象11 sourceのRuffとmypyはPASS、`git diff --check`はPASS（GitのLF/CRLF警告のみ）。R063/R064の動的block／response adapter、R1 runner接続、R032照合、R065 profileは未実施であり、これらの未実施をPASS・修正済みとは扱わない。

## M04 R063/R064 decision-time adapter（2026-09-15 JST）

`r063_exec_event`は、選択されたblock lengthの凍結済みposition別Uを用い、blockを時系列順に探索する。最初のq70 shockを発見した後は、そのresponse prefix終端までで停止する。`r064_exec_event`は、選択されたobservation windowの凍結済みUと、そのwindow＋15分response prefixだけを使う。両者ともentry/exit bar、未選択感度window、legacy common-E最終ordinalを実行時判定へ入れない。`make_event`は変更していない。

R063ではordinal 101、R064ではordinal 136を欠損化した合成入力を用い、R1 adapterが`E_EXEC`とA selectionを維持する一方、旧`make_event`だけがcommon path不足でskipすることを確認した。これはrun再実行、既存成績の再集計、影響件数・PnL推定を意味しない。SHA-256は`r063.py=1693b6ec…c7fc4`、`r064.py=ebeaf11a…c6ba`、testは`67a5046d…1033d`、`b565c050…9e99c`である。

R060を含むM04各study testは **3 + 39 passed**、変更対象13 sourceのRuffとmypy、`git diff --check`はPASS（GitのLF/CRLF警告のみ）。これはsource-levelの因果契約・回帰確認であり、runner結合、R032元台帳照合、R065 cross-session照合、データ品質確認、旧run影響判定、OOS／Final Holdout開封は依然未実施である。状態は`PAUSED_METHOD_REPAIR`を維持する。

## selection/execution不変ledger境界（2026-09-15 JST）

`research/execution_ledger.py`を追加した。`freeze_selection`は、明示された`selection_status`／`execution_status`を持つ`E_EXEC`だけを決定recordとして受け入れる。`record_execution`は後続のfilled/canceled/rejectedを別recordに接続し、選択状態を更新しない。execution語（`filled`等）をselectionへ書く、not-selected eventをscheduledにする、予定entryなしでscheduledにする不正な入力は拒否する。R046/R049 adapterにも明示状態を加えた。

RG13相当の合成テストで、A選択を凍結した後にfilledを記録してもselectionがAのまま残ること、`selection_status=filled`への上書きが拒否されることを確認した。このledgerはR1の純粋な境界であり、既存runnerや既存結果を接続・更新していない。変更対象14 sourceのRuff/mypy、対象群 **40 passed**、R060単体 **3 passed**、`git diff --check`はいずれもPASSである。市場データ、価格由来成果物、OOS、Final Holdoutは未読である。

## M06 R032 metric-lineage contract（2026-09-15 JST）

R032 runnerのコードのみを読み、既存result ledger、日次PnL、bootstrap数値は開かなかった。`gap_direction_0tick_diagnostic.json`の確認／非確認表示は、各filled event群のgross PnLをtrade countで割る`JPY_PER_TRADE`の算術平均である。一方、`bootstrap.json`の確認−非確認は、固定target night軸の各日gross PnL（no-trade日は0）を差し引く`JPY_PER_TARGET_NIGHT`の日次平均との差であり、20日moving-block bootstrapの中心もこの日次estimandである。

`research/reconciliation.py`は値を入力に取らず、artifact・field・単位・population・aggregation・weighting・centerだけを検査する。R032の二つを比較するとunit、population、aggregation、weighting、centerが一致せず、`RECONCILIATION_REQUIRED`となる。これは原文の数値、正しい差、CIを推測・再計算するものではない。実台帳を読める別監査で、固定axis、events、daily ledger、bootstrap implementationを同一hashで照合するまで表示値は保留する。

## M10 R065 calendar-only cross-session profile（2026-09-15 JST）

`r065.py`に、既存`boundary_event`とは別の`CrossSessionProfile`と`boundary_order_plan`を加えた。profileは`explicit_cross_session`、正の最大壁時計保有、最大1建玉、calendar boundary限定の保留注文、末端`open_position_null_pnl`、モデル限界注記をすべて明示しない限り拒否する。planは版管理calendarからA（n0→d0）とD（d0→n1）の予定orderを作るだけで、Bar・価格・run artifactを受け取らない。order作成時刻が予定openより前であること、保有時間上限、Development末端のn1が範囲外ならDを価格読取り前に除外することを検査する。

合成予定表テストでA/Dのcalendar-only E_exec、session-flat profileの拒否、2025-06-30のDを`PLANNED_N1_OUTSIDE_DEVELOPMENT`として除外することを確認した。これは既存engineがcross-session保有を実行できること、新R065の完全な事前登録、R65-01〜10のPASS、価格未読／未実行の実システム証明ではない。R032/R065/R1 governance対象は **13 passed**、RuffとmypyはPASSである。

### R065 shared-engine capability guard（2026-09-15 JST）

共有`BacktestEngine`とbaseline設定をコード／設定だけで確認した。engineはbar-closeの`on_bar` signalとnext eligible bar openのpending orderだけを持ち、calendar-scheduled open order型を持たない。また、入力末端の建玉を最後の観測価格で`END_OF_DATA`決済し、baselineはsession終端前force-flatとsession跨ぎpending取消を有効にする。これはR065の予定open order、休場跨ぎ保有、exit欠損時の`OPEN_POSITION`／null PnL契約を満たす実行能力の証明にならない。

`assess_shared_engine_for_cross_session`は設定を変更せず`BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED`を返す。合成testはこのblockと根拠（calendar-scheduled orderなし、force-flat）を確認した。したがってR065の実行接続はR1の範囲外であり、別executorの設計・実装・経済回帰・R65-01〜10を伴う後続段階なしには開始しない。市場データ・run artifact・OOS・Final Holdoutは未読である。

## TASK-R1-02 合成決定binding（2026-09-15 JST）

新規`research audit-synthetic-decision`は、`AUDIT_SYNTHETIC`だけを明示受入し、7件の加算adapter（R046/R049/R060/R061/R062/R063/R064）のbinding表、selection/executionの別record、R065 profileの能力状態を排他的監査dirへ保存する。これは`research run`、market loader、cache、`BacktestEngine`を呼ばない。non-synthetic route、raw/Silver/Gold/Parquet、OOS、Final Holdout、legacy routeは読み取り前に拒否し、未知profileはfallbackせず拒否する。R065 shared-engine profileは引き続き`BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED`であり、R1 synthetic profileの実測を停止しない。

監査`AUDIT-R1-02-20260915T020000Z`は`results/research_audit/AUDIT-R1-02-20260915T020000Z/`に保存した。実在commitは`a129555ad0f2104ebe4745f8fbacef7d55a1e9a2`、source/fixtureの完全SHA-256、環境、access ledger、R065 dependency、pytest stdout/stderr、成功時のみ生成する`COMPLETED.json`を含む。限定node群は**56 passed**、Ruff、対象sourceのmypy、`git diff --check`はPASSだった。全7件の実adapter（R046/R049/R060/R061/R062/R063/R064）の合成bar出力を`freeze_selection`へ通した。R049とR060は旧common-E契約を変更せず、execution adapterだけへtop-level `planned_entry_jst`（R060には`planned_exit_jst`も）を加算した。R062はmonkeypatchなしに合成night barから実night observation、120 scheduled reference、reaction prefix、ledgerまでを通し、reaction後のprice変異は不変、decision時点前reactionを変えた場合はselectionが変化することを確認した。R063/R064は凍結値を注入せず、合成bar履歴の`candidate_rows`→rolling U→adapter→ledgerを通した。RI-02では同一の合成価格変化からlong/short 0-tick費用前平均を導出する有限証人を追加し、反対side恒等式を確認した。RI-07は`quality_available_at`と`quality_detected_at`を分け、後刻訂正を過去decisionへ遡及適用しないas-of QCを合成検査した。RI-05はA/B/C conditionごとにfirst anchorを独立選択し、取消後retryを明示policyなしに行わないことを検査した。filled注入は`INJECTED_TEST_EVENT`としてselection外へ追記する。open-position/null PnLとknown-no-trade/0円、duplicate scheduled intent、既存dir上書き拒否、read-spyによるmarket loader／`BacktestEngine.run`未呼出も確認した。実市場入力、OOS、Final Holdout、旧run artifactは読んでいない。

この**TASK-R1-02の合成決定pipeline受入**は完了とする。ただしこれはR1全体、R2、R3-A、R065専用executor、Development実行の完了・許可を意味しない。R065 shared-engineは引き続き`BLOCKED_SEPARATE_EXECUTION_IMPLEMENTATION_REQUIRED`であり、状態は`PAUSED_METHOD_REPAIR`を維持する。
