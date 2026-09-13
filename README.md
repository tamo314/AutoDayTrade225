# 日経225mini 戦略研究

構築済みの1分足バックテスト基盤を使い、局面ごとに期待値のある戦略と、過去情報だけで切り替えるメタ戦略を研究します。中心限月連続系列による研究結果と実運用可能性を区別します。

- [研究規約・期間分割](docs/strategy/01_research_protocol.md)
- [既存APIと研究機能の設計](docs/strategy/02_research_design.md)
- [事前登録した仮説と実験計画](docs/strategy/03_experiment_plan.md)
- [価格帯突破の追加仮説](docs/strategy/06_breakout_plan.md)
- [検証結果と次の実験](docs/strategy/04_research_results.md)

Development: 2021-01-01～2025-06-30、OOS: 2025-07-01～2025-12-31。2026年以降は最終候補の凍結まで未開封とします。期間判定はOSEの trade_date を使います。

## 研究実行

```powershell
uv sync --group dev
.venv/Scripts/python.exe -m n225m_bt.cli validate
.venv/Scripts/python.exe -m n225m_bt.cli research run --calendar-override config/local_calendar.yaml
.venv/Scripts/python.exe -m n225m_bt.cli research run --calendar-override config/local_calendar.yaml --study-config config/strategy_breakout.yaml
```

研究条件は config/strategy_research.yaml、約定・手数料は config/backtest.yaml に記録します。ユーザー指定の手数料は片道1枚30円です。研究実行は専用の期間制限つきローダーを使用し、centerとforwardを混在させません。実験ごとの仮説、設定、指標、取引明細、コードとデータの識別情報を results/research/ に保存します。結果は上書きしません。初回は追随・反転・価格帯突破の3仮説、計45実験を完了し、いずれもDevelopmentで棄却しました。OOSと2026年のFinal Holdoutは未使用です。詳細と次の仮説は上記の検証結果文書を参照してください。

## 開発用検証

```powershell
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy
```

基盤の仕様・データ取り込みは [infrastructure](docs/infrastructure/09_cli_and_outputs.md) を参照してください。実データは data/raw/ 以下に保持し、変更・コミット・再配布しません。バックテストはGold Parquetを読みます。夜間には config/local_calendar.yaml のOSEカレンダーを指定します。旧 backtest run は基盤確認用のalways-flatデモです。戦略研究には期間制限のある research run を使用してください。
