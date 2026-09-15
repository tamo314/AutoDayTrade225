# R092-Q001: 前回TSE通常session終値から09:00始値への大幅gapのcash-close fade（事前登録）

タスクID: `TASK-R092-Q001`。登録日: 2026-09-15 JST。これはDevelopmentの価格・return・PnLへアクセスする前に凍結する、1回限りの実行仕様である。判定の上限は **INVESTIGATE** とし、Walk Forward、2025H2 OOS、2026-01-01以降のFinal Holdoutは実行・参照しない。

## 既知情報・重複

R001--R091をPnLアクセス前に照合した。R006-Q001はOSE night終了とday開始の整数point gap反転であり既知 **REJECT**、R057-Q001は同じ「前回TSE通常session終値--当日09:00始値gap」familyに15分acceptanceを加えた短期追随で既知 **REJECT**、R091-Q001は09:00--公式TSE現物終了の無条件fixed shortで既知 **REJECT** である。本件はR057と同じgap familyへの、既知Development結果を見た後の事後追加仮説である。R057の定義・結果・実装を変更せず、R057が持つ15分確認、短期holding、acceptance/continuationの選別を使わない。

R092は、同じclose/open gapを使うため独立仮説・未使用検証とは主張しない。Developmentは反復利用済みである。入力は225Labo由来の正規化済みParquet連続系列に限定し、実限月を捏造せず、連続系列の価格解釈・執行可能性を主張しない。

## 固定仮説と集合

仮説: 「前回公式TSE通常session終値から当日09:00始値までの因果的に大きいgapは、cash session終了までに反転し、費用後期待値を持つ」。予定日軸は保存済み公式TSE営業日表のDevelopment `trade_date=2021-01-01..2025-06-30` のみである。R004 whole-session tick-grid隔離日は説明付き無注文として残す。

R057と同一の価格定義を使う。各対象日について、`p` は予定表で対応させた直前TSE通常sessionの最終予定1分足の `close`、`o` は当日09:00予定足の `open`、`g=(o-p)/p`、`x=abs(g)` とする。対象日を含まない直前120予定TSE営業日の有効な `x` だけを補充なしで用い、有効100件以上ならnearest-rank `q75`（等号は上側）を得る。`E` は `g!=0 and x>=q75` である。price、曜日、gap fill、Stop、Target、re-entry、途中更新、09:00以後の確認は一切filterに使わない。

`E_exec` は09:00足確定後の情報だけで確定する。09:00 closeでdecision/submitし、09:01 openに1枚fillする。公式現物終了 `T` のopenで固定決済する（2024-11-01まで15:00、2024-11-05以降15:30）。`T`の将来可用性はentry取消へ使わず、既約定でexit不明なら`UNRESOLVED_EXIT_*`、日次PnL nullとしてPnL前に停止する。

条件は同一E・entry・exitで、A=gap-fade（`-sign(g)`）、B=gap-continuation（`sign(g)`）、C=fixed long、D=fixed short、N=no trade JPY0である。1日最大1取引・最大1ポジションである。

## ゲート・費用・判定

PnL前ANDは、説明不能除外0、entry後未完了0、A/B/C/D各200取引以上、gap-up/down各80件以上、2021--2024各年30件以上、2025H1 15件以上、旧制度180件以上、新制度20件以上である。不具合、漏洩、不整合、件数不足はPnLを取得せず`INCONCLUSIVE`で停止する。

基本費用は片道1 tick+片道30円で、`Net=Gross-fees`、slippageは二重控除しない。ゲート通過後のみ、`q70/q80`、09:02/09:05 entry（exitを延長しない）、T-5分exit、片道2/3 tick、手数料2倍を固定感度として実行する。3 tickは必須診断である。

共通予定日軸の20 `trade_date`非循環moving-block bootstrapを10,000回、`seed=20260915`、全条件共通index、末尾切詰め、linear percentileでA日次Netとpaired A-B/A-C/A-D差を評価する。主ANDはA Net>0、PF>1、A日次Net CI下限>0、A-B/A-C/A-Dの各CI下限>0である。一つでも未達なら**REJECT**。全て成立しても、q70/q80、両entry、T-5 exit、2 tick、手数料2倍が全てNet>0、gap-up/down双方と旧新制度双方がNet>0、2021--2024の少なくとも3年と2025H1が正、top-10勝ち取引除外後Net>0を全て満たさなければ**INVESTIGATE**とする。

結果依存の片側gap選択、閾値・時刻・exit変更、Walk Forward、OOS、Final Holdoutには進まない。
