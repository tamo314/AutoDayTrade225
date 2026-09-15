# R076-Q001 実行結果

run_id: `r076-q001-20260915-tse-opening-failed-auction-01`  
実行日: 2026-09-15 JST  
状態: **REJECT（Development一回限り）**

事前登録 [40_r076_q001_tse_opening_failed_auction.md](40_r076_q001_tse_opening_failed_auction.md) を価格成績の読取り前に、実効runner・event/strategy helper・tests・config・input partition hash・seed `20260915`とともに不変保存した。主条件はナイトレンジ・ナイト方向で選別しない。Development正規化Parquetだけを読み、R004固定whole-session隔離（45 session、27,345 bar、一覧hash `2974bec…3213`）の一致を再現した。OOS、Walk Forward、Final Holdoutは物理I/Oを含め未アクセスである。

## S2: PnL前の可用性

主failed eventは688件、initial long/shortは342/346件、説明不能除外は0件で、全ての固定下限を通過した。

|年|実行可能failed event|
|---|---:|
|2021|119|
|2022|160|
|2023|170|
|2024|165|
|2025 H1|74|

従って200件以上、両direction各60件以上、2021--2024各25件以上、2025H1 12件以上を満たし、S3へ一度だけ進んだ。

## Development結果（片道1 tick＋片道30円）

確認後の逆張り主戦略は688取引、Net **-1,445,780円**、PF **0.736**、期待値 **-2,101.42円/取引**、最大実現DD **1,600,200円**だった。同一failed event・同一confirmed entry/exitで最初の突破方向を取引する`confirmed_continuation`はNet -12,780円、確認なしで突破直後から逆張りする`unconfirmed_fade`はNet +157,720円だった。

固定20 trade_date非循環MBB 10,000回（seed `20260915`、共通1,131日index、tail truncation、linear percentile）の95% CIは以下である。

|estimand|推定値|95% CI|
|---|---:|---:|
|主scheduled-axis日次平均Net| -1,278.32円|[-2,178.82, -438.84]円|
|主 − confirmed-continuation対応日次差| -1,267.02円|[-3,045.18, +405.84]円|
|主 − unconfirmed-fade対応日次差| -1,417.77円|[-1,557.04, -1,282.49]円|

主Net>0、PF>1、主平均CI下限>0、両対照との差CI下限>0はいずれも不成立であり、主ANDは失敗した。したがって **R076-Q001=REJECT** である。

## 感度・構成・診断

11の事前指定感度のNetは、復帰15/45本=-1,229,480/-1,541,900円、opening range20/45分=-1,210,960/-1,528,260円、internal close 2本=-1,419,440円、entry 1分遅延=-1,427,780円、exit14:15/14:45=-1,369,280/-1,456,280円、片道2/3 tick=-2,133,780/-2,821,780円、手数料2倍=-1,487,060円で、全感度正Net条件も不成立だった。

initial上方/下方突破別Netは-713,040/-774,020円、年別Netは2021..2025H1で-62,780/-295,200/-584,900/-601,300/+57,120円、top-10勝ち取引除外後Netは-2,053,860円だった。R075互換q25/M/q75ナイト状態の診断集計はlow/middle/high=129/210/150件、Net=-178,740/-600,100/-349,500円である。これは主failed event、CI、判定へ使っていない。

`execution_accounting_audit.json`で、1 trade/trade_date、主/継続対照の同一event・entry・exitと反対side、確認なし対照の同一event日・同一exit、確認時点のprefix制約、日内完結、Stop/Targetなし、`Net=gross-fees`をすべてPASSした。pytest 13件、Ruff、mypyもPASSした。

主AND不成立のため、救済探索、Walk Forward、OOS、2026年以降のFinal Holdoutには進まない。完全な再現可能成果物は`results/research/r076-q001-20260915-tse-opening-failed-auction-01/`に保存した。
