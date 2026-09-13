# 戦略研究結果

## R009-Q001: セッション内価格経路の方向一貫性を伴う継続 — INCONCLUSIVE（Development一次評価）

`r009-q001-20260913-directional-consistency-01` は、価格統計・イベント・PnLへアクセスする前に、コード・設定・入力識別子とhashを固定して登録した。R005は予定終了前の60分のopen-to-close方向とF相対のentry/exitを使うのに対し、R009は固定したS+119までの60個の1分close変化の `|Δ|/V` と固定S+120→S+180の保有を使う。R001--R004/R008の初動・突破・レンジ条件、R006のセッション間gap、R007の局所急変反転とも参照値・方向・時間窓が異なるため、同等仕様の既存評価には該当しない。既知Development上の追加探索であり、独立確認・探索全体の多重性補正ではない。

day/nightの版管理された予定始値Sから、`t=S+119`、`E=S+120`、`X=S+180`に固定した。`E<=new-entry cutoff` と `X<=F` を事前に満たす予定セッションだけを対象にし、観測行の端から時刻を逆算していない。同一sessionの`[t-60,t]`の連続61本の適格足を要求し、60変化について `Δ=close_t-close_(t-60)`、`V=sum(abs(d_j))` とした。Aは`V>0`、`Δ!=0`、`2|Δ|>=V`（等号を含む）ならΔ方向、B/Cは同じA事前イベントで常時買い/売り、Dは同一時刻・品質と`V>0, Δ!=0`だけを使い比率条件なしでΔ方向へ入った。全条件はStop/Targetなし、1枚、最大1ポジション、entryはt確定後の最短E始値、exitはX-1確定後の最短X始値であり、entry遅延でXを延長していない。

R004の固定隔離を一致再現した。45 session・27,345バー（day 20/night 25）を隔離し、2,216 session・1,326,086バーを残した。物理I/OはDevelopment選択済み正規化Parquetだけ、論理価格アクセスはtrade_date 2021-01-01..2025-06-30だけであり、OOS/Final Holdoutは未読である。実限月・roll・調整方式、旧R003-Q001隔離一覧未保存、事後whole-session隔離への条件付けのため、品質は **PASS_LIMITED** を超えない。

|条件|取引|Gross|slippage寄与|手数料|Net|期待値/取引|PF|最大実現DD|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|A 方向一貫性、1 tick|2|14,000円|2,000円|120円|13,880円|6,940円|null|0円|
|B 常時買い|2|5,000円|2,000円|120円|4,880円|2,440円|1.878|5,560円|
|C 常時売り|2|-9,000円|2,000円|120円|-9,120円|-4,560円|0.274|12,560円|
|D 方向追随（比率条件なし）|2,147|-1,612,500円|2,147,000円|128,820円|-1,741,320円|-811.048円|0.729|1,781,380円|
|A 2 tick|2|12,000円|4,000円|120円|11,880円|5,940円|null|0円|

Aはlong/short=1/1で、すべてday（Net 13,880円）だった。利益は2021-10の3,440円と2024-06の10,440円の2取引のみで、上位10利益取引除去後Netは0円、正の月は2/54である。Aの0損失によるPF=nullはPF>1の条件を満たしたとは扱わない。Dはday/night Net=-816,040/-925,280円、long/short=-967,520/-773,800円である。A/Dの事前イベントはboth=2、A-only=0、D-only=2,145で、差は経路条件だけの因果効果ではなく、イベント構成・取引数・費用を含むルール全体の差である。2025年は1--6月のみを含む。

対象1,120 trade_date（無取引0、両session隔離日は除外、一方隔離日は残存sessionを使用）にday/nightを合算した20日非循環moving-block bootstrap 10,000回、seed=20260913、全条件共通index、末尾切詰め、linear percentileの95%区間は次の通りである。A日次平均は **[0.000, 34.107]円**、A-Bは **[0.000, 24.107]円**、A-Cは **[0.000, 61.607]円**、A-Dは **[955.513, 2,152.192]円**。下限は厳密に正である必要があり、0.000の3区間は不合格である。

合成検証は61本/60変化、`Δ=sum(d_j)`、比率の等号/直下、単調・往復・V=0・Δ=0、固定信号時刻、prefix不変性、session跨ぎ/窓欠損禁止、翌足約定、固定exitとentry遅延、方向と会計を確認した。Ruff、mypy、関連pytest（3件）は通過した。実データの全5条件で注文取消0、force-flat 0、end-of-data 0、entry/exit遅延0、signal exit、`Net=Gross-fees` を照合し、経路・会計ゲートはPASSした。Grossはfill-to-fillでslippage込み、slippage寄与はNetから二重控除していない。

**INCONCLUSIVE**。A/B/Cの取引数2（各<200）、A long/short=1/1（各<50）で必要件数を満たさないため、Aの正Net・2 tick期待値やA-Dの区間を採用根拠にしない。A PF、A/A-B/A-C下限、正の月、上位10除去後Netも不合格である。閾値・窓・時間帯を緩めず、WFA、3 tick・手数料増・追加遅延、OOS、Final Holdout、独立期間の再現は未評価である。CANDIDATEへは昇格させない。成果物: `results/research/r009-q001-20260913-directional-consistency-01/`。

最小の次作業は、この固定定義の件数不足を記録したまま、Plannerが別の未重複仮説を事前登録するまでR009の比率・観測窓・保有窓・時刻・フィルターを探索しないことである。

### 研究状態（R009追記）

- R003本体: **BLOCKED / Development PnL=NOT_RUN**。
- R003-Q001: **旧REJECT維持**、EXIT影響監査12条件は **UNAFFECTED_PROVEN**、修正版バックテスト未実行。
- R004/R005修正版: **REJECT維持**。R006/R007/R008: **REJECT維持**。
- R009: **Development complete / INCONCLUSIVE**。Data quality: **PASS_LIMITED**。
- OOS: **NOT_EVALUATED**。Final Holdout: **NOT_ACCESSED**。

## R008-Q001: セッション内の値幅収縮後の突破継続 — REJECT（Development一次評価）

R008-Q001はR001--R007と重複しない、S+90からS+180の同一セッション内3本の連続30分窓を使う規則として、価格統計・イベント・PnLへのアクセス前に登録した。W0/W1/W2は各30本で候補足tを含まず、R0>0、R1+R2>0、4R0<=R1+R2、かつclose(t)>=U+5（買い）／close(t)<=L-5（売り）の最初の成立をAとした。B/CはAの事前イベントで常時買い／売り、Dは同一品質・時間帯・range正値で収縮を要求しない独立の最初の突破である。E=t+1、X=E+30の絶対時刻で、遅延でもXを延長せず、Stop/Target・再entry・追加探索は行っていない。

R004固定隔離を再現し、45セッション・27,345バーを除外、2,216セッション・1,326,086バーを残した。物理I/Oと論理価格アクセスはDevelopment（trade_date 2021-01-01..2025-06-30）のみに限定し、OOS/Final Holdoutは未読である。品質はroll/adjustment/実限月の未解決等によりPASS_LIMITEDである。合成検証（窓とtの分離、91本、収縮等号・ゼロ、突破等号、欠損回復、セッション境界、翌足約定、固定exit/遅延、対照、会計）4件、Ruff、mypyは通過し、全条件でcancel/force-flat/end-of-dataなし、Net=Gross-fees、A/B/C事前イベント一致だった。

|条件|取引|Gross|Fees|Net|期待値|PF|最大実現DD|
|---|---:|---:|---:|---:|---:|---:|---:|
|A 収縮突破 1 tick|1,019|-1,417,500円|61,140円|-1,478,640円|-1,451.070円|0.476|1,490,280円|
|B 常時買い|1,019|-961,500円|61,140円|-1,022,640円|-1,003.572円|0.597|1,050,960円|
|C 常時売り|1,019|-1,076,500円|61,140円|-1,137,640円|-1,116.428円|0.563|1,137,640円|
|D 非収縮突破|2,196|-2,098,500円|131,760円|-2,230,260円|-1,015.601円|0.618|2,257,740円|
|A 2 tick|1,019|-2,436,500円|61,140円|-2,497,640円|-2,451.070円|0.295|2,503,280円|

20 trade_date moving-block bootstrap 10,000回（seed=20260913、共通index、linear percentile、1,120対象日）の95%区間は、A日次平均[-1,627.431, -1,022.963]円、A-B[-840.179, 51.808]円、A-C[-642.857, 3.571]円、A-D[228.720, 1,123.789]円である。Aは買い/売り508/511、正の月4/54、上位10利益除去後Net -1,678,540円であった。A-Dの良い差は、イベント時刻・方向構成・取引数・費用を含むルール差であり、収縮だけの因果効果ではない。

**REJECT**。全条件の件数とA方向別件数は満たすが、A Net、PF、A平均区間、A-B/A-C区間、2 tick期待値、正の月、上位10利益除去後Netが不合格である。WFA、OOS、Final Holdout、3 tick・手数料増・追加遅延・パラメータ近傍は未評価であり、CANDIDATEには昇格させない。成果物: `results/research/r008-q001-20260913-session-compression-breakout-06/`。

## R007-Q001 / Decision: REJECT（セッション内局所急変後15分反転）

実行日: 2026-09-13。成果物: `results/research/r007-q001-20260913-local-shock-reversal-01/`。R001--R006との重複を事前に照合した。R001--R003は初動レンジ・初動方向、R004/R005は失敗ブレイク/終盤方向、R006はセッション間ギャップであり、同一セッションの直近60個の1分変化の中央値を尺度にする「最初の局所急変への逆張り15分保有」と同一の参照尺度・方向・保有窓ではない。R007は既知Developmentを見た後の追加探索であり、独立の確認実験ではない。

事前登録済み固定仕様: day/nightを独立に、版管理された予定始値Sから `t=S+61`～`S+180` 分（両端含む）だけを候補にした。各候補は同一セッションの `[t-61,t]` に連続した62本の適格足を要求した。`q_t=close_t-close_(t-1)`、`m_t=median(|close_j-close_(j-1)|, j=t-60..t-1)`（60個の中央2値の算術平均、候補自身の変化を除外）、`T=max(4m_t,20)` とし、最初の `|q_t|>=T` のみをイベントにした。Aはq>0で売り/q<0で買い、Bは常時買い、Cは常時売り。E=t+1始値、X=E+15始値で決済（X-1足の終値後にEXIT発行）とし、Stop/Target、再エントリー、追加フィルター、パラメータ探索は行っていない。Grossはfill-to-fillで片道slippageを含み、Net=Gross-feesであり、slippageを二重控除していない。

実行前の合成検証はPASS（62本/60変化、偶数中央値、候補変化の尺度除外、閾値一致/直下/m=0、候補境界と最初のイベント、夜間trade_date・セッション跨ぎ禁止、欠損後の窓回復、翌足約定、固定出口と入口遅延、対照、最大1取引、会計）。R004の固定隔離を再現し、45セッション/27,345本隔離、残存2,216セッション/1,326,086本、隔離一覧hash `2974bec…3213`、親入力hash `f6c267…a7db0` は一致した。品質は連続系列/実限月・roll・調整方式、旧R003隔離一覧未保存、事後的隔離への条件付けのため **PASS_LIMITED** にとどまる。OOS/Final Holdoutは選択・読込していない。

|条件（片道30円）|取引|Gross円|slippage寄与円|手数料円|Net円|期待値円/取引|PF|最大DD円|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|A 局所急変逆張り、1 tick|1,690|-1,387,000|1,690,000|101,400|-1,488,400|-880.710|0.590|1,503,600|
|B 常時買い、1 tick|1,690|-1,876,000|1,690,000|101,400|-1,977,400|-1,170.059|0.497|1,989,220|
|C 常時売り、1 tick|1,690|-1,504,000|1,690,000|101,400|-1,605,400|-949.941|0.576|1,605,690|
|A 局所急変逆張り、2 tick|1,690|-3,077,000|3,380,000|101,400|-3,178,400|-1,880.710|0.324|3,189,160|

対象は1,120 trade_date（両session隔離日は除外、一方のみ隔離の24日は残るsessionを使用、無取引は0円）で、1,690イベント、526イベントなし、評価不能候補0、予定範囲外0、取消0、未約定0、signal EXIT 1,690、force-flat/end-of-data 0だった。Aのday/night Netは -901,620/-586,780円、q<0に対応するA買い/q>0に対応するA売りは -877,480/-610,920円。年別A Netは2021 -305,140、2022 -312,560、2023 -317,860、2024 -309,000、2025前半 -243,840円。正の月は54か月中5、上位10利益取引除去後Netは -1,674,800円である。

20日非循環moving-block bootstrap（10,000回、seed=20260913、全条件共通index、末尾切詰め、linear percentile）の95%区間は、A日次平均 -1,646.912～-1,029.732円（推定 -1,328.929）、A-B -53.594～930.357円（436.607）、A-C -401.786～547.321円（104.464）だった。Development探索上の区間であり、研究全体の多重探索は補正していない。

条件別に、A/B/C各200取引、A買い/売り各50は合格。A Net>0、PF>1、A/A-B/A-Cの全bootstrap下限>0、A 2 tick期待値>0、正の月>=27、上位10除去後Net>0はいずれも不合格。経路・会計はPASSだが件数充足後の必要条件未達のため **R007=REJECT**。閾値/時間窓/方向の救済探索、3 tick・手数料増・追加遅延、WFA、OOS、独立期間での再現は未評価のまま実行しない。結果から正当化される次の最小作業は、この棄却結果を保持したまま、未重複の単一仮説を別IDで事前登録する前にR001--R007の対応表を更新することだけである。

状態: R003本体=BLOCKED / Development PnL=NOT_RUN、R003-Q001=旧REJECT維持・EXIT影響監査12条件UNAFFECTED_PROVEN・修正版バックテスト未実行、R004/R005修正版=REJECT維持、R006=REJECT維持、R007=REJECT、Data quality=PASS_LIMITED、OOS=NOT_EVALUATED、Final Holdout=NOT_ACCESSED。

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

## R005-Q001 / Decision: BLOCKED

R005-Q001 remains **BLOCKED**. Its raw Development execution and provisional PnL/REJECT outputs remain invalidated and are not R005 results. This diagnostic does not rerun Development, PnL aggregation, bootstrap, OOS, or Final Holdout; it only repairs and verifies a documented execution-semantics violation with synthetic bars.

The normative configuration calls the boundary a **new-entry** cutoff (`scheduled_close-15`), while the prior engine skipped every strategy callback at and after that time. A held A position therefore could not receive its required EXIT callback after `F-1 = scheduled_close-6`, even though the documented event order says that its pending market EXIT must apply at F open before force-flat. This is a specification violation, not a new economic capability. The minimal correction keeps flat strategies uncalled after the cutoff (so no new entry can be created), but lets a strategy with the existing sole position issue an EXIT. Fills, slippage, fees, max-delay cancellation, force-flat, and position limits are unchanged.

B independently falsifies the earlier blanket explanation: B enters at `F-175`, emits EXIT at `F-121` (before cutoff `F-10`), and fills at `F-120`. The invalidated local B event ledger's diagnostic columns show 2,122 `exit_order_issued` / `signal` exits at their scheduled B times (plus 94 no-position rows), rather than all B trades force-flattening. Thus the old statement that all B exits were force-flat is an artifact/report inconsistency and must not be used as research evidence. A's 2,114 diagnostic rows did reach force-flat at F after its callback was suppressed, which the pre-fix synthetic test reproduced.

Post-fix synthetic coverage proves A's one EXIT at F before forced-flat, B's normal `F-120` exit, day/night schedule versions (including the 2021-09-20-night rule selected for `trade_date=2021-09-21`), cutoff-adjacent new-entry rejection and EXIT acceptance, missing/delayed bars, maximum delay cancellation, duplicate EXIT/force-flat ordering, maximum one position, and `Net = Gross - fees` without double-counted slippage. R001–R004 synthetic comparisons show unchanged entry order/fill and round-trip fee; only previously suppressed timed EXITs move from force-flat at 15:40 to signal/fill at 15:30/15:31 in the constructed cases. Any prior real-data ledger that can contain such a timed EXIT needs a separately registered rerun before it is relied upon; none was run here.

Facts: preregistration was saved before R005 price work; the R004 frozen view reproduced (45 sessions / 27,345 bars isolated; 2,216 sessions / 1,326,086 bars retained; hash `2974bec…3213`); raw/Gold, OOS, and Final Holdout were not accessed. Data quality remains `PASS_LIMITED`. Development PnL is `NOT_RUN_VALID`; OOS is `NOT_EVALUATED`; Final Holdout is `NOT_ACCESSED`.

The fixed R005 time contract is now reproducible within the existing economic contract, but **R005-Q001 stays BLOCKED** because its historical execution was invalid. The next permitted work is a new R005 execution ID with a new preregistration that references this engine correction and states whether the prior invalid history is being rerun. It must preserve the fixed A/B rule and the record that this is Development-after-prior-exploration, then rerun only under that new ID. Artifacts: `results/research/r005-q001-20260913-session-end-momentum-03/` (invalidated source) and `results/research/r005-q001-20260913-exit-path-diagnostic-01/`.

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

## R004-Q001 / Decision: REJECT（初回ブレイク失敗確認後の逆張り）

### Confirmed facts

- 実験ID `r004-q001-20260913-failed-breakout-01` を、R004固有のDevelopmentイベント・PnLの前に `preregistration.json` と固定2条件の `campaign_plan.json` として保存した。これはR003結果を閲覧後に着想したDevelopment探索であり、独立OOSの証拠ではない。
- R003-Q001の保存済み12条件の台帳を読取り専用で再集計した。全12条件で取引数、Gross（slippage込みfill価格ベース）、手数料、slippage帰属額、Net、期待値、Net PFが既存集計と一致した。全件で `Net = Gross - fees`。slippageはreference/fill差からの帰属であり、Netから二重控除していない。
- 同じ親Development data version `f6c267…22a7db0` とwhole-session隔離規則を再現した。非5刻みを含む45セッション（day 20、night 25）・27,345バーを除外し、2,216セッション・1,326,086バーを残した。今回生成した隔離対象一覧とhashをpreflightへ保存した。旧R003-Q001成果物には対象一覧自体は永続保存されていなかったため、同一親ビューと凍結済み規則から再構成して件数・入力識別子を照合した。
- raw/Goldの変更、丸め、補間、年単位の除外、PnLによるセッション除外は行っていない。OOS/Final Holdoutのパーティションを読み込んでいない。

### Hypothesis and frozen implementation

最初の30分初動レンジを確定し、初回の終値が1 tick（5）以上外へ出た後、連続する次の5分以内に終値がレンジへ復帰した場合、その失敗ブレイクを逆張りして中央へ戻る期待値がコスト後も正かを反証する。

主条件Aは復帰足 `r` の確定後、次足始値で逆張りする。対照Bは同じ初回ブレイク足 `b` の確定後、復帰を待たずに次足始値で逆張りする。両者は確認待ち、参入時刻、固定Stopを含む規則全体の比較であり、復帰確認だけの因果効果とは主張しない。初動30分、break探索offset 30..119、復帰5分、保有60分、1枚、片道1 tickと30円、既存の15分新規締切と5分前強制決済を固定した。バー時刻はJST始値時刻で、判断は同1分足終値確定後、最短約定は次足始値である。

R001の「初動方向」追随／反転、R002の「固定初動レンジを終値突破」追随、R003の圧縮条件付き追随ブレイクとは異なり、今回だけが「最初のδ超え→直後のレンジ復帰→逆張り」を固定ルールとして検証した。同一規則を未検証扱いで再ID化したものではない。

### Development result

対象はtrade_date 2021-01-01～2025-06-30だけ（2025年は前半のみ）である。

| 条件 | 取引 | Gross円 | 手数料円 | slippage帰属円 | Net円 | 期待値円/取引 | Net PF | 最大実現DD円 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: 5分以内の復帰確認後 | 1,206 | -1,315,000 | 72,360 | 1,206,000 | -1,387,360 | -1,150.381 | 0.529 | 1,387,360 |
| B: 復帰を待たない対照 | 2,032 | -2,555,500 | 121,920 | 2,032,000 | -2,677,420 | -1,317.628 | 0.277 | 2,677,800 |

Aの2,216対象セッションは、初回ブレイク2,032、初回ブレイクなし184、復帰なし820、復帰時点で保護価格条件不成立6、注文・約定1,206だった。決済はStop 734、Target 435、時間決済37、end-of-data 0、注文取消0。Aのday/night Netは -804,440円 / -582,920円、long/short Netは -730,620円 / -656,740円であり、良い部分だけを選ぶ再実行は行わない。年別Netは2021 -230,020円、2022 -409,880円、2023 -241,580円、2024 -267,200円、2025前半 -238,680円。詳細な年別・session別・方向別の同じ指標と日次0円を含む整列系列は各条件の `metrics_research.json` / `daily_net_pnl.parquet` に保存した。

同一1,120 trade_dateの日次Netにday/nightを合わせ、20取引日のmoving-block bootstrap 10,000回（seed=20260913、A/B共通の再標本化index）を実施した。Aの日次平均Netは -1,238.714円、95% percentile区間 [-1,486.232, -944.143]円。A-B日次平均Net差は +1,151.839円、区間 [+929.625, +1,392.982]円だった。対照より損失が小さいことは、A自体の正の期待値を示さない。

Aの各往復に追加1,000円を控除する費用感応度は -2,593,360円である。注文・fillを変えない帰属感度であり、完全な再約定ストレスではない。

### Validation and decision

合成テストは初動前シグナル禁止、同一break足の復帰禁止、5分境界、復帰なし時のA/B差、次足約定、固定Stop、非tick-grid中央の注文価格離散化、entry/Stop gap、同一足TP/SLの保守的Stop優先、60分決済、最大1取引、prefix不変性、Final Holdout入力拒否を確認した。Ruff、mypy、関連pytest 16件が通過した。

**REJECT**。必要件数（1,206 >= 200）は満たしたが、A Net<0、PF<1、A日次平均Net区間下限<0、追加費用後Net<0である。A-B差の区間下限のみ正でも採用・派生条件探索はしない。これは今回の研究継続基準に不合格という意味であり、市場一般で失敗ブレイク現象が存在しないことの証明ではない。

### Unresolved limitations and next minimum work

隔離ビューの品質は **PASS_LIMITED** を超えない。セッション全体の事後品質隔離に条件付けられた連続系列研究であり、contract ID、roll時刻、調整方式、実限月の約定可能性は未解決である。外部roll表・調整証跡の再取得は、この実験の前提条件や次作業には戻さない。

最小の次作業は、R004を救済せず、このREJECTとR003-Q001のREJECTを研究記録として固定し、Plannerが未重複の別仮説を事前登録するまで追加実行を行わないことである。WFA、OOS、Final Holdoutは実行しない。

成果物: `results/research/r004-q001-20260913-failed-breakout-01/`（preregistration、実効設定・source snapshot、限定preflight、R003会計照合、条件別events/trades/orders/fills、日次Net、集計、区間推定、費用感度、完了状態）。

## R005-Q001 / corrected EXIT execution: REJECT（2026-09-13）

### 記録訂正と実行前ゲート

旧R005実行 `r005-q001-20260913-session-end-momentum-03` は、AのF-1で必要なEXIT callbackがnew-entry cutoffにより抑止されたため、**BLOCKED / NOT_RUN_VALID** のまま保存した。EXIT診断 `r005-q001-20260913-exit-path-diagnostic-01` はこれをAの規範仕様逸脱として再現し、held positionのEXITだけをcutoff後も許可する最小の共有エンジン修正を合成ケースで検証した。

同時に、旧BLOCKED記録の「Bも全件force-flat」は訂正する。旧Bイベント台帳にはscheduled F-120での `signal` EXITが2,122件、no-position行が94件ある。この2,122件は診断上の訂正であり、本実行の取引数・PnL・選定には一切流用していない。

R001--R004の保存済みEXIT診断を新旧合成台帳として再比較した。R001--R003はentry注文・entry fill・往復60円手数料・slippage帰属が不変だが、scheduled EXITがforce-flat 15:40からsignal/fill 15:31へ変わり、当該一定価格ケースではexit価格・Gross・Netも変わった。R004は同じ一定価格ケースで時刻・reasonだけが変わり、価格・Gross・Netは不変だった。いずれも通常EXITの規範仕様への修正結果であり、実データの旧研究は再実行・再集計・再判定していない。R003-Q001/R004-Q001のREJECTは旧版の歴史的判定として保持し、修正版で確認済みとは表現しない。

新ID `r005-q001-20260913-corrected-exit-02` は、R005固有の価格統計・イベント集計・PnLより前に事前登録した。同固定仮説の修正版実行であり、新規仮説または未使用Developmentではない。AはE=F-55、BはE=F-175、各直前60本のD=close(E-1)-open(E-60)の符号を次足始値で1枚追随し、予定55分後にEXITする。Fは観測終端ではなく版管理された予定終了時刻-5分で固定した。基本費用は片道1 tick/30円、Aだけ2 tick/30円を既存エンジンで再約定した。Stop/Target、再entry、フィルター、時間探索、ポートフォリオ化は行っていない。A/B差を終了時刻の因果効果とは扱わない。

R004のwhole-session隔離を再現し、45セッション（27,345バー、day 20/night 25）を除外、2,216セッション・1,326,086バーを残した。hashと入力識別子は一致した。物理I/OはDevelopmentの選択済みParquetだけ、論理アクセスはtrade_date 2021-01-01..2025-06-30だけであり、OOSとFinal Holdoutを読み込んでいない。品質は連続系列の事後的whole-session隔離に条件づく **PASS_LIMITED** で、実限月・roll・調整方式の未解決は維持する。

### Development結果

|条件|取引|Gross円|手数料円|slippage帰属円|Net円|期待値円/取引|PF|最大実現DD円|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|A: session end, 1 tick|2,114|-2,390,000|126,840|2,114,000|-2,516,840|-1,190.558|0.621|2,539,640|
|B: time control, 1 tick|2,122|-2,113,000|127,320|2,122,000|-2,240,320|-1,055.759|0.668|2,263,660|
|A: session end, 2 tick|2,114|-4,504,000|126,840|4,228,000|-4,630,840|-2,190.558|0.423|4,630,840|

Grossはslippage込みfill間PnLで、Net=Gross-feesである。slippage帰属はreference/fill差の表示であり、Netから二重控除していない。

共通1,120 trade_date（無取引は0、隔離セッションは対象外）で、20日moving-block bootstrap 10,000回、seed=20260913、同一index、末尾切詰め、95% percentile `sorted[floor((n-1)q)]` を実施した。A日次平均Netは -2,247.179円、95%区間 [-2,908.268, -1,623.661]円。A-B日次平均差は -246.893円、区間 [-1,142.321, 680.661]円。

Aは正の月8/54、最大利益取引91,940円、上位10利益除去後Net -2,968,740円だった。年別A Netは2021 -641,580円、2022 -704,320円、2023 -417,340円、2024 -292,460円、2025前半 -461,140円。Aのday/nightは -1,353,040/-1,163,800円、long/shortは -1,383,620/-1,133,220円。Bについても年・月・day/night・方向別、信号と見送り理由、注文・fill・決済理由、日次損益を成果物に保存した。2025年は1--6月のみである。

### 実行経路・判定

各条件の予定/実際entry・exitと遅延を全件照合した。3条件すべてでscheduled signal EXIT、entry/exit遅延0、force-flat 0、end-of-data 0、取消0、最大1取引/セッション、二重費用なしだった。AのFでpending EXITをforce-flatより先に一度だけ処理する規範競合規則も台帳で一致した。

関連pytest 36件、Ruff、mypyはすべて通過した。取引数はA/Bとも200以上だが、A Net>0、PF>1、A日次平均区間下限>0、A-B差区間下限>0、A 2 tick期待値>0、正の月>=27、上位10利益除去後Net>0の必要条件を満たさない。したがって本修正版の研究判定は **REJECT**。WFA、OOS、Final Holdout、3 tick、手数料増、追加遅延、パラメータ近傍は未評価であり、CANDIDATEにはしない。

成果物: `results/research/r005-q001-20260913-corrected-exit-02/`。`preregistration.json`、effective config/source snapshot、preflight、R001--R004 synthetic impact、events/orders/fills/trades、日次損益、集計、bootstrap、経路監査、完了状態を保存した。途中で合成ケース構築に失敗した `...corrected-exit-01` は事前登録のみを保持し、価格・PnL未アクセスの停止記録として上書きしていない。

### 研究状態

- R003本体: **BLOCKED / Development PnL=NOT_RUN**。
- R003-Q001/R004-Q001: 旧版の**REJECT維持**。修正版EXITコードでの再検証は未実施。
- R005旧実行: **BLOCKED / NOT_RUN_VALID**。
- R005新実行: **development complete / REJECT**。
- Data quality: **PASS_LIMITED**。
- OOS: **NOT_EVALUATED**。Final Holdout: **NOT_ACCESSED**。

## R006-Q001: ナイト終了から日中開始までの乖離の反転（Development一次評価）

新規ID `r006-q001-20260913-night-gap-reversal-01` を、R006固有のイベント数・価格統計・PnL取得前に登録した。R001--R005の既存Development結果を見た後の追加探索であり、未使用の検証標本ではない。R001--R005の再実行・仕様変更、WFA、OOS、Final Holdoutは行っていない。

既存仮説との差は、日中セッション内の初動・ブレイク・終了前方向ではなく、同一OSE `trade_date` に対応する直前ナイトの予定最終通常足終値 C と日中予定開始足始値 O の乖離 `G=O-C` を使う点である。AはG>0で売り／G<0で買い、Bは常時買い、Cは常時売りである。G=0・参照不能は3条件すべて見送り、実約定の共通部分で比較対象を絞っていない。S=日中08:45、E=S+1分=08:46の始値で最短約定、S+60分足の確定後にEXITを出しS+61分=09:46始値で決済する固定60分規則である。

版別Cは観測最終行から選ばず、取引日対応と予定表から固定した。2021-09-17はC足開始05:29／確定05:30、2021-09-21はナイト開始日が旧版のため同じ05:29／05:30、2021-09-22は05:59／06:00、2024-11-05はナイト開始日が旧版のため05:59／06:00、2024-11-06以降は終値オークションを除く通常足05:54／確定05:55である。週末をまたぐナイト対応もcalendar mappingで検証した。

R004固定隔離を一致再現した。親Developmentから45セッション（27,345バー、day 20/night 25）を除外し、残存は2,216セッション・1,326,086バー、隔離一覧hash `2974bec…3213`で一致した。対象日中1,111のうちG非ゼロで1,058取引、G=0が38、参照ナイト隔離14、参照ナイト欠損1だった。Aのlong/shortは537/521。参照ナイト隔離・欠損は対象日中を残して共通0円無取引、日中自身の隔離は対象外とした。品質は連続系列のroll・調整方式・実限月が未解決なため **PASS_LIMITED** を超えない。とくにセッション間乖離の解釈はこれらに敏感であり、良否にかかわらず実限月で再現可能な利益とは断定しない。

|条件|取引数|Gross|Fees|Net|PF|期待値/取引|
|---|---:|---:|---:|---:|---:|---:|
|A 乖離反転（1 tick）|1,058|-982,500円|63,480円|-1,045,980円|0.843|-988.639円|
|B 常時買い（1 tick）|1,058|-1,171,500円|63,480円|-1,234,980円|0.816|-1,167.278円|
|C 常時売り（1 tick）|1,058|-944,500円|63,480円|-1,007,980円|0.847|-952.722円|
|A 乖離反転（2 tick）|1,058|-2,040,500円|63,480円|-2,103,980円|0.709|-1,988.639円|

Grossはfill内slippage込み、Net=Gross-feesであり、slippage attributionは二重控除していない。Aの最大実現DDは1,212,780円（終値mark DD 1,222,340円）、正の月は18/54、最大利益取引は85,440円、上位10利益取引を除いたNetは-1,572,880円。年別A Netは2021 -138,240円、2022 -173,400円、2023 -154,080円、2024 -290,440円、2025年1--6月 -289,820円だった。

共通1,111日中trade_date（対象内無取引0円）の20日非循環moving-block bootstrap 10,000回、seed=20260913、共通index、末尾切詰め、linear percentileでは、A日次平均Net -941.476円・95%CI [-1,674.568, -229.271]円、A-B +170.117円・CI [-812.804, +1,225.023]円、A-C -34.203円・CI [-1,051.305, +861.409]円だった。探索済みDevelopment上の区間であり、全仮説探索に対する多重性は補正していない。

合成検証は版別時刻、制度変更日のナイトtrade_date、週末対応、Cの確定と終値オークション除外、参照不能・G=0、翌足約定、方向、固定EXIT、遅延時の保有非延長、最大1取引、費用会計、prefix不変性、Final Holdout入力拒否を確認した。実行経路は4条件すべてPASS（全filled tradeがsignal exit、force-flat/end-of-dataなし、最大遅延内、Net=Gross-fees）。関連pytest 25件、Ruff、mypyを通過した。

必要件数とA両方向50件は満たしたが、A Net、PF、A平均区間、A-B/A-C区間、2 tick期待値、正の月、上位10除去後Netが不合格である。したがってR006-Q001は **REJECT**。次の最小作業は、このREJECTを固定し、Plannerが未重複の別仮説を事前登録するまでR006の時間窓・方向・参照価格・適用期間を追加探索しないことである。

### 研究状態（R006追記）

- R003本体: **BLOCKED / Development PnL=NOT_RUN**。
- R003-Q001: **旧REJECT維持**、EXIT影響監査12条件は**UNAFFECTED_PROVEN**、修正版バックテスト未実行。
- R004/R005修正版: **REJECT維持**。
- R006: **development complete / REJECT**。Data quality: **PASS_LIMITED**。
- OOS: **NOT_EVALUATED**。Final Holdout: **NOT_ACCESSED**。

## R004-Q001 / corrected EXIT revalidation: REJECT（2026-09-13）

新ID `r004-q001-20260913-corrected-exit-01` をR004固有のイベント集計・価格統計・PnLの前に登録した。これは、保有ポジションの60分EXITだけをnew-entry cutoff後にも発行可能にした規範EXIT修正後の、旧R004固定A/BのDevelopment再検証である。新仮説、独立再現標本、未使用Developmentではない。R005やR001--R003の再実行、WFA、OOS、Final Holdout、パラメータ探索は行っていない。

EXIT修正が作用し得るのは、R004の実約定から60分後のEXITが15分new-entry cutoff以後となり、かつStop/TPより前に存続した取引である。旧callback gateではそのEXITが抑止され、後のforce-flatになり得る。R005の一定価格合成例はこの経路を確認したが、実データの価格・gap・保護決済・時刻を代表しないため、全件不変の根拠にはならない。今回の実データ全件照合で初めて影響を判定した。

R004の固定隔離を一致再現し、45セッション・27,345バー（day 20 / night 25）を除外、2,216セッション・1,326,086バーを残した。Developmentの選択済みParquetだけを物理I/Oし、論理的価格アクセスはtrade_date 2021-01-01..2025-06-30に限定した。OOS/Final Holdoutは未読である。連続系列・事後whole-session隔離・実限月/roll/調整方式未解決のため品質は **PASS_LIMITED** を超えない。

新旧比較では、対象4,432 session-event行、A 1,206取引、B 2,032取引、A 2,412注文/fill行、B 4,064注文/fill行のいずれも差分0・片側のみ0だった。event_id、初回break、復帰、見送り理由、注文、entry、exit label/timestamp/reference/fill price、Gross、slippage帰属、fee、Netはすべて同一である。よって実データにおけるEXIT修正の影響は **0取引、0円**。これは合成例の不変性を一般化した結論ではなく、保存済み旧台帳との実データ全件照合の結果である。

|条件|取引|Gross円|手数料円|slippage帰属円|Net円|期待値円/取引|Net PF|最大実現DD円|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|A: 5分以内の復帰確認後|1,206|-1,315,000|72,360|1,206,000|-1,387,360|-1,150.381|0.529|1,387,360|
|B: 復帰を待たない対照|2,032|-2,555,500|121,920|2,032,000|-2,677,420|-1,317.628|0.277|2,677,800|

Aの決済理由はStop 734、Target 435、60分signal EXIT 37、force-flat/end-of-data/取消0。Aのyear Netは2021 -230,020円、2022 -409,880円、2023 -241,580円、2024 -267,200円、2025前半 -238,680円。day/nightは -804,440 / -582,920円、long/shortは -730,620 / -656,740円である。共通1,120 trade_date（日次無取引は0、隔離sessionは対象外）の20日moving-block bootstrap 10,000回、seed=20260913、no-wrap・末尾切詰め・`sorted[floor((n-1)q)]` percentileでは、A日次平均Net -1,238.714円、95%区間 [-1,486.232, -944.143]円、A-B差 +1,151.839円、区間 [+929.625, +1,392.982]円だった。Aの追加1,000円/往復の注文経路不変な費用感応度Netは -2,593,360円。

関連pytest 33件、Ruff、mypyを通過した。R004修正版は、Aの件数条件（1,206 >= 200）**とA−B日次平均Net差の95%区間下限>0**を通過した。一方で、A自体の収益性（Net>0）・PF>1・A日次平均Netの95%区間下限>0・追加費用後Net>0は不合格である。したがってA-B差下限だけを採用根拠にせず、修正版判定は **REJECT**、旧R004の**REJECTも維持**である。R005修正版についても「全条件不合格」ではなく、**件数条件は合格、他の継続条件に未達のためREJECT** が正しい記述である。

成果物: `results/research/r004-q001-20260913-corrected-exit-01/`。事前登録、実効設定・識別情報、検証/品質ゲート、A/B events/orders/fills/trades、日次系列、指標、区間、費用感応度、全件新旧影響表、完了状態を保存した。最小の次作業は、R004を救済せずこの修正版REJECTを固定し、Plannerが未重複仮説を事前登録するまで新規実行をしないことである。

### 研究状態（更新）

- R003本体: **BLOCKED / Development PnL=NOT_RUN**。
- R003-Q001: **旧REJECT維持・修正版未再検証**。
- R004旧実行: **旧REJECT維持**。
- R004修正版: **development complete / REJECT**（EXIT実影響0）。
- R005旧実行: **BLOCKED / NOT_RUN_VALID**。
- R005修正版: **REJECT維持**（件数条件は合格、他の継続条件に未達）。
- Data quality: **PASS_LIMITED**。
- OOS: **NOT_EVALUATED**。Final Holdout: **NOT_ACCESSED**。

## R006-Q001: 最終記録（Development一次評価）

上記のR006記録を最新判断として追記する。`r006-q001-20260913-night-gap-reversal-01` は、事前登録後にDevelopment限定で実行し、45セッション・27,345バー隔離／残存2,216セッション・1,326,086バーを再現した。日中1,111対象、1,058取引（A long/short=537/521）で、A/B/Cの1 tick Netはそれぞれ-1,045,980円/-1,234,980円/-1,007,980円、A 2 tick Netは-2,103,980円だった。AのPF=0.843、期待値=-988.639円、正の月18/54、上位10利益除去後Net=-1,572,880円である。

20日moving-block bootstrap（10,000回、seed=20260913、linear percentile）の95%区間はA日次平均[-1,674.568, -229.271]円、A-B[-812.804, +1,225.023]円、A-C[-1,051.305, +861.409]円であり、全継続条件を満たさない。実行経路・会計検証はPASS（signal exit、force-flat/end-of-dataなし、Net=Gross-fees）だが、研究判定は **R006=REJECT**。時間窓・方向・参照価格・期間の追加探索、WFA、OOS、Final Holdoutは実行しない。

- R003本体: **BLOCKED / Development PnL=NOT_RUN**。
- R003-Q001: **旧REJECT維持**、EXIT影響監査12条件は**UNAFFECTED_PROVEN**、修正版バックテスト未実行。
- R004/R005修正版: **REJECT維持**。Data quality: **PASS_LIMITED**。
- OOS: **NOT_EVALUATED**。Final Holdout: **NOT_ACCESSED**。
