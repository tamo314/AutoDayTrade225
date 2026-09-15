# TASK-R086-Q001: 日中・同時刻5分shockの短期反転

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Developmentのみ（trade_date 2021-01-01～2025-06-30）

## 仮説と独立性

版管理された日中の連続取引区間で、同じ区間開始からの時刻に過去分布と比べて極端な5分価格shockが生じたとき、その一部は恒久的な新情報の価格発見ではなく、一時的な流動性・在庫不均衡である。その場合、shockと逆方向への建玉は次の20分で正の期待値を持つ。

これはR083/R085の日・夜再開gap fadeでも、R084の午前rangeを用いる昼休み後breakoutでもない。夜間情報、overnight gap、出来高、将来の日中最大shock、event後価格、又は`|X|`以外の選別は使わない。既知Developmentの反復利用であるため、いかなる結果でも判定上限は**INVESTIGATE**であり、Walk Forward、OOS、2026-01-01以降のFinal Holdoutは読取・実行しない。

## 凍結した因果的定義

- 対象は各trade_dateの、`sessions.yaml`で当該日に有効なDAY sessionの連続取引区間だけである。区間を跨ぐ昼休み又はsession境界は使わない。2021-01-01～2025-06-30ではDAY sessionは一つの連続区間であり、制度版と開始・終了時刻を各eventに保存する。
- 区間開始30分後から、区間終了の少なくとも30分前までに終端があるよう、5分の**非重複**blockを区間開始から固定する。各blockは`[start,start+4分]`の5本であり、終端を`t`とする。未完blockは作らない。
- `X_t = 10,000 × (close_t − open_(t−4))/open_(t−4)`。blockの全5本、開始open、終端closeが正価格かつ適格であり、同一trade_date、同一DAY連続区間に属するときだけ`X_t`は有効である。0は過去`|X|`参照には残すがsignalにはならない。
- 同一連続区間名・同一「区間開始からのblock開始offset（分）」ごとに、current-excludedの直前60**適格**trade_dateの`|X|`を古い順に保存し、nearest-rank `ceil(n×0.90)-1`（補間なし）を`q90`とする。historyはそのblockの有効観測だけで更新し、currentは絶対に含めない。60未満ならsignalなしである。
- 各trade_dateについて時刻順に最初の`X_t != 0`かつ`|X_t| >= q90`だけをeventとする。それより後のshockは記録するが選択・発注しない。signal時点はt closeであり、`X>0`ならfade short、`X<0`ならfade longである。
- entryはt観測後の次の適格bar open（固定感度の追加1本遅延時はその次）であり、exitはentry後20分の最初の適格bar openである。lookback block、signal、entry予定時刻、exit予定時刻のいずれかが連続区間外、昼休み、session境界を跨ぐものは注文前の予定規則で不適格とする。entry後に実際のexit price/barが不明なら既約定を遡及取消せず、outcome unknownとして主集計を停止する。
- 1日最大1取引、最大1ポジション、Stop・Target・再entryなし。next eligible openは既存engineの最大10分遅延規則の範囲だけを許し、遅延で同一区間を出る場合は取消とする。

## PnL前S2ゲート

PnL、return、勝敗、PF、順位、bootstrapを計算する前に、取引時間制度版、固定block、同時刻参照キー、current-excluded rolling分位、first-event選択、翌適格bar約定、同一区間exit、欠損・隔離を監査する。以下の全てを要求する。

- 説明不能除外0件
- outcome-observable executable event 300件以上
- `X>0`、`X<0` 各100件以上
- 2021～2024各年45件以上、2025H1 20件以上

一つでも不足又は監査不合格なら**INCONCLUSIVE**としてPnL取得前に停止する。

## 会計・対照・推論

- 基本費用は片道1 tick + 片道30円、1枚。既存engineの翌適格bar約定を使い、slippageを二重控除しない。
- 主対照は共通scheduled trade_date軸の無取引0円、同一event・同一entry/exitでX方向に入るcontinuation、及び同一eventの直前非重複5分blockの非ゼロ符号を逆張りするpre-shock-sign controlである。後者は直前blockが非ゼロのeventだけの対応比較であり、主発注集合には逆流させない。
- 全適格trade_dateを共通日次軸に置き無取引日を0円として、20 trade_date non-wrapping moving-block bootstrap、tail truncation、10,000反復、seed `20260915`、linear percentile 95% CIを行う。pre-shock対照はそのcommon-event日次軸で同じ方式を行う。
- 主ANDはshock-fadeのNet>0、PF>1、平均日次Net CI下限>0、shock-fade−continuationの対応日次差CI下限>0、shock-fade−pre-shock-sign controlの対応日次差CI下限>0。

## 固定感度・保存・判定

- 感度はthreshold q80/q95、参照40/80日、shock長3分/10分、entry追加1本遅延、保有10分/30分、片道2/3 tick、手数料2倍だけである。各shock長も開始30分後・終端30分前・非重複という同じblock規則を守る。
- shock-fadeについて年別、X正負別、午前・午後別、event時刻帯別、取引時間制度版別、`|X|`五分位別、取引発生率、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。部分群を救済選択には使わない。
- 主AND不成立は**REJECT**。成立しても全固定感度Net>0、X正負ともNet>0、午前・午後ともNet>0、2021～2024の少なくとも3年と2025H1が正、top-10除外後Net>0を満たさなければ**INVESTIGATE**。全て満たしてもDevelopment反復利用のため**INVESTIGATE**である。

結果依存のthreshold・時刻帯・保有時間探索、Walk Forward、OOS、Final Holdoutには進まない。
