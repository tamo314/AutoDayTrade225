# R074-Q001 実行結果

run_id: `r074-q001-20260915-low-night-range-opening-breakout-01`  
実行日: 2026-09-15 JST  
状態: **INCONCLUSIVE — S2_GATE_FAILED**

事前登録 [31_r074_q001_low_night_range_opening_breakout.md](31_r074_q001_low_night_range_opening_breakout.md) と、source/config/documentation snapshot、input hash、seed `20260915`を価格成績の読取り前に保存した。Developmentの正規化Parquetだけを読み、R004固定whole-session隔離（45 session、27,345 bar、一覧hash `2974bec…3213`）を一致再現した。OOSとFinal Holdoutは物理I/Oを含め未アクセスである。

|S2項目|結果|閾値|判定|
|---|---:|---:|---|
|圧縮A outcome-observable trade|172|150以上|通過|
|A: 2021|12|25以上|不通過|
|A: 2022|34|25以上|通過|
|A: 2023|56|25以上|通過|
|A: 2024|45|25以上|通過|
|A long|82|45以上|通過|
|A short|90|45以上|通過|
|中位D outcome-observable trade|300|300以上|通過|
|説明不能除外|0|0|通過|

2021年の圧縮Aが13件不足するため、固定したS2 AND gateは不通過である。これは年別標本量の不足であり、取引成績による判定ではない。原因別statusはAで、予定night windowなし207、current night R004隔離4、Development外reference 26、reference night R004隔離132、状態外562、breakoutなし28、実行可能172だった。参照nightをより古い有効nightで補充していない。

仕様どおり、PnL、return、勝敗、orders/fills/trades、PF、MBB、逆方向対照、状態対照差、感度、Candidate判定は計算・表示していない。固定exit・next-eligible-open・side reverse・会計を含む合成pytest 15件、Ruff、mypyは実行前にPASSした。再現可能な事前登録、S2 event ledger、access ledger、validation、decisionは `results/research/r074-q001-20260915-low-night-range-opening-breakout-01/` に不変保存した。事前登録外の標本閾値・lookback・時刻・状態定義の救済、WFA、OOS、Final Holdoutは実施しない。
