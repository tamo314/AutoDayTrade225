# R074-Q002 実行結果

run_id: `r074-q002-20260915-low-night-range-opening-breakout-01`  
実行日: 2026-09-15 JST  
状態: **REJECT**

事前登録 [34_r074_q002_low_night_range_opening_breakout.md](34_r074_q002_low_night_range_opening_breakout.md) を価格成績の読取り前に、source/config/documentation snapshot、input partition hash、seed `20260915`とともに不変保存した。R074-Q001との差分は年別S2 gateだけである。pytest 17件、Ruff、mypyは全てPASSした。Development正規化Parquetだけを読み、R004固定whole-session隔離（45 session、27,345 bar、一覧hash `2974bec…3213`）のexpected matchを再現した。OOS、Walk Forward、Final Holdoutは物理I/Oを含め未アクセスである。

## S2: PnL前の可用性

説明不能除外は0件、A executable tradeは172件、long/shortは82/90件、D executable tradeは300件であり、固定の全期間gateを通過した。年別の名目25%状態適格性は下表のとおりである。これは収益検定ではなく、利用可能な20-reference母数に対してA状態が病的に欠落していないことの確認だけである。

|年|20-reference N|A発生|Binomial(N, 0.25)下側5%分位|A後実行可能|実行可能率|判定|
|---|---:|---:|---:|---:|---:|---|
|2021|63|14|10|12|85.71%|通過|
|2022|193|43|39|34|79.07%|通過|
|2023|205|64|41|56|87.50%|通過|
|2024|202|50|41|45|90.00%|通過|

分位は事前登録どおり`min{k: Pr[Binomial(N,0.25)<=k] >= 0.05}`である。2021年の成績は下記の記述的内訳に保存したが、単独の有意性主張には使用しない。S2通過後にfilled後exit unknownはなく、指定されたS3実行へ進んだ。

## Development一次評価（片道1 tick＋片道30円）

|条件|取引|Net円|PF|
|---|---:|---:|---:|
|A 主戦略|172|-425,820|0.669|
|A_reverse|172|+61,180|—|
|D 中位nightレンジ対照|300|+130,000|—|

Aの期待値は-2,475.70円/取引、最大realized DDは473,400円である。予定軸1,111 trade_dateの20 trade_date non-wrapping MBB（10,000回、seed `20260915`、tail truncation、linear percentile）の95% CIは、予定軸平均Net `[-794.40, -55.12]`円/trade_date、A−A_reverse `[-1,259.06, +206.94]`円/trade_date、A−D取引当たりNet差 `[-6,276.32, -168.01]`円である。Net>0、PF>1、三つのCI下限>0の主ANDはすべて不通過である。

年別A Netは2021=-63,220円、2022=-137,040円、2023=+2,640円、2024=-172,700円、2025前半=-55,500円。方向別はlong=-189,920円、short=-235,900円である。top-10 winning trades除外後Net=-711,720円であり、利益集中度のCandidate条件も不通過だった。

登録済み全10感度のNetはq20=-352,100円、q33=-437,020円、opening20=-395,240円、opening45=-307,300円、1分delay=-399,320円、exit14:15=-431,820円、exit14:45=-407,820円、片道2 tick=-597,820円、片道3 tick=-769,820円、手数料2倍=-436,140円である。

execution/accounting auditは、日付当たり1取引、主・逆方向の同時点・反対side、day内完結、Stop/Targetなし、`Net=gross-fees`の全項目をPASSした。主成績、PF、予定軸CI、逆方向差CI、状態差CIが固定基準を満たさないため、**R074-Q002=REJECT** とする。救済的な探索・仕様調整、Walk Forward、OOS、Final Holdoutは実施しない。

完全な再現可能成果物は `results/research/r074-q002-20260915-low-night-range-opening-breakout-01/` に保存した。
