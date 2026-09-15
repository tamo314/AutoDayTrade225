# TASK-R089-Q001 結果: 現物寄付き後60分の終盤集中と day 後半継続

実行日: **2026-09-15 JST**  
最終run: `r089-q001-20260915-late-concentration-continuation-04`  
判定: **REJECT**

## 実行範囲と事前監査

事前登録は[75_r089_q001_late_concentration_continuation.md](75_r089_q001_late_concentration_continuation.md)に凍結し、Development（trade_date 2021-01-01--2025-06-30）のみを読んだ。OOS、Walk Forward、Final Holdoutは未アクセスである。

R004 DAY隔離20日、D=0の21日、reference有効数不足169日を含む予定軸で、PnL前監査はすべてPASSした。時刻/F算式、strict-prior 160予定日・140有効日、current M帯に再分類した過去Fだけのq30/q70、next eligible open、14:55固定exit、欠損・隔離の各検査に先読みはない。

LC/ECは114/115件、LCの上昇/下落は59/55件、B1はLC/EC=53/57、B2=61/58だった。LCは2022--2025H1で28/36/32/16件であり、全件数ゲートを通過した。帯内M共通支持もB1でLC/EC=52/54、B2で60/57が重複区間内にありPASSした。したがって指定済みPnL評価だけを実行した。

## 主結果

LC continuationは114取引、Net **+134,660円**、PF **1.156**、平均 **+1,181円/取引**、最大実現DD **138,680円**だった。paired fadeは同一event・同一entry/exitでNet **−376,340円**、EC continuationは115取引でNet **−232,900円**だった。

しかし、20 trade_date moving-block bootstrap（10,000回、seed=20260915）の95%CIは全て下限が負だった。

| estimand | 点推定 | 95% CI |
|---|---:|---:|
| LC 日次Net（円/trade_date） | +119.06 | [−335.85, +806.11] |
| LC − paired fade（円/trade_date） | +451.81 | [−460.68, +1,837.38] |
| LC − EC、LC M帯標準化（円/trade） | +3,048.21 | [−1,985.93, +8,876.69] |

従って主ANDはNet>0とPF>1だけ成立し、3つのCI下限条件が不成立である。登録済み規則により **REJECT** とした。

## 事前固定の頑健性・記述診断

固定感度ではrecent-20が**−256,960円**、3 tickが**−93,340円**であり、「全感度Net>0」は不成立である。上昇LCは−146,040円、B1は−51,680円、2025H1は−94,460円で、方向・帯・期間診断も一貫しない。top-10勝ち取引除外後Netは**−509,740円**（top-10 gross wins share 64.54%）だった。これらは部分群救済には使用していない。

反対に30/90分窓、M q50/q70、F q60/q80、120/200日参照、追加1本遅延、14:30/15:10 exit、2 tick、手数料2倍の各一点感度のNetは正だったが、REJECTを覆さない。結果依存の窓・閾値・exit変更、Walk Forward、OOS、Final Holdoutは行わない。

## 再現性と技術履歴

最終成果物には事前登録、設定・実装・入力hash、S2台帳、orders/fills/trades、共通日次軸、bootstrap index、監査、決定を保存した: `results/research/r089-q001-20260915-late-concentration-continuation-04/`。実行会計監査は1日1取引、paired fadeの同一時刻反対side、Stop/Targetなし、`net=gross-fees`を全てPASSした。

`...-01`はDevelopment読込み前のimport不足、`...-02`と`...-03`はPnL前の静的検査不備で停止した不変技術記録である。いずれも価格・PnLにはアクセスせず、仮説・パラメータ・評価規則を変えずに最終runへ修正を引き継いだ。
