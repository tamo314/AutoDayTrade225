# TASK-R081-Q001: TSE lunch-break futures direction continuation

状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**  
登録日: **2026-09-15 JST**  
family_id: `tse_cash_lunch_information_price_discovery` / study_id: `R081-Q001` / spec_version: `v1`  
対象: Development trade_date 2021-01-01--2025-06-30 のみ

## 仮説・位置付け・探索制限

東京現物市場の昼休み中に日経225先物で形成された方向は、休止中に到来した情報を表し、現物取引再開後14:30まで同方向のprice discoveryが続く。本件は、R078--R080の時刻又は閾値を救済する試行ではなく、現物昼休みという別の制度的メカニズムを検査する新しいfamilyである。

Developmentは既知の探索期間であり、`prior_information_seen=true`とする。結果にかかわらず判定上限は`INVESTIGATE`であり、結果依存の窓・方向・強度探索、Walk Forward、OOS、2026年以降のFinal Holdoutへは進まない。

## 固定イベント・実行

版管理された取引時間に従うscheduled Development trade-date軸上で、各day sessionのA=11:30 bar open、B=12:29 bar close、L=B-Aとする。R004 whole-session tick-grid隔離対象でなく、A/Bがeligible、同一trade_date/day session、正値で、L!=0である日だけを候補とする。gap、night range、opening range、09:00--09:59方向、12:30以後の情報、`|L|`による選別はいっさい使わない。

Bの観測後、最初の適格bar open（12:30 open）でL方向（L>0ならlong、L<0ならshort）に1枚入り、14:30 openで決済する。1日1取引、最大1ポジション、Stop・Target・re-entryなしである。entry時点のsignal又はnext openが不適格なら約定前取消・予定日次軸0円、既約定後の固定exitが不明ならnullとして全PnL判定を停止する。費用は片道1 tick+片道30円で、engineのslippageを一度だけ控除する。

## PnL前ゲート

主L eventについて、説明不能除外0、実行可能900以上、L正負各350以上、2021--2024各年150以上、2025H1 70以上を要求する。不足ならreturn/PF/bootstrapを算出せず`INCONCLUSIVE`で停止する。

morning-sign control用に、M=11:29 bar close - 09:00 bar openを別に記録する。morning-sign比較は、主Lが実行可能かつM!=0でMの必要barがeligibleな**同一trade_date集合**だけで行い、その集合件数、L/M符号不一致件数、Mの欠測・zero理由をPnL前に保存する。Mは主選別又は主entry取消に使わない。

## 主評価・対照・判定

主対照は、(1)予定日次軸の無取引0円、(2)同一日・同一entry/exitでLと逆方向のfade、(3)上記のL/M共通日集合で、同一entry/exitにM符号で建てるmorning-sign controlである。

20 trade_date非循環moving-block bootstrap（tail truncation、linear percentile、10,000回、seed=20260915）で次の全てを要求する。

1. lunch-continuation Net > 0。
2. PF > 1。
3. continuation予定日次平均Netの95%CI下限 > 0。
4. continuation--fade対応日次差の95%CI下限 > 0。
5. L/M共通日集合のcontinuation--morning-sign control対応日次差の95%CI下限 > 0。

主AND不成立は`REJECT`。成立しても、全固定感度Net>0、L正負ともNet>0、2021--2024の少なくとも3年と2025H1が正、top-10勝ち取引除外後Net>0を満たさなければ`INVESTIGATE`。全条件を満たしてもDevelopment再利用のため上限は`INVESTIGATE`である。

## 固定感度・記述保存

感度は、昼休み窓の両端5分除外（A=11:35 open、B=12:24 close）、entry追加1本遅延、exit14:15/14:45、片道2/3 tick、手数料2倍だけである。年別、L正負別、午前変位との同符号・逆符号別、主実行可能eventを`abs(L), trade_date`昇順に固定順位で5等分した事前固定五分位別、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。部分群は選別又は救済に用いない。

全profileのevent/orders/fills/trades、予定日次軸、metrics、MBB index、実行・会計・因果性監査、入力・コード・設定hashを不変成果物として保存する。OOS、Walk Forward、Final Holdoutはいずれもアクセスしない。
