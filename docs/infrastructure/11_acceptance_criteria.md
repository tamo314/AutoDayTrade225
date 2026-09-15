# 11. Acceptance Criteria

## A. Project quality

- [ ] `pytest` passes from clean checkout without proprietary data.
- [ ] `ruff check .` passes.
- [ ] formatter check passes.
- [ ] `mypy` passes at the agreed strictness level.
- [ ] CLI help works.

## B. Configuration

- [ ] All provided YAML examples validate.
- [ ] Unknown critical keys are rejected or warned clearly.
- [ ] Invalid tick size/multiplier fails fast.
- [ ] Overlapping session effective ranges fail fast.

## C. Calendar/session

- [ ] Day session on 2021-09-20 uses regime A.
- [ ] Day session on 2021-09-21 uses regime B; night selection is separately based on its actual start date.
- [ ] Day session on 2024-11-04 uses regime B.
- [ ] Day session on 2024-11-05 uses regime C; a night starting before the change retains its start-date regime.
- [ ] JST timezone is retained.
- [ ] Day/night session classification is correct at boundaries.
- [ ] Trade-date/calendar-date distinction is tested with an evening night-session bar.

## D. Ingestion

- [ ] UTF-8 and CP932 synthetic fixtures ingest.
- [ ] Explicit source mapping works.
- [ ] Auto-detection reports mapping before normalization.
- [ ] Missing required price column fails with actionable error.
- [ ] Source SHA-256 and row lineage are persisted.
- [ ] Raw file bytes are unchanged after ingestion.

## E. Data quality

- [ ] Duplicate conflicting timestamp detected.
- [ ] Impossible OHLC detected.
- [ ] 5-yen tick violation detected, not rounded.
- [ ] Missing expected minute detected.
- [ ] Statistical price jump is flagged/reported but not automatically deleted.
- [ ] Fatal quality issue prevents Gold generation in strict mode.

## F. Backtest causality

- [ ] Close-of-t signal never fills before next eligible bar.
- [ ] Buy slippage worsens price upward.
- [ ] Sell slippage worsens price downward.
- [ ] 1 tick equals 5 price points and 500 JPY/contract economic value.
- [ ] Long/short PnL formulas pass exact integer examples.
- [ ] Stop/target same-bar ambiguity uses conservative outcome by default.
- [ ] Gap-through stop does not fill optimistically at unreachable stop price.
- [ ] No pyramiding in V1.
- [ ] Forced-flat behavior respects historical session close.

## G. Output/reproducibility

- [ ] Every run writes run manifest.
- [ ] Every completed trade has gross, fees, slippage attribution, net PnL.
- [ ] Re-running same deterministic case produces byte-equivalent or semantically equivalent ledgers.
- [ ] Dataset manifest contains source hashes and schema version.
- [ ] No 225Labo actual data is present in repository artifacts.

## H. Performance

- [ ] Synthetic/representative multi-million-row Parquet scan is feasible on a normal workstation without reading the entire raw CSV repeatedly.
- [ ] Backtest does not perform pathological per-row DataFrame concatenation.

## I. Decision-time causality (RG-20260915-01)

以下は追加受入仕様。チェック欄は未実施であり、パッケージの文書検査通過とは別である。

- [ ] 全pipelineのfuture mutationで、cutoffまでのQC・U・E_exec・特徴量・signal・orderが一致する。
- [ ] R046型の後刻placeboのzero/nonzero変更で、前刻主戦略の注文が変化しない。
- [ ] R049型の後刻anchorの無効化で、前刻の適格candidate・最初のevent選択が変化しない。
- [ ] 未来のexit/感度足欠損で既約定entryを遡及除外しない。
- [ ] E_analysisの変更は対応付き診断だけに作用し、主E_execに戻らない。
- [ ] future fill/exitが変わり得るケースを誤って不変とassertしない。
- [ ] bar開始／終了ラベルとavailable_atの根拠がなく、minute境界を推測する入力を拒否する。

## J. Specification logic and shared conditions

- [ ] R031型の同一経路・反対side・0tick費用前の「両平均<0」を矛盾として拒否する。
- [ ] 各主／co-primaryゲートの共同成立例を、整合する一つの合成価格経路から生成できる。
- [ ] A⊂B、B=A∪C、排他性、first-event、rootとは異なるside/quantileを合成と台帳で検査する。
- [ ] selection_statusとexecution_statusの混同で、回帰eventが消える実装を拒否する。
- [ ] Satisfiability検査、数値のnull・単位・不等号、検出感度の記録がfreezeに結び付く。
- [ ] 未指定の閾値、nuisance、seed、「依頼本文参照」だけの不完全計画を新規実行しない。

## K. Outcome, axes, accounting, reporting

- [ ] 既知無取引0、費用あり取消、UNKNOWN_PNL、OPEN_POSITIONを区別する。
- [ ] 未確定損益を除いて合計した部分成績を、完全Net・Sharpe・CIとして表示しない。
- [ ] metricsとbootstrapが同じscheduled_axis hashと日数を使う。
- [ ] R032型の「表示平均差」と「記載差」の不一致を検出し、estimand・重み・CI中心を照合する。
- [ ] entry前後・exit後OHLCの扱いを守り、実現DD・終値mark DD・日次DDを混同しない。
- [ ] reference gross・fill gross・fees・slippage・netを照合し、追加costは明示する。

## L. Governance, migration, access

- [ ] family/study/spec/runを別々に保存し、経済改訂と技術訂正を区別する。
- [ ] 再開バッチの最大3family・合計3新売買仕様版・同一family最大2を超過させない。
- [ ] 経済性、機構診断、情報量、頑健性、実装を独立状態で保存する。
- [ ] 旧REJECT/INCONCLUSIVE/BLOCKEDと原版hashを保持し、文書改訂で再判定しない。
- [ ] PASS_LIMITED・未テスト・不完全spec・未知損益ではOOS候補を作らない。
- [ ] OOSは内容検証と事前アクセス台帳を必要とし、失敗・中断後も既読を未読へ戻さない。
- [ ] Final Holdoutの価格・cache・価格由来QCを拒否する。
- [ ] 既存CLI回帰と追加stageの拒否条件を実測し、未実装CLIを案内しない。

## M. R065 and execution-clock profiles

- [ ] new-entry cutoff後も保有EXITを許可し、pending EXIT/force-flatを一度だけ処理する。
- [ ] actual_fill_anchorとscheduled_entry_anchorを明示し、旧R003と後続固定exitを混同しない。
- [ ] cross-sessionは専用profileのみ。通常session_flatの経済契約を変更しない。
- [ ] 事前時刻注文はopen観測前に確定し、closeベース注文を同じopenへ遡及しない。
- [ ] n0/d0/n1、休日・制度版・最大保有・Development末端・不明exitを合成検査する。

合格の記録にはtest名、実行コマンド、対象code/config hash、実際の終了状態を添える。チェックを埋めるために品質・費用・期間保護を緩和しない。
