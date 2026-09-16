# Architecture Decisions

## ADR-014: 登録済み有限バッチだけを共通入口で実行する（2026-09-16）

- `research execute`が完全一致するmanifest・review・閉鎖台帳・hash・累積枠を検査し、価格I/O前にSQLiteへ予約する。
- 同時起動、未修復の同一試行、ID/batch変更による予算復活、未予約の直接起動を拒否する。失敗・中断も消費を維持する。
- 自由文Planner/Executorのループは研究実行の権限とせず、登録された決定的なdispatchに置き換える。
- OOS/Final Holdoutのadapter、chunk途中再開は本変更に含めない。経済算式・旧結果は不変。
- 対応範囲・検証・運用は[123 登録実行管理](docs/strategy/123_registered_execution_control.md)。


## ADR-013: Research completion and validation access are separate gates (2026-09-16)

Registered batch verdicts, budget limits and blocking gates can end research without a profitable
candidate or OOS access. The legacy schema-v1 campaign now stops after Development and saves an OOS
review request instead of automatically loading OOS. Automation is paused before state mutation or
process dispatch. These controls do not change engine fills, fees or historical PnL and do not imply
that all individual runners implement the common budget/access contract. Current scope and evidence:
[policy review](docs/strategy/122_project_policy_review.md).

## ADR-001: Continuous series is research data, not an exact tradable contract history

225Labo center-series data is treated as a continuous research series. It is not assigned a fabricated contract code. `contract_month` is nullable and `series_type=center_continuous`.

## ADR-002: Raw/Bronze/Silver/Gold layers

- Raw: user-downloaded files, immutable.
- Bronze: decoded/parsed source rows with source fields preserved.
- Silver: canonical typed 1-minute bars with session/trade-date metadata.
- Gold: backtest-ready datasets plus derived flags/features.

## ADR-003: Parquet is canonical storage after ingestion

CSV is an interchange/source format only. All repeated research reads use Parquet.

## ADR-004: Time model

`ts_jst` is actual calendar timestamp in Asia/Tokyo. `trade_date` is OSE trading date. Night session bars can have a calendar date earlier than the exchange trade date.

## ADR-005: Versioned session schedules

Trading schedules change over time. Session classification must use effective-date rules, not one current schedule applied to history.

## ADR-006: Conservative OHLC execution

With only OHLC bars, intrabar path is unknown. When stop and target are both touched, V1 assumes the adverse event happens first unless a test explicitly uses a different policy.

## ADR-007: Signal/execution separation

Strategies return desired intent/signals. Execution determines fills, slippage, cost and position transitions.

## ADR-008: Next-bar execution

Signals calculated using close of minute `t` are first eligible for fill at the open of next eligible minute bar `t+1`.

## ADR-009: Integer accounting

N225M price is stored in integer JPY. Tick size is 5 JPY. Contract multiplier is 100 JPY per index point. PnL is stored as integer JPY where possible.

## ADR-010: One-contract V1

V1 uses at most one contract, no pyramiding, no averaging down. Position sizing is a later extension.

## ADR-011: Explicit missing data

Missing minutes are detected and flagged. Do not create synthetic flat bars unless a future feature explicitly requests it and labels them synthetic.

## ADR-012: No third-party backtesting engine in V1

Execution semantics are central to correctness, so V1 uses a small custom engine with transparent event ordering.
