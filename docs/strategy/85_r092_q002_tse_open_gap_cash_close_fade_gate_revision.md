# R092-Q002: R092-Q001 の2021初期化部分期ゲート改訂（事前登録）

タスクID: `TASK-R092-Q002`。登録日: 2026-09-15 JST。これはR092-Q001を置換せず、そのPnL前情報量ゲートだけを一度だけ改訂するDevelopment限定の実行仕様である。判定の上限は **INVESTIGATE** とし、Walk Forward、2025H2 OOS、2026-01-01以降のFinal Holdoutは実行・参照しない。

## Q001の保存状態と改訂根拠

Q001はPnL前ゲートで `INCONCLUSIVE` となった。Q001から既知として引き継ぐのは、primary complete Eが2021=17、2022=65、2023=60、2024=66、2025H1=32であったこと、および2021の最低30件だけが未達だったことである。**Q001では価格return、PnL、PF、bootstrap、trade ledger、感度成績、利益集中度を一切取得していない。**

Q002では価格成績へ進む前に、2021年について以下をPnL非開示で再監査する。直前120予定TSE営業日（当日除外、補充なし）と最低100有効xという凍結済みwarm-up、q-ready開始日、月別E件数、理由別除外を保存する。R004隔離を有効xへ混入させないこと、予定日軸整合、開始境界又は直前R004隔離によるtarget-gap無効以外の欠測がないこと、entry選択がexit可用性に依存しないことも監査する。

この監査が不合格ならPnLを取得せず `INCONCLUSIVE` で停止する。通過した場合に限り、Q001の仮説、gap定義、q75、方向、時刻、対照、費用、bootstrap、全固定感度、主AND、頑健性条件を完全に固定する。変更は次のPnL前ゲートのみである。

- 2021年をinitialization partial periodとしてA/B/C/D各15件以上とする。
- 2021年E件数を、q-readyかつ`g!=0`の日数に対して20--30%とする。
- 2022--2024は各年30件以上、2025H1は15件以上とする。
- 総数、gap方向、制度別、説明不能除外、entry後未完了、その他すべてのQ001ゲートは変更しない。

## 完全に固定する売買・統計仕様

仮説は「前回公式TSE通常session終値から当日09:00始値までの因果的に大きいgapは、cash session終了までに反転し、費用後期待値を持つ」である。予定日軸は保存済み公式TSE営業日表のDevelopment `trade_date=2021-01-01..2025-06-30` のみであり、R004 whole-session tick-grid隔離日は説明付き無注文として残す。

R057/Q001と同一の価格定義を使う。`p` は対応する直前TSE通常session最終予定1分足のclose、`o` は当日09:00予定足open、`g=(o-p)/p`、`x=abs(g)`。対象日を含まない直前120予定TSE営業日の有効xだけを補充なしで用い、有効100件以上ならnearest-rank `q75`（等号上側）を得る。`E` は`g!=0 and x>=q75`。価格、曜日、gap fill、Stop、Target、re-entry、途中更新、09:00以後の確認はfilterに使わない。

`E_exec` は09:00足確定後の情報だけで確定する。09:00 closeでdecision/submitし、09:01 openに1枚fillする。公式現物終了`T`のopenで固定決済する（2024-11-01まで15:00、2024-11-05以降15:30）。既約定でexit不明なら`UNRESOLVED_EXIT_*`、日次PnL nullとしてPnL前に停止する。A=gap-fade（`-sign(g)`）、B=gap-continuation（`sign(g)`）、C=fixed long、D=fixed short、N=no trade JPY0であり、A--Dは同一E・entry・exit、1日最大1取引・最大1ポジションとする。

基本費用は片道1 tick+片道30円、`Net=Gross-fees`、slippageは二重控除しない。通過後のみ`q70/q80`、09:02/09:05 entry（exit非延長）、T-5分exit、片道2/3 tick、手数料2倍を固定感度として実行する。共通予定日軸の20 `trade_date`非循環moving-block bootstrapを10,000回、`seed=20260915`、全条件共通index、末尾切詰め、linear percentileでA日次Netとpaired A-B/A-C/A-D差を評価する。

主ANDはA Net>0、PF>1、A日次Net CI下限>0、A-B/A-C/A-Dの各CI下限>0。一つでも未達なら **REJECT**。全て成立しても、q70/q80、両entry、T-5 exit、2 tick、手数料2倍が全てNet>0、gap-up/down双方と旧新制度双方がNet>0、2021--2024の少なくとも3年と2025H1が正、top-10勝ち取引除外後Net>0を全て満たさなければ **INVESTIGATE** とする。満たしても上限は **INVESTIGATE**。結果依存の救済変更、追加時間・閾値探索、Walk Forward、OOS、Final Holdoutには進まない。
