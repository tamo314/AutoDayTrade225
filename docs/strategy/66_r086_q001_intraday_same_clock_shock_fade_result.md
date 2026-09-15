# TASK-R086-Q001 結果: 日中・同時刻5分shock短期反転

実行ID: `r086-q001-20260915-intraday-same-clock-shock-fade-01`  
判定: **REJECT**  
対象: Developmentのみ（2021-01-01～2025-06-30、1,131 trade_date）

事前登録は[65_r086_q001_intraday_same_clock_shock_fade.md](65_r086_q001_intraday_same_clock_shock_fade.md)、完全な不変成果物は`results/research/r086-q001-20260915-intraday-same-clock-shock-fade-01/`に保存した。OOS、Walk Forward、2026年以降のFinal Holdoutはアクセスしていない。

## 監査とS2

DAY連続区間の制度版、開始30分後から終端30分前までの固定非重複block、同一interval/開始offset参照、current-excludedの直前60有効date nearest-rank q90、各日の最初のshockのみ、next eligible open entry、同一区間内のentry+20分exitをPnL前に監査した。S2はexecutable event **964**（X正468、負496、年別2021--2025H1=151/226/240/233/114）、説明不能除外0で全通過した。

実行・会計監査も、1日1取引、shock-fade/continuationの同event・同entry/exit・反対side、pre-shock-sign controlのcommon event/entry/exit、Stop/Targetなし、`Net=Gross-fees`を全てPASSした。合成pytest 14件、Ruff、mypy、runner compileもPASSである。

## 主結果

片道1 tick+30円のshock-fadeは964取引、Net **-1,289,340円**、PF **0.604**、期待値 **-1,337.49円/取引**、最大実現DD **1,305,180円**だった。20 trade_date non-wrapping MBB（10,000回、seed 20260915）の95% CIは以下の通りである。

|推定量|推定値（円/日）|95% CI（円/日）|
|---|---:|---:|
|shock-fade予定日次Net|-1,140.00|[-1,640.63, -661.49]|
|shock-fade − continuation|-473.03|[-1,444.78, 490.74]|
|shock-fade − pre-shock-sign control（779 common event）|16.69|[-591.78, 706.03]|

従って主ANDのNet>0、PF>1、主CI下限>0、continuation差CI下限>0、pre-shock control差CI下限>0は**全て不成立**である。

## 固定感度と記述診断

全固定感度はNet負だった。q80/q95=-1,543,660/-1,122,060円、lookback40/80=-1,355,120/-1,280,140円、shock 3/10分=-1,669,960/-1,361,660円、追加1本delay=-1,278,340円、hold 10/30分=-1,095,840/-1,262,340円、2/3 tick=-2,253,340/-3,217,340円、手数料2倍=-1,347,180円である。

X正/負別は-671,580/-617,760円、午前/午後別は-1,218,540/-70,800円、年別2021--2025H1は-210,560/-360,560/-202,900/-297,480/-217,840円である。top-10勝ち取引除外後Netは-1,556,240円だった。これらは救済選択には使わない記述保存である。

情報量は十分だったが、主ANDと全固定感度・符号・時間帯・年別・集中度の必要条件が不成立であるため、**R086-Q001=REJECT**とする。結果依存の閾値、時刻帯、保有時間、方向の探索、Walk Forward、OOS、Final Holdoutには進まない。
