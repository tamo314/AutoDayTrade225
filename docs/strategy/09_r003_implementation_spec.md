# R003：実装設計とCodexへの引継ぎ

作成日：2026-09-13 / 状態：**実装仕様。以下の追加API・CLIは未実装。**

## 1. 成果物と境界

`07_r003_compression_breakout_plan.md` の仮説を、既存の翌適格バー約定エンジンを変更せず再現できるようにする。価格取得、特徴量、売買判断、約定、評価を分離する。

今回の文書一式は、会話で共有された `19bc508` 時点の説明から設計した。実装担当は変更箇所に対応する実コード・データ契約を確認し、関数名や構造に差があればアダプターで合わせる。以下の新規クラス・関数が既に存在すると仮定しない。

基盤に経済的意味を変える修正が必要なら、再現例・影響・回帰テストを別に記録する。戦略を勝たせる目的で fill、コスト、強制決済、取引時間を変更しない。

## 2. 既存APIとの接続

共有された設計に記載されている入口は次のとおり。

```python
Strategy.on_bar(ctx: StrategyContext, bar: Bar) -> Signal | None
BacktestEngine(spec, config, classifier).run(bars, strategy, parameter_hash)
```

`ctx.history` は現在バーまで、`ctx.has_position` は建玉の有無。現在の実装が追加のfill情報を公開しているかは未確認である。

R003は同一セッションの売買状態だけをStrategyが持ち、**過去セッションの特徴量履歴はresearch層が管理して、不変スナップショットをStrategyへ注入**する。従来のセッション別Engine実行でStrategyを毎回作り直しても、過去20回の履歴を失わない構造にする。

### fill時刻の取得契約

実際の約定時刻が既存contextから取得できればそれを使う。そうでない場合は、Engineが当該バー始値でentryを処理した後に `on_bar` を呼ぶことを合成テストで確認したうえで、最初の `has_position: false -> true` が観測されたバー開始時刻を実entry時刻として利用できる。

遅延約定時にもこの対応が成り立つことを必須テストにする。注文送信時刻や「通常は次の分だろう」という推測で代用しない。対応を証明できない場合は `INVESTIGATE_API_CONTRACT` として実行を止め、戦略から独自の約定計算をしない。

## 3. 配置案と責務

| 配置 | 変更内容 | 完了条件 |
|---|---|---|
| `research/config.py` | 旧設定と区別したR003の型付き設定、未知キー拒否 | 既存R001/R002設定を解釈でき、R003の固定値・代表点・上限を検査できる |
| `research/data.py` | splitを限定した読取り、ウォームアップと採点区間の分離 | 2026価格・OOSの先行アクセスを防ぎ、取得範囲を記録できる |
| `research/features.py`（新規） | 予定セッション台帳、初動サマリー、過去履歴スナップショット | prefix不変性、時刻as-of、Day/Night分離を満たす |
| `research/preflight.py`（新規） | データ品質ゲート | 08の3段階判定を根拠付きで保存できる |
| `strategies/compression.py`（新規） | R003とmatched controlの売買判断 | 約定処理を持たずSignalだけを返す |
| `research/runner.py` | 戦略factory、段階実行、実験予算、スナップショット注入 | セッション独立実行と過去履歴保持を両立できる |
| `research/preregistration.py`（新規または既存保存機能へ統合） | 事前凍結・OOS開封記録 | 未完了/仕様相違でOOSを開かず、既存結果を上書きしない |
| `research/metrics.py` / `robustness.py` | 対照差、日次ブロックbootstrap、WFA | 取引台帳と評価値を照合できる |
| `research/report.py` | 見送り理由、特徴量、ゲート証跡、未検証事項 | 追加データを読むことなく保存済み成果物からレポートを再構築できる |
| `cli.py` | 下記の段階指定と監査コマンド | 旧コマンドの互換性を維持し、R003は明示stageを要求 |
| `tests/` | 10の合成・回帰テスト | 新旧の重要な不変条件を満たす |

モジュール分割の細部は既存構造に合わせてよい。ただし、情報の利用可能時刻、取引ルール、研究ゲートは変更しない。

## 4. データ契約

### 4.1 ScheduledSession

`session_key` は `(trade_date, session, scheduled_start_ts)`。`scheduled_end_ts`、`calendar_version`、`source_presence` を持つ。予定の順序は実時刻で決定する。同じtrade_date内でDay/Nightを文字列順に並べない。

予定なしと予定あり・データなしを区別するため、研究用の予定台帳をカレンダーから作る。これは価格補完ではない。完全に欠落した予定セッションも履歴の1回として数える。

### 4.2 OpeningSummary

| 項目 | 型・意味 |
|---|---|
| `session_key` | 対象セッション識別子 |
| `opening_minutes` | 30。履歴窓診断でも変更しない |
| `opening_start_ts` / `opening_available_at` | 予定開場 / 初動最終バー確定時刻 |
| `upper`, `lower`, `range_points` | 整数価格/差。無効な初動ならnull可 |
| `observed_opening_bars`, `expected_opening_bars` | 実際/予定本数 |
| `opening_valid` | 初動30分だけで判断した有効性 |
| `invalid_reasons` | 欠損、不適格、日時不整合など |
| `input_bar_hash` | 実際に使用した初動バーのhash |

現在セッションで売買しなかった場合でも、初動が有効ならSummaryを作る。履歴不足で見送る最初の20回を履歴へ追加し忘れると、永遠にウォームアップが終わらない。

セッション後半の価格・取引結果・後日確定の品質で、`opening_valid` や `range_points` を書き換えない。

### 4.3 HistorySnapshot

`asof_ts`、同種の直前B個のsession_key、各回の初動幅・有効性、`baseline_n`、`baseline_median_points`、`history_status`、`max_source_available_at`、`snapshot_hash` を持つ不変オブジェクト。

必須条件は、全履歴セッションの `scheduled_end_ts < current.scheduled_start_ts`、全価格の `available_at <= current.scheduled_start_ts`。初動だけが既に確定していても、まだ同種の現行セッションは参照しない。

`history_status` は `ready / insufficient_count / invalid_member / zero_median`。値を計算できないときはnullを使い、ゼロや架空の中央値を入れない。

### 4.4 SessionFeature・監査出力

初動確定時に、U/D/R/M/q/θ、履歴キー、許可状態、利用可能時刻を保存する。取引ごとには `signal_bar_start_ts`、`signal_available_at`、`entry_fill_ts`、`planned_exit_ts`、`actual_exit_ts`、方向、コスト設定を紐付ける。

特徴量キャッシュのキーには、使用データhash・calendar hash・初動時間・履歴本数・特徴量版・対象期間・as-of方針を含める。全期間一括キャッシュをキー不一致のまま使わない。

## 5. 因果的な処理順序

以下は擬似コードであり、既存関数名を表すものではない。

```text
scheduled_sessions = enumerate_calendar_sessions(allowed_split)
history = separate_history_for_day_and_night()

for session in chronological_order(scheduled_sessions):
    prior = history.snapshot_previous_scheduled(
        session_type=session.type,
        count=B,
        completed_before=session.start,
    )

    # 売買用には過去のsnapshotだけを渡す。
    strategy = make_strategy(params, prior, session.calendar)
    opening_collector = make_opening_collector(session, L=30)

    # Engineへ渡す価格は当該セッションだけ。
    # コレクターは順次観測した初動バーからsummaryを凍結する。
    result, opening_summary = run_existing_engine_with_observer(
        session_bars, strategy, opening_collector
    )

    save_trades_and_audit(result, strategy.audit)

    # 現在の初動は、現在の売買判定を終えた後、未来のsession向けに追加。
    # 初動以外の結果でsummaryを修正しない。
    history.append(session, opening_summary_or_missing_marker)
```

既存Engineにobserverがなければ、バーを同じ因果順で読むresearch側collectorを用いる。全セッションを先に集計する実装も、各summaryを `available_at` 付きの厳格なas-of lookupで渡し、prefixテストを満たせる場合に限り許容する。セッション全体の最終品質フラグを事前にStrategyへ渡さない。

仮説のスナップショットを渡すのであって、未来を含むサマリー表全体をStrategyに渡して「使わないようにする」設計にはしない。

## 6. 閾値判定の実行可能な参照コード

以下は価格幅から圧縮状態を求める純粋関数の参照実装。既存パッケージへの統合コードではない。外側でセッション選択・連続性・利用可能時刻を保証してから呼ぶ。

```python
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Sequence


@dataclass(frozen=True)
class CompressionState:
    baseline_median: Fraction
    ratio: Fraction
    allowed: bool


def compression_state(
    current_range: int,
    previous_ranges: Sequence[int | None],
    threshold: str,
    *,
    baseline_sessions: int = 20,
) -> CompressionState | None:
    """Return None when the state is not tradable/defined.

    previous_ranges must be the immediately preceding scheduled sessions,
    oldest first, selected as-of the current session start.
    """
    if baseline_sessions <= 0:
        raise ValueError("baseline_sessions must be positive")
    if isinstance(current_range, bool) or not isinstance(current_range, int):
        raise TypeError("current_range must be an integer")
    if current_range < 0:
        raise ValueError("current_range must be nonnegative")
    if len(previous_ranges) != baseline_sessions:
        return None
    if any(value is None for value in previous_ranges):
        return None
    if any(isinstance(value, bool) or not isinstance(value, int)
           for value in previous_ranges):
        raise TypeError("previous ranges must be integers or None")

    values = [value for value in previous_ranges if value is not None]
    if any(value < 0 for value in values):
        raise ValueError("previous ranges must be nonnegative")
    values.sort()
    n = len(values)
    median = (Fraction(values[n // 2], 1) if n % 2
              else Fraction(values[n // 2 - 1] + values[n // 2], 2))
    cut = Fraction(Decimal(threshold))
    if not Fraction(0) < cut < Fraction(1):
        raise ValueError("threshold must be between 0 and 1")
    if current_range == 0 or median == 0:
        return None
    ratio = Fraction(current_range, 1) / median
    return CompressionState(median, ratio, ratio <= cut)
```

`None` の理由は呼出側で区別して監査出力する。参照関数だけで履歴の時系列性が保証されるわけではない。無効な閾値文字列は例外として設定検証で拒否する。

## 7. Strategyの状態遷移

| 状態 | 入力・条件 | 次状態 / 出力 |
|---|---|---|
| `COLLECTING_OPENING` | 初動バーを受信 | 初動確定まで待機 |
| `COLLECTING_OPENING` | 初動・履歴無効、ゼロ幅、圧縮不成立 | `DISABLED`。新規注文なし |
| `COLLECTING_OPENING` | 初動確定・圧縮成立 | `ARMED` |
| `ARMED` | 期限内の最初の終値ブレイク | directionとsignal時刻を固定、`entry_attempted=true`、`ENTRY_PENDING`、Signalを1回発行 |
| `ARMED` | 判定期限を超過 | `DONE` / no breakout |
| `ENTRY_PENDING` | `has_position` がtrueになり実fillを確認 | 実entry時刻を固定し `OPEN` |
| `ENTRY_PENDING` | 取消が確定、またはセッション終了までfillなし | `DONE` / entry not filled |
| `OPEN` | 決済指示の予定バーを観測、または期限超過 | `EXIT_PENDING`、exit Signalを1回発行 |
| `EXIT_PENDING` | 建玉が解消 | `DONE` |
| 任意 | 予期しないEngineの強制決済等 | 結果照合で異常を記録。独自に取引を消さない |

`ctx.has_position=false` だけを理由に再エントリーしない。Pending状態と `entry_attempted` を分けて持つ。現在APIに取消通知がなければ、pendingのままセッション終了まで新規注文を出さず、終了後のorders/fillsで理由を照合すればよい。

entry期限を過ぎたことだけで、すでに送信した注文が取消済みだと見なさない。遅れてfillした建玉も必ず管理する。最大約定遅延はEngineの設定を使う。

初動collectorはStrategyが `DISABLED` でも動かし、未来セッションの履歴を更新する。建玉やpending注文を別セッションへ持ち越さない。

### 遅延ストレス

entry 1分遅延は元のブレイク時点の方向を固定し、送信イベントだけ1分遅らせる。遅延後にもう一度ブレイク成立を要求しない。対応バーがない場合は架空の送信を生成せず、既存の遅延方式と最大期限に従って記録する。

exit 1分遅延は実entryから計算した時間決済の指示を1分遅らせる。entry遅延とexit遅延は別実験。遅延によりfillしなかった取引や保有時間が変わった取引を隠さず、元のsignal集合との対応率と取消理由を報告する。

既存の遅延機能がこの意味と異なる場合は差分を文書化し、実データPnLを見る前にR003版として凍結する。成績を見てから都合のよい方式に切り替えない。

## 8. 設定と検証

`config/strategy_compression.example.yaml` は**新規スキーマの雛形**である。現在のResearchConfigへそのまま渡して動くとは想定しない。実装後に `config/strategy_compression.yaml` として配置する。

rootの `schema_version: 2`、`study_kind: state_conditioned_opening` で既存設定と区別する。旧形式の読取りは残す。新規スキーマ・各入れ子は未知キーを拒否する。

検証対象は、期間の完全一致、Final Holdout無効、L=30、代表点が格子内、θが3指定値、Hが3指定値、B=20、診断B=15/25、1セッション1試行、1枚、手数料30円、コスト条件の重複排除、基本20条件上限。閾値は文字列Decimalとして正規化する。

新しい固定値へ変えた設定はR003 v1として受理せず、新研究ID/仕様版と再登録を要求する。ファイル形式・改行の差だけならcanonical config hashを使えるが、意味が変わる差を同一hashにしない。

Instrument/Calendar/Backtestの既存設定を正本にし、雛形の数値は整合性assertionとする。不一致時に研究設定でこっそり上書きしない。

## 9. WFA・再標本化の実装定義

### 9.1 WFA

14個のtrain/testをtrade_dateで切る。各trainのゲートはそのtrainに属する取引だけで計算する。全Developmentの判定結果を各trainへコピーしない。

過去価格によるウォームアップとPnLを評価する範囲は別の引数にする。後半のtrain窓はDevelopment内のtrain開始より前の20回を履歴に使ってよい。最初のtrain窓に2020年データを追加しない。

全期間台帳の再利用は、連続実行・prefix切出し・各fold独立実行で取引が一致することを受入テストで確認してから有効化する。取引結果に依存する資金配分・学習・ポジション競合を将来追加したら、この最適化を無条件で流用しない。

### 9.2 日次ブロックbootstrap

まずDevelopmentの観測可能なtrade_dateを昇順に並べる。取引がないが価格観測のある日は `net=0, trade_count=0`。カレンダー上はあるが価格が完全欠落した日を、黙って「観測した0円日」に置換しない。完全欠落は品質報告へ記録し、統計の完全性を満たさなければゲートは計算不能とする。

1日内のDay/Nightの全取引をまとめる。長さN日の系列から、長さb=5の連続ブロックを取り得る開始位置 `0..N-b` を等確率・復元抽出する。ブロックを連結しN日を超えた分を末尾から切る。循環wrapはしない。N<bなら計算不能。

各反復で総net、日次終端実現DD、総net/総取引数を計算する。圧縮群と非圧縮群の期待値差も、同じ日付インデックスの抽出で計算する。群ごとに別の日付を抽出しない。

seed=225、反復1,000回。5日を主ゲート、10日を同じ方式の診断とする。再標本化方式ごとに独立した乱数生成器を使い、他の診断の追加で主結果が変わらないようにする。結果値を昇順 `x` としたとき `p(q)=x[floor((n-1)*q)]`。主表示はp05/p50/p95。

総取引数0の反復は期待値をnullにし、その件数を保存する。主ゲートに必要な分布にnullが含まれる場合は、都合よく捨てて合格にせず `INVESTIGATE` とする。日次終端DDを分足含み損益DDと呼ばない。

### 9.3 その他の診断

10%取引欠落は `floor(0.10 * N)` 件を非復元抽出で除き、残りの元順序を保つ。取引bootstrapはN件を復元抽出する。順序shuffleは同じ損益集合の順序だけを変え、総netが不変であることをassertする。

出口の1分不利overlayは、同一セッションで厳密に1分後の適格バーがある取引だけ、元のexit referenceと次の始値の不利な方を使う。費用は固定し、未対応件数を保存する。これをEngineで再約定した結果とは表現しない。

## 10. 実験台帳と見送り理由

各セッションに1行の `session_audit.parquet` を作る。すべての理由を `reason_flags` に保存し、件数集計には重複しない `primary_outcome` を1つ付ける。

優先順位は `SESSION_MISSING` → `OPENING_INVALID` → `HISTORY_SHORT` → `HISTORY_INVALID` → `ZERO_BASELINE` → `ZERO_RANGE` → `NOT_COMPRESSED` → `NO_BREAKOUT` → `ENTRY_NOT_FILLED` → `TRADED_EXIT_ANOMALY` → `TRADED`。後半2項目は約定済み取引の有無とEngine結果で決める。対照では `NOT_COMPRESSED` を使用しない。

```text
予定セッション数 = 全primary_outcome件数の和
約定したセッション数 = TRADED + TRADED_EXIT_ANOMALY
約定したセッション数 = 完了取引数   # 未完了建玉0の場合のみ
```

ゲートが複数不成立でも、重複集計で予定セッション数を超えない。primary_outcomeの優先順位と、実際の情報利用可能順序は別概念であり、過去の売買判断を後知恵で変えない。

保存物の例：

```text
results/research/<unique-r003-campaign>/
  preregistration.json
  hypothesis.md
  config.yaml
  code_snapshot.zip
  data_manifest.json
  access_ledger.jsonl
  preflight/
  <experiment-id>/
    hypothesis.md
    config.yaml
    metrics.json
    trades.parquet
    orders.parquet
    fills.parquet
    equity.parquet
    session_features.parquet
    session_audit.parquet
    summary.md
    manifest.json
  sensitivity.json
  matched_control.json
  walk_forward.json
  resampling.json
  family_decisions.json
  frozen_candidate.json             # Development合格・品質解決時だけ
  DEVELOPMENT_COMPLETED.json
```

既存writerの保存形式に合わせて統合してよい。`equity.parquet` が日次実現損益なら、その粒度をmanifestに明記する。分足含み損益DDと同じ系列であるかのように扱わない。

ディレクトリは排他的に作成し、完了markerは必要ファイルの照合後にのみ作る。再実行は新しいIDとし、失敗したフォルダーや部分結果を上書きしない。生価格・特徴量・取引明細・コードsnapshot内の混入ファイルをGitへ追加しない。

## 11. OOSアクセスゲート

Development段階ではOOSの価格・特徴量を取得しない。`PASS_LIMITED` のままなら、たとえ経済条件を通過しても `INVESTIGATE` とし、候補manifestを作らない。

OOS用の凍結manifestは、研究ID、固定代表点、仕様・code・config・data・calendar hash、品質判定、全Development/WFAゲートの結果、開封する期間・5条件を含む。パスにファイルが存在するだけで合格とは判断せず、内容を検証する。

開封処理は排他ロック下で `OOS_ACCESS_REQUESTED` を永続記録してからデータを読む。読取り開始・対象範囲・成功/失敗を追記する。中断しても「未使用」へ戻さない。同じ凍結runの未読了部分を再開する場合も履歴を保持し、仕様変更後の再利用を独立検証と呼ばない。

Final Holdoutの解放コマンドは追加しない。2026価格へのアクセス試行は、CLI/loader/cacheの各境界で拒否する。

## 12. 実装後に使用するCLI案

**以下の `audit-data`、`--stage`、`--frozen-candidate` は追加予定であり、現在のCLIに存在すると断定しない。** 実装前は実行例として配布しない。既存の `research run --study-config ...` は共有された履歴に記載されているが、R003スキーマには対応追加が必要。

```powershell
# 実装後：品質監査のみ。売買損益を計算しない。
.venv/Scripts/python.exe -m n225m_bt.cli research audit-data `
  --study-config config/strategy_compression.yaml `
  --calendar-override config/local_calendar.yaml

# 実装後：事前登録・監査ゲートを検証してDevelopmentだけを実行。
.venv/Scripts/python.exe -m n225m_bt.cli research run `
  --study-config config/strategy_compression.yaml `
  --calendar-override config/local_calendar.yaml `
  --stage development

# 実装後：凍結manifestが全条件を満たす場合に限る。
# <campaign-id> は実際に作成されたIDへ置き換える。
.venv/Scripts/python.exe -m n225m_bt.cli research run `
  --study-config config/strategy_compression.yaml `
  --calendar-override config/local_calendar.yaml `
  --stage oos `
  --frozen-candidate results/research/<campaign-id>/frozen_candidate.json
```

R003にはstageの明示を要求し、省略時にOOSへ自動進行しない。旧設定を指定した既存コマンドの動作は回帰テストで保持する。preflight結果を複数段階で使う場合も、データ・コード・設定hashが一致することを再確認する。

## 13. 実装順序と完了条件

まず品質監査と因果的特徴量を実装し、合成データで確認する。次にR003/対照のStrategy、設定、研究runnerを接続する。最後に評価・保存・レポートを完成させ、10の受入条件を満たす。

ローカル実行を依頼された段階では、監査に通れば、凍結済み20条件、WFA、頑健性、最終判断・次の実験までを1キャンペーンとして完了する。監査に通らなければ、止まった理由と解決条件を残すことを完了とする。利益が出ることは実装完了条件ではない。

文書作成だけの依頼を、実データ研究・OOS開封・実売買への許可と読み替えない。本パッケージ作成時には実装・バックテスト・OOS開封・コミットを行っていない。

### 既存README / AGENTSへの追記案

READMEには、既存の棄却結果を消さず、次のリンクを加える。実行後の結果を先に書かない。

```markdown
- [R003：初動レンジ圧縮後のブレイク（設計済み・未実施）](docs/strategy/07_r003_compression_breakout_plan.md)
```

AGENTSには長い手順を複製せず、戦略の入口に次の1文を加える。

```markdown
R003の実装・検証では docs/strategy/07_r003_compression_breakout_plan.md を入口に、
品質監査は08、実装契約は09、受入テストは10を必要に応じて参照する。
```

これらは追記案であり、この文書パッケージはリポジトリ本体のREADME/AGENTSを書き換えない。

## 14. 結果報告テンプレート

`04_research_results.md` に追記するR003章は、少なくとも次の見出しを持つ。

```markdown
## R003 / Decision: NOT_RUN | REJECT | INVESTIGATE | CANDIDATE
### Hypothesis
### Implementation
### Parameters tested
### Development result
### Validation result
### Robustness result
### Problems discovered
### Decision
### Next experiment
### Reproduction / artifact locations
```

未読のOOSは `NOT_EVALUATED`、2026は `NOT_ACCESSED` とする。品質PASS、テスト通過、合格ゲートを実際の証跡なしに記入しない。
