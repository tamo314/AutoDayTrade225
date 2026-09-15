# R073-Q001 実行結果

run_id: `r073-q001-20260915-official-night-direction-day-reversal-01`  
実行日: 2026-09-15 JST  
状態: **INCONCLUSIVE — S2_GATE_FAILED**

事前登録、source/config/documentation snapshot、SHA-256、seed `20260915` を価格成績の読取り前に保存してから、Development限定のS2可用性診断を実行した。公式night開始は版管理`CalendarClassifier.session_open(trade_date, NIGHT)`を用い、旧制度は16:30、新制度は17:00として判定した。OOSおよびFinal Holdoutへのアクセスはない。

|S2項目|結果|閾値|判定|
|---|---:|---:|---|
|予定`trade_date`|1,131|—|記述|
|実行可能trade|878|900以上|不通過|
|2021|186|180以上|通過|
|2022|191|180以上|通過|
|2023|202|180以上|通過|
|2024|201|180以上|通過|
|buy|396|300以上|通過|
|sell|482|300以上|通過|

実行可能trade総数が22件不足したため、S2のAND gateは不通過である。仕様どおり、反転・momentum対照のPnL、Net、PF、MBB、感度、Candidate判定は算出も表示もしていない。R072-Q001のコード・文書・成果物は変更していない。

再現用成果物は `results/research/r073-q001-20260915-official-night-direction-day-reversal-01/` に保存した。`pre_execution_validation.json`はpytest 14件、Ruff、mypyの全通過を記録し、`access_ledger.json`、`s2_feasibility.json`、`decision.json`、`COMPLETED.json`はDevelopmentだけを対象とする。既存の事前登録済み範囲を超える救済実験は実施しない。
