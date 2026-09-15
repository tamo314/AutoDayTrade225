# TASK-R090-Q002 結果: 現物昼休み変位の再開否定後fade

実行日: **2026-09-15 JST**  
最終run: `r090-q002-20260915-lunch-rejection-fade-gate-revision-01`  
判定: **REJECT（Development限定）**

Q001ではPnL・PF・bootstrap・orders/fills/trades・感度成績を取得していなかった。Q002は、事前に記録した2025H1 LR=7というPnL-freeラベル件数だけを既知情報として、同部分期のLR最低件数を8から6（各月平均1件）へ一回だけ改訂した。他の仮説、価格定義、時刻、strict-prior rolling分位、M帯、LR/LA/PR、entry/exit、placebo、費用、対照、bootstrap、感度、判定規則はQ001から変更していない。

改訂S2はPASSした。LR/LA/PR=106/109/134、LR上昇/下落=61/45、B1 LR/LA=55/55、B2=51/54、2022/2023/2024/2025H1のLR=25/35/36/7、説明不能除外0件である。因果性監査（時刻/J、strict-prior独立placebo参照、翌適格entry・固定exit）とB1/B2のM共通支持もPASSした。事前検証はpytest 14件、Ruff、mypy、py_compileのすべてPASSし、post-executionでは最大1取引/trade_date、paired continuationの同一event/entry/exitかつ反対side、主/Placeboの時刻、Stop/Target不使用、`Net=Gross-fees`をすべて確認した。

基本費用（片道1 tick+30円）でLR fadeは106取引、Net **−209,860円**、PF **0.686**だった。20 trade_date non-wrapping MBB（10,000回、seed=20260915）の95% CIは、LR日次Net `[−438.58, +44.37]`円、LR−paired continuation `[−660.50, +287.36]`円、M帯標準化LR−LA `[−2,938.55, +8,658.95]`円/取引、M帯標準化LR−PR `[−6,718.77, +700.33]`円/取引である。したがってNet>0、PF>1、および全CI下限>0からなる主ANDのすべてが不成立であり、登録済み規則どおり**REJECT**とする。

補助的にも、全14固定感度のNetはすべて負（−87,500円から−428,600円）、上昇/下落は−50,660/−159,200円、B1/B2は−14,300/−195,560円、2022--2025H1の年Netは+22,500/−22,600/−106,660/−42,920円、top-10勝ち取引除外後Netは−471,760円だった。これらを救済選択には使わない。

不変成果物は`results/research/r090-q002-20260915-lunch-rejection-fade-gate-revision-01/`に保存した。OOS、Walk Forward、2026年以降のFinal Holdoutは**NOT_ACCESSED**であり、結果依存の仕様変更は行わない。
