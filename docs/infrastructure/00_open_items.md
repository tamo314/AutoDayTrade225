# Open specification items

The following items are intentionally represented as explicit configuration,
quality state, or TODOs. Synthetic implementation work may proceed where independent of them,
but unresolved time/price semantics block dependent real-data PnL; unresolved execution provenance blocks candidate promotion.
The blanket non-blocking interpretation of Phases 0--8 is superseded by RG-20260915-01.

| Item | Treatment |
|---|---|
| Complete OSE trading-day/holiday calendar | A user-supplied CSV/YAML override is supported. Missing mappings fail normalization instead of assuming Japanese bank holidays. |
| 225Labo CSV headers and date/time representation | The adapter detects encoding, delimiter, headers, and candidate mappings; unresolved required columns fail with candidates. |
| Precise historic auction record convention | Session boundaries are classification rules; expected-minute checks do not invent bars. Fine-grained historic auction definitions remain TODO. |
| Center-series rollover/SQ definition | `roll_risk` is a conservative, injectable marker. No contract code or fabricated contract month is inferred. |
| Stop execution rule beyond V1 stop-market default | V1 uses adverse stop-market behaviour and exposes the policy in configuration. |
| Exact treatment of an entry on force-flat cutoff | Reject new entries; do not suppress an existing position's EXIT callback. Verify pending EXIT/force-flat ordering and missing-bar behavior against the versioned execution contract before use. |

## RG-20260915-01: 現在の修復優先度と作業許可

現状を `PAUSED_METHOD_REPAIR` とする。これは過去runのBLOCKEDへの一括変更ではなく、新たな売買仮説の自動実行を止める運用状態である。利益が出ないこと自体をデータ修復の理由にしない。

|優先|項目|現在の扱い|解除に必要な証拠|
|---|---|---|---|
|P0|R031の同時成立不能ゲート|文書上の欠陥。旧REJECTは保存|gateの算術検査・共有価格の合成証人|
|P0|E_execとE_analysisの混同|R046等の利用制限を追記|loader/QC/集合/発注までのprefix監査|
|P0|時刻ラベル・価格意味・出来高・roll|未確認部分は未確認のまま|供給者仕様・来歴・用途別quality gate|
|P1|条件side/quantile・日次軸・回帰行|旧訂正runと親を保持|共通条件台帳と全件整合テスト|
|P1|R032の表示平均差|正値・CIは未確定|保存台帳と集計式の読取り専用照合|
|P1|研究family・予算・独立検証への遷移|新規規約を文書化、実装未確認|完全なregistryと段階別manifest|
|P1|R065の保有・時刻注文|REVIEW_REQUIRED|専用profile、期間境界、因果性・リスク仕様|

文書・合成fixtureの作業は進められる。実データの仕様監査はDevelopment範囲とアクセス方法を先に固定する。重大な時刻／価格の意味が不明なら、その価格を使うPnLは停止する。限定的な研究価格として説明可能な範囲だけをPASS_LIMITEDとし、OOS候補への昇格は認めない。

段階の正本は [修復・移行計画](14_research_repair_plan.md)。監査回数を増やすだけで同じ不明を永久に持ち越さず、証拠取得の可否と代替データ版の必要性を判断する。問い合わせ・購入・実行を本書の存在だけで開始しない。
