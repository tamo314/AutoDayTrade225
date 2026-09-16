# 文書案内

最初に[現在の研究方針](strategy/00_current_research_policy.md)を読む。全文書を毎回読む必要はない。

## 戦略研究

- 運用状態・読み順：[00 現在方針](strategy/00_current_research_policy.md)
- 恒常規約：[13 研究統治](strategy/13_research_governance.md)
- 横断整理：[120 結果と有限探索](strategy/120_research_reconciliation_and_finite_search.md)
- 個別研究への索引：[121 対応表](strategy/121_research_inventory_index.md)
- 方針・実装の再点検：[122 レビュー](strategy/122_project_policy_review.md)
- 実行・残枠・中断確認：[123 登録実行管理](strategy/123_registered_execution_control.md)
- 次の候補・初期指示：[124 引継ぎ](strategy/124_next_research_orchestrator_handoff.md)、[125 初期タスク](strategy/125_next_research_initial_task.md)
- Planner／Executorの起動：[126 有限オーケストレーション](strategy/126_bounded_orchestration.md)
- 自律継続の現行手順：[128 自律継続](strategy/128_autonomous_orchestration.md)、[129 C01 S1カレンダー検証](strategy/129_c01_s1_calendar_causality.md)、[130 C01 S2入場判定](strategy/130_c01_s2_admission.md)、[131 C01例外レビュー](strategy/131_c01_exception_review.md)

01～119の計画・結果・監査は履歴。120/121とregistryは2026-09-16の棚卸しsnapshotで、将来の実行許可ではない。新しい運用変更は00に反映し、旧runの仕様・結果と区別する。

## 基盤

- [設計判断](../DECISIONS.md)
- [アーキテクチャ](infrastructure/02_architecture.md)
- [データ契約](infrastructure/04_data_contract.md)
- [約定・PnL](infrastructure/06_backtest_execution_semantics.md)
- [CLIと出力](infrastructure/09_cli_and_outputs.md)
- [基盤受入条件](infrastructure/11_acceptance_criteria.md)

基盤仕様のCLI例は、現在の研究実行許可を意味しない。実データ、取引明細、成果物はローカルのdata/resultsに保持する。
