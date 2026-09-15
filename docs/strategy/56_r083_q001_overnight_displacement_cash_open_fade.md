# TASK-R083-Q001: Overnight displacement cash-open fade（事前登録）

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Developmentのみ（trade_date 2021-01-01～2025-06-30）

## 仮説と範囲

前trade_dateのday-session終値から当日cash openまでの極端なovernight displacementは、夜間参加者の一時的な在庫・流動性不均衡を含み、東京現物参加者の参入後に午前中反転する。

これはR078～R082のcontinuation時刻救済ではない。night-to-cash参加者移管に基づく、固定された反転仮説である。既知のDevelopmentを反復利用しているため、いかなる結果でも判定上限はINVESTIGATEとする。OOS、Walk Forward、2026-01-01以降のFinal Holdoutは読取も実行もしない。

## 固定した因果的定義

- 版管理された取引時間 `config/sessions.yaml` により、各trade_dateの直前trade_dateの最終day-session境界bar closeを **D**、当日09:00 bar openを **A** とする。該当barはeligible、正価格、対応するtrade_date/sessionでなければならない。最終境界barの欠損を先行barで補完しない。
- `G=A-D`。`G=0`、D/A不在・非eligible、直前trade_date連鎖不整合、または既存R004 whole-session隔離対象の前日又は当日day-sessionは状態作成・発注対象から除外する。
- 直前60**適格**trade_dateの非zero `|G|` のみを、current-excluded、oldest-to-newestのdequeで保持する。nearest-rankは `ceil(n*p/100)-1`（補間なし）。60未満は状態なしであり、古い値によるbackfillはしない。
- `q20`、`q80` を同じ過去集合から計算し、`E: |G| >= q80`、`M: q20 < |G| < q80` とする。`q20>=q80` は状態なし。境界のいずれにも属さない日も状態なし。
- E日に限り、Aを観測した後の最初の適格bar open（通常09:01）でGと逆方向に1枚入り、11:30 openで決済する。1日1取引、最大1ポジション、Stop・Target・再entryなし。night内の経路、09:00以後の方向、gap以外の特徴は一切使用しない。
- entryに必要なsignal/next-openが不在なら未約定0円。entry後に固定exitが不明なら既約定結果はnullであり、entryを遡及取消しない。

## PnL前S2ゲート

PnL、return、勝敗、PF、順位、bootstrapを計算する前に、取引時間版、D/A対応、trade_date境界、rolling分位の過去情報限定を台帳・合成テスト・実行監査で確認する。次をすべて要求する。

- 説明不能除外 0件
- E実行可能件数 180件以上
- EのG正・負が各70件以上
- 2021～2024の各年が30件以上
- 2025H1が15件以上

不足又は監査不合格なら **INCONCLUSIVE** としてPnL取得前に停止する。

## 固定した会計、対照、推論

- 基本費用は片道1 tickと片道30円。1枚、既存engineの翌適格bar約定を使用し、slippageを二重控除しない。
- 主対照は、(1) 予定trade_date軸上の無取引0円、(2) 同一E日・同一entry/exitのG方向continuation、(3) M日に同じfade規則を適用するmagnitude-control。
- MBBは20 trade_dateのnon-wrapping block、tail truncation、10,000反復、seed 20260915、linear percentile 95% CIとする。E-fade日次NetとE-fade−E-continuationは予定軸平均、E-fade−M-fadeは各状態の取引当たりNet差とする。共通block indexを保存する。
- 主AND: E-fade Net>0、PF>1、E-fade日次Net MBB 95%CI下限>0、E-fade−E-continuation対応日次差CI下限>0、E-fade−M-fade取引当たりblock-bootstrap差CI下限>0。

## 固定感度・記述保存・判定

- 感度は q70/q90、参照40/80日、entryを09:05以後の最初の適格bar openへ遅延、exit 10:30/14:30、片道2/3 tick、手数料2倍に限定する。それ以外の結果依存探索はしない。
- E-fadeについて年別、G正負別、`|G|`五分位別、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。部分群は救済選択に使わない。
- 主AND不成立は **REJECT**。成立しても、全固定感度Net>0、G正負ともNet>0、2021～2024の少なくとも3年と2025H1が正、top-10除外後Net>0を満たさなければ **INVESTIGATE**。全て満たしてもDevelopment反復利用のため **INVESTIGATE**。

