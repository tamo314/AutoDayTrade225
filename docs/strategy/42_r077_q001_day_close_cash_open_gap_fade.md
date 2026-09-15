# TASK-R077-Q001: prior day-session close to 09:00 gap fade

状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**  
登録日: **2026-09-15 JST**  
対象: Development trade_date 2021-01-01--2025-06-30 のみ

## 仮説

直前day session終値から当日09:00始値までの極端な価格変位は、night・休場時間に蓄積した一方向在庫を表し、cash-session開始後の最初の1時間に部分的に反転する。

これは既知Developmentの反復利用である。`prior_information_seen=true` を記録し、判定上限は `INVESTIGATE` とする。本登録は既存結果を独立確認へ戻さず、Walk Forward、OOS、2026年以降のFinal Holdout、実運用を許可しない。

## 固定した入力・状態・注文

予定母集団は版管理calendarのDevelopment `trade_date` 全日である。各当日 `t` に対し、calendarで一意に接続された直前 `trade_date p` を使う。`p` の版管理day session `session_close(p, DAY)` と等しい時刻の最後の適格barの `close=C`、`t` の09:00 day barの `open=O` を用い、`G=O-C` とする。`C`または`O`が欠損、不適格、非正値、session/trade_date不一致、または当日day／直前dayが既存R004隔離対象なら、その日を取引せず、他状態へ救済しない。

参照集合は当日を含まない直前60**適格trade_date**の `|G|` であり、隔離、zero、必要bar欠損等で有効な`G`を得られないtrade_dateも参照件数として補充しない。直前60適格値に満たない日は取引しない。nearest-rankは昇順の `ceil(n*p/100)-1`（補間なし）である。主状態は `|G| >= q80` の極端 `E`、対照状態は `q20 < |G| < q80` の中位 `M`で、等値はE側または非選択であり、状態は排他的である。`G=0`、`q20>=q80`、その他の必要条件不足は見送りである。

選択は09:00 bar closeで確定する。E-fadeは`G`と逆方向（G>0ならshort、G<0ならlong）へ、09:00観測後の翌適格bar open（通常09:01 open）で1枚入り、10:00 openで決済する。E-continuationは同一E日・同一entry/exitで`G`方向へ入る。M-fadeはM日で同じ逆張り規則を行う。1 trade_date 1取引、最大1建玉。Stop、Target、再entry、opening-range、night range、09:00以後の値動きによる選別は用いない。entry時点でbarがなければ約定前取消・0円、既約定後の10:00 exitが不明ならnullであり0円化しない。

基準費用は1枚、片道1 tick（5 points）と片道30円であり、往復1,060円相当である。slippageはexecution engineに一度だけ適用し、feesとの二重控除をしない。

## PnL前ゲート

説明不能な除外は0件、Eでentry/exitまで実行可能な件数150件以上、EのG正・G負各50件以上、2021--2024の各年E 20件以上、2025H1のE 10件以上を必要とする。不成立なら`INCONCLUSIVE`で停止し、return/PnL/PF/bootstrapを算出しない。

## 主評価・対照・判定

主対照は無取引（予定日軸0円）、同一E日のcontinuation、M-fadeである。既知無取引は予定日軸に0円で残し、nullがあれば完全な主推論をせず`INCONCLUSIVE`とする。

主ANDは全て以下である。

1. E-fade Net > 0。
2. E-fade PF > 1。
3. 20 trade_date非循環MBB（tail truncation、linear percentile、10,000回、seed=20260915）による予定日軸E-fade日次平均Netの95%CI下限 > 0。
4. 同一E日の E-fade minus continuation 日次差の同MBB 95%CI下限 > 0。
5. E-fade minus M-fade の取引当たりNet差について、同じ20 trade_date block bootstrap（分子=各状態のblock抽出Net合計、分母=各状態のblock抽出取引数。片方が0件の反復は捨てず空反復数を保存）の95%CI下限 > 0。

主AND不成立は`REJECT`である。成立しても、全感度Net>0、G正負ともNet>0、2021--2024の少なくとも3年と2025H1が正、top-10勝ち取引除外後Net>0の全てを満たさなければ`INVESTIGATE`とする。全てを満たしてもDevelopment反復利用のため`INVESTIGATE`を上限とする。

## 固定感度・保存

感度はq70/q90、参照40/80日、exit 09:45/10:30、entryをさらに1本遅延、片道2 tick、片道3 tick、手数料2倍だけである。各profileのevents、orders/fills/trades、予定日次軸、metrics、年別、G正負別、最大DD、利益集中度、top-10勝ち取引除外後Net、bootstrap、execution/accounting/causality auditを保存する。結果を見た閾値救済、追加感度、Walk Forward、OOS、Final Holdoutには進まない。
