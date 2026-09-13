# 戦略研究結果

更新: 2026-09-13。R001/R002完了、3仮説・27基本条件・18ストレス、計45実験。すべてDevelopmentで棄却。OOS/Final Holdoutは未使用。

## R001 / Decision: REJECT（両仮説）

初動の方向のみを使う追随・反転は、各9条件すべてで片道1 tick・30円のコスト後期待値が負だった。採用候補なし。OOS（2025年後半）とFinal Holdout（2026年以降）は未使用。

Hypothesis: 初動の注文フロー継続 / 初動の需給偏りからの反転。

Implementation: OpeningStrategy v1、既存BacktestEngineをセッション別に使用。エンジン本体の変更なし。

Parameters tested: lookback=[15,30,45]、holding=[30,60,90]、固定代表点30/60。基本18試行と代表点ストレス12試行、合計30実験。

Development result: 2021-01-04～2025-06-30の1,353,431本、1,131 trade_date。元の年次重複5,197本を完全一致条件で1本化。

|代表点の指標|追随|反転|
|---|---:|---:|
|Net PnL（円）|-2,056,340|-2,573,740|
|Gross PnL（円、slippage含む）|-1,925,300|-2,442,700|
|手数料（円）|131,040|131,040|
|slippage寄与（円）|2,184,000|2,184,000|
|件数|2,184|2,184|
|期待値/取引（円）|-941.548|-1,178.452|
|PF|0.780|0.731|
|勝率|0.438|0.440|
|平均勝ち（円）|7,612.594|7,284.062|
|平均負け（円）|-7,600.961|-7,815.719|
|Payoff|1.002|0.932|
|最大DD（終値含み損益、円）|2,218,960|2,580,680|
|最大DD（実現損益、円）|2,210,480|2,575,180|
|日次Sharpe（年率）|-1.852|-2.316|
|日次Sortino（年率）|-2.425|-2.937|
|Exposure（観測バー比）|0.097|0.097|
|平均保有分|60.000|60.000|
|平均MAE（円）|7,862.408|8,093.864|
|平均MFE（円）|7,106.456|6,879.808|

## 感度（1 tick、30円/片道、期待値 円/取引）

### opening_momentum

|初動分 / 保有分|30|60|90|
|---|---:|---:|---:|
|15|-972.2|-1,031.8|-911.8|
|30|-821.6|-941.5|-867.2|
|45|-983.0|-862.3|-674.3|

### opening_reversal

|初動分 / 保有分|30|60|90|
|---|---:|---:|---:|
|15|-1,147.8|-1,088.2|-1,208.2|
|30|-1,298.4|-1,178.5|-1,252.8|
|45|-1,137.0|-1,257.7|-1,445.7|

## Robustness result

|代表点の条件|追随 Net円|反転 Net円|
|---|---:|---:|
|0tick|127,660|-389,740|
|1tick|-2,056,340|-2,573,740|
|2tick|-4,240,340|-4,757,740|
|3tick|-6,424,340|-6,941,740|
|double_fee|-2,187,380|-2,704,780|
|entry_delay_1m|-2,366,640|-2,263,440|
|exit_delay_1m|-2,049,640|-2,580,440|

0 tickでも手数料は片道30円。追随の0 tick利益127,660円は片道1 tickを加えると損失2,056,340円に転じ、採用根拠にはならない。exit_delayは価格が改善する場合もあり、常に不利とみなさない。

12か月train / 3か月test / 3か月stepのWalk Forwardは両仮説とも14窓すべてでtrainの条件未達、稼働0窓。test PnLは0円、PFと期待値は未定義。これは売買が成功した結果ではなく、売買不採用の結果。各窓のtrain件数、PF、test損益・期待値・DDはfamily_decisions.jsonに保存。

seed=225、各1,000回の取引10%欠落・shuffle・bootstrapで、両仮説とも総損益が正の試行は0回。bootstrap総損益p05/p95は追随 -2,918,840/-1,233,140円、反転 -3,400,740/-1,719,740円。独立取引仮定なので未来の損益確率を保証しない。

出口1分の不利価格overlayは全2,184件で対応可能。追随Net -3,193,640円、反転Net -3,717,740円。再約定とは区別した感度評価。

## 利益集中・期間・方向

|指標|追随|反転|
|---|---:|---:|
|最大利益取引（円）|71,440|101,940|
|上位5利益除去後Net（円）|-2,344,040|-2,894,440|
|上位10利益除去後Net（円）|-2,567,240|-3,078,640|
|上位5 / 勝ち合計|0.040|0.046|
|上位10 / 勝ち合計|0.070|0.072|
|最良月（円）|118,980|72,040|
|最悪月（円）|-158,960|-208,020|
|正の月比率|0.241|0.222|

総Netが負のため、総Net利益を分母にした寄与率はnull。勝ち取引総額を分母にした比率は別に上表へ掲載。

|分割|追随Net円|反転Net円|
|---|---:|---:|
|year: 2021|-621,760|-366,160|
|year: 2022|-324,240|-691,240|
|year: 2023|-558,760|-492,760|
|year: 2024|-282,200|-767,200|
|year: 2025|-269,380|-256,380|
|session: day|-1,061,460|-1,293,860|
|session: night|-994,880|-1,279,880|
|side: long|-911,560|-1,257,180|
|side: short|-1,144,780|-1,316,560|

2025年は6月まで。全54か月、曜日、JST時間帯別の指標は各metrics.jsonに保存。Day/Night、Long/Shortとも負であり、後付けの局面フィルターは作成しない。

## Validation result

Developmentの必要条件に不合格のため、OOSは未評価。Final Holdoutも未参照。未評価を成功とは扱わない。

## Problems discovered

- 年次ファイルの完全同一バー重複を検出し、処理方針をPnL計算前に追加した。詳細は05_data_overlap.md。最初の読み込み中止は未完了実験として保存。
- GoldにはMISSING_PREV_EXPECTED 244本、STATISTICAL_PRICE_JUMP 303本、TICK_GRID_VIOLATION 1,221本がある。既存の適格性を維持し、価格丸め・欠損補完・都合の悪い期間の除外は行っていない。
- 代表点は2,261セッション、初動ゼロで77回見送り。初動欠損による見送り、注文キャンセル、強制決済、データ終端決済はいずれも0件。
- 代表点のroll_riskは全件false。ロール局面別の頑健性を検証済みとは言えない。CANDIDATEを検討する前にマーカーの根拠と供給を点検する。
- 初回キャンペーンの既存writerによるequity.parquetは空。完成レポートに日次損益から照合済みのdaily_equity.parquetを追加し、元の出力を上書きせず補った。後続実行ではequity.parquetにも日次実現損益を保存する。

## Next experiment

R002を続けて実施し、以下に結果を記録した。現在、複数戦略やメタ戦略の採用を正当化できる結果はまだない。

## 再現と保存先

- Campaign: `results/research/20260913T090313-b797c081`。30実験を完了。
- 詳細レポート: `results/research/20260913T090313-b797c081/reports/20260913T091523-fb8b2ae6/research_report.md`。
- 各実験: hypothesis.md / config.yaml / metrics.json / trades.parquet / summary.md。最終判断付きの詳細summaryと日次equityはreports以下。
- ソースzip・hash、git commit、仮説、全コスト条件、実使用データhash、seedを保存。後から追加したレポート機能は売買結果を変更せず、台帳と指標を照合した。
- 現時点の検証: 44 tests passed、Ruffとmypyを通過。

## R002 / Decision: REJECT（価格帯突破）

Hypothesis: 初動の固定高安をその後の終値が突破する局面では、新たな注文フローが続く可能性がある。R001のDevelopment結果を受けて事前登録した追加探索であり、独立した未使用Development標本ではない。

Implementation: L本の高安を固定し、L本終了後から開場120分未満までの最初の終値突破でシグナル。翌適格バー始値で約定、H分保有。詳細は06_breakout_plan.md。

Parameters tested: L=[15,30,45]、H=[30,60,90]の9条件、代表30/60と追加ストレス6条件、計15実験。

Development result: 全9条件で1 tick + 片道30円の期待値が負。固定代表点も以下の結果となった。

|指標|価格帯突破|
|---|---:|
|net_pnl_jpy|-1,611,940|
|gross_pnl_jpy|-1,487,500|
|fees_jpy|124,440|
|slippage_cost_jpy|2,074,000|
|trade_count|2,074|
|expectancy_jpy|-777.213|
|profit_factor|0.798|
|win_rate|0.429|
|average_win_jpy|7,172.846|
|average_loss_jpy|-6,741.435|
|payoff_ratio|1.064|
|max_close_marked_drawdown_jpy|1,696,080|
|max_realized_drawdown_jpy|1,690,520|
|sharpe_daily_pnl_annualized|-1.685|
|sortino_daily_pnl_annualized|-2.256|
|exposure_observed_bar_fraction|0.092|
|average_holding_minutes|60.000|
|average_mae_jpy|7,209.257|
|average_mfe_jpy|6,566.297|

|初動分 / 保有分（期待値 円/取引）|30|60|90|
|---|---:|---:|---:|
|15|-860.1|-856.5|-952.9|
|30|-967.2|-777.2|-653.1|
|45|-1,181.9|-910.6|-1,029.4|

Robustness result:

|条件|Net円|期待値 円/取引|
|---|---:|---:|
|0tick|462,060|222.8|
|1tick|-1,611,940|-777.2|
|2tick|-3,685,940|-1,777.2|
|3tick|-5,759,940|-2,777.2|
|double_fee|-1,736,380|-837.2|
|entry_delay_1m|-1,749,940|-843.8|
|exit_delay_1m|-1,612,940|-777.7|

0 tick（手数料あり）では+462,060円だが、1 tickで-1,611,940円。2 tickで-3,685,940円。倍手数料・entry/exit遅延も負。出口不利overlayは全2,074件を評価でき、Net -2,423,940円。

Walk Forwardは14窓すべてtrain条件未達、稼働0窓。10%欠落、shuffle、bootstrap各1,000回の総損益プラス割合は0%。bootstrap総損益p05/p95は-2,309,940/-900,940円。

|利益集中指標|値|
|---|---:|
|best_month_jpy|89,600|
|best_month_share_of_net|null（総Netが負）|
|largest_winning_trade_jpy|84,440|
|net_excluding_top10_jpy|-2,063,340|
|net_excluding_top5_jpy|-1,873,640|
|positive_month_fraction|0.2593|
|top10_share_of_gross_wins|0.0708|
|top10_share_of_net|null（総Netが負）|
|top5_share_of_gross_wins|0.0410|
|top5_share_of_net|null（総Netが負）|
|worst_month_jpy|-238,840|

|分割|Net円|
|---|---:|
|year: 2021|-675,960|
|year: 2022|-215,960|
|year: 2023|-288,660|
|year: 2024|-274,540|
|year: 2025|-156,820|
|session: day|-592,260|
|session: night|-1,019,680|
|side: long|-1,048,780|
|side: short|-563,160|

Validation result: Developmentの採用条件に不合格のためOOS未評価、Final Holdout未参照。

Problems discovered: 2,261セッションのうち187は期限内の突破なし。初動欠損、キャンセル、強制決済、データ終端決済は0件。R001と同じデータ品質上の限界とroll-risk未評価が残る。エンジン本体は変更していない。

Decision: REJECT。今回の単純な価格帯突破を採用候補としない。損益が比較的良い一点や方向を選んで採用しない。

Next experiment（未実施）: まず入力roll-riskマーカーとtick-grid警告の出所を点検し、次の仮説では「過去20セッションだけで算出した通常の変動幅に対する現在の初動幅」など、事前に説明できる状態変数で局面を定義する。Development内の追加探索として条件数を制限し、同じ頑健性基準を適用する。複数の独立した候補が出るまではメタ戦略を組まない。

保存先: `results/research/20260913T091013-506b627c/`。完成レポート: `results/research/20260913T091013-506b627c/reports/20260913T091523-7bcab951/research_report.md`。全15実験の台帳と指標の件数・Net/Gross/fees/slippage・日次equity終値が一致することをレポート生成時に照合した。

## R003 / Decision: INVESTIGATE（品質ゲートで停止）

### Hypothesis

同種の直近20セッションに比べ、初動30分のレンジが小さい場合に限る終値ブレイクが、無条件ブレイクよりコスト後の期待値を改善するかを検証する。仕様は `07_r003_compression_breakout_plan.md` に固定した。

### Implementation

`CompressionBreakoutStrategy v1.0.0`、因果的な初動レンジ履歴、R003の厳格な設定読取り、Development限定preflightを追加した。注文・fill・費用・強制決済は既存 `BacktestEngine` のままである。

### Parameters tested

設定をPnL前に凍結したが、品質ゲートにより売買実験は未実行。θ=[0.60, 0.75, 0.90]、H=[30, 60, 90]、代表点θ=0.75/H=60、B=20、1 tick/片道30円の事前登録は保存済み。

### Development result

**NOT_RUN**。Development Gold（trade_date 2021-01-01～2025-06-30）のpreflightで、1,221本の未解明 `TICK_GRID_VIOLATION` を検出した。価格を丸めず、原因（取り込み・連続系列調整・市場区分等）を特定するまでPnLを計算しない。

### Validation result

合成テストでは、圧縮率の等号境界、偶数本中央値、終値ブレイク、翌適格バー始値約定、実fill起点の時間決済を確認した。全テストは47 passed、Ruff・mypyは通過。

### Robustness result

NOT_RUN。品質ゲート前のため、20条件、WFA、bootstrap、遅延・コスト感度は未実行。

### Problems discovered

rollの実観測根拠（contract ID/切替時刻）が入力に供給されていないため `roll_observation_status=unknown`。この問題はtick-grid原因と併せて品質ゲートに保存した。

### Decision

**INVESTIGATE**。`quality_status=BLOCKED` のため、経済性について結論を出さない。OOSは `NOT_EVALUATED`、Final Holdoutは `NOT_ACCESSED`。

### Next experiment

R003を続行する前に、Development対象行に限定してtick-grid違反の出所・正規化経路・連続系列加工を監査し、実限月/roll証跡を供給する。原因が解決して新データ版・PnL回帰を凍結するまで、同一R003のPnL・OOSを実行しない。

### Reproduction / artifact locations

最終preflight: `results/research/r003-20260913-preflight-final/`。`preflight/quality_gate.json`、`tick_grid_audit.parquet`、`roll_audit.parquet`、`session_coverage.parquet`、`provenance.json`、事前登録hashとsource snapshotを保存した。

## R003 / Development price-lineage audit: INVESTIGATE（2026-09-13）

監査ID `r003-20260913-dev-price-audit-01` を、R003 v1.0.0の仮説・パラメータ・採否基準（settings hash `968158…e1cc6`、hypothesis hash `ec0988…8fa4e0b`）を変更せず、結果を見る前に登録した。命題は「観測価格の生成過程と商品仕様から、既存エンジンで価格・tick・約定を一貫して解釈できる」。固定許容差はN225Mの5ポイントで、丸め、バー除外、raw/Gold変更、品質ゲート緩和、BacktestEngine/PnL集計を行わない。

### Confirmed facts

- `trade_date=2021-01-01..2025-06-30` の正規化済みGoldのみを返すフィルタで、既存の完全一致重複規約後に **1,221違反バー** を再現した。これは **1,556 OHLC違反フィールド** とは別の単位である。入力1,358,628行から完全一致重複5,197行を1バー化し、1,353,431バーを監査した。
- 年別では2021年dayが880バー/1,134フィールド、2021年nightが341バー/422フィールドであり、2022年以降と2025年前半は0件。全1,221バーは `N225M` / `center_continuous` / `225labo` で、`contract_month` は全件null、`TICK_GRID_VIOLATION` フラグと完全一致した。
- 入力ファイル別では `N225minif_2021.xlsx` が1,221バー/1,556フィールド、その他のDevelopmentに対応する6ファイルは0件だった。817連続違反区間のうち205区間が複数バー、最長12バーであり、孤立例だけではない。OHLC全列が同一の非ゼロ剰余を共有するバーは4件である。
- Gold OHLCは整数であり、残差は整数mod 5で算出したため、Gold上の観測は浮動小数点残差ではない。取り込みは整数化、正規化はOHLCをそのままBarへコピー、Goldは派生列追加のみである。固定層別規則で選んだday/night各1行ではrawとGold OHLCが完全一致し、少なくともその層の非5刻み値は正規化後に作られたものではない。

### Inference and unresolved items

- 1,221バー/1,556フィールドの場所は225Labo 2021 raw center seriesと特定できた。ただしこれは市場データ異常とも約定可能価格とも断定しない。連続系列の無調整・加算調整・比率調整、ロール選択規則、実効時刻を示す供給者証跡がないためである。
- `contract_month=null` と既定`roll_risk=false` は実ロールなしの証拠ではない。価格ジャンプから限月やロール日を推定していない。canonical label上の商品混在は検出されなかったが、元データの市場区分・価格種別は未証明である。
- よって既存品質ゲートの「tickの説明」「観測済みroll証拠」「価格の約定可能性解釈」の必須根拠が不足し、**quality_status=BLOCKED / Decision=INVESTIGATE** を維持する。Development PnL=**NOT_RUN**、OOS=**NOT_EVALUATED**、Final Holdout=**NOT_ACCESSED**。

### Next minimum work

225Laboのcenter series構築仕様とDevelopmentのcontract ID・選択規則・切替実効時刻・調整方式を示すroll表を取得し、この監査の`source_file/source_row_number`来歴へ読取り専用で結合する。その証跡で各offset classを「実限月の約定可能価格」「明示的な調整研究価格」「source anomaly」のいずれかに分類するまでは、R003のPnL/OOSは開かない。

成果物: `results/research/r003-20260913-dev-price-audit-01/`（事前登録、stratum集計、連続区間、固定層別raw照合、原因分類、入力・コードhashを保存）。

## R003 / Development continuous-series provenance audit: INVESTIGATE（2026-09-13）

監査ID `r003-20260913-dev-roll-provenance-01` を事前登録し、価格やPnLを読まずに公開225Labo/JPX仕様、ローカル設定、Development 2021 workbookのsheet名・headerだけを照合した。R003の仮説・パラメータ・採否基準は変更していない。

### Confirmed facts

- 225Laboの2021年ページは、miniを「出来高の一番多いラージ期近と同じ限月」のつなぎ足と説明し、単純なmini期近と異なる場合があると明記する。これは中心系列の**選択原則**の証跡である。
- JPXは通常立会の日経225mini呼値を5円、J-NETは別の呼値単位として示す。従って5ポイントは通常立会の品質許容差として維持するが、データ行の市場区分はこれだけでは分からない。
- 2021 raw workbookのheaderは日付・時間・OHLC・出来高だけで、contract ID、observed contract change、切替実効時刻、調整方式の列を持たない。canonical `contract_month` はnullであり、`roll_risk=false` を実ロールなしの証拠にしていない。
- JPXのQUICK提供情報は同社チャートの中心限月を前日取引高で日次見直しすると説明するが、225Laboが同じ時点・方法を用いた証拠ではない。類似の中心限月説明を225Laboのroll観測証拠へ読み替えなかった。

### Unresolved and decision

各Development日時に選択されたmini限月、切替時刻、セッション内切替、無調整／加算／比率調整のいずれか、ならびに1,221非5刻みバーの約定可能性は未証明である。満期日・SQ日・価格ジャンプからこれらを推定しなかった。よって **quality_status=BLOCKED / Decision=INVESTIGATE** を維持する。Development PnL=**NOT_RUN**、OOS=**NOT_EVALUATED**、Final Holdout=**NOT_ACCESSED**。

### Next minimum work

225Laboまたは権威ある供給者から、Development期間のmini contract ID、選択結果、切替実効時刻、調整方式を含む中心系列constituent/roll表を取得し、既存の`source_file/source_row_number`へ読取り専用で結合する。

成果物: `results/research/r003-20260913-dev-roll-provenance-01/`。

## R003-Q001 / Decision: REJECT（隔離データのDevelopment限定感度）

roll・調整方式の外部証跡を得られない前提で、R003本体を修復・変更せず、別ID `r003-q001-20260913-development-campaign-01` をPnL前に登録した。`TICK_GRID_VIOLATION` を含むバーだけを抜くのではなく、初動30分・20セッション履歴の因果性を守るため、そのバーを含む `(trade_date, session)` 全体を研究ビューから隔離した。raw/Goldは変更していない。

### Data policy and quality

親Development 1,353,431バー・2,261セッションから、45セッション（day 20、night 25）・27,345バーを隔離し、1,326,086バー・2,216セッションを残した。残存ビューの`TICK_GRID_VIOLATION`は0で、価格丸めも許容差緩和もないため、この**隔離ビューに限り** `PASS_LIMITED` とした。実限月、roll、約定可能性は依然として未証明であり、R003本体の`BLOCKED`は変わらない。

### Development result

固定済みの1 tick/side・片道30円、θ=[0.60, 0.75, 0.90]、H=[30,60,90]の9条件、および同一履歴・同一隔離規則のmatched control H=[30,60,90]をDevelopmentだけで実行した。9条件はすべてNetと期待値が負だった。代表点θ=0.75/H=60は498件、Net **-594,880円**、期待値 **-1,194.538円/取引**、PF 0.637、正の月比率0.389、上位10利益除去後Net -800,280円。matched control H=60も1,994件、Net -1,521,140円、期待値 -762.859円/取引だった。

### Decision

**REJECT**。隔離により元データを修復したとは扱わず、R003を救済するために除外規則・閾値・時間帯を追加探索しない。全9条件が同じ経済方向で不合格のため、この限定感度では追加ストレス・WFAを実行しない。OOS=**NOT_EVALUATED**、Final Holdout=**NOT_ACCESSED**。

成果物: `results/research/r003-q001-20260913-development-quarantine-01/`（隔離規則の事前登録）および `results/research/r003-q001-20260913-development-campaign-01/`（12条件の個別台帳と決定）。
