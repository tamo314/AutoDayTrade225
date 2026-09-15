# R074-Q002: 低ボラティリティ公式ナイト後のTSE寄付きレンジ突破継続

タスクID: `TASK-R074-Q002`  
family_id: `night_range_state_opening_breakout` / study_id: `R074-Q002` / spec_version: `v2`  
run_id: `r074-q002-20260915-low-night-range-opening-breakout-01` / protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

## 継承する仮説と凍結規則

これはR074-Q001を別IDで再事前登録するものであり、変更は下記S2だけである。経済仮説、入力、scheduled axis、20-night参照仕様、nearest-rankと25%圧縮A／中位Dの定義、entry・exit、対照、費用、bootstrap、感度、経済的判定基準はR074-Q001（[31_r074_q001_low_night_range_opening_breakout.md](31_r074_q001_low_night_range_opening_breakout.md)）から完全に据え置く。

仮説は、公式night session全体が低ボラティリティで終わった後、TSE 09:00寄付きレンジを最初にstrict closeで突破した方向の価格発見が14:30 exitまで継続する、である。`prior_information_seen=true`であり、Developmentは反復利用済みである。本実行は独立確認ではない。

- Development scheduled axisはversion-controlled `trade_date`の2021-01-01..2025-06-30全日である。OOS（2025-07-01..2025-12-31）、Walk Forward、Final Holdout（2026-01-01以降）には物理I/Oを含めアクセスしない。
- currentより前60 calendar days内の新しい順、night開始calendar_dateがtrade_date前日の直近20 calendar-eligible nightを参照Uとする。20件に満たない、Development外、R004 whole-session隔離、または公式night開始から05:29までの欠損・不適格・session/trade_date不一致が一件でもあれば見送り、古い有効nightで補充しない。
- `R=max(high)-min(low)`、`q_p=sorted_R[ceil(20*p/100)-1]`。Aは`R_current<=q25`、Dは`q25<R_current<=q75`。同値は低側A、A/Dは排他的である。
- A/Dでは09:00..09:29の連続30適格day barをopening rangeとし、09:30..11:00の最初の`close>H`をlong、最初の`close<L`をshortとする。signal close後のnext eligible bar open（最大10分）で1枚entry、14:29 close signal後14:30 eligible bar openでexitする。Stop、Target、再entry、night方向、当日損益選別は使わない。
- 主対照は予定軸JPY 0。co-primaryは同一event・同一entry/exit/費用でsideだけを反転した`A_reverse`、状態co-primaryはDの同一規則である。片道1 adverse tick＋片道30円を基準とし、slippageはgrossへ一度だけ、`Net=gross_fill-fees`とする。

## Q002で置換するS2（PnL前）

S2はPnL、return、勝敗、PF、成績順位を計算・表示しない。説明不能除外0件、Aの全期間outcome-observable executable trade 150件以上、A long/short各45件以上、Dのoutcome-observable executable trade 300件以上を必要とする。

加えて2021--2024の各年で次を全て必要とする。各年のNは、当該年のscheduled axis上で20本のreference nightがすべて有効に成立し、20個のreference rangeが作れた日数である。A発生数はこのN日中の`R_current<=q25`日数であり、day opening range・breakout・entry/exit可用性の成否でA発生を遡及変更しない。`K~Binomial(N, 0.25)`について、下側5%予測分位を `min{k: Pr(K<=k)>=0.05}` と固定し、観測A発生数がこれ以上でなければならない。さらにA成立後の実行可能率（Aかつ`EXECUTABLE`の日数／A発生数）は75%以上とする。分母A発生数が0なら率条件は不通過である。

この二項基準は収益検定でも状態優位性の検定でもない。名目25%状態が利用可能な20-reference母数に対して病的に欠落していないことだけを確認する適格性基準である。2021年の年別成績は、S2通過後にも記述的診断として保存するだけであり、単独で有意性を主張しない。S2のANDが未達なら`INCONCLUSIVE`で停止し、S3・感度・OOSを行わない。

## S2通過後の一回限りのDevelopment実行

S2通過時だけ凍結仕様をDevelopmentで一度実行する。A、A_reverse、D、予定軸日次Net、Net、PF、最大DD、年別・方向別成績、利益集中度を保存する。seed `20260915`、20 trade_date non-wrapping MBB、10,000回、tail truncation、linear percentileで、予定軸平均Net、A-A_reverse日次差、A-D取引当たりNet差の95% CIを保存する。

感度は閾値q20/q33、opening range20/45分、entry 1分遅延（exit非延長）、exit14:15/14:45、片道2/3 tick、手数料2倍の全10 profileだけである。主ANDはA Net>0、PF>1、予定軸平均Net CI下限>0、A-A_reverse CI下限>0、A-D CI下限>0。未達は`REJECT`、主AND通過でCandidate追加条件未達は`INVESTIGATE`、Candidateには全10感度Net>0、両方向Net>0、2021--2024各年A Net>0、top-10 winning trades除外後Net>0を必要とする。救済的探索・仕様調整はしない。

## 保存と検証

価格成績の読取り前に本書、runner、S2 helper、既存event/strategy helper、tests、config、input partition hash、seedを新規不変の`results/research/r074-q002-20260915-low-night-range-opening-breakout-01/`へ保存する。合成検証は既存のR074因果性・約定・会計検査に加え、二項inverse-CDF、20-reference N、A発生と後続実行可能率の分離を対象とする。S2通過後だけorders/fills/trades、daily axes、metrics、bootstrap index/CI、execution/accounting audit、decisionを保存する。
