# TASK-R090-Q002: 現物昼休み変位の再開否定後fade — PnL前ゲート改訂

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Developmentのみ（trade_date 2021-01-01--2025-06-30）

## 変更理由と事前既知情報

R090-Q001は仮説棄却ではなくPnL前標本ゲートのINCONCLUSIVEである。Q001ではreturn、PnL、PF、bootstrap、orders/fills/trades、感度成績を一切取得していない。既知なのは、Q001のPnL-freeラベル監査でLR/LA/PR=106/109/134、2025H1のLR=7という件数だけである。方向、M帯、因果性、共通支持、説明不能除外は合格していた。

本Q002は、半年部分期の最低件数を「各月平均1件」に相当する6件へ一度だけ修正する標本設計の修復である。PnLは未開示であるため結果追随の変更ではない。これ以外の仮説、価格定義、時刻、strict-prior rolling分位、M帯、LR/LA/PRラベル、方向、entry/exit、placebo、費用、対照、bootstrap、固定感度、保存項目、判定条件はQ001から完全に凍結する。

## 凍結仕様

主条件はQ001と同一である。各予定trade_dateで、p0=11:30 open、p1=12:29 close、p2=12:39 close、D=p1-p0、M=10000*abs(D)/p0、J=-sign(D)*(p2-p1)/abs(D)を用いる。直前160予定trade_dateの有効M/Jが140以上のときのみ、strict-prior M q60/q80と同帯J q30/q70をnearest-rankで計算する。LRはJ>=q70、LAはJ<=q30である。LR/LAは12:39後の次の適格bar openからfade方向に1枚、14:55 openで決済する。午前placebo PRは10:00/10:59/11:09、11:10--13:25で、独立したplacebo履歴だけを使う。

費用は片道1 tick+30円、対照は無取引0円、同一LR event/entry/exitのpaired continuation、LA、午前PRである。20 trade_date non-wrapping moving-block bootstrap（tail truncation、linear percentile、10,000回、seed=20260915）と、Q001の全固定感度（確認5/15分、M q50/q70、J q60/q80、参照120/200日、1本遅延、exit14:30/15:10、2/3 tick、手数料2倍）をそのまま一度だけ実行する。OOS、Walk Forward、2026年以降のFinal Holdoutは実行しない。

## PnL前ゲートと判定

変更は**2025H1 LR最低件数を8から6へ下げることだけ**である。これは半年部分期に月平均1件を要求する。説明不能除外0件、LR/LA/PR各90件以上、LRのD両方向各30件以上、B1/B2それぞれLR/LA各25件以上、2022--2024各年LR15件以上、因果性監査、帯内M共通支持は変更しない。Q001報告との不整合、実装不具合、時刻/trade_date対応誤り、分位漏洩、説明不能欠測を監査で見つけた場合はPnLを取得せずINCONCLUSIVEで停止する。

通過時のみ評価する。主AND（LR Net>0、PF>1、LR日次Net CI下限>0、LR-paired continuation、LR-LA、LR-PRの各CI下限>0）のいずれか不成立はREJECT。主AND成立後も全固定感度Net>0、D両方向・B1/B2ともNet>0、2022--2024の少なくとも2年と2025H1が正、top-10勝ち取引除外後Net>0を満たさなければINVESTIGATEである。全条件を満たしても判定上限はINVESTIGATEとし、結果依存の救済変更は行わない。
