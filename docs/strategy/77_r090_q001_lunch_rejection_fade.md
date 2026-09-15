# TASK-R090-Q001: 現物昼休み変位の再開否定後fade

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Development のみ（trade_date 2021-01-01--2025-06-30）

## 仮説と範囲

現物昼休み中に生じた大きな先物変位が12:30の現物再開直後に明確に否定されたとき、先物だけの暫定的な価格発見の修正が残り、その後も昼休み変位と逆方向へ進む。本件はR089の窓・方向・変位帯の救済ではなく、現物休止と再開という別の制度的機序を検査する。Developmentは反復利用済みであり`prior_information_seen=true`、判定上限はINVESTIGATEである。Walk Forward、OOS、Final Holdoutは実行しない。

## 凍結した状態、時間、発注

現物昼休みはソースに固定した`TSE-CASH-LUNCH-2021-2025-v1`（11:30--12:30 JST）、先物day sessionは`config/sessions.yaml`と`config/local_calendar.yaml`の版管理済み予定を用いる。各予定trade_dateで`p0=11:30 bar open`、`p1=12:29 bar close`、`p2=12:39 bar close`、`D=p1-p0`、`M=10000*abs(D)/p0`、`J=-sign(D)*(p2-p1)/abs(D)`とする。D=0、p0--p2の必要1分バーの欠損・非適格・非連続、R004隔離、または予定先物day session外は不適格である。night、寄付き前gap、出来高、12:39後の情報、M/J以外の選別を使わない。

各日当日を除く直前160**予定**trade_dateに有効M/Jが140以上あるときだけ、過去Mのnearest-rank q60/q80を求める。M>=q60をB1=[q60,q80)、B2=[q80,infinity)とし、同一の過去窓をcurrent日のq60/q80で帯に再分類した過去Jのみからq30/q70を求める。`J>=q70`をLR（rejection）、`J<=q30`をLA（acceptance）とする。同値は双方に含む。LRは12:39観測後の次の適格bar openから`-sign(D)`方向に1枚、14:55 bar openで決済する。LAは同方向fade・同時刻の対照である。各系列は最大1ポジション、日次最大1取引、Stop/Target/re-entryなしである。

placeboは価格時刻を単に60分移動せず、市場構造イベントを含まない固定午前区間`p0P=10:00 open`、`p1P=10:59 close`、`p2P=11:09 close`に同一のM/J・帯別因果分位・fade規則を独立適用する。PRは11:10の次の適格bar openから13:25 bar openまで（主条件と135分の保有時間一致）保有する。placeboの閾値はplacebo自身の過去観測だけから計算する。

## PnL前ゲート

全時刻対応、昼休み・先物予定、Jの符号・分母、strict-prior rolling窓、M帯、過去情報限定、翌適格bar約定、固定exit、placeboの保有時間、欠損・隔離を監査する。説明不能除外0件、LR/LA/PR各90件以上、LR上昇/下落各30件以上、B1/B2ごとLR/LA各25件以上、2022--2024各年LR15件以上、2025H1 LR8件以上を必須とする。LR/LAの各M帯で範囲交差があり交差内各10件以上を共通支持とする。不足時はPnLを一切取得せずINCONCLUSIVEで停止する。

## 評価、推論、感度

基本費用は片道1 tick+30円。主対照は予定軸無取引0円、同一LR event/entry/exitで`sign(D)`方向のpaired continuation、LA fade、午前placebo PRである。LR、LA、PRのM分布を保存し、B1/B2ごとにLRを標準母集団とした固定重みの取引当たり平均差をLR-LA、LR-PRに算出する。全予定trade_dateを共通日次軸に置き、非取引日を0円とし、同一20 trade_date non-wrapping moving-block bootstrap（tail truncation、linear percentile、10,000回、seed=20260915）で日次成績と両ratio estimatorを評価する。

主ANDはLR Net>0、PF>1、LR日次Net 95%CI下限>0、LR-paired continuationの対応日次差CI下限>0、LR-LAのM帯標準化差CI下限>0、LR-PRのM帯標準化差CI下限>0。主AND不成立はREJECT。成立後も全固定感度Net>0、両D方向・両M帯Net>0、2022--2024の少なくとも2年と2025H1が正、top-10勝ち取引除外後Net>0でなければINVESTIGATE、満たしてもDevelopment再利用のためINVESTIGATE止まりとする。

一度に一要素だけ、再開確認5/15分、M q50/q70、J q60/q80、参照120/200日、entry追加1本遅延、主exit14:30/15:10（placeboも保有時間一致）、片道2/3 tick、手数料2倍を実行する。年別、D方向別、B1/B2別、J五分位別、取引発生率、最大DD、利益集中度、top-10勝ち取引除外後Netを保存し、部分群は救済選択に使わない。
