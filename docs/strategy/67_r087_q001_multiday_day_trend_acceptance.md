# TASK-R087-Q001: 複数day-session方向累積と現物寄付きacceptance後の継続

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Developmentのみ（trade_date 2021-01-01～2025-06-30）

## 仮説と独立性

過去複数日のDAY sessionで同方向の価格変化が累積した局面では、機関投資家の注文分割が残存する。当日現物寄付き後15分も同方向なら、14:55まで価格発見が継続する。本件は既存の短期shock、gap、range規則の救済ではなく、複数日にわたるday-session注文分割と当日寄付きacceptanceの交互作用を検査する別機序である。

night、session間gap、当日09:15以後の情報、出来高、将来の最大変動、T/P以外の選別は使わない。既知Developmentの反復利用であるため、いかなる結果でも判定上限は**INVESTIGATE**である。Walk Forward、OOS、2026-01-01以降のFinal Holdoutは読取・実行しない。

## 凍結した因果的定義

- 予定軸は版管理`local_calendar.yaml`のDevelopment trade_date全日である。各日iの版管理DAY session内で、最初の適格barのopenをO_i、最後の適格barのcloseをC_iとし、いずれも正価格・同trade_date・DAY・非隔離なら `r_i=10,000×(C_i−O_i)/O_i` とする。O/Cのbar timestamp、DAY schedule version、session境界を保存する。
- 各dのTは直前10**予定**trade_dateだけを古い順に取り、全10日のrが有効かつ非隔離なら `T_d=Σr_i` とする。欠損・隔離日は古い有効日で補充しない。T=0は有効な参照値として保持するが、発注対象にはしない。
- dより前の正確に120予定trade_dateに属する有効Tだけを参照し、最低100件を必要とする。currentは参照に含めず、120日窓の外の古い有効Tで補充しない。`|T|`のnearest-rank `ceil(n×p/100)-1`（補間なし）でq30/q70を求める。E=`|T|>=q70`、M=`q30<|T|<q70`である。
- 当日09:00 bar open Aと09:14 bar close Bで `P=10,000×(B−A)/A` を計算する。P≠0かつT≠0のとき、EA=Eかつsign(P)=sign(T)、EO=Eかつ符号不一致、MA=Mかつ符号一致とする。その他は無取引である。
- 09:14 close観測後の次の適格bar openでsign(P)方向に1枚入り、14:55 bar openで決済する。最大10分の既存next-eligible遅延だけを許す。1日最大1取引・最大1ポジション、Stop・Target・re-entryなし。entry後exitが不明なら遡及取消せずunknownとし主集計を停止する。

## PnL前監査・情報量ゲート

PnL前に取引時間制度版、O/C対応、直前10予定日非補充、120予定日参照、当日・将来情報の排除、09:14 signal、翌適格bar entry、固定exit、欠損・隔離規則を監査する。説明不能除外0件、EA/EO各90取引以上、MA150取引以上、EAのT正負各35件以上、2021～2024各年EA15件以上、2025H1 EA8件以上を全て要求する。不足又は監査不合格なら**INCONCLUSIVE**でPnL前に停止する。

## 会計・対照・推論

基本費用は片道1 tick＋片道30円、1枚である。主対照は共通予定trade_date軸の無取引0円、同一EA event・entry・exitで−sign(P)を取るpaired fade、EOでsign(P)継続を取るalignment-control、MAで同じ継続規則を取るtrend-strength controlとする。

全予定trade_dateの共通系列に非取引日を0円として保存する。20 trade_date non-wrapping moving-block bootstrap、tail truncation、10,000反復、seed `20260915`、linear percentile 95% CIを行う。EA対EO/MAは同じ再標本blockで、群別の日次損益和÷群別取引数の差をratio estimatorとする。主ANDはEAのNet>0、PF>1、平均日次Net CI下限>0、EA−paired-fade対応日次差CI下限>0、EA−EO取引当たり差CI下限>0、EA−MA取引当たり差CI下限>0である。

## 固定感度・保存・判定

固定感度は累積期間5/20予定日、強度cutoff q60/q80、参照80/160予定日、寄付き確認5/30分、entry追加1本遅延、exit14:30/15:10、片道2/3 tick、手数料2倍だけである。参照80/160は主100/120と同じ5/6有効率を凍結し、それぞれ67/134有効Tを要求する。各感度は他の主規則を変えない。

EAについて年別、T正負別、`|T|`五分位別、取引発生率、最大DD、利益集中度、top-10勝ち取引除外後Netを保存し、EA/EO/MA各条件の成績も保存する。部分群を救済選択に使わない。

主AND不成立は**REJECT**。成立しても、全固定感度Net>0、T正負ともNet>0、2021～2024の少なくとも3年と2025H1が正、top-10除外後Net>0を満たさなければ**INVESTIGATE**とする。全条件を満たしてもDevelopment反復利用のため判定上限はINVESTIGATEである。結果依存の累積期間、閾値、確認時間、exit探索、Walk Forward、OOS、Final Holdoutへは進まない。
