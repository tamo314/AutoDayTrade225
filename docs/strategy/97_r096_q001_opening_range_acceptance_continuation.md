# TASK-R096-Q001: 現物開始60分の方向側レンジ端受容と day 継続

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Development のみ（trade_date 2021-01-01--2025-06-30）

## 重複照合と探索履歴

PnLアクセス前に R001--R095 の研究記録・実装を照合した。特に、R022 は直前の day/night セッション全体のレンジ端、R042 はnight終端レンジ端、R056 は別の初動経路指標であり、同じ当日09:00--09:59のOHLCではない。R079/R080 は同じ開始60分の方向を使うが、`abs(D)`だけの60日閾値と14:30 exitであり方向側終値位置を選別しない。R088 は60分のpath-efficiency、R089 は直近15分の集中度で、いずれも `z` を使わない。

したがって、当日60分の `x` と方向側レンジ終値位置 `z` を、current-excluded 120予定TSE日で共同的に閾値化し、版管理した cash終了5分前まで同方向に保有する本件と materially equivalent な既存実験はない。本件は R088/R089 の結果を既知とする**事後追加仮説**であり、反復利用済みDevelopment上の一度限りの研究である。`prior_information_seen=true`、結果によらず判定上限は **INVESTIGATE** とする。連続系列の正規化Parquetだけを使い、OOS、Walk Forward、Final Holdoutは使わない。

## 仮説と固定仕様

公式TSE営業日ごとに、予定された09:00--09:59の60本から `O=open_09:00`、`C=close_09:59`、`H=max(high)`、`L=min(low)`、`D=C-O`、`x=abs(D)/O` を作る。`H>L`かつ`D!=0`のときだけ、`D>0`なら`z=(C-L)/(H-L)`、`D<0`なら`z=(H-C)/(H-L)`を作る。

当日を含めない直前120予定TSE営業日の有効`x/z`を、古い日で補充せず使用する。100件以上でnearest-rank `ceil(n*p/100)-1` の`qx50`、`qz25`、`qz75`を算出し、`qz25<qz75`を必須とする。`E: x>=qx50 and z>=qz75`、`K: x>=qx50 and z<=qz25`とし、等号は各外側群に含める。`A`はEのD方向、`B`は同じE・entry/exitの逆方向、`C/D`は同じEのfixed long/short、`M`はKのD方向、`N`は予定日軸の無取引0円である。

09:59 close確定後の最初の適格通常足open（最大10予定分）で1枚約定し、版管理した公式TSE終了Tの5分前の通常足openで決済する。night、寄付き前gap、10:00以後の選別情報、曜日、Stop、Target、re-entry、途中更新は用いない。基準費用は片道1 tickと片道30円で、Net=Gross-fees、slippageはengine内で一度だけ適用する。

## PnL前ゲート

予定日軸、旧（T=15:00）/新（T=15:30）制度、trade_date/calendar_date、R004隔離、60本OHLC/H/L/z、strict-prior参照、signal後submit、最大1建玉、会計、将来entry/exit可用性を使わないE/K選別を監査する。entry未約定は理由付き取消、entry後のexit不能はnull・未完了としてPnL前に停止する。

説明不能除外0、q-ready nonzero日800以上、E/K完了各100以上、EのD正/負各35以上、Eの2021初期化部分8以上・2022--2024各18以上・2025H1 8以上、旧/新制度85/8以上を要求する。さらに`x>=qx50 and x<qx75`を`[0.50,0.75)`、`x>=qx75`を`[0.75,1.00]`帯とし、各帯でE/K各20以上を要求する。未達、漏洩、不具合、未完了ならreturn/PnL/PF/bootstrap/感度を取得せず **INCONCLUSIVE** とする。

## 評価、判定、固定感度

共通予定日軸の20 trade_date非循環moving-block bootstrapを10,000回、seed=20260915、全条件共通index、末尾切詰め、linear percentileで実行する。A日次Net、paired A-B/A-C/A-D日次差、および二つのx帯をA件数で等重みとする標準化A-M取引当たりNet差を評価する。

主ANDはA Net>0、PF>1、A日次Net CI下限>0、A-B/A-C/A-D各CI下限>0、標準化A-M差CI下限>0である。一つでも未達なら **REJECT**。成立後も、全固定感度Net>0、D正負・旧新制度双方Net>0、2022--2024の少なくとも2年と2025H1が正、top-10勝ち取引除外後Net>0を全て満たさなければ **INVESTIGATE**。全て満たしても上限は **INVESTIGATE** とする。

固定感度は `z=q70/q30`、`z=q80/q20`、`x=q40/q60`、観測30/90分、entry追加1分、exit T-20、exit14:55、片道2 tick、手数料2倍である。3 tickも必須診断として保存する。結果依存の終値位置・変位閾値、片側方向、entry/exit、観測窓変更、追加探索、Walk Forward、2025H2 OOS、Final Holdoutには進まない。
