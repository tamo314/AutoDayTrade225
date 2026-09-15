# TASK-R087-Q002: 複数DAY方向累積×寄付き15分acceptance継続 — PnL-blind S2改訂

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_PNL**  
対象: Developmentのみ（state構築はtrade_date 2021-01-01～2025-06-30、評価可能期間は下記S2監査で因果的に確定する日から2025-06-30）

## 親登録との同一性とPnL隔離

親登録は [R087-Q001](67_r087_q001_multiday_day_trend_acceptance.md) である。本Q002は、PnL、return、勝敗、PF、順位、bootstrap、感度、年別損益、対照損益を**一切取得しない**状態で、Q001のPnL-free S2が示したラベル別実行可能件数だけ（EA=129、EO=126、MA=147）を根拠に別IDで再登録する。

仮説、DAY return `r`、直前10予定trade_dateの非補充T、current-excludedの直前120予定trade_date・最低100有効T・nearest-rank q30/q70、09:00--09:14 P、EA/EO/MA、09:14確定後next-eligible open entry、14:55 open exit、1枚・1日1取引・最大1ポジション・Stop/Target/re-entryなし、R004隔離、基本費用（片道1 tick＋30円）、scheduled-axis無取引0円対照、paired fade、EO/MA対照、20 trade_date non-wrapping MBB（tail truncation、10,000回、seed=20260915、linear percentile）、主AND、全固定感度（5/20、q60/q80、reference 80/160の5/6有効率、confirmation 5/30分、entry 1本遅延、exit 14:30/15:10、2/3 tick、手数料2倍）は、Q001と完全に同一である。OOS、Walk Forward、2026-01-01以降のFinal Holdoutは読取も実行もしない。

## PnL前監査とQ002で唯一置換するS2

PnL前に、利用可能データの開始timestamp/trade_date、最初の有効`r`、最初の有効`T`、最初に因果的に確定するq30/q70、EA/EO/MAの全trade_date、及び2021年EA=0の原因を保存する。コード不具合、誤ったtrade_date対応、又は説明不能な欠測なら、PnLを取得せず**INCONCLUSIVE**で停止する。

開始データと固定参照窓から必然となる因果的ウォームアップだけ（既知のR004隔離を含む、古い有効値への補充なし）が原因であること、説明不能除外0件、因果性監査全通過を要求する。この場合に限り、評価可能期間を最初にq30/q70が因果的に確定するtrade_dateから固定する。

このS2以外はQ001から変更しない。評価可能期間について、説明不能除外0件、EA>=120、EO>=90、MA>=140、EAのT正負各35件以上、2022～2024各年EA15件以上、2025H1 EA8件以上を全て要求する。不足又は監査不合格なら**INCONCLUSIVE**でPnL前に停止する。改訂値は既知の成績ではなく、上記EA=129/EO=126/MA=147のラベル別件数だけに基づく。

## 判定

S2通過後だけ、Q001と同じ主ANDと全固定感度を実行する。主AND不成立は**REJECT**。主AND成立後も、全固定感度Net>0、EAのT正負ともNet>0、2022～2024の少なくとも2年と2025H1が正、top-10勝ち取引除外後Net>0のいずれかを満たさなければ**INVESTIGATE**とする。全条件を満たしてもDevelopment反復利用のため上限は**INVESTIGATE**である。結果依存の仕様変更、救済探索、Walk Forward、OOS、Final Holdoutには進まない。
