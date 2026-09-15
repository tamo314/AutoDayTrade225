# TASK-R082-Q001 result: US cash-open N225 night-session continuation

run_id: `r082-q001-20260915-us-open-continuation-02`  
状態: **INCONCLUSIVE — PnL未取得**  
対象: Development trade_date 2021-01-01--2025-06-30 のみ

事前登録は [52_r082_q001_us_open_continuation.md](52_r082_q001_us_open_continuation.md) に固定した。OOS、Walk Forward、Final Holdoutにはアクセスしていない。

## PnL前監査

固定NYSE calendar `nyse_regular_trading_days_2021_2025h1_v1` のSHA-256は `ca4f74d7866af3f970be7e610979460e3935546f76adcf8503adc3a95d54b520`。`America/New_York` のzoneinfo変換で、2024-03-08→03-11はSが23:30→22:30 JST、2024-11-01→11-04は22:30→23:30 JSTへ変わることを確認した。JST固定時刻によるDST近似は使用していない。

NYSE通常取引日とN225 trade_dateの対応は1,089日、全てでSが版管理されたN225 night session内だった。説明不能な除外は0件である。pre-open-sign controlのU/P共通実行可能集合は747日、符号不一致386日、同符号361日だった。

## 可用性ゲートと停止

実行可能件数は806件で、必要850件を44件下回った。従って固定ゲートは不合格である。他の固定下限は、U正432／負374（各300以上）、年別2021:154、2022:182、2023:191、2024:187（各140以上）、2025H1:92（60以上）で満たした。

除外内訳は、entry signal又はnext open不適格83、固定exit不適格148、NYSE休場42、R004 night隔離24、U始点bar不適格1、U=0が27である。これらはすべて定義済み理由であり、価格成績による選別ではない。

事前登録どおり、Net、PF、MBB、fade／pre-open control差、感度、年別PnL、DD、利益集中度はいずれも**算出していない**。本件の決定は **INCONCLUSIVE** であり、規則を緩めた再実行、結果依存の変更、Walk Forward、OOS、Final Holdoutへは進まない。

初回の不変run `...-01` はPnL前可用性集計の未設定`status`参照で例外終了した。価格・PnL集計前の実装修正後、既存成果物を上書きせず`...-02`を作成した。`...-02`のpytest 14件、Ruff、mypy、構文検査は全てPASSである。

保存成果物: [run directory](/C:/Work/AutoDayTrade225/results/research/r082-q001-20260915-us-open-continuation-02), [decision.json](/C:/Work/AutoDayTrade225/results/research/r082-q001-20260915-us-open-continuation-02/decision.json), [S2 feasibility](/C:/Work/AutoDayTrade225/results/research/r082-q001-20260915-us-open-continuation-02/s2_feasibility.json), [calendar audit](/C:/Work/AutoDayTrade225/results/research/r082-q001-20260915-us-open-continuation-02/nyse_calendar_audit.json).
