# R071-Q001: ナイト開始後のopening range突破継続

タスクID: `TASK-R071-Q001`  
family_id: `night_opening_price_discovery` / study_id: `R071-Q001` / spec_version: `v1`  
run_id: `r071-q001-20260915-night-opening-range-continuation-01`（停止記録）/ `r071-q001-20260915-night-opening-range-continuation-02`（技術訂正版）/ protocol: `RG-20260915-01`  
状態: **FROZEN_BEFORE_PRICE_PERFORMANCE**（2026-09-15 JST）

## 仮説と既存研究との関係

仮説は一つだけである。版管理されたナイトsessionの序盤opening rangeを初めて厳密に突破することは、海外時間帯の新規情報による価格発見を表し、その後の同一ナイトsession内の価格変化は突破方向に継続する。

これはR070-Q001の無条件・固定時刻方向保有とは異なり、価格条件で選ばれた最初の突破と、その方向情報を検証する。R069-Q001はday sessionの09:00 opening rangeと14:30 exitであり、対象session、時刻、保有区間、仮説機構が異なる。既存のナイト固定時刻、night/day条件、前session条件、または異なるholding windowを使う研究とも同一の選択規則ではない。Developmentは既知の探索期間なので、`prior_information_seen=true`、独立な未使用検証とは主張しない。

## データ範囲、予定軸、実行契約

- 読取りは正規化済み`N225M` `center_continuous` 1分OHLCと、版管理calendar・session・適格性だけである。volume、板、外部市場、ニュース、実限月、day/prior-session価格は使わない。
- `scheduled_axis`はcalendarのDevelopment trade_date `2021-01-01..2025-06-30` 全日である。OOS (`2025-07-01..2025-12-31`)とFinal Holdout (`2026-01-01`以降)は読まない。
- 各trade_dateについてcalendarが定めるnight sessionを使う。16:30、05:30がそのsessionにない版変更日はdecision前から既知の無取引0円で軸に残す。night開始日は前calendar_dateになり得るが、集計・年判定はtrade_dateで行う。
- 16:30から16:59までの30本すべてが存在・適格なら、その確定OHLCから`opening_high=max(high)`、`opening_low=min(low)`を固定する。範囲内の欠損・不適格はその日時点で無取引とし、後の有効値で補充しない。
- 17:00から23:30（両端を含む）を時刻順に調べ、最初に`close > opening_high`ならlong、最初に`close < opening_low`ならshortとする。等値は突破でない。探索中の欠損・不適格は最初の突破を観測不能にするため、その時点で無取引とする。23:30まで突破がなければ無取引である。
- 突破barのclose確定後、市場注文を出し、既存契約の最大10分以内の次の適格1分bar openで1枚だけ約定する。entry前に次適格barがなければ取消・費用0円である。entry後は同じtrade_dateの05:29確定後、05:30適格openでexitする。exit signal又はexit barが不適格・欠損なら、既約定損益はnullであって0円に置換しない。
- 最大1取引/trade_date、最大1建玉。Stop、Target、再entry、突破幅閾値、day/prior-session情報、休場跨ぎ保有を使わない。基準は片道1 adverse tickと片道30円手数料で、slippageはgrossに一度だけ入り、`Net=gross_fill-fees`とする。

## 対照、推論、停止規則

- 主対照はscheduled axis上の無取引0円である。
- 機構対照（co-primary）は、主戦略と同じsignal trade_date、同じentry/exit時刻・価格で方向だけを反転する`night false-breakout`である。対応日次差は`primary Net - false-breakout Net`、単位は全scheduled trade_date（既知の無取引0、未知exitはnull）である。
- S2はPnLを計算しない。entryとexitが実行可能なsignal trade_dateが400以上、2021--2024の各年60以上、long/short各120以上を全て満たす必要がある。未達なら`INCONCLUSIVE`で停止し、S3・感度・OOSを実行しない。
- S2通過後、未知のfilled exitが主またはco-primary軸に一つでも残れば`INCONCLUSIVE`で停止する。完全観測時の主ゲートは (1) Net>0、(2) PF>1、(3) 20 trade-date non-wrapping MBB（10,000回、linear percentile、tail truncation、seed `20260915`）による主戦略scheduled-axis日次平均Netの95% CI下限>0、(4) 同じMBB設計による対応日次`primary-false_breakout`差のCI下限>0、の全てである。
- 主ゲート不通過は`REJECT`、通過してcandidate条件が未達は`INVESTIGATE`とする。S2またはunknown outcomeによる停止だけは`INCONCLUSIVE`である。

## 事前固定感度とCandidate追加条件

許可する追加profileは以下だけで、主代表点・対照・他の仕様を選び直さない。

|profile|変更|
|---|---|
|`opening_20m`|opening windowを16:30--16:49（20分）|
|`opening_45m`|opening windowを16:30--17:14（45分）|
|`deadline_2230`|探索期限を22:30|
|`deadline_0030`|探索期限を翌calendar_date 00:30|
|`entry_delay_1m`|突破の1分後bar closeで注文し、その次の適格openでentry。exitは延長しない。|
|`cost_2tick` / `cost_3tick`|片道adverse slippageを2 / 3 ticks|
|`fee_x2`|片道手数料60円|

`CANDIDATE`には主ゲートに加え、2tick費用後Net>0、1分遅延後Net>0、opening-window/探索期限の4 profile全てでscheduled-axis日次平均Net>0、long群・short群の双方Net>0、2021--2024の少なくとも3年と2025H1のNet>0を要求する。3tick・手数料2倍は固定ストレスの記述結果として保存するが、Candidateの追加ANDには含めない。`PASS_LIMITED`の入力品質はOOSまたは実運用の許可を意味しない。

## 再現性と成果物

runnerは実行IDごとに新規の`results/research/r071-q001-20260915-night-opening-range-continuation-*/`へ一度だけ保存する。事前にこの文書、runner、event helper、strategy adapter、合成テスト、関連configのSHA-256をmanifestへ固定し、実行後にsource/config snapshot、access ledger、S2 feasibility、event ledger、profile別trades/daily axis/metrics、MBB、execution-accounting audit、decisionを保存する。R071実装のS0/S1は合成経路で、strict inequality、前calendar_dateのnight開始、00:30境界、next-open、遅延、後刻exit欠損がentryを遡及取消しないこと、反転対照の同時刻・逆方向、および会計恒等式を検査する。

## -01停止と意味不変の技術訂正

`-01`は2021-01-04のevent/engine一致監査で停止した。そこでcalendarの`night_calendar_start_date=2020-12-30`と`trade_date=2021-01-04`の組合せが、05:30 exitを複数calendar日後に置くことを検出した。これは本仕様の休場跨ぎ保有禁止に反するため、`-01`は失敗成果物として不変保存した。集計PnL、profile比較、bootstrap、gate判定を読む前の停止である。

`-02`は同じ凍結仕様で、night開始calendar_dateの翌日がtrade_dateでない版管理calendar組合せを、decision前から既知の`NO_SCHEDULED_NIGHT_WINDOW`・0円にする技術訂正を加える。また、entryが既にfillした後の非exit gapが、明示的に存在・適格な05:29/05:30 exitを無効化しないようadapterを訂正する。これはentry前のopening/search欠損処理、突破条件、方向、cost、期間、感度、gate、seedを変更しない。holiday-gap合成回帰を追加し、`-02`の新規manifestで訂正理由とhashを固定する。
