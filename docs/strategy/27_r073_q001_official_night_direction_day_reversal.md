# R073-Q001: 公式ナイト方向の日中反転

タスクID: `TASK-R073-Q001`  
family_id: `night_directional_inventory_reversal` / study_id: `R073-Q001` / spec_version: `v1`  
run_id: `r073-q001-20260915-official-night-direction-day-reversal-01` / protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

## 仮説、範囲、非重複

仮説は一つだけである。公式night session全体の方向には一時的な在庫・センチメント偏りが含まれ、同一`trade_date`の日中sessionで反転する。

R072-Q001の16:30固定開始は、2024-11-05制度変更後の17:00開始を公式night sessionとして扱えず、同制度期を予定窓から除外した。本登録は、版管理calendarが返す実際のnight開始（そのnightの`calendar_date`が2024-11-05より前なら16:30、同日以降なら17:00）を使う一点だけが異なる独立実装である。R072-Q001およびその成果物は変更・上書きしない。R070/R071/R062を含め、関連するcross-session directional familyの反復Development探索であり、`prior_information_seen=true`である。

## 固定した因果的ルール

- `scheduled_axis`はDevelopmentの全版管理`trade_date`（2021-01-01..2025-06-30）。集計・分割は`calendar_date`でなく`trade_date`。OOS（2025-07-01..2025-12-31）とFinal Holdout（2026-01-01以降）は物理I/Oを含め一切アクセスしない。
- `CalendarClassifier.session_open(trade_date, NIGHT)`で版管理された公式night開始bar openを`p0`とする。night終点05:30の適格bar openを`pN`とする。night開始が前calendar_dateでなく、公式night windowが存在せず、又は休場を跨ぐ場合は見送る。
- `pN>p0`なら05:30にsideを固定し、08:59は価格を読まない時刻トリガーとして、09:00適格bar openで1枚sellする。`pN<p0`なら1枚buy、同値なら見送る。14:29確定後、14:30適格bar openで決済する。
- 1日最大1取引、最大1建玉、day内完結。Stop/Target、day寄付き後の選択情報、変化幅閾値、再entry、休場跨ぎ保有は用いない。entry前の欠損は取消・予定軸0円、filled後exitの欠損はnullであり0円にしない。
- 基準費用は片道1 adverse tick + 30円。`Net=gross_fill-fees`でslippageを二重控除しない。

## 対照、S2、推論、判定

- 主対照は予定軸上の無取引0円。機構対照（co-primary）は同一signal `trade_date`、同一entry/exitでnight方向へ入る`night_direction_momentum`。対応差は日次`reversal Net - momentum Net`で、分母は全scheduled axis（既知無取引0、unknown exit=null）。
- S2はPnL、return、勝敗、成績順位を計算・表示しない。実行可能tradeが900件以上、2021--2024の各年180件以上、buy/sell各300件以上が必要。未達ならPnLを計算せず`INCONCLUSIVE`で停止する。
- S2通過後、primary又はmomentumにunknown filled exitが一件でもあれば`INCONCLUSIVE`。完全観測時の主ゲートは、reversalのNet>0、PF>1、seed `20260915`・20 `trade_date` non-wrapping MBB（10,000回、tail truncation、linear percentile）によるscheduled-axis日次平均Netの95% CI下限>0、かつ対応日次差の同CI下限>0。主ゲート未達は`REJECT`、主通過でCandidate追加条件未達は`INVESTIGATE`。

## 事前固定感度とCandidate

感度は`night_end_0500`、`entry_0901`、`exit_1415`、`exit_1445`、`cost_2tick`、`cost_3tick`、`fee_x2`だけである。Candidateには主ゲートに加え、2tick費用後Net>0、09:01 entry後Net>0、night終点・entry・exitの3時刻軸の全登録profile平均Net>0、buy/sell signal群双方Net>0、2021--2024の少なくとも3年と2025H1でNet>0、旧16:30制度期と新17:00制度期双方でNet>0を要求する。3tickと手数料2倍は保存する記述ストレスでありCandidate追加ANDではない。救済探索、未登録感度、OOS、Final Holdoutは実行しない。

## 凍結・成果物

価格結果を読む前に、本書、event helper、runner、固定time strategy、合成test、configのSHA-256、run ID、bootstrap seedを`preregistration.json`と`run_manifest.json`に凍結する。成果物は新規かつ不変の`results/research/r073-q001-20260915-official-night-direction-day-reversal-01/`にのみ保存する。S2後に許可された場合だけprofile別event、orders/fills/trades、日次軸、metrics、MBB、execution/accounting audit、decisionを保存する。合成検証は16:30/17:00制度、night開始calendar_dateとtrade_dateの区別、符号反転、等値見送り、05:00/09:01/exit感度、休場schedule、filled後exit欠損、day-only entry/exit、Net会計、bootstrapを対象とする。
