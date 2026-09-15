# 研究機能の設計と既存基盤の調査

## 既存API

- Gold: data/gold/year=YYYY/month=MM/bars.parquet。forwardは別サブディレクトリ。通常の再帰scanは両方を拾うため研究ローダーはcenter直下の年月を指定する。
- Bar: JST時刻、trade_date、calendar_date、Session、OHLC整数、volume、品質フラグ。系列識別はParquet側で検証する。
- Strategy: on_bar(StrategyContext, Bar) -> Signal | None。ctx.historyは現在バーまでの読取り用系列、ctx.has_positionがポジション有無。戦略に約定処理を持たせない。
- BacktestEngine(spec, config, classifier).run(bars, strategy, parameter_hash) -> BacktestResult。既存の翌適格バー、保守的Stop/Target、強制フラットを使用する。
- YAML: instrument/sessions/data/backtest/research、ローカルOSEカレンダー。旧research分割を提供文書の期間に同期した。
- 既存writer: trades/orders/fills/equity、基本指標、manifestを保存する。従来writerは上書き可能なので、研究ランナーで実験ディレクトリを排他的に作成する。

## 追加モジュール

research/config.py: 型付きの有界な探索設定。
research/data.py: Development/OOSだけを許可。年月パーティションの列挙時に2026年を除き、trade_dateでfilterする。forward混入、相違する重複、重大品質フラグを拒否。完全同一Barの重複は1バー化し品質件数と元ファイルを報告（05_data_overlap.md）。実際に使用したBar列のIPCバイト列のSHA-256をdata_versionにする（Parquet/Polars版も環境で固定）。2026年のOHLCを開かず、カレンダーの日時対応だけを設定として使用する。
strategies/opening.py: 初動の継続・反転と、固定した初動高安の終値突破。連続した開場L分と次バー約定、保有時間による退出。
research/metrics.py: 必須指標、日次PnL、分割集計、利益集中度、出口不利価格の感度。
research/robustness.py: seed付き再標本化と12/3/3 Walk Forward。
research/report.py: 保存済み台帳と指標を照合し、新しいreportsディレクトリに最終summaryと日次equityを作成する。データは再読込しない。
research/runner.py: 実験ID、事前仮説、コードと設定のzip、実行台帳、Developmentゲートを保存する。OOSは候補保存だけで自動実行せず、共通規約の別stage・内容検証・開封許可を必要とする。

## セッション別実行

初回戦略はセッションをまたいだ状態を持たず、1セッション1取引で早い時間帯に時間決済する。エンジンを各セッションに独立適用し、完成した台帳を時刻順に連結する。既存エンジンの累積取引再集計コストを小さく保つためであり、売買・約定規則は変更しない。IDのみ連結後に一意にする。

欠損により決済できずEND_OF_DATAに残ったケースは件数を報告し、候補昇格を拒否する。欠損セッションを黙って落とさない。セッション横断戦略へこの実行方式を流用しない。今回Stop/Targetは使わないため、複雑な保護注文の研究は別途事前登録する。

## 指標の定義

- gross_pnlは約定価格ベースでスリッページを既に含む。net=gross-fees。slippage_costは別表示の寄与で二重控除しない。
- PFは正のnet合計/負のnet合計の絶対値。負の取引なし・計算不能はnull。平均損失は負の値、payoffは平均勝ち/平均負けの絶対値。
- 最大DD: 初期0円からの実現損益累積DDに加え、各適格バー終値で含み損益を評価したDDを保存する。ポジション中は片道手数料を控除、決済時に往復を反映。分足内の最大DDではない。固定1枚/倍率100円のN225M用。
- Sharpe/Sortino: 新仕様では凍結したscheduled_axis上の完全に既知な日次net PnLを使い、既知無取引は0、未確定損益はnullとする。252日年率化・無リスク0、Sharpeは標本標準偏差、Sortinoは全日のmin(PnL,0)^2の平均平方根を用いる。不明損益が残る主系列では指標を未確定とし、ゼロ補完しない。固定枚数の金額PnL指標で、証拠金収益率・複利運用ではない。旧実験の観測日軸と計算値は原記録として保存する。
- exposure: entry_ts <= ts < exit_ts にある適格観測バー数 / 全適格観測バー数。欠損を含む壁時計時間の比率ではない。
- MAE/MFEは既存エンジン台帳。entryバーのOHLCを含み、始値で退出したバーの退出後OHLCは含まない。今回は時刻決済のみ。基盤で保護注文を使う場合の分足内順序不明の限界は別に考慮する。
- 年/月/曜日はtrade_date基準。時刻はentry_tsのJST時間帯。Day/Nightはentry_session、方向はside。
- 利益寄与率の分母は総net利益（総net<=0はnull）。別途gross勝ち合計を分母にした比率も保存。上位5/10の正の取引を除いたnet、最良/最悪月を保存する。
- 出口1分不利overlay: 同セッションに厳密に1分後の適格バーがある取引だけ、元のreferenceと次始値のうち不利な方を採る。コストは固定。対応できない件数を報告する。これは台帳感度であり注文再実行ではない。別のexit_delay_1m試行が実際の遅延シグナルをエンジンで再実行する。
- Monte Carlo: 取引10%を欠落させ元順序を維持、順序shuffle、独立復元抽出bootstrapを各1,000回。p05/p50/p95はソート後の下側インデックス。時間依存性を再現する保証はなく、Walk Forwardで別途期間依存を評価する。

## 完了と再現

実験manifestのcompleteは台帳・指標の保存完了。全キャンペーンの完了はCOMPLETED.json。family_decisions.jsonが最終判断であり、単独パラメータの利益順ランキングは作らない。作業ツリーを含むソースzip、ファイルhash、git commit、仮説hash、設定、データhash、seedを保存する。既存の同名IDは実行前に拒否する。

Final Holdoutの解放コマンドは今回実装しない。最終候補ができた時点で別の凍結手順を設計する。

## 実行例と到達点

R001は既定のconfig/strategy_research.yaml、R002は--study-config config/strategy_breakout.yamlで指定。仮説文書はhypothesis_documentで対応付ける。research report <campaign-directory>で市場データを開かずに完成レポートを追加できる。R001/R002計45実験は棄却。OOS/Final Holdout未評価、メタ戦略は未構築。

旧ランナーのequity.parquetはtrade_dateごとの実現PnL累積と記録されている。新仕様ではscheduled_axis、0/null、実現／含み損益の粒度を明記して実装を確認する。分足終値の含み損益DDと日次実現DDを同じものとして扱わず、分足内経路は再構成しない。

## RG-20260915-01: 共通研究基盤の改訂設計

「既存API」「実行例と到達点」は原文の調査・記録で、本改訂におけるコード確認結果ではない。追加仕様を既存の実装済み機能と混同しない。

### 1. 汎用化する責務

`research`層へ、完全なstudy/spec/run registry、SpecAudit、ConditionResolver、UniverseBuilder、AsOfDataView、OutcomeLedger、StageGateを追加する。モジュール名は配置案で、実コードとの整合を優先する。Strategyは価格判断、Engineは注文・建玉・約定、reportは保存台帳からの表示だけを担当する。

条件表を正本とし、A_follow／A_buy／A_sell等のside、Bのquantile、独立first-event、包含・排他、共通reference経路を明示的に解決する。root条件の値を暗黙に全対照へ流すdefaultを使わない。定義event、選択、実行、fillを別状態として保持する。

### 2. 集合と処理順序

```text
versioned schedule -> scheduled_axis
past source + semantic/as-of evidence -> U -> features
current as-of state -> E_exec -> signal/order -> execution -> outcomes
outcomes + observation evidence -> E_analysis -> paired diagnostics
```

前刻の取引対象に後刻placebo・全感度の価格や可用性を要求しない。Uに後のentry/exitやrolling自身の成立を要求しない。予定軸の主成績と、事後対応付き解析の成績を別ファイル・別estimandとして保存する。

### 3. 固定日次軸と不明損益

新仕様はカレンダーから凍結した予定軸を単一の集計入口へ渡す。データが存在する日の一覧やconditionごとの残存Barから分母を作り直さない。R004隔離後1,111日／1,120日／1,105日等は旧研究の識別子として保持し、新主軸へ自動継承しない。

KNOWN_NO_TRADEは0、UNKNOWN_PNL／OPEN_POSITIONはnull。主損益が不明なら既知分だけをpartialと表示し、完全なSharpe・Sortino・CIを出さない。対象数、観測数、無取引数、不明数、除外理由、axis hashを全条件のmetricsへ持たせる。

### 4. 推論とゲート

主経済指標、主対照、co-primary、診断を区別する。円/取引と円/trade_date、標本差とbootstrap中心、総額と平均を混同しない。R032型の差の不一致を検出する。gateは独立にboolean化するだけでなく、全必須条件の共同成立性を検査する。

FWLのnuisance冗長性とQ非識別を分け、未推定を0に置換しない。診断回帰が未識別でも妥当な主PnLを消さず、機構状態に記録する。co-primaryの場合のみその欠落を当該昇格の停止理由にする。旧固定規約は変えない。

bootstrapのblock長・seed・分位点・再推定対象・空標本処理はspecで固定する。保存eventを固定したbootstrapが、研究familyの選択まで補正したことにはならない。売買用学習を追加する場合はその手順を再現する検証が必要である。

### 5. 段階・再開・出力

修復後はS0～S6を明示して進め、Development完了からOOSを自動実行しない。未実装CLIは[09_cli_and_outputs.md](09_cli_and_outputs.md)へ能力単位で記録する。中断再開はchunkとhash・入力・既知情報を確認し、技術的な訂正を新経済仮説と混同しない。

追加成果物はregistry、仕様・因果性audit、U/E_exec/E_analysis、outcome_missingness、全条件解決値、固定日次軸、decision、アクセス台帳。新runの初期状態は未実施とし、過去runのPASSをそのままコピーしない。
