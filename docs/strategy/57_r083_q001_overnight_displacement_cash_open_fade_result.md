# TASK-R083-Q001 結果: Overnight displacement cash-open fade

実行ID: `r083-q001-20260915-overnight-displacement-cash-open-fade-01`  
実行状態: **COMPLETE / REJECT**  
対象: Developmentのみ（trade_date 2021-01-01～2025-06-30）

## 実行保全

- 事前登録は追加PnL取得前に凍結し、artifactの `preregistration.json` SHA-256 は `af268d18a1c97f77418a8d744622aa4cbaec58eaf847369930cd6d707fc5fb71`。
- PnL前S2はPASS。E実行可能230件（必要180）、G<0=117・G>0=113（各必要70）、年別は2021=31、2022=52、2023=57、2024=60、2025H1=30（必要30/30/30/30/15）、説明不能除外0件。
- 取引時間版D、09:00のA、trade_date境界、current-excluded rolling 60日nearest-rank、固定09:01 entry/11:30 exitの因果性監査は全項目PASS。既約定後の未知exitは0件。
- 実行・会計監査は、1日1取引、E-fade/E-continuationの同一event・同一時刻・逆side、E/Mの非重複、Stop/Targetなし、`net=gross-fees` の全項目PASS。
- OOS、Walk Forward、Final Holdoutはいずれも未アクセス。

## 主結果（片道1 tick + 30円）

E-fadeは230取引、Net **-131,800円**、PF **0.9364**、期待値 -573.04円/取引、最大実現DD 643,860円だった。

20 trade_date MBB（10,000回、seed 20260915）のE-fade日次Net平均は -116.53円、95% CI [-726.93, 572.95]。E-fade−E-continuation対応日次差は +198.05円、CI [-1,012.38, 1,588.86]。E-fade−M-fade取引当たり差は -430.69円、CI [-3,772.75, 3,187.63]。従って、事前登録した主ANDの5条件はすべて不成立である。

G正はNet -143,560円、G負は -2,040円。年別Netは2021=-84,220円、2022=-210,240円、2023=-25,340円、2024=107,800円、2025H1=66,400円。top-10勝ち取引除外後Netは -875,900円である。

## 固定感度（救済選択に使わない）

|プロファイル|Net（円）|
|---|---:|
|q70|164,820|
|q90|10,020|
|lookback40|-165,220|
|lookback80|52,680|
|entry 09:05|-331,300|
|exit 10:30|-189,300|
|exit 14:30|-391,300|
|片道2 tick|-361,800|
|片道3 tick|-591,800|
|手数料2倍|-145,600|

固定感度の全Net>0にも不成立。`|G|`五分位は46取引ずつでNetが順に23,740、-306,760、49,240、-347,760、449,740円だったが、これは保存済み記述診断であり、閾値・部分群の救済選択には用いない。

## 判定と次工程

**REJECT**。主AND不成立のため、本結果に基づく閾値・時刻・方向・費用の追加探索、Walk Forward、OOS、Final Holdoutには進まない。

再現用の完全artifactは [results/research/r083-q001-20260915-overnight-displacement-cash-open-fade-01](../../results/research/r083-q001-20260915-overnight-displacement-cash-open-fade-01) に保存した。主な根拠は `preregistration.json`、`s2_feasibility.json`、`causality_s2_audit.json`、`execution_accounting_audit.json`、`profiles.json`、`bootstrap.json`、`decision.json`、全event/trade/daily-axis ledger、共通bootstrap index、source/config/document snapshotsである。
