# TASK-R089-Q001: 現物寄付き後60分の終盤集中と day 後半継続

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Development のみ（trade_date 2021-01-01--2025-06-30）

## 仮説

現物寄付き後60分の大きな方向変位のうち、変位が序盤に完了せず直近15分にも集中している日は、未完了の分割注文フローが残り、day-session後半も同方向へ継続する。

この仮説は既知Developmentを反復利用する新規family内の事前登録であり、`prior_information_seen=true` とする。R088を含む既存研究のPnL、閾値又は結果を本仕様の選別に使用しない。結果にかかわらず上限は **INVESTIGATE** とし、Walk Forward、OOS、Final Holdout（2026-01-01以降）へ進まない。

## 固定した状態・可用性・注文規則

各 scheduled `trade_date` について、`p0=09:00 bar open`、`p45=09:44 bar close`、`p60=09:59 bar close`、`D=p60-p0`、`M=10,000*abs(D)/p0`、`F=sign(D)*(p60-p45)/abs(D)` とする。`D=0`、09:00--09:59の必要bar欠損・非適格・非連続、又はR004 DAY隔離は不適格である。night、寄付き前gap、出来高、10:00以後の情報、M/F以外の選別を使わない。

各日dで、直前160 **予定** `trade_date` のみを参照し（currentを除外）、この中で有効なM/Fが140以上必要である。Mのnearest-rank `ceil(n*p/100)-1` により因果的に`q60/q80`を求める。`M_d>=q60`だけを対象に、`B1=[q60,q80)`、`B2=[q80,infinity)`へ置く。current Fを使わず、同じ直前窓の有効過去日を**current日のq60/q80で同じM帯に再分類**し、そのFからnearest-rank `q30/q70`を求める。`F>=q70`をLC、`F<=q30`をECとする（同値は双方に含む）。

LC/ECとも09:59 close観測後の次の適格bar openで`sign(D)`方向に1枚入り、14:55 bar openで決済する。最大10分の既存engine遅延規則を適用する。1日最大1取引・最大1ポジション、Stop/Target/re-entryなし。基本費用は片道1 tick（5pt）+片道30円、倍率100円である。

## PnL前ゲート

時刻対応、Fの符号と分母、strict-prior rolling窓、M帯所属、過去Fのみの分位、翌適格bar約定、固定exit、欠損・隔離を台帳で監査する。説明不能除外0件、LC/EC各90件以上、LCの上昇・下落各30件以上、B1/B2それぞれLC/EC各25件以上、2022--2024各年LC15件以上、2025H1 LC8件以上を満たさなければ、return/PnL/PF/bootstrap/感度を取得せず **INCONCLUSIVE** で停止する。帯内のM共通支持は、LC/ECの範囲の交差が非空で、その交差区間内にLC/EC各10件以上と事前固定する。いずれかの帯で欠ける場合もPnL前INCONCLUSIVEで停止する。

## 評価・対照・推論

主対照は予定trade_date軸の無取引0円、同一LC event・entry・exitの逆方向paired fade、ECの同方向continuationである。LC/ECのM分布を保存し、B1/B2ごとにLCを標準母集団とする固定重み付き取引当たり平均差を算出する。

全scheduled trade_dateを共通系列に置き、既知の非取引日は0円とする。20 trade_date non-wrapping moving-block bootstrap（tail truncation、10,000回、seed=20260915、linear percentile）では同一再標本blockで日次成績と帯別ratio estimatorを評価する。

主ANDは、LCのNet>0、PF>1、平均日次Netの95%CI下限>0、LC−paired-fade対応日次差CI下限>0、LC−ECのM帯標準化平均差CI下限>0。主AND不成立は **REJECT**。成立後も、全固定感度Net>0、上昇/下落とB1/B2が各Net>0、2022--2024の少なくとも2年と2025H1が正、top-10勝ち取引除外後Net>0でなければ **INVESTIGATE**。全て満たしてもDevelopment反復利用のため **INVESTIGATE** を上限とする。

## 固定感度

一度に一要素だけ変更する。全観測窓30/90分（endpointは09:29/10:29、直近窓は15分のまま）、直近窓10/20分、M cutoff q50/q70、F cutoff q60/q80、参照120/200予定日、entry追加1本遅延、exit14:30/15:10、片道2/3 tick、手数料2倍。参照窓の最低有効数は主仕様と同じ87.5%をceilで保ち、120日では105、200日では175とする。年別、上昇/下落別、B1/B2別、F五分位別、取引発生率、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。結果依存の窓・閾値・exit探索はしない。
