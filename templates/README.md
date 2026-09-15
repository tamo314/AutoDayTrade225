# 設定・台帳の雛形

これらはRG-20260915-01で追加する**論理契約のJSON例**です。すべて`template=true`、`execution_authorized=false`、`DRAFT_NOT_FROZEN`で、既存のResearchConfigやCLIへそのまま渡す実行設定ではありません。

|ファイル|用途|
|---|---|
|[preregistration.example.json](preregistration.example.json)|完全な新仕様、四集合、目的別ゲート、統計、risk、freeze|
|[decision.example.json](decision.example.json)|原判定と多軸状態、既知0／未知null、段階判断|
|[source_semantics_evidence.example.json](source_semantics_evidence.example.json)|供給者仕様・時刻・価格・volume・roll証拠|
|[family_registry.example.json](family_registry.example.json)|最大3仕様版の空の再開バッチ、family、全試行履歴|
|[access_ledger_entry.example.json](access_ledger_entry.example.json)|段階別の読取要求・実際のアクセス・既読履歴|

null・空配列は未確定・未実施であり、既定PASSや0へ変換して実行してはいけません。実装担当が新schemaへ適合させ、必須項目・相互整合・価格範囲の検証を実装してください。本パッケージの検査はJSON構文と雛形の非実行状態だけを確認し、実際のrunnerとの互換性は確認していません。

[研究統治](../updated_docs/13_research_governance.md)と[修復計画](../updated_docs/14_research_repair_plan.md)を正本とします。
