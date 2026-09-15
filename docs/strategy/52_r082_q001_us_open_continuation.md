# TASK-R082-Q001: US cash-open N225 night-session continuation

状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**  
登録日: **2026-09-15 JST**  
family_id: `us_cash_open_n225_night_price_discovery` / study_id: `R082-Q001` / spec_version: `v1`  
対象: Development trade_date 2021-01-01--2025-06-30 のみ

## 仮説と固定範囲

米国現物株市場の開始直後に日経225先物へ反映された方向は、米国時間前半の共通リスク情報を表し、その後も同方向へ価格発見が続く。Developmentは既知の探索期間なので`prior_information_seen=true`である。本仕様は結果による時刻、保有時間、強度の探索を許可しない。Walk Forward、OOS、2026年以降のFinal Holdoutには進まない。

版固定NYSE calendar `config/calendars/nyse_regular_trading_days_2021_2025h1_v1.yaml` と `America/New_York` を用いる。同ファイルのcoverage内で週末・closed_dates以外をNYSE通常取引日とする。早期終了日は09:30 ETの通常開始を持つため取引日である。DSTはJST時刻の手入力で近似せず、zoneinfoで09:30 ETをJSTへ変換する。

各N225 scheduled Development trade_dateのnight sessionに含まれるNYSE通常取引日09:30 ETをSとする。Sの1分bar openをA、S+29分bar closeをB、U=B-Aとする。R004 whole-session tick-grid隔離対象でなく、S/S+29の必要barがeligible、同一trade_date/night session、正値で、U!=0の日だけを候補とする。day-session、gap、night開始後からS以前の方向、S+30分以後の情報、`|U|`による選別は使わない。

B観測後の最初の適格bar open（S+30分）でU方向に1枚入り、S+180分bar openで決済する。1日1取引、最大1ポジション、Stop・Target・re-entryなし。entry signal又はnext open不適格なら約定前取消で予定日次軸0円、既約定後のexit不明はnullとしてPnL判定を停止する。費用は片道1 tick + 片道30円である。

## PnL前ゲート

カレンダーのID/SHA-256、NYSE日とN225 trade_date対応、DST境界日のS、Sが版管理N225 night session内であることを保存・監査する。説明不能除外0、実行可能850以上、U正負各300以上、2021--2024各年140以上、2025H1 60以上が必須。不一致又は不足ならreturn/PF/bootstrapを算出せず`INCONCLUSIVE`で停止する。

pre-open-sign controlはP=S-30分bar openからS-1分bar closeの変位の符号で、主strategyを変更せず、U!=0かつP!=0の同一日集合だけで同一entry/exitに建てる。その集合件数とU/P符号不一致数をPnL前に保存する。

## 評価、対照、判定

主対照は予定日次軸の無取引0円、同日同entry/exitのU逆方向fade、及び上記共通日集合のpre-open-sign control。20 trade_date非循環moving-block bootstrap（tail truncation、linear percentile、10,000回、seed=20260915）で、continuation Net>0、PF>1、continuation予定日次平均Netの95%CI下限>0、continuation--fade対応日次差CI下限>0、continuation--pre-open-sign control対応日次差CI下限>0の全てを要求する。主AND不成立は`REJECT`。

感度はsignal長15分/45分、entry追加1本遅延、exit S+120分/S+240分、片道2/3 tick、手数料2倍だけ。年別、U正負別、米国DST/標準時間別、Pとの同符号/逆符号別、`abs(U), trade_date`昇順の固定順位五分位、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。主AND成立後も、全固定感度Net>0、U正負・DST/標準時間ともNet>0、2021--2024の少なくとも3年と2025H1が正、top-10除外後Net>0を満たさなければ`INVESTIGATE`。満たしてもDevelopment反復利用のため上限は`INVESTIGATE`。
