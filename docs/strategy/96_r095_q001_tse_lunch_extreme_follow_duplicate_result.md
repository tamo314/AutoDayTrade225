# TASK-R095-Q001 結果: 重複のためPnL前停止

実行ID: `task-r095-q001-tse-lunch-extreme-follow-duplicate-20260915-01`  
状態: **DUPLICATE_STOPPED / NO_NEW_PNL_ACCESS**

R001--R094との事前照合で、R038-Q001が同じ11:30--12:29昼休み先物変位、rolling q75極端値、同方向、12:30 open entry、13:00 open exit、1枚・片道1 tick+30円をすでに評価していることを確認した。R095の120予定日・100有効件・等号包含は、同じ機序と売買経路の閾値履歴上の変更であり、統治規約に照らしてmaterially equivalentである。

したがって、R095ではDevelopment価格、event件数、orders/fills/trades、return、PnL、PF、bootstrap、感度を一切新規に取得・算出していない。OOS、Walk Forward、Final Holdoutも未アクセスである。再現用の停止記録は[results/research/task-r095-q001-tse-lunch-extreme-follow-duplicate-20260915-01](../../results/research/task-r095-q001-tse-lunch-extreme-follow-duplicate-20260915-01)に保存した。

これは収益性のREJECTではなく、重複実験を禁止するための停止である。依頼により禁止された結果依存の閾値・方向・entry/exit・昼休み窓の変更、追加探索、Walk Forward、2025H2 OOS、Final Holdoutは実施していない。
