# R075-Q002 実行結果

run_id: `r075-q002-20260915-high-night-range-opening-breakout-01`  
実行日: 2026-09-15 JST  
状態: **REJECT（Development一回限り）**

事前登録 [38_r075_q002_high_night_range_opening_breakout.md](38_r075_q002_high_night_range_opening_breakout.md) を価格成績の読取り前に、source/config/documentation snapshot、input partition hash、seed `20260915`とともに不変保存した。R075-Q001からの唯一の変更はS2のM最低件数を300から250へ下げたことである。Q001のPnL-free S2でM=294を確認後、300には事前の検出力根拠がなかったため、この変更をPnL-blindな設計修正として再登録した。Development正規化Parquetだけを読み、R004固定whole-session隔離（45 session、27,345 bar、一覧hash `2974bec…3213`）のexpected matchを再現した。OOS、Walk Forward、Final Holdoutは物理I/Oを含め未アクセスである。

## S2: PnL前の可用性

M executable=294はQ002の250件下限を満たした。H executable=205（long/short=106/99）、説明不能除外=0であり、2021--2024の名目25%状態適格性も全て通過した。

|年|20-reference N|H発生|下側5%分位|H後実行可能|実行可能率|
|---|---:|---:|---:|---:|---:|
|2021|63|21|10|18|85.71%|
|2022|193|55|39|53|96.36%|
|2023|205|64|41|55|85.94%|
|2024|202|64|41|57|89.06%|

二項分位は`min{k: Pr[Binomial(N,0.25)<=k]>=0.05}`であり、収益検定・状態優位性の検定ではない。

## Development結果（片道1 tick＋30円）

Hは205取引、Net **-16,300円**、PF **0.990**、期待値-79.51円/取引、最大実現DD 290,380円だった。Mは294取引、Net +120,360円、PF 1.062だった。H reverseは同一205 eventでNet -418,300円、PF 0.783である。

固定20 trade_date非循環MBB 10,000回（seed `20260915`、共通1,131日index、tail truncation、linear percentile）の95% CIは次のとおりである。

|estimand|推定値|95% CI|
|---|---:|---:|
|H予定軸日次平均Net| -14.41円|[-535.85, +517.64]円|
|H − H_reverse予定軸日次差|+355.44円|[-687.02, +1,440.32]円|
|H − M取引当たりNet差|-488.90円|[-4,452.19, +3,310.63]円|

従って主ANDのH Net>0、PF>1、三つのCI下限>0は全て不成立である。判定は **REJECT** である。

## 既定感度・診断

|profile|取引|Net円|PF|
|---|---:|---:|---:|
|q67|227|+18,380|1.010|
|q80|176|+81,440|1.056|
|opening 20分|217|+42,480|1.023|
|opening 45分|180|-93,800|0.936|
|entry 1分遅延|205|+13,200|1.008|
|exit 14:15|205|-73,300|0.957|
|exit 14:45|205|+60,200|1.035|
|片道2 tick|205|-221,300|0.878|
|片道3 tick|205|-426,300|0.779|
|手数料2倍|205|-28,600|0.983|

Hの年別Netは2021..2025H1で+35,420 / -149,680 / +48,200 / +215,080 / -165,320円、方向別Netはlong +107,640円、short -123,940円だった。top-10 winning trades除外後Netは-579,200円である。最大DD、年別・方向別、利益集中度は成果物の`profiles.json`に保存した。

`execution_accounting_audit.json`で、1 trade/trade_date、H/H_reverseの同日entry・exit一致と反対side、日内完結、Stop/Targetなし、`Net=gross-fees`をすべてPASSした。pytest 20件、Ruff、mypyもPASSした。

主ANDが不成立のため、Development再利用とゲート修正時の上限`INVESTIGATE`は適用されず、R075-Q002はREJECTで終了する。救済探索、Walk Forward、OOS、2026年以降のFinal Holdoutには進まない。

完全な再現可能成果物は`results/research/r075-q002-20260915-high-night-range-opening-breakout-01/`に保存した。
