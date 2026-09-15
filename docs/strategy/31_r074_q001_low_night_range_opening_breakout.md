# R074-Q001: 低ボラティリティ公式ナイト後のTSE寄付きレンジ突破継続

タスクID: `TASK-R074-Q001`  
family_id: `night_range_state_opening_breakout` / study_id: `R074-Q001` / spec_version: `v1`  
run_id: `r074-q001-20260915-low-night-range-opening-breakout-01` / protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

## 仮説、探索履歴、非重複

仮説は一つだけである。公式night session全体が低ボラティリティで終わった後は、TSE 09:00寄付きレンジを最初にstrict closeで突破する方向の価格発見が日中後半（14:30 exit）まで継続する。

`prior_information_seen=true`。Developmentは反復利用済みであり、本実行は独立確認ではない。R019-Q001は過去20同種sessionの**開始60分**値幅変化と現在30分方向からday/night両方の追随・反転を選ぶ規則であり、公式night全体の低分位状態、TSE 09:00 range、同日first strict breakout、14:30 exitを持たない。R069-Q001は全dayを対象に同じTSE opening-range breakoutを検査したが、公式night range状態・rolling quantile・中位状態対照を持たない。したがって既存結果を本件の採否や閾値選択に利用しない。

## 固定した状態、予定軸、因果的売買規則

- `scheduled_axis`はversion-controlled Development `trade_date`の全日（2021-01-01..2025-06-30）である。OOS（2025-07-01..2025-12-31）とFinal Holdout（2026-01-01以降）は物理I/Oを含め一切選択しない。既知no-tradeは予定軸上JPY 0、filled後exit不明はnullである。
- `CalendarClassifier.session_open(trade_date, NIGHT)`が返す制度対応公式night開始（16:30又は17:00）から、同じ`trade_date`の05:29 barまでを含む連続した適格1分OHLCだけで、`R=max(high)-min(low)`を作る。night開始calendar_dateが当該`trade_date`の前日でない予定窓は対象外である。05:30以降、night方向、当日成績、volume、外部市場は使わない。
- 参照Uはcurrentより前の、night開始calendar_dateがそのtrade_dateの前日である直近**20 calendar-eligible night**を新しい順で固定する。calendar上の探索はcurrentの前60 calendar daysまでに限定する。20件に満たない、Development外、R004固定whole-session隔離、又は公式night開始から05:29までの欠損・不適格・session/trade_date不一致が一件でもあるとcurrentは見送る。より古い有効nightを補充しない。zero rangeは有効観測値である。
- 分位点は20個の`R`を昇順に並べ、nearest-rank `q_p=sorted_R[ceil(20*p/100)-1]` とする。主圧縮状態Aは `R_current <= q25`、中位状態Dは `q25 < R_current <= q75`、それ以外はno-tradeである。同値は低側（A）へ、q75同値はDへ固定し、A/Dを排他的にする。
- A又はDのとき、09:00..09:29の連続30適格day barの`H=max(high)`、`L=min(low)`を固定する。09:30..11:00（両端を含む）で最初に`close>H`ならlong、最初に`close<L`ならshortとし、同値、wick-only contact、breakoutなしはno-tradeである。signal bar close後、既存next-eligible-bar-open契約（最大10分）で1枚entryし、14:29 close signal後の14:30 eligible bar openでexitする。Stop、Target、再entry、night方向、当日損益選別は使わない。
- entry前の欠損は約定前取消で費用なし、filled後のexit signal/bar欠損はnullである。1枚、最大1建玉、day内完結で、`Net=gross_fill-fees`（slippageはgrossに一回だけ含む）とする。基準費用は片道1 adverse tickと片道30円である。

## 対照、S2、推論、判定

- 主対照は予定軸の無取引JPY 0である。方向機構co-primaryはAの全く同じevent、entry、exit、費用でsideだけを反転した`A_reverse`であり、予定軸日次`A Net-A_reverse Net`を比較する。
- 状態co-primaryはD（上記25--75%中位night range）の同一opening-range breakout規則である。状態差estimandは、各conditionの実行可能tradeの費用後Net平均の差 `mean(Net_A per A trade)-mean(Net_D per D trade)` であり、no-trade 0円を混ぜない。20 trade_date non-wrapping MBBのresample内で、resampled日次Net合計をresampled trade数で割る。空condition resampleはnullとして回数を保存する（本件の件数gate下では想定しない）。
- S2はPnL、return、勝敗、成績順位を計算・表示しない。outcome-observable A tradeが150以上、Aの2021--2024各年25以上、A long/short各45以上、outcome-observable D tradeが300以上、文書化したstatus以外の説明不能除外が0件を全て必要とする。未達なら`INCONCLUSIVE`で停止し、S3・感度・OOSを行わない。
- S2通過後にA、A_reverse、Dのfilled後exit unknownが一件でもあれば`INCONCLUSIVE`。主判定はAのNet>0、PF>1、seed `20260915`・20 trade_date non-wrapping MBB（10,000回、tail truncation、linear percentile）による予定軸日次平均Netの95%CI下限>0、同一indexの`A-A_reverse`日次差CI下限>0、及び上記A-D trade当たりNet差CI下限>0の全ANDである。未達は`REJECT`、主AND通過後にCandidate追加条件未達は`INVESTIGATE`。

## 事前固定感度とCandidate

許可する感度は、圧縮閾値20%/33%（それぞれ`R<=q20`/`R<=q33`、nearest-rank、同じU）、opening range 20分/45分、entry 1分遅延（exitを延長しない）、exit 14:15/14:45、片道2 tick/3 tick、手数料2倍だけである。閾値感度以外は主A stateを据え置く。すべてのprofileについてNet、取引数、予定軸日次Netを保存する。これは救済探索ではない。

`CANDIDATE`には主ANDに加えて、登録した全10感度profileのNet>0、Aのlong/short双方Net>0、2021--2024各年A Net>0、top-10 winning trades除外後A Net>0を必要とする。年別、方向別、月別とprofit concentration（top5/top10、除外後Net）を全て保存する。これら以外の時刻・閾値・方向・保有時間・filter変更、WFA、OOS、Final Holdoutには進まない。

## 事前実装・保存

価格成績の読取り前に本書、event helper、fixed-signal execution adapter、runner、synthetic test、config、input partition hash、seedを新規かつ不変の`results/research/r074-q001-20260915-low-night-range-opening-breakout-01/`へ凍結する。R004の固定whole-session隔離（tick-grid violation session一覧hash）を再現し、reference/target隔離を明示したevent ledgerを保存する。S2通過時だけevents、orders/fills/trades、daily axes、metrics、bootstrap index/CI、execution/accounting audit、decisionを保存する。合成検証は16:30/17:00制度、公式night全range、20 calendar-eligible Uと60-day cap、欠損・隔離での無補充、nearest-rankと等値、future prefix不変、strict close、next eligible open、delay非延長、fixed exits、side reverse、Net会計、block-bootstrapを対象とする。
