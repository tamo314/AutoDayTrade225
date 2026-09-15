# TASK-R079-Q001: cash-session first-hour extreme displacement continuation

状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**  
登録日: **2026-09-15 JST**  
family_id: `cash_session_first_hour_extreme_price_discovery` / study_id: `R079-Q001` / spec_version: `v1`  
対象: Development trade_date 2021-01-01--2025-06-30 のみ

## 既知の探索履歴と仮説

これは R078-Q001 の Development 結果に依存する follow-up であり、独立した確認ではない。R078 では E-fade minus E-continuation の対応日次差 CI が負と判明している。`prior_information_seen=true`、`learned_from_r078_failure=true` を記録し、結果に関わらず判定上限は `INVESTIGATE` とする。

cash-session 最初の1時間の極端な価格変位は、一時的な不均衡ではなく情報主導の price discovery を表し、その方向は14:30まで持続する。

## 凍結した event・注文・費用

R078 の正式成果物 `r078-q001-20260915-cash-first-hour-extreme-fade-01/primary_events.json` を完全に再利用する。すなわち A=09:00 open、B=09:59 close、R=B-A、当日を含まない直前60適格trade_dateの `|R|`、nearest-rank `ceil(n*p/100)-1`、E=`|R|>=q80`、M=`q20<|R|<q80`、R004 day-session隔離、zero・欠損・同値・entry/exit observability の処理を変更しない。R079 は同じ保存済み event ledger の全 event ID（`trade_date`）と digest が一致することを PnL 前に要求する。再選別、補充、救済はしない。

Eでは R 方向（R>0 long、R<0 short）へ、Bを観測後の最初の適格bar open（通常10:00 open）で1枚入り、14:30 openで決済する。Stop、Target、再entry、gap、night range、opening range、10:00以後の選別は使わない。既約定後のexit不明はnullであり0円に置換しない。

基本費用は片道1 tick（5 points）+ 片道30円、1枚とする。slippage は engine 内で一度だけ適用し、Net=`Gross-fees` とする。

## PnL前整合性ゲート

R078 event ledger と完全一致し、E executable=230、R正/負=111/119、年別 E=2021:36、2022:48、2023:65、2024:57、2025H1:24、説明不能除外=0をすべて要求する。不一致なら `AUDIT_STOPPED` とし、return、PnL、PF、bootstrapを算出しない。

## 主評価・対照・判定

主対照は予定日軸の無取引0円、同一E日・同entry/exitでR逆方向のfade、ならびにMで同じcontinuation規則を行うmagnitude-controlである。

主ANDは以下の全てである。

1. E-continuation Net > 0。
2. E-continuation PF > 1。
3. 20 trade_date 非循環MBB（tail truncation、linear percentile、10,000回、seed=20260915）のE-continuation予定日軸平均Net 95%CI下限 > 0。
4. 同一E日のE-continuation minus fade 対応日次差の同CI下限 > 0。
5. E-continuation minus M-continuation の取引当たりNet差（同じblock抽出の各状態Net合計/取引数、空反復は捨てず保存）の同CI下限 > 0。

主AND不成立は `REJECT`。成立しても、全固定感度Net>0、R正負ともNet>0、2021--2024の少なくとも3年と2025H1が正、top-10勝ち取引除外後Net>0を満たさなければ `INVESTIGATE`。満たしても Development 再利用および結果依存 follow-up のため上限は `INVESTIGATE` である。

## 固定感度・保存範囲

感度は R078 と同じ q70/q90、参照40/80日、signal終点09:44/10:29、entry追加1本遅延、exit 14:15/14:45、片道2/3 tick、手数料2倍だけである。各profileの event、orders/fills/trades、予定日次軸、metrics、年別、R正負別、最大DD、利益集中度、top-10勝ち取引除外後Net、bootstrap、execution/accounting/causality auditを保存する。

結果を用いた追加の閾値・時刻・方向・費用探索、Walk Forward、OOS、2026年以降のFinal Holdoutには進まない。
