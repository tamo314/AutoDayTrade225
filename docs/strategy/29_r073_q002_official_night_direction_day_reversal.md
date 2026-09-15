# R073-Q002: 公式ナイト方向の日中反転 — S2母数ゲート改訂再登録

タスクID: `TASK-R073-Q002`  
family_id: `night_directional_inventory_reversal` / study_id: `R073-Q002` / spec_version: `v1-s2-886-calendar-gate`  
run_id: `r073-q002-20260915-official-night-direction-day-reversal-01` / protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

これはR073-Q001を別IDで再事前登録するものである。経済仮説、シグナル、対象期間、約定、退出、1枚・最大1建玉の制約、無取引0円の主対照、同日・同entry/exitの`night_direction_momentum` co-primary対照、片道1 adverse tick＋片道30円の費用、感度、bootstrap、主判定、Candidate条件を完全に据え置く。`prior_information_seen=true`であり、Developmentは反復利用済みである。OOS（2025H2）とFinal Holdout（2026年以降）は物理I/Oを含めアクセスしない。

## 据置仕様

版管理calendarの公式night開始bar open（2024-11-05以前16:30、以降17:00）を`p0`、同一`trade_date`の05:30適格bar openを`pN`とする。`pN>p0`なら05:30に09:00 sell、`pN<p0`なら09:00 buy、同値なら見送る。14:29確定後14:30適格bar openで決済する。day内完結でStop/Target、day寄付き後の選択、閾値、再entry、休場跨ぎ保有は使わない。entry前欠損は取消・予定軸0円、filled後exit欠損はnullである。`Net=gross_fill-fees`でslippageは一度だけ控除する。

主ゲートはreversalのNet>0、PF>1、seed `20260915`・20 `trade_date` non-wrapping MBB（10,000回、tail truncation、linear percentile）の予定軸日次平均Net 95%CI下限>0、および対応日次`reversal - momentum`同CI下限>0である。未達は`REJECT`、主通過かつCandidate追加条件未達は`INVESTIGATE`、全Candidate条件通過だけが`CANDIDATE`である。事前固定感度は`night_end_0500`、`entry_0901`、`exit_1415`、`exit_1445`、`cost_2tick`、`cost_3tick`、`fee_x2`である。Candidate追加条件もQ001から不変である。

## 変更するS2標本ゲートだけ

TASK-R073-D001で確定した、同一calendar hash・Development予定軸の価格非依存カレンダー母数886日を分母として固定する。S2通過には、次の全てを要する。

1. 886カレンダー適格日の全6必須時刻（公式night開始、05:30、08:59、09:00、14:29、14:30）について、データ・`trade_date`・実装由来の説明不能除外が0件。
2. 同値のno-tradeを除く実行可能件数が886日の95%以上、すなわち`ceil(0.95 × 886)=842`件以上。
3. Q001の年別・方向別下限を据置き、2021--2024各年180件以上、buy/sell各300件以上。

S2ではPnL、return、勝敗、成績順位を計算・表示しない。S2通過後だけDevelopmentで一度、凍結済み主仮説とmomentum対照を基本費用で実行する。年別・方向別の取引数、期待値、PF、最大DD、利益集中度、日次軸、bootstrap、execution/accounting監査、判定を保存する。パラメータ探索・仕様調整・未登録感度を行わない。
