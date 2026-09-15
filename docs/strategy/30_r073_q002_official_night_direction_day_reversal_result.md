# R073-Q002 実行結果

run_id: `r073-q002-20260915-official-night-direction-day-reversal-01`  
実行日: 2026-09-15 JST  
状態: **REJECT — Development一次評価**

TASK-R073-D001に基づく別IDの事前登録後、Development（trade_date 2021-01-01--2025-06-30）だけを一度実行した。Q001から変更したのはS2標本ゲートだけであり、仮説、公式night開始（旧16:30／新17:00）から05:30の方向に対する09:00反転、14:30退出、day内1枚・最大1建玉、主無取引対照、同日時の`night_direction_momentum` co-primary、基本費用（片道1 tick＋30円）、感度、bootstrap、判定は不変である。OOS（2025H2）とFinal Holdoutは未アクセスである。

## S2

カレンダー適格886日、同値8日、同値以外の実行可能878日（下限842日）、説明不能なデータ・`trade_date`・実装除外0日で、S2は通過した。実行可能件数は2021--2024で186/191/202/201、反転のbuy/sellは396/482であり、既存の年別・方向別下限も全て満たした。

## 基本費用での主結果

|条件|取引|Net円|期待値 円/取引|PF|最大実現DD円|
|---|---:|---:|---:|---:|---:|
|反転（主仮説）|878|-1,035,580|-1,179.48|0.878|1,786,040|
|night-direction momentum|878|-825,780|-940.52|0.899|1,375,840|

主反転の年別Netは2021--2025で-462,560 / -770,960 / -380,120 / +283,940 / +294,120円、方向別Netはlong=-321,160円（396件）、short=-714,420円（482件）だった。利益集中度はtop-5/top-10 gross-wins shareが7.25%/11.25%、top-10利益取除後Net=-1,875,980円、正月比率は44.44%である。

seed `20260915`、20 trade_date non-wrapping MBB、10,000回、tail truncation、linear percentileの95%CIは、主日次平均が`[-2,229.78, +291.28]`円、対応日次差（reversal−momentum）が`[-2,812.94, +2,230.11]`円だった。従って主Net>0、PF>1、主平均CI下限>0、対応差CI下限>0の全てが未達である。

事前登録済み感度も、night-end 05:00=-892,400円、entry 09:01=-885,080円、exit 14:15=-1,122,680円、exit 14:45=-990,580円、2 tick=-1,913,580円、3 tick=-2,791,580円、fee×2=-1,088,260円で、いずれもNet正ではない。従って固定判定により **R073-Q002=REJECT**。パラメータ探索、方向反転、仕様調整、WFA、OOS、Final Holdoutは実施しない。

pytest 15件、Ruff、mypyは実行前にPASSした。execution/accounting監査では、1 trade/trade_date、主・momentumの同日同entry/exitかつ逆side、day内完結、Stop/Targetなし、`Net=gross-fees`をすべてPASSした。再現可能な事前登録、config/source/doc snapshot、S2、全trade/daily axis、年別・方向別・集中度、bootstrap、監査、判定は`results/research/r073-q002-20260915-official-night-direction-day-reversal-01/`に不変保存した。
