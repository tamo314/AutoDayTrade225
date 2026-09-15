# TASK-R085-Q001 結果: Day終了からNight再開への短期ギャップfade

最終実行ID: `r085-q001-20260915-day-night-reopen-gap-fade-04`  
対象: Developmentのみ（NIGHT公式`trade_date` 2021-01-01～2025-06-30）  
判定: **REJECT**

## 実行の完全性

事前登録、設定、入力partition hash、source snapshot、validation、予定軸、event/order/fill/trade/daily-axis ledger、共通MBB indexを保存した。-01はengine局所trade IDを横断一意と誤判定した会計監査bug、-02はDAY日を予定軸としていた実装誤り、-03は明示済みDevelopment境界reasonをS2許可一覧に登録し忘れたPnL前分類誤りとして不変保存した。各訂正は技術的で、経済規則・価格・約定・費用・感度・seedを変えない。-04はofficial NIGHT `trade_date`を予定軸にした最終実行である。

S2はPASSした。E実行は228件（必要180）、J正/負は110/118件（各必要60）、年別は2021=32、2022=51、2023=57、2024=60、2025H1=28（必要25/25/25/25/12）、説明不能除外は0件だった。予定軸の一意性、D/N一対一対応とcalendar/trade_date境界、制度版boundary、current-excluded参照60件、翌適格open entry、night open+60分以後の最初の適格exitをPnL前に全てPASSした。既約定後の未知exitは0件だった。

実行・会計監査も、1 event 1取引、E fade/continuationの同event・同entry/exit・逆side、E/M非重複、Stop/Targetなし、`net=gross-fees`を全てPASSした。OOS、Walk Forward、Final Holdoutは`NOT_ACCESSED`である。

## 主結果

E-fadeは228取引、Net **-701,680円**、PF **0.4756**、期待値 **-3,077.54円/取引**、最大実現DD **733,780円**だった。20 trade_date non-wrapping MBB（10,000回、seed=20260915）の予定日次Netは-620.41円、95% CI **[-1,219.86, -203.53]**円だった。

同一E eventのJ方向continuationはNet **+218,320円**、PF 1.2490だった。E-fade−continuationの日次差は-813.44円、95% CI **[-1,974.38, +7.10]**円。M-fadeは510取引、Net -862,600円、PF 0.5411で、E−M群別予定日次Net差は+142.28円、95% CI **[-448.25, +623.30]**円だった。従って主ANDの5条件は全て不成立である。

全固定感度もNet<0だった。q70=-841,800円、q90=-609,740円、lookback40=-674,300円、lookback80=-739,600円、entry+5分=-610,680円、exit+30分=-425,680円、exit+90分=-654,680円、2 tick=-929,680円、3 tick=-1,157,680円、手数料2倍=-715,360円。J正/負も-357,100円/-344,580円で負、全night制度版も負だった。年別は2022 +25,440円と2025H1 +7,820円のみ正、top-10勝ち取引除外後Netは-915,080円だった。

よって、固定された短期night再開gap fade仮説は**REJECT**。結果依存の閾値・保有時間・制度版選択、Walk Forward、OOS、Final Holdoutへは進まない。

完全artifactは[results/research/r085-q001-20260915-day-night-reopen-gap-fade-04](../../results/research/r085-q001-20260915-day-night-reopen-gap-fade-04)に保存した。
