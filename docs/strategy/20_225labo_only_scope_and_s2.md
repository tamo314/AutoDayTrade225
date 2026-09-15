# 20. 225Labo OHLC限定スコープとS2可用性診断

タスクID: **TASK-225LABO-ONLY-01** / 記録日: **2026-09-15 JST**  
規約: **RG-20260915-01** / 実行ID: **AUDIT-225LABO-ONLY-01-20260915T120000Z**

## 今回の限定

市場入力は保有済み225Labo `N225M`・`center_continuous`・1分の日時とOHLCだけである。補助的に版管理calendar、商品・費用設定、既存の行内適格性を使うが、volume、板、bid/ask、他市場、ニュース、実限月間spread、roll売買は使わない。最初の実行対象はOSE day session内で完結し、休場・session境界をまたぐ保有、指値、Stop/Target、trailingは使わない。

|案・依存|今回|理由|
|---|---|---|
|R3B-R062-MR-01（night→day）|対象外|直前nightを特徴量に使うため、最初のday-only範囲ではない。R062-Q001 BLOCKED／Q002 REJECTは不変。|
|R065と専用cross-session executor|対象外|予定時刻注文・session跨ぎ保有を必要とする。既存BLOCKは不変。|
|volume/VWAP、板・約定順序、外部価格|対象外|225Labo OHLC限定では必要入力を満たさない。|
|R3B-R064-MR-01|対象|同一day sessionのopen/closeだけで判定でき、既存のdecision adapterとnext-eligible-open契約に適合する。|

この表は現在の適用範囲であり、過去のREJECT、BLOCKED、INCONCLUSIVEを変更しない。

## 最初の一案: R3B-R064-MR-01

これは新しい経済仮説ではなく、R064-Q001の「開始60分drive後の15分partial pullbackを同方向へ30分保有する」経済規則について、M04の`E_exec`／`E_analysis`分離を適用する技術修正候補である。親R064-Q001はINCONCLUSIVEのまま、既知の負の参考値と情報量不足を`prior_information_seen`として引き継ぐ。閾値、方向、時刻、保有時間を成績に合わせて変えない。

代表点は、day開始から60本目closeで確定する `d=c59-o`、`m=abs(d)/o`、`s=sign(d)` とする。`U`は当日を含まない直前120予定day trade_dateで、各日の最初の60本が適格・正値ならmを1個として、100個以上でnearest-rank q50/q70/q75/q80を固定する。古い有効日への補充はしない。続く15本の最終closeを`c74`とし、`p=-s(c74-c59)/abs(d)`、全response closeで`s*(close-o)>0`、かつ`0.20<=p<=0.50`をRとする。Aは`m>=q75 and R`、Cは`q50<=m<q75 and R`で、いずれも`s`方向である。d=0、欠損、不適格、U不足、R外は見送りである。

signalは75本目（ordinal 74）close、entryは次の適格1分bar open、exitはentryから30予定分後openである。entry欠損は約定前取消（費用なし・scheduled axis上0円）、filled後のexit欠損はnullであって0円ではない。1枚・最大1建玉、同一day sessionで完結し、entry遅延はexitを延長しない。基準費用は片道1 tick + 30円（往復1,060円）、ストレスは既存R064の2/3 tick、手数料2倍、1分entry delay、q70/q80、pullback 0.15--0.45/0.25--0.55、45/75分observation、15/45分holdに限定する。Stop/Target/re-entryはない。

主目的は固定scheduled-axisのA費用後日次Net、主対照は同じRで中程度driveのC、co-primaryはA-C日次Netである。推論は20 trade-date non-wrap MBB、10,000回、seed=20260916、linear percentileで、単位・分母は全scheduled date（既知無取引=0、未知PnL=null）とする。S2の情報量確認には従来R064の `E>=800, B>=400, A/C>=45, D/M>=35, A long/short>=15, q80 A>=30, observation45/75 A>=35` を用い、未達はINCONCLUSIVEとする。

## S0/S1/S2の状態と限定品質

R1の`r064_exec_event`は、selectionにentry/exitや他感度を入れず、response prefixだけでE_execを決める合成受入を再利用する。既存`R064TrendPullbackContinuationStrategy`はbar-close signal、次適格bar open fill、絶対時刻exit、delay非延長の受入対象である。今回の追加S2実装はこの経路を呼ばず、取引・return・PnL・勝敗・成績順位を生成しない。

R2は`PASS_LIMITED`である。用途別の判断は、時刻・trade_date・session・OHLCの構造整合と限定モデル上の固定bar解釈には `PASS_LIMITED`、実限月価格・roll/adjustment、bar開始/終了ラベル、訂正のdecision-time可用性、実約定再現には未解決、volume意味には非依存である。今回の研究仮定は「提供連続系列の明示bar約定・費用モデルにおける限定Development結果」であり、実限月の約定可能性や実運用を主張しない。`is_eligible`はS2では観測済みの構造QCとして集計するだけで、供給者の時点可用性を証明しない。

S0は、旧R064の経済gateが「all specified economic gates」とだけ保存され、必要な正確な全数値が正本から回収できていないため`REVIEW_REQUIRED`である。これはM09の未補完であり、値を推測してfreezeしない。S1の既存adapter/strategy合成受入は再利用可能、今回のS2コードは別の限定列読取り・固定軸・future-outcome非混入を検査する。

## S2読取り契約と次工程

S2はDevelopment trade_date `2021-01-01..2025-06-30`だけを対象に、該当する2021-01から2025-06のParquetだけを列挙し、filter前の全期間cacheを使わない。読取列は`ts_jst, trade_date, calendar_date, session, schedule_version, open, high, low, close, is_eligible, quality_flags, instrument, series_type, source`である。volumeは読まない。E_execは最初の75本だけで決め、entry ordinal 75 / exit ordinal 105の存在・適格性は事後の別欄にのみ記録する。

実行済みS2成果物は`results/research_audit/AUDIT-225LABO-ONLY-01-20260915T120700Z/`である（`…T120000Z`と`…T120500Z`は追記前の同一非PnL実行記録として不変保存）。版管理calendarのscheduled axisは1,131日、U不足は100日、E_execとしてresponseまで分類できた日は1,031日だった。cellはA=41、C=52、D=93、M=50、NONE=795で、A/Cの全93件は後刻のentry/exit観測も存在・適格だった。許可済み入力列のIPC fingerprintも保存した。return、Gross/Net PnL、勝敗、順位は生成・表示していない。

S3は実行しない。第一の停止理由は、継承した情報量gate A/C各45に対してA=41で不足したためであり、規約どおりINCONCLUSIVEである。加えて、上記M09の完全な経済gate（および資本/DD/運用最小値が必要ならその所有者決定）は未回収である。これは外部データやR065の不足を共通BLOCKへ戻す理由ではない。値を推測してfreezeしたり、閾値・時刻・方向・holdを救済探索したりせず、当該案だけを止める。OOS、Final Holdout、実売買はこの文書・S2によって許可されない。
