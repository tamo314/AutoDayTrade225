# 09. 実装済みCLIと出力

## データの取り込み

```powershell
.venv/Scripts/python.exe -m n225m_bt.cli validate
.venv/Scripts/python.exe -m n225m_bt.cli data inspect data/raw/225labo/center/N225minif_2024.xlsx
.venv/Scripts/python.exe -m n225m_bt.cli data build-calendar --source-root data/raw/225labo/center --source-root data/raw/225labo/forward --output config/local_calendar.yaml
.venv/Scripts/python.exe -m n225m_bt.cli data ingest data/raw/225labo/center/N225minif_2024.xlsx --calendar-override config/local_calendar.yaml
```

build-calendarは元ファイルの日付列からローカルカレンダーを作る。rawは変更しない。新規データの投入・カレンダー変更は既存実験とは別のデータ版として扱う。Gold研究入力の年次重複処理は [データ重複記録](../strategy/05_data_overlap.md) を参照。

forwardを取り込む場合は data ingest に --dataset forward --series-type next_continuous を加えてcenterと分離する。

## 戦略研究

```powershell
.venv/Scripts/python.exe -m n225m_bt.cli research run --calendar-override config/local_calendar.yaml
```

研究の仕様・結果は [docs/strategy](../strategy/01_research_protocol.md)。旧 backtest run はalways-flatによる基盤確認用で、任意戦略選択や期間制限を備えない。研究には専用コマンドを使う。旧文書の config validate / data quality / backtest sweep / report build は実装済みCLIではないため、例から削除した。

## 保存物

基盤のwrite_resultsは trades/orders/fills/equity Parquet、metrics JSON/Markdown、run_manifestを出力する。研究では別の排他的ディレクトリ作成と詳細集計を重ねる。ローカルの結果と実データはGit除外。
