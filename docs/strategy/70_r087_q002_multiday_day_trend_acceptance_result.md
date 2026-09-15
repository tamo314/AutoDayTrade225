# TASK-R087-Q002 結果: 複数DAY方向累積×寄付き15分acceptance継続

実行ID: `r087-q002-20260915-multiday-day-trend-acceptance-01`  
判定: **REJECT**  
対象: Developmentのみ。状態構築は2021-01-01～2025-06-30、PnL評価軸は固定済みの2022-02-10～2025-06-30（870 scheduled trade_date）。

事前登録は[69_r087_q002_multiday_day_trend_acceptance.md](69_r087_q002_multiday_day_trend_acceptance.md)、不変成果物は`results/research/r087-q002-20260915-multiday-day-trend-acceptance-01/`に保存した。OOS、Walk Forward、2026-01-01以降のFinal Holdoutにはアクセスしていない。

## PnL前監査とS2

PnL、return、勝敗、PF、順位、bootstrap、感度、対照成績を取得しない監査で、利用可能データの開始は2020-12-30 16:30 JST（trade_date=2021-01-04）、最初の有効`r`は2021-01-04、最初の有効`T`は2021-01-19だった。q30/q70は、current-excludedの直前120予定日内で100本目の有効Tが揃った**2022-02-10**に初めて因果的に確定した（q30=132.1021 bps、q70=275.0650 bps）。この日を評価開始として固定した。

2021年EA=0の原因は、100有効Tを要求し古い有効値で補充しない因果的ウォームアップである。全2021日でq30/q70は未確定で、事前定義済みR004 DAY隔離20日以外の不明な`r`欠測はなく、説明不能除外も0件だった。trade_date、O/C、直前10予定日非補充、current-excluded reference、09:14 signal、next eligible entry、14:55 exitの因果性6チェックは全てPASSした。

改訂S2は、EA/EO/MA=129/126/147、EA T正/負=75/54、EA年別2022/2023/2024/2025H1=26/44/49/10、説明不能除外0で全通過した。この改訂は事前に知っていた上記ラベル別件数だけに基づき、PnL成績には基づかない。

## 主結果

基本費用（片道1 tick＋30円）でEA continuationは129取引、Net **−191,740円**、PF **0.867**、期待値**−1,486.36円/取引**、最大実現DD **480,200円**だった。20 trade_date non-wrapping MBB 10,000回（seed=20260915）の95% CIは、EA日次平均 `[-957.66, +631.12]`円、EA−paired fade日次差 `[-1,604.68, +1,596.51]`円、EA−EO取引当たり差 `[-13,374.38, +40.70]`円、EA−MA取引当たり差 `[-7,246.16, +5,120.39]`円だった。

従って、Net>0、PF>1、EA日次平均CI下限>0、paired fade差CI下限>0、EA−EO差CI下限>0、EA−MA差CI下限>0からなる主ANDは**全6条件不成立**である。主AND不成立の規約により **R087-Q002=REJECT** とする。

## 固定感度・補助保存

固定感度Netは、累積5/20日=−496,240/−100,140円、q60/q80=−197,020/+5,540円、reference 80/160=−137,060/−139,060円、confirmation 5/30分=−134,180/−10,940円、entry追加1本=−221,240円、exit 14:30/15:10=−174,240/−147,740円、2/3 tick=−320,740/−449,740円、手数料2倍=−199,480円だった。T正/負は+73,000/−264,740円、2022/2023/2024/2025H1は+92,940/−245,640/+6,060/−45,100円、top-10勝ち取引除外後Netは−849,640円である。

注文・fill・会計監査は、1日1取引、paired fadeの同一event/entry/exit・反対side、Stop/Targetなし、`Net=Gross−fees`をすべてPASSした。主ANDが不成立のため、結果依存の仕様変更、救済探索、Walk Forward、OOS、Final Holdoutには進まない。
