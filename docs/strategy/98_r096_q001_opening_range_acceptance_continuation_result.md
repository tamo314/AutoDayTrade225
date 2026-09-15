# TASK-R096-Q001 結果: 現物開始60分の方向側レンジ端受容と day 継続

実行日: **2026-09-15 JST**  
最終run: `r096-q001-20260915-opening-range-acceptance-continuation-01`  
判定: **INCONCLUSIVE — PnL前停止**

## 事前登録と重複照合

PnLアクセス前に R001--R095 を照合し、特にR022/R042/R056/R079/R080/R088/R089と比較した。R079/R080は同じ60分方向だが`abs(D)`だけ、R088は経路効率、R089は直近15分集中であり、いずれも本件の方向側終値位置`z`、exact prior-120のx/z閾値、版管理T-5 exitの組合せとmaterially equivalentではない。本件はR088/R089後の事後追加仮説であり、連続系列の反復利用済みDevelopmentに限定した。OOS、Walk Forward、Final Holdoutは未アクセスである。

## PnL-free 実行結果

予定軸1,131日で、R004 DAY隔離20日、zero displacement 21日、exact-prior有効数不足100日を記録した。q-ready nonzero日は990件で、必要800件を満たした。60本の09:00--09:59 OHLC、`H/L/z`、current-excluded 120予定日・補充なし、signal後next eligible open（最大10分）、旧/新TSE終了時刻からのT-5 exit、future entry/exit可用性を使わないE/K選別の監査はすべてPASSした。説明不能除外とentry後exit未完了はいずれも0件だった。

しかし、Eは197件に対してKは**49件**であり、各100件以上の要件を満たさなかった。x帯別には`[0.50,0.75)`がE/K=79/37、`[0.75,1.00]`がE/K=118/**12**で、後者のKが必要20件を満たさなかった。他のE標本条件は通過した（D上昇/下落=100/97、年別2021--2025H1=16/51/52/50/28、旧/新TSE制度=157/40）。

固定されたS2 ANDゲートが不成立のため、return、PnL、PF、orders/fills/trades、bootstrap、感度、execution/economics監査は生成していない。閾値・帯・時刻を救済的に変更せず、判定を **INCONCLUSIVE** として終了した。

事前登録、入力・設定・実装snapshot/hash、PnL-free event ledger、S2台帳、因果性監査、アクセス台帳、検証出力、決定は `results/research/r096-q001-20260915-opening-range-acceptance-continuation-01/` に保存した。pytest（12 passed）、Ruff、mypy、構文検査はすべてexit code 0だった。
