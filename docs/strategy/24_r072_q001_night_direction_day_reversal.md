# R072-Q001: ナイト方向の日中反転

タスクID: `TASK-R072-Q001`  
family_id: `night_directional_inventory_reversal` / study_id: `R072-Q001` / spec_version: `v1`  
run_id: `r072-q001-20260915-night-direction-day-reversal-01` / protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

## 仮説と重複確認

仮説は一つだけである。版管理night sessionの16:30 bar openから05:30適格bar openまでの方向には一時的な在庫・センチメント偏りが含まれ、次のday sessionでは反転する。

R070はnight内の無条件固定long、R071はnight opening-range突破後のnight内継続であり、本件の全night方向を用いる次day 09:00--14:30反転ではない。R062はnight入力に加えTSE開始後15分の拒否・確認と閾値選別を持ち、本件にはない。本件は関連するcross-session directional familyとして履歴を連結し、Developmentが反復利用済みであることを隠さない（`prior_information_seen=true`）。

## 範囲、予定軸、因果的実行

- 入力は正規化済み`N225M` `center_continuous` 1分OHLC、版管理calendar/session、適格性のみ。volume、板、外部市場、day寄付き後の価格、閾値、Stop/Target、再entry、休場跨ぎ保有は使わない。
- `scheduled_axis`はDevelopment trade_date `2021-01-01..2025-06-30`の全版管理日。OOS（`2025-07-01..2025-12-31`）とFinal Holdout（`2026-01-01`以降）は読み取らない。集計年はcalendar_dateでなくtrade_dateである。
- night開始calendar_dateがtrade_dateの前日で、版管理night sessionが16:30と05:30を含む場合だけ対象とする。16:30 bar openを`p0`、05:30適格bar openを`pN`とし、`pN>p0`なら09:00に1枚sell、`pN<p0`なら1枚buy、等値は見送る。シグナルは05:30に固定する。
- 固定済みのsideはdayの08:59で価格を読まず時刻トリガーとして発注し、09:00適格bar openで1枚entryする。これは05:30の方向判定を再計算せず、day寄付き後の情報をselectionへ入れない既存bar-close/next-open契約上の実装である。14:29確定後に14:30適格bar openでexitする。entry前の欠損は約定前取消・0円、filled後のexit欠損はnullである。
- 1日最大1取引・最大1建玉で、day内で完結する。基準費用は片道1 adverse tick + 30円、`Net=gross_fill-fees`でslippageを二重控除しない。

## 対照、S2、推論、判定

- 主対照は予定軸上の無取引0円。機構対照（co-primary）は同一signal trade_date、同一entry/exitでnight方向へ入る`night_direction_momentum`である。対応日次差は`reversal Net - momentum Net`、分母は全scheduled trade_date（既知無取引0、unknown exit=null）。
- S2はPnLを計算・表示しない。entry/exit実行可能signalが900件以上、2021--2024各年180件以上、buy/sell各300件以上が必要で、未達なら`INCONCLUSIVE`としてS3・感度・OOSを実行しない。
- S2通過後にprimaryまたはmomentum対照にunknown filled exitが一件でもあれば`INCONCLUSIVE`。完全観測時の主ゲートは、reversalのNet>0、PF>1、seed `20260915`・20 trade-date non-wrapping MBB（10,000回、tail truncation、linear percentile）によるscheduled-axis日次平均Netの95% CI下限>0、及び対応日次`reversal-momentum`差の同CI下限>0である。主ゲート不通過は`REJECT`、通過しCandidate追加条件未達は`INVESTIGATE`。

## 事前固定感度とCandidate

感度は`night_end_0500`、`entry_0901`、`exit_1415`、`exit_1445`、`cost_2tick`、`cost_3tick`、`fee_x2`だけである。Candidateには主ゲートに加え、2tick Net>0、09:01 entry Net>0、三つの時刻軸（night終点、entry、exit）の全登録profileで平均Net>0（exit軸は14:15と14:45の両方）、buy/sell群双方Net>0、2021--2024の少なくとも3年及び2025H1でNet>0を必要とする。3tickと手数料2倍は記述ストレスとして保存し、Candidateの追加ANDではない。

## 再現性と成果物

価格成績読取り前に本書、runner、event helper、再利用adapter、合成test、configのSHA-256、成果物ID、bootstrap seedをmanifestへ固定する。runnerは新規の`results/research/r072-q001-20260915-night-direction-day-reversal-01/`だけに保存し、source/config/documentation snapshot、access ledger、S2 feasibility、event ledger、profile別trade/daily axis/metrics、MBB、execution/accounting audit、decisionを保存する。合成検証は前calendar_dateのnight、sign反転、等値見送り、05:00/09:01/exit感度、休場schedule、filled後exit欠損、day-only entry/exit、Net会計、bootstrapを対象とする。
