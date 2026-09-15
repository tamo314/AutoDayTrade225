# TASK-R088-Q002: 同程度の寄付き後60分変位内の相対的経路効率と day 後半継続

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_PNL**  
対象: Development のみ（trade_date 2021-01-01--2025-06-30）

## Q001 監査と、この一度だけの改訂

Q001 の正式な PnL-free 成果物を監査する。Q001 は PnL、return、勝敗、PF、順位、bootstrap、感度又は対照損益を一切取得していない。Q002 も、下記 PnL 前ゲートを通過するまでこれらを取得しない。

Q001 の日別 M/E、strict-prior 閾値、ラベルを保存して、HL=1 の原因を判定する。実装不具合、時刻対応誤り、分位計算誤り、又は説明不能欠測なら PnL 前で **INCONCLUSIVE** とする。これらがなく、大変位と経路効率の構造的依存によって無条件 HL の共通支持が消失したことだけが原因の場合に限り、本 Q002 を実行する。

改訂仮説は、**同程度の寄付き後60分変位を持つ日々の中でも相対的に高効率な価格経路は、後半の継続を予測する**、である。これは Q001 の結果に基づく一回限りの条件付き再登録であり、Development の反復利用を記録する。いかなる結果でも判定上限は **INVESTIGATE**。Walk Forward、OOS、2026-01-01以後の Final Holdout には進まない。

## 固定した定義と売買仕様

Q001 から `p0...p60`、`M=10,000*abs(p60-p0)/p0`、`L=sum(abs(pk-p(k-1)), k=1..60)`、`E=abs(p60-p0)/L`、DAY隔離、適格性、欠損、L=0、M=0、09:59 close 観測後の次の適格 bar open entry、最大10分遅延、14:55 open exit、1枚、1日1取引、最大1ポジション、Stop/Target/re-entryなし、片道1 tick+30円を一切変更しない。

各 d の直前 **120予定 trade_date** の有効な M/E 値だけを古い順に使い、current を除外し、100個以上を要求する。ここから nearest-rank `ceil(n*p/100)-1` により `q60(M)` と `q80(M)` を計算する。`M_d >= q60(M)` の日だけを条件付き効率の対象とする。現在日の E は使わず、上記の過去有効日を `abs(M_i-M_d)` 昇順、同距離なら新しい `trade_date` 順に並べた先頭60件を変位比較集合とする。その集合の E の nearest-rank `q30/q70` を計算して、`CHE=E_d>=q70`、`CHL=E_d<=q30` とする。等値は双方に含める。

因果的 M 帯は `B1=[q60,q80)`、`B2=[q80,infinity)` とする。CHE/CHL と帯は、09:59 時点で利用可能な値だけで確定する。CHE と CHL のいずれも `sign(p60-p0)` 方向へ入る。

## PnL 前ゲート

説明不能除外0件、CHE/CHL各90件以上、CHEの上昇・下落各30件以上、2021--2024 各年CHE12件以上、2025H1 CHE6件以上、さらに B1/B2 の双方でCHE/CHL各20件以上を要求する。不足又は監査不合格なら **INCONCLUSIVE** として PnL を取得せず停止する。

## 評価、対照、感度、判定

主対照は予定 trade_date 軸の無取引0円、同一CHE event・entry・exitの逆方向 paired fade、CHL の同方向 continuation である。CHE/CHL の M 分布を保存する。B1/B2別に CHE を標準母集団とする固定重み付き取引当たり平均差を算出する。

全予定 trade_date を共通系列に置き非取引日を0円とし、20 trade_date non-wrapping moving-block bootstrap（tail truncation、10,000回、seed=20260915、linear percentile）を実行する。同一再標本 block で日次成績と帯別 ratio estimator を評価する。主ANDは CHE の Net>0、PF>1、平均日次Net 95%CI下限>0、CHE-paired-fade 対応日次差CI下限>0、CHE-CHLのM帯標準化平均差CI下限>0 とする。

固定感度は観測窓30/90分、M cutoff q50/q70、条件付きE cutoff q60/q80、参照80/160日、比較集合40/80件、entry追加1本遅延、exit14:30/15:10、片道2/3 tick、手数料2倍で、一度に一要素だけ変更する。年別、上昇・下落別、B1/B2別、E五分位別、取引発生率、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。

主AND不成立は **REJECT**。成立後も、全固定感度Net>0、両方向・両M帯がNet>0、2021--2024の少なくとも3年と2025H1が正、top-10除外後Net>0でなければ **INVESTIGATE**。全条件を満たしても上限は **INVESTIGATE**。結果依存の閾値・比較集合・時刻選択、Walk Forward、OOS、Final Holdoutには進まない。
