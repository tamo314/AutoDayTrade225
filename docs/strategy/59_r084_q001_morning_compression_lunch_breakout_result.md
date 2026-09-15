# TASK-R084-Q001 結果: 午前圧縮レンジの昼休み後突破継続

実行ID: `r084-q001-20260915-morning-compression-lunch-breakout-01`  
実行状態: **COMPLETE / REJECT**  
対象: Developmentのみ（trade_date 2021-01-01～2025-06-30）

## 実行保全とS2

追加PnL取得前に事前登録を凍結し、事前登録文書SHA-256は`95f58c2478777fcc13d3b0e505d056128ae5a8c8de573eb9ed5fdaa247b5c4d1`である。入力Development Parquetの全partition SHA-256、設定、実装、テスト、文書のsnapshotをartifactに保存した。

予定軸1,131 trade_dateについて、09:00--11:29の150本からH/L/AとWを構成し、current-excluded直前60有効Wのnearest-rank q30/q70だけで状態を作成した。S2はC状態325日（必要280）、C breakout実行147件（必要80）、long/short=73/74（必要各25）、年別=2021:17、2022:32、2023:34、2024:45、2025H1:19（必要10/10/10/10/5）、説明不能除外0件でPASSした。

PnL前の因果性監査は、trade_date軸一意性、150本H/L/A、参照集合の厳密な過去限定・current除外、状態での60有効参照、12:30--13:29 strict close breakout後のnext-eligible open、14:30 exitを全件PASSした。実行・会計監査も、1日1取引、同event/同entry/exitのfade逆side、C/M非重複、Stop/Targetなし、`net=gross-fees`をすべてPASSした。既約定後の未知exitは0件だった。

## 主結果（片道1 tick + 30円）

C-breakout continuationは147取引、Net **-151,820円**、PF **0.7374**、期待値 **-1,032.79円/取引**、最大実現DD **204,680円**だった。20 trade_date非循環MBB（10,000回、seed=20260915）の予定日次平均Netは-134.24円、95% CI **[-323.57, +40.45]**円/trade_dateである。

同一C breakout event・同一entry/exitのfadeはNet -159,820円、PF 0.7235だった。C-breakout--C-fade対応日次差は+7.07円、95% CI **[-354.58, +355.46]**円/trade_dateである。M日の同一breakout規則は200取引、Net -28,000円、PF 0.9627で、C--M群別予定日次平均Net差は-109.48円、95% CI **[-438.04, +215.76]**円/trade_dateだった。

したがって、Net>0、PF>1、主MBB下限>0、C--fade下限>0、C--M下限>0の主ANDは全5条件で不成立である。

## 固定感度・記述保存

固定感度のNetはq20=+13,380円、q40=-160,400円、参照40=-118,620円、参照80=-120,000円、entry追加1本=-176,820円、exit14:15=-146,320円、exit14:45=-150,320円、片道2 tick=-298,820円、片道3 tick=-445,820円、手数料2倍=-160,640円だった。従って全固定感度Net>0は不成立である。q20の単独正値は、事前固定した感度の記録であり救済選択には使用しない。

long/short別Netは-75,380/-76,440円。年別Netは2021=+45,480円、2022=-46,420円、2023=-43,040円、2024=-64,700円、2025H1=-43,140円で、2021--2024の正年は1年である。breakout時刻帯は12:30--12:49=-153,100円、12:50--13:09=+26,300円、13:10--13:29=-25,020円。C-breakout内W順位五分位は順に-14,300、+27,260、-49,300、-60,240、-55,240円だった。これらの部分群は選別・救済に使わない。top-10勝ち取引除外後Netは**-350,720円**である。

## 判定と次工程

**REJECT**。主AND不成立のため、この結果に基づく閾値・確認時刻・exit・方向の追加探索、Walk Forward、OOS、Final Holdoutには進まない。OOS、Walk Forward、Final Holdoutはすべて`NOT_ACCESSED`である。

完全な再現artifactは[results/research/r084-q001-20260915-morning-compression-lunch-breakout-01](../../results/research/r084-q001-20260915-morning-compression-lunch-breakout-01)に保存した。`preregistration.json`、`s2_feasibility.json`、`causality_s2_audit.json`、`execution_accounting_audit.json`、`profiles.json`、`bootstrap.json`、全event/order/fill/trade/daily-axis ledger、共通bootstrap index、source/config/document snapshotを含む。
