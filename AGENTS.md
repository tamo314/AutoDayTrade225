# N225M 戦略研究

## 目的と完了条件

構築済みの1分足バックテスト基盤で、未知の期間にも期待値が残る説明可能な日中・ナイト戦略を研究する。最終目標は、過去情報で適用局面を判定する複数戦略とメタ戦略。利益が出ることを完了条件にせず、登録したバッチの判定・予算・停止条件で終了し、結果と未解決事項を残す。情報不足や候補なしでも完了できる。

## 研究の境界

- Development は 2021-01-01～2025-06-30、OOS は 2025-07-01～2025-12-31。分割は trade_date 基準。
- 2026-01-01以降は Final Holdout。最終候補の仕様・選定記録を凍結するまで、成績を取得・表示・分析しない。OOSを見た後の変更に同じOOSを未使用の検証期間として使わない。
- 仮説・探索範囲・判定基準を実行前に記録する。最大利益の一点を選ばず、近傍安定性、Walk Forward、コスト耐性、利益集中度で判断する。
- 基本評価は片道1 tick + 明示した手数料。0 tickは診断用。2～3 tick、手数料増、遅延も検証する。現在の手数料はユーザー指定の片道30円。
- data/raw/ は変更しない。225Laboの実データ・派生バー・取引明細はローカルに保持し、コミット・再配布しない。エンジン入力は正規化済みParquetのみ。
- エンジンは明確なバグ以外で変更しない。成績改善のために約定、Stop、コスト、取引時間、欠損補完を変えない。バグ修正は再現例、修正前後、PnL回帰テスト、既存実験への影響を docs/strategy/ に記録する。
- JSTの時刻、calendar_dateとtrade_dateの区別、版管理した取引時間、翌適格バー約定、保守的な同時Stop/Target判定、1枚・最大1ポジションを維持する。連続系列に実限月を捏造しない。スリッページを二重控除しない。
- 実験IDは一意にし、既存結果を上書きしない。コード・データ・設定・乱数seedを保存する。

## 必要な文書への入口

- 最初に読む現在の方針・作業範囲: docs/strategy/00_current_research_policy.md。新規実データPnLと旧自由文ループは停止中。設計・記録整理・合成検証は進める。
- Planner／Executor自動連携: docs/strategy/126_bounded_orchestration.md。固定タスク・成果物・回数上限を持つdesignバッチを実行できる。登録済み実データバッチはresearch executeへ渡す。Plannerの次タスク文で範囲・入力・予算を拡張しない。
- 登録実行・残枠・中断確認: docs/strategy/123_registered_execution_control.md。実データ研究は完全仕様・証拠・有限枠を登録し、research executeを使う。旧script直起動、台帳reset、失敗の枠返却、ID変更による再試行をしない。
- 研究開始・選定: docs/strategy/13_research_governance.md、docs/strategy/14_research_repair_plan.md。
- 既存結果照合・近縁仮説の再開判定: docs/strategy/120_research_reconciliation_and_finite_search.md、docs/strategy/121_research_inventory_index.md、docs/strategy/registry/20260916_review.json。新規提案前に閉鎖群・親仕様・既知結果を確認し、ID変更で探索枠を補充しない。
- API・研究機能: docs/strategy/02_research_design.md。
- 過去の結果集: docs/strategy/04_research_results.md。旧「次の実験」は現在の実行指示ではない。
- 基盤に触れるとき: DECISIONS.md と変更対象に対応する docs/infrastructure/ の仕様。時刻は05、約定・PnLは06、品質は07、基盤受入条件は11。

## 実装と検証

Python 3.12～3.13、Polars/Parquet、Pydantic v2、PyYAML、Typerを使用する。研究支援は src/n225m_bt/research/、売買判断は strategies/、研究文書は docs/strategy/。重い依存追加には理由を記す。

公開APIに型を付け、設定と乱数を注入可能にする。因果性・期間漏洩・PnL集計・実験保存の境界を合成データで検証する。経済的意味を変える修正には回帰テストが必要。変更に応じたpytest、Ruff、mypyを実行し、実験結果まで確認する。ローカル検証・研究実行は個別の承認待ちにしない。

並列化が明確に有効な独立作業以外はサブエージェントを起動しない。読取り・探索・機械的編集・テスト・単純修正を委任する場合はLunaを使う。
