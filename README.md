# 日経225mini 戦略研究

構築済みの1分足バックテスト基盤で、説明可能な戦略と過去情報による切替を研究します。再現できる不採用・情報不足・探索終了も研究成果です。中心限月連続系列の研究結果と実運用可能性を区別します。

- **最初に読む：[現在の研究方針・再開条件](docs/strategy/00_current_research_policy.md)**
- [文書案内](docs/README.md) / [研究統治](docs/strategy/13_research_governance.md)
- [結果の整理と有限探索](docs/strategy/120_research_reconciliation_and_finite_search.md) / [全研究索引](docs/strategy/121_research_inventory_index.md)
- [方針レビューと実装修正](docs/strategy/122_project_policy_review.md) / [登録実行管理・操作手順](docs/strategy/123_registered_execution_control.md)

Development: 2021-01-01～2025-06-30、OOS: 2025-07-01～2025-12-31。2026年以降のFinal Holdoutは閉鎖中です。凍結だけで自動開封しません。期間判定はOSEのtrade_dateを使います。

## 現在の作業範囲

設計・記録整理・合成検証を進めます。Planner／Executorは[有限バッチ](docs/strategy/126_bounded_orchestration.md)で自動連携できます。新しい実データPnL探索と旧自由文ループは停止を維持し、`config/research_execution.json`は停止・grant 0件です。実データ計画は仕様・証拠・有限枠を登録して`research execute`から実行します。旧ループの設定解除や`--reset`では枠を作れません。

### Planner／Executorの開始

プロジェクトrootで実行します。現在の既定バッチはC05のR103設計閉鎖監査で、実データ・価格・PnL・外部時系列を使いません。モデル・CLI設定は既存の`config.json`を使います。

```powershell
.venv/Scripts/python.exe orchestrator.py --check
.venv/Scripts/python.exe orchestrator.py
```

状態だけを見る場合は `--status`、途中で区切る場合は `--once`。同じ開始コマンドで保存状態から続行します。`--reset`は使えません。現在は[自律継続モード](docs/strategy/128_autonomous_orchestration.md)で、C01の研究準備を再設計・修正・検証し、全完了条件と最終監査が通るまで継続します。旧3/5回上限は適用せず、重大障害や連続失敗・停滞は未完了として停止します。

環境と設定だけの確認:

```powershell
uv sync --group dev
.venv/Scripts/python.exe -m n225m_bt.cli validate
.venv/Scripts/python.exe -m n225m_bt.cli research execution-status
```

`research run`や旧scriptの直接起動は無予約なら停止します。登録実行は初回・再呼出しで同じ台帳を確認し、失敗・中断・別IDでも消費済み枠を保持します。対応adapterと再開手順は[123](docs/strategy/123_registered_execution_control.md)を参照してください。OOS/Final Holdoutはこの入口では実行できません。

手数料は片道1枚30円、基準スリッページは片道1 tickを維持します。実験の仕様・設定・結果・hashは`results/research/`へ一意IDで保存し、上書きしません。

## 開発用検証

```powershell
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy
```

基盤の仕様・データ取り込みは[CLIと出力](docs/infrastructure/09_cli_and_outputs.md)を参照してください。実データは`data/raw/`以下に保持し、変更・コミット・再配布しません。バックテスト入力は正規化Parquetのみです。基盤CLIの存在は研究実行の許可を意味しません。
