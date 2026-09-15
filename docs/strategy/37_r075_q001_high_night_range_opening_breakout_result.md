# R075-Q001 実行結果

run_id: `r075-q001-20260915-high-night-range-opening-breakout-01`  
実行日: 2026-09-15 JST  
状態: **INCONCLUSIVE（S2_GATE_FAILED、価格成績未取得）**

事前登録 [36_r075_q001_high_night_range_opening_breakout.md](36_r075_q001_high_night_range_opening_breakout.md) を価格成績の読取り前に、source/config/documentation snapshot、input partition hash、seed `20260915`とともに不変保存した。Development正規化Parquetだけを読み、R004固定whole-session隔離（45 session、27,345 bar、一覧hash `2974bec…3213`）のexpected matchを再現した。pytest 19件、Ruff、mypyはすべてPASSした。OOS、Walk Forward、Final Holdoutは物理I/Oを含め未アクセスである。

## S2: PnL前の可用性

説明不能除外は0件、H executable tradeは205件、long/shortは106/99件で、H側の全期間gateを通過した。2021--2024の名目25%状態適格性もすべて通過した。

|年|20-reference N|H発生|Binomial(N, 0.25)下側5%分位|H後実行可能|実行可能率|判定|
|---|---:|---:|---:|---:|---:|---|
|2021|63|21|10|18|85.71%|通過|
|2022|193|55|39|53|96.36%|通過|
|2023|205|64|41|55|85.94%|通過|
|2024|202|64|41|57|89.06%|通過|

ただし状態対照Mのoutcome-observable executable tradeは**294件**で、固定下限300件に6件不足した。S2はANDであるため、主条件は不通過である。二項分位は`min{k: Pr[Binomial(N,0.25)<=k]>=0.05}`であり、収益検定・状態優位性検定ではない。

従って凍結規則どおり、PnL、PF、bootstrap、orders/fills/trades、年別／方向別成績、最大DD、利益集中度、10感度、H_reverse、M比較、L診断対照を作成していない。これは高night仮説のREJECTではなく、指定済み状態対照の標本要件を満たせないため、当該仕様では価格評価に進めないという**INCONCLUSIVE**である。

R069の無条件breakout、R074の低night条件、R067とは別の高状態に関する統制仮説として登録したが、Developmentは反復利用済みであり独立確認ではない。救済的な閾値・時刻・状態境界の変更、Walk Forward、OOS、Final Holdoutは行わない。

完全な再現可能成果物は `results/research/r075-q001-20260915-high-night-range-opening-breakout-01/` に保存した。
