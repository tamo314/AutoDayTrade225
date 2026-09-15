# R075-Q001: 高ボラティリティ公式ナイト後のTSE寄付きレンジ突破継続

タスクID: `TASK-R075-Q001`  
family_id: `night_range_state_opening_breakout` / study_id: `R075-Q001` / spec_version: `v1`  
run_id: `r075-q001-20260915-high-night-range-opening-breakout-01` / protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

## 仮説と範囲

公式night session全体のレンジが直近状態に対して高い日は、未消化の情報到来とボラティリティ持続により、TSE 09:00寄付きレンジを最初にstrict closeで突破した方向が14:30 exitまで継続する。これはR069の無条件breakoutおよびR074-Q002の低night状態が失敗した後の、状態効果の向きを識別する対称的な統制仮説である。`prior_information_seen=true`であり、Developmentは反復利用済みであるため独立確認ではない。

Development scheduled axisはversion-controlled `trade_date`の2021-01-01..2025-06-30全日だけである。OOS、Walk Forward、Final Holdout（2026-01-01以降）は、物理I/Oを含めアクセスしない。

## 固定した状態・売買規則

- currentより前60 calendar days内の新しい順、night開始calendar_dateがtrade_date前日の直近20 calendar-eligible nightを参照する。20件不足、Development外、R004 whole-session隔離、公式night開始から05:29までの欠損・不適格・session/trade_date不一致が一件でもあればskipし、古い有効nightで補充しない。
- `R=max(high)-min(low)`、`q_p=sorted_R[ceil(20*p/100)-1]`。主状態Hは`R_current>=q75`、低診断Lは`R_current<=q25`、中位Mは`q25<R_current<q75`で排他的に固定する。`q25>=q75`は`STATE_DEGENERATE_Q25_GTE_Q75`としてPnL前に分類し、別状態へ救済しない。
- Hで09:00..09:29の連続30適格day barをopening rangeとし、09:30..11:00の最初の`close>H`をlong、最初の`close<L`をshortとする。signal close後のnext eligible bar open（最大10分）で1枚entryし、14:29 close signal後14:30 eligible bar openでexitする。Stop、Target、再entry、night方向、当日成績による選別は使わない。
- 主対照はscheduled-axis JPY 0、方向対照は同一event・entry・exit・費用でsideのみ反転したH_reverse、状態対照はMの同一breakout規則である。LはR074の低状態効果を同一実装で再現する診断対照に限り、選定規則には入れない。基本費用は片道1 adverse tick＋片道30円で、`Net=gross_fill-fees`とする。

## S2: PnL前の可用性 gate

S2ではPnL、return、勝敗、PF、成績順位を計算・表示しない。説明不能除外0件、H executable trade 150件以上、H long/short各45件以上、M executable trade 300件以上を要求する。

さらに2021--2024の各年で、20本reference nightがすべて有効に成立し20個のreference rangeが作れた日数を`N`とする。状態縮退日はNに残るがHには分類しない。H発生数は`q25<q75`かつ`R_current>=q75`の日数で、day opening range・breakout・entry/exit可用性で遡及変更しない。`K~Binomial(N,0.25)`の下側5%予測分位を`min{k: Pr(K<=k)>=0.05}`と固定し、H発生数はこれ以上、かつH発生後実行可能率は75%以上とする。未達なら`INCONCLUSIVE`で停止し、S3・感度・OOSへ進まない。

## S3、感度、判定

S2通過時だけseed `20260915`、20 trade_date non-wrapping MBB、10,000回、tail truncation、linear percentileで、H予定軸平均Net、H-H_reverse日次差、H-M取引当たりNet差の95% CIを保存する。主ANDはHのNet>0、PF>1、三CI下限>0である。

感度は高状態閾値q67/q80、opening range20/45分、entry 1分遅延（exit非延長）、exit14:15/14:45、片道2/3 tick、手数料2倍の10 profileだけとする。年別・方向別成績、最大DD、利益集中度を保存する。Candidateには主ANDに加え、全10感度Net>0、両方向Net>0、2021--2024の少なくとも3年と2025H1が正、top-10 winning trades除外後Net>0を必要とする。主AND不通過は`REJECT`、主AND通過でCandidate追加条件未達は`INVESTIGATE`である。

R069、R074、R067との関連と、Development反復利用済みで独立確認ではないことを結果に明記する。救済探索、Walk Forward、OOS、Final Holdoutには進まない。
