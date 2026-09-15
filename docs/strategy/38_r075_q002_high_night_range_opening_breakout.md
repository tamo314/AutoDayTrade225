# R075-Q002: 高nightレンジ局面のTSE寄付きレンジ突破継続

タスクID: `TASK-R075-Q002`  
family_id: `night_range_state_opening_breakout` / study_id: `R075-Q002` / spec_version: `v2`  
run_id: `r075-q002-20260915-high-night-range-opening-breakout-01` / protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

## 再事前登録の根拠と範囲

これはR075-Q001を別IDで再事前登録する。一切の価格成績（PnL、return、勝敗、PF、順位、bootstrap）へアクセスする前に、Q001のPnL-free S2で中位対照Mのoutcome-observable executable tradeが294件であることだけを確認した。Q002の唯一の変更はS2のM最低件数を300から250へ下げることである。300には事前の検出力根拠が存在しなかったため、この変更はM=294を確認後の**PnL-blindな設計修正**として記録する。仮説、データ、状態、時刻、execution、費用、推論、感度、判定はR075-Q001から完全に据え置く。

仮説は、公式night session全体のレンジが直近状態に対して高い日は、未消化の情報到来とボラティリティ持続により、TSE 09:00寄付きレンジを最初にstrict closeで突破した方向が14:30 exitまで継続し、その期待値が中位nightレンジ局面および同一eventの逆方向より高い、である。`prior_information_seen=true`。Developmentは反復利用済みであり、独立確認ではない。

Development scheduled axisはversion-controlled `trade_date`の2021-01-01..2025-06-30だけである。OOS、Walk Forward、2026-01-01以降のFinal Holdoutは物理I/Oを含めアクセスしない。

## 完全に固定する状態・売買規則

- currentより前60 calendar days内の新しい順、night開始calendar_dateがtrade_date前日の直近20 calendar-eligible nightを参照する。20件不足、Development外、R004 whole-session隔離、公式night開始から05:29までの欠損・不適格・session/trade_date不一致が一件でもあればskipし、古い有効nightで補充しない。
- `R=max(high)-min(low)`、`q_p=sorted_R[ceil(20*p/100)-1]`。主状態Hは`R_current>=q75`、低診断Lは`R_current<=q25`、中位Mは`q25<R_current<q75`で排他的に固定する。`q25>=q75`は`STATE_DEGENERATE_Q25_GTE_Q75`としてPnL前に分類し、別状態へ救済しない。
- Hで09:00..09:29の連続30適格day barをopening rangeとし、09:30..11:00の最初の`close>H`をlong、最初の`close<L`をshortとする。signal close後のnext eligible bar open（最大10分）で1枚entryし、14:29 close signal後14:30 eligible bar openでexitする。Stop、Target、再entry、night方向、当日成績による選別は使わない。
- 主対照はscheduled-axis JPY 0、方向対照は同一event・entry・exit・費用でsideのみ反転したH_reverse、状態対照はMの同一breakout規則である。LはR074の低状態効果を同一実装で再現する診断対照に限り、選定規則には入れない。基本費用は片道1 adverse tick＋片道30円で、`Net=gross_fill-fees`とする。

## Q002で置換するS2（PnL前）

S2ではPnL、return、勝敗、PF、成績順位を計算・表示しない。説明不能除外0件、H executable trade 150件以上、H long/short各45件以上、**M executable trade 250件以上**を要求する。この250以外のS2条件はQ001から不変である。

さらに2021--2024の各年で、20本reference nightがすべて有効に成立し20個のreference rangeが作れた日数を`N`とする。状態縮退日はNに残るがHには分類しない。H発生数は`q25<q75`かつ`R_current>=q75`の日数で、day opening range・breakout・entry/exit可用性で遡及変更しない。`K~Binomial(N,0.25)`の下側5%予測分位を`min{k: Pr(K<=k)>=0.05}`と固定し、H発生数はこれ以上、かつH発生後実行可能率は75%以上とする。この二項基準は収益検定・状態優位性の検定ではない。S2のANDが再計算でも未達なら`INCONCLUSIVE`で停止し、S3、感度、OOSへ進まない。

## S2通過後の一回限りのDevelopment実行

S2通過時だけ、片道1 tick＋30円でDevelopmentを一度実行する。seed `20260915`、20 trade_date non-wrapping MBB、10,000回、tail truncation、linear percentileで、H予定軸平均Net、H-H_reverse日次差、H-M取引当たりNet差の95% CIを保存する。主ANDは、H Net>0、PF>1、H予定軸平均Netの95%CI下限>0、H-H_reverse対応日次差CI下限>0、H-M取引当たり差block-bootstrap CI下限>0、の全てである。主AND不成立は`REJECT`である。

感度は高状態閾値q67/q80、opening range20/45分、entry 1分遅延（exit非延長）、exit14:15/14:45、片道2/3 tick、手数料2倍の10 profileだけとする。年別・方向別成績、最大DD、利益集中度、top-10 winning trades除外を保存する。主AND成立時もDevelopment再利用とゲート修正のため判定上限は`INVESTIGATE`であり、Candidateにはしない。救済探索、Walk Forward、OOS、Final Holdoutには進まない。

価格成績の読取り前に本書、runner、S2 helper、event/strategy helper、tests、config、input partition hash、seedを新規不変の`results/research/r075-q002-20260915-high-night-range-opening-breakout-01/`へ保存する。S2通過後だけorders/fills/trades、daily axes、metrics、bootstrap index/CI、execution/accounting audit、decisionを保存する。
