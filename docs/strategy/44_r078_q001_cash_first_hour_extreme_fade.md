# TASK-R078-Q001: cash-session first-hour extreme displacement fade

状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**  
登録日: **2026-09-15 JST**  
family_id: `cash_session_first_hour_extreme_fade` / study_id: `R078-Q001` / spec_version: `v1`  
対象: Development trade_date 2021-01-01--2025-06-30 のみ

## 仮説

cash-session開始後の最初の1時間に生じた極端な価格変位は、一時的な寄付き注文不均衡を含み、その後14:30までに部分的に反転する。

これは既知Developmentの反復利用である。`prior_information_seen=true` を記録し、判定上限は `INVESTIGATE` とする。本登録は独立確認、Walk Forward、OOS、2026年以降のFinal Holdout、実運用を許可しない。

## 固定した入力・状態・注文

予定母集団は版管理calendarのDevelopment `trade_date` 全日である。各trade_date `t` について、`t` の09:00 day barの`open=A`、09:59 day barの`close=B`を使い、符号付き第1時間変位を`R=B-A`とする。必要barが欠損・不適格・非正値、barのsession/trade_date不一致、`R=0`、または当日day sessionが既存R004 whole-session隔離対象なら取引しない。別状態への救済はしない。

参照集合は当日を含まない直前60**適格trade_date**の`|R|`だけである。隔離、zero、必要bar欠損などで有効なRを得られないtrade_dateは参照件数として補充しない。直前60有効Rに満たない日は取引しない。nearest-rankは昇順の`ceil(n*p/100)-1`（補間なし）である。主状態は`|R| >= q80`の極端`E`、対照状態は`q20 < |R| < q80`の中位`M`で、等値はE側または非選択であり、状態は排他的である。`q20>=q80`も見送りである。

状態と方向は09:59 bar closeで確定する。E-fadeはRと逆方向（R>0ならshort、R<0ならlong）へ、Bを観測した後の最初の適格bar open（通常10:00 open）で1枚入り、14:30 openで決済する。E-continuationは同一E日・同一entry/exitでR方向へ入る。M-fadeはM日で同じ逆張り規則を行う。1 trade_date 1取引、最大1建玉。Stop、Target、再entry、opening-range突破、nightレンジ、day-close→09:00 gap、10:00以後の値動きによる選別は用いない。entry時点でbarがなければ約定前取消・0円、既約定後の14:30 exitが不明ならnullであり0円化しない。

基準費用は1枚、片道1 tick（5 points）と片道30円であり、往復1,060円相当である。slippageはexecution engineに一度だけ適用し、feesとの二重控除をしない。

## PnL前ゲート

説明不能な除外は0件、Eでentry/exitまで実行可能な件数150件以上、EのR正・R負各50件以上、2021--2024の各年E 20件以上、2025H1はE 10件以上を必要とする。不成立なら`INCONCLUSIVE`で停止し、return/PnL/PF/bootstrapを算出しない。

## 主評価・対照・判定

主対照は無取引（予定日軸0円）、同一E日・同一entry/exitでR方向へ取引するcontinuation、およびMで同じfade規則を行うmagnitude-controlである。既知無取引は予定日軸に0円で残し、nullがあれば完全な主推論をせず`INCONCLUSIVE`とする。

主ANDは全て以下である。

1. E-fade Net > 0。
2. E-fade PF > 1。
3. 20 trade_date非循環MBB（tail truncation、linear percentile、10,000回、seed=20260915）による予定日軸E-fade日次平均Netの95%CI下限 > 0。
4. 同一E日のE-fade minus continuation日次差の同MBB 95%CI下限 > 0。
5. E-fade minus M-fadeの取引当たりNet差について、同じ20 trade_date block bootstrap（分子=各状態のblock抽出Net合計、分母=各状態のblock抽出取引数。片方が0件の反復は捨てず空反復数を保存）の95%CI下限 > 0。

主AND不成立は`REJECT`である。成立しても、全感度Net>0、R正負ともNet>0、2021--2024の少なくとも3年と2025H1が正、top-10勝ち取引除外後Net>0の全てを満たさなければ`INVESTIGATE`とする。全てを満たしてもDevelopment反復利用のため`INVESTIGATE`を上限とする。

## 固定診断・感度・保存

事前指定診断として、calendarで一意に接続された直前trade_dateの版管理day session closeから当日09:00 openまでのgapとRについて、同符号・逆符号・欠損を保存する。これは選別、救済、ゲート、主推論に使わない。

感度はq70/q90、参照40/80日、signal終点09:44/10:29、entryをさらに1本遅延、exit 14:15/14:45、片道2 tick、片道3 tick、手数料2倍だけである。signal終点はAと指定bar closeの差に置換し、観測後の次bar openへ入る。各profileのevents、orders/fills/trades、予定日次軸、metrics、年別、R正負別、最大DD、利益集中度、top-10勝ち取引除外後Net、bootstrap、execution/accounting/causality auditを保存する。結果を見た閾値救済、追加感度、Walk Forward、OOS、Final Holdoutには進まない。
