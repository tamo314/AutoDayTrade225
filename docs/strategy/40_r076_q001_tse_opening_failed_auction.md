# R076-Q001: TSE寄付きレンジfailed auction逆張り

タスクID: `TASK-R076-Q001`  
family_id: `tse_opening_failed_auction` / study_id: `R076-Q001` / spec_version: `v1`  
run_id: `r076-q001-20260915-tse-opening-failed-auction-01` / protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

## 仮説と期間保護

TSE寄付きレンジをstrict closeで最初に突破した後、短時間にレンジ内へstrict closeで戻る動きはfailed auctionを示す。この確認後には、最初の突破方向と反対方向の14:30までの期待値が、同じfailed eventで最初の突破方向へ取引する継続対照および確認なしの直後逆張りより高い。`prior_information_seen=true`であり、Developmentは反復利用済みで独立確認ではない。

対象はversion-controlled scheduled `trade_date`のDevelopment、2021-01-01..2025-06-30だけである。主母集団はナイトレンジ・ナイト方向・当日値動きによる追加選別をしない全適格日である。R004固定whole-session隔離はデータ品質のためだけに適用し、ナイト状態は主条件・救済条件には使わない。OOS、Walk Forward、2026-01-01以降のFinal Holdoutは物理I/Oを含めアクセスしない。

## 固定ルール

- 09:00..09:29の連続30本の適格day barのhigh/lowをopening rangeとする。09:30..10:30の最初のstrict close `close>high`を上方突破（initial direction=`long`）、`close<low`を下方突破（`short`）とする。同値closeは突破でない。
- その突破barの**後**のbarだけを順に調べ、30本以内かつ11:00以下で最初のstrict internal close `low<close<high`を確認する。これをfailed breakoutとする。同値closeは復帰でない。主戦略は確認close後のnext eligible bar open（最大10分）で最初の突破と反対sideへ1枚entryし、14:29 close signal後の14:30 eligible bar openでexitする。
- 1日1取引・最大1ポジション、Stop、Target、再entry、night方向、当日return・勝敗による選別は使わない。later exitの欠損はentryを遡及取消せずunknownとして扱う。基本費用は片道1 adverse tick＋片道30円、`Net=gross_fill-fees`である。
- 主対照はscheduled-axisの無取引0円、および同一failed event・同一confirmed entry・同一exit・同一費用で最初の突破方向へ取引する`confirmed_continuation`である。統制アブレーション`unconfirmed_fade`は、同じfailed-event日にだけ、最初のbreakout close直後のnext eligible openから逆sideで入り同じ14:30 exitへ向かう。これは確認情報の増分を測る事後の対応比較であり、主戦略の発注可能集合には逆流させない。

## PnL前ゲート

PnL、return、勝敗、PF、順位、bootstrapを取得する前に、説明不能除外0件、主戦略のoutcome-observable executable failed event 200件以上、initial long/short各60件以上、2021--2024各25件以上、2025H1 12件以上を確認する。ANDが不足した場合は`INCONCLUSIVE`で停止し、S3、感度、OOSへ進まない。

## 一回限りのDevelopment評価

S2通過時だけ、20 trade_date non-wrapping MBB、tail truncation、linear percentile、10,000回、seed `20260915`で、主scheduled-axis平均Net、主−`confirmed_continuation`の対応日次差、主−`unconfirmed_fade`の対応日次差の95%CIを保存する。主ANDは、主Net>0、PF>1、三つの95%CI下限>0の全てである。不成立は`REJECT`とする。

感度は、復帰期限15/45本、opening range20/45分（各range終了の次barから同じ10:30 deadlineまでbreakoutを探索）、strict internal close 2本連続、entry 1分遅延、exit14:15/14:45、片道2/3 tick、手数料2倍の11 profileだけとする。年別・initial breakout方向別、最大DD、top-10勝ち取引除外、利益集中度を保存する。R075と同じ20本night rangeのq25/M/q75状態別集計は、主failed eventを変えない診断だけであり、主結果不成立時の救済・選定には使わない。

主AND成立時も、全11感度Net>0、両initial direction Net>0、2021--2024の少なくとも3年と2025H1が正、top-10除外後Net>0を満たさなければ`INVESTIGATE`とする。すべて満たしてもDevelopment反復利用のため判定上限は`INVESTIGATE`である。救済探索、Walk Forward、OOS、Final Holdoutへは進まない。

価格成績の読取り前に本書、runner、event/strategy helper、tests、config、input partition hash、seedを新規不変の`results/research/r076-q001-20260915-tse-opening-failed-auction-01/`へ保存する。S2通過後だけorders/fills/trades、fixed daily axes、metrics、bootstrap index/CI、execution/accounting/causality audit、decisionを保存する。
