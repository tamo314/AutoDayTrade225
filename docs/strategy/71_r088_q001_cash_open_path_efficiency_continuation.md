# TASK-R088-Q001: 現物寄付き後60分の経路効率とday後半の継続

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Development のみ（trade_date 2021-01-01--2025-06-30）

## 仮説と独立性

現物寄付き後60分の大きな方向変位が、細かな往復を伴わず高い経路効率で形成された日は、分割執行された注文フローが残存し、day-session後半も同方向へ継続する。本件は R087 の複数日変位や既存の単一変位規則の救済ではなく、変位量を共通化したうえで経路効率の増分識別力を検査する別機序である。

night、寄付き前gap、出来高、09:59以後の情報、M/E以外の選別は使わない。Development は反復利用済みなので、どの結果でも判定上限は **INVESTIGATE** である。Walk Forward、OOS（2025H2）、2026-01-01以降の Final Holdout を読取・実行しない。

## 凍結した因果的定義

- 版管理カレンダーの Development 全予定 trade_date を母集団とする。各 d で `p0` は 09:00 bar open、`p1`--`p60` は 09:00--09:59 の各 bar close とする。対象60本の各barが同一 `trade_date`・DAY・適格・正価格で、09:00--09:59 が連続する公式DAY取引区間内にあり、DAY session が隔離されていなければ有効である。欠損、隔離、非連続、又は `L=0` は無効であり古い有効日で補充しない。
- `M=10,000*abs(p60-p0)/p0`、`L=sum(abs(pk-p(k-1)), k=1..60)`、`E=abs(p60-p0)/L` を計算する。M=0 は有効な履歴値だが発注対象外である。全 `p0..p60`、Lの60項、制度版、無効理由を保存する。
- d の直前**120予定trade_date**だけから、M/Eがともに有効な日を古い順に取る。current は含めず、最低100件を要求する。nearest-rank `ceil(n*p/100)-1`（補間なし）で `q60(M)`, `q30(E)`, `q70(E)` を計算する。`HE = M>=q60(M) and E>=q70(E)`、`HL = M>=q60(M) and E<=q30(E)` とする。等値は指定どおり各条件に含め、両方成立した場合も両conditionを台帳に明記する。
- HE は09:59 close観測後、次の適格bar openで `sign(p60-p0)` 方向に1枚入り、14:55 bar openで決済する。最大10分の既存next-eligible遅延だけを許す。1日最大1取引・最大1ポジション、Stop/Target/re-entryなし。entry後に固定exitが不明なら遡及取消せずunknownとして主集計を停止する。

## PnL前監査と可用性ゲート

PnL前に p0--p60の時刻対応、L全項、直前120予定日だけのrolling分位（current除外）、翌適格bar約定、固定exit、欠損・隔離規則を監査する。説明不能除外0件、HE/HL各90取引以上、HEの上昇・下落各30件以上、2021--2024各年HE12件以上、2025H1 HE6件以上を要求する。不足又は監査不合格なら **INCONCLUSIVE** として PnL前に停止する。

## 会計、対照、推論

基本費用は片道1 tick＋片道30円、1枚である。主対照は共通予定trade_date軸の無取引0円、同一HE event・entry・exitの逆方向paired fade、及びHLで同じ変位方向へ入るmagnitude-controlである。

HE/HLのM分布と、各eventのstrict-prior M referenceから `q20/q40/q60/q80` で付与する因果的M五分位別件数を保存する。共通支持のM五分位だけを用い、HEの当該五分位取引比率を固定重みとして `sum(wq*(mean(HE,q)-mean(HL,q)))` を標準化取引当たり平均差とする。全HE五分位がHLにも存在しない場合、このprimary comparisonは不成立である。

全予定trade_dateを共通系列に置き非取引日を0円として、20 trade_date non-wrapping moving-block bootstrap（tail truncation、10,000回、seed `20260915`、linear percentile 95% CI）を行う。標準化差は各同一block再標本内で群別・五分位別比率を再計算し、空cell反復は捨てず保存する。主ANDはHEのNet>0、PF>1、平均日次Net CI下限>0、HE-paired-fade対応日次差CI下限>0、HE-HL標準化取引当たり平均差CI下限>0である。

## 固定感度、保存、判定

一度に一要素だけ変える固定感度は、観測窓30/90分、M cutoff q50/q70、E cutoff q60/q80（下側は補完分位q40/q20）、参照80/160予定日、entry追加1本遅延、exit14:30/15:10、片道2/3 tick、手数料2倍である。参照80/160の有効最小件数は主100/120と同じ5/6比率で67/134とする。他は主規則のままである。

HEについて年別、上昇・下落別、E五分位別、M五分位別、取引発生率、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。部分群は救済選択に使わない。

主AND不成立は **REJECT**。成立しても全固定感度Net>0、上昇・下落ともNet>0、2021--2024の少なくとも3年と2025H1が正、top-10除外後Net>0を満たさなければ **INVESTIGATE**。全条件を満たしても Development 反復利用のため **INVESTIGATE** が上限である。結果依存の時刻窓・効率閾値・変位閾値・exit探索、Walk Forward、OOS、Final Holdoutには進まない。
