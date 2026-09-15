# TASK-R080-Q001: cash-session first-hour all-magnitude continuation

状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**  
登録日: **2026-09-15 JST**  
family_id: `cash_session_first_hour_price_discovery` / study_id: `R080-Q001` / spec_version: `v1`  
対象: Development trade_date 2021-01-01--2025-06-30 のみ

## 仮説と探索履歴

cash-session最初の1時間の方向は、極端度に依存せず、その後14:30までのprice discovery方向を予測する。R079-Q001で極端状態continuationの正の点推定を、またE--M差が成立しなかったことを見た後の同一Development上のfollow-upである。従って独立確認ではない。`prior_information_seen=true`、`learned_from_r079_positive_point_estimate_and_e_minus_m_nonfinding=true`とし、結果にかかわらず判定上限は`INVESTIGATE`とする。Walk Forward、OOS、2026年以降のFinal Holdout、追加閾値・時刻探索を許可しない。

## 固定入力・状態・注文

R078の正式`primary_events.json`の全1,131 event ID（`trade_date`）を台帳完全性の正本として使う。A=09:00 day bar open、B=09:59 day bar close、R=B-Aである。R078と同じR004 whole-session隔離、必要barのeligible/session/trade_date/正値検査、B観測後の最初の適格bar open、14:30 open exit、欠損規則を使う。ただし`|R|`、gap、night range、opening range、10:00以後の情報による選別を一切行わない。

R≠0でentryとexitが実行可能な全日を、R方向（R>0 long、R<0 short）に1枚で取引する。1 trade_date 1取引、最大1ポジション、Stop・Target・再entryなしである。entry時点でbarが欠損なら約定前取消・予定日次軸0円、既約定後のexitが不明ならnullであり0円化しない。基準費用は片道1 tick+片道30円、slippageはengineで一度だけ控除する。

## PnL前ゲート

R078 event ledger SHA-256が凍結値と一致し、全event IDが予定軸と同じ順序で一意に一致することを要求する。R080の全R≠0かつ実行可能eventについて、説明不能除外0、実行可能900以上、R正/負各350以上、2021--2024各年150以上、2025H1 70以上を要求する。不一致・不足なら`INCONCLUSIVE`としてreturn/PnL/PF/bootstrapを算出せず停止する。

## 主評価・対照・判定

主対照は無取引（予定日次軸0円）、同一日・同一entry/exitのfade、当日のR符号の代わりに直前適格trade_dateのR符号だけを用いるlagged-sign placeboである。placeboに直前適格Rがない予定日は0円のまま残す。20 trade_date非循環MBB（tail truncation、linear percentile、10,000回、seed=20260915）で次を全て要求する。

1. continuation Net > 0。
2. continuation PF > 1。
3. continuation予定日次平均Netの95%CI下限 > 0。
4. continuation--fade対応日次差CI下限 > 0。
5. continuation--lagged-sign placebo対応日次差CI下限 > 0。

主AND不成立は`REJECT`。成立しても、全固定感度Net>0、R正負ともNet>0、2021--2024の少なくとも3年と2025H1が正、top-10勝ち取引除外後Net>0を満たさなければ`INVESTIGATE`。全て満たしてもDevelopment再利用と結果選定により上限は`INVESTIGATE`である。

## 固定感度・保存

感度はsignal終点09:44/10:29、entry追加1本遅延、exit14:15/14:45、`|R|<=1 tick`の事前固定除外、片道2/3 tick、手数料2倍のみである。年別、R正負別、R絶対値の五分位別（全主実行可能eventを`abs(R), trade_date`昇順の固定順位で5等分する記述分解）、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。各profileのevent/orders/fills/trades、予定日次軸、metrics、MBB、実行・会計・因果性監査、input/code/config hashを不変成果物として保存する。
