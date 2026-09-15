# R091-Q001: 公式TSE現物取引時間の固定short（事前登録）

タスクID: `TASK-R091-Q001`。登録日: 2026-09-15 JST。Developmentの価格・return・PnLへアクセスする前に凍結する。判定の上限は `INVESTIGATE` であり、Walk Forward、2025H2 OOS、Final Holdoutへ進まない。

仮説は「公式TSE現物取引時間中には負の無条件リターンがあり、fixed shortが費用後期待値を持つ」である。主Aは公式TSE営業日ごとに1枚short、Bは同一日・同一時刻の1枚long、Cは予定軸の無取引0円である。価格、曜日、Stop、Target、re-entry、途中更新、価格由来の選別は使わない。

予定軸Uは、保存済み公式TSE営業日表のDevelopment `trade_date=2021-01-01..2025-06-30` のみである。09:00 bar openへの時刻注文は08:59にsubmit/decisionし、TSE現物終了Tのbar openで決済する。Tは観測行ではなく版管理した制度表から、2024-11-01まで15:00、2024-11-05以降15:30とする。entry/exitのsignalは各々一分前、fillは指定bar openである。

E_execは09:00のdecision/fill可用性だけで確定する。後刻Tのbar欠損はentryを取消さず、既約定建玉を`UNRESOLVED_EXIT_*`、日次PnLをnullとして停止理由にする。R004はwhole-day隔離を再現して監査するが、除外理由を明示台帳に残す。PnL前のANDは、説明不能除外0、未完了建玉0、A/B各900以上、2021--2024各年180以上、2025H1 80以上、旧制度700以上、新制度100以上である。不成立ならPnLを取得せず`INCONCLUSIVE`とする。

基準は片道1 tick + 片道30円、`Net=Gross-fees`、slippage二重控除なし、1枚・最大1ポジションである。ゲート通過後にのみ、Aの固定1分entry遅延（exit非延長）、09:05 entry、T-5分exit、片道2/3 tick、手数料2倍を実行する。20 trade_date非循環MBBを10,000回、seed `20260915`、共通index、末尾切詰め、linear percentileでA日次Netとpaired A-B差を評価する。

主ANDはA Net>0、PF>1、A日次Net CI下限>0、A-B差CI下限>0。いずれか未達なら`REJECT`。全通過しても、2 tick・手数料2倍・両entry感度・T-5 exitがすべてNet>0、旧新両制度Net>0、2021--2024の少なくとも3年と2025H1が正、top-10勝ち取引除外後Net>0の全てを満たさない限り`INVESTIGATE`にとどめる。3 tickは必須診断として保存する。

既知情報として、R001--R090を照合する。R066-Q001は09:00--14:30 fixed longで、同じ日中無条件方向familyだが、今回のTSE終了T（旧15:00/新15:30）とはexitが異なる。R070-Q001はナイト16:30--05:30 fixed longであり、既知結果はREJECTである。いずれもDevelopment反復利用の既知情報であり、本研究を独立検証とは主張しない。
