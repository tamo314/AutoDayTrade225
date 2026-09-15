# R1監査のBLOCKレビューと次の作業

作成日：2026-09-15 JST  
根拠：添付`16_r1_logical_causality_audit.md`の全文、および同会話で提供済みの改訂規約・修復計画・タスク指示書。外部調査はしていません。実リポジトリ・テストログ・市場データ・実run台帳は今回監査していません。

## 結論

報告書で明示されているBLOCKは、R065の要求する経済能力と共有BacktestEngineの非適合を検出した能力guardです。収益性のREJECTでも、R1追加コード全体のテスト失敗でもありません。R065のBLOCKを解除せず、共通のR1決定パイプライン結合を継続することを提案します。

直ちに渡す指示は`01_TASK-R1-02_runner_integration.md`だけです。`02_TASK-R65-EXEC-DESIGN-01_optional.md`はR065を残すと決めた場合の別依頼であり、一括自動実行しません。

## 報告から確認したこと

|項目|報告にある到達点|今回の評価・残件|
|---|---|---|
|M01条件監査|R031型矛盾拒否・条件モジュール・仕様JSON監査|追加済みとの報告。CLI静的監査のwitness/causalityはNOT_RUNで、全体受入ではない|
|M02～M04|R046/R049/R060～R064のdecision-time adapterとfuture欠損単体テスト|source-levelの契約確認。loader/QC/U/runner結合は未実施|
|selection/execution|freeze_selectionとrecord_execution、状態不変テスト|境界追加済みとの報告。既存runnerへ未接続|
|M06/R032|円／取引と円／対象nightの異なるestimandをコードから識別|単純な算術ミスと断定しない。実台帳・日次軸・bootstrapとの数値照合は未実施|
|M10/R065|calendar-only profileと予定order plan、一部合成検査|order planと執行能力は別。共有engineの非対応を理由に実行接続はBLOCKED|
|データ意味・実データ研究|未実施・未読との報告|品質PASS、価格アクセス履歴全体の独立証明、旧run影響ゼロとは扱わない|

報告のテスト件数は変更時点・対象が異なり、重複し得るため合算しません。PASSは「文書にその結果が記載されている」証拠であり、本レビューで再実行した結果ではありません。

## BLOCKの理由（報告L83～L87）

共有engineはbar-close signalとnext eligible bar open pending注文を持ち、予定openに事前発行するcalendar-scheduled order型を持たないと報告されています。さらにbaselineのforce-flat・session跨ぎpending取消、末端で最後の観測価格を使うEND_OF_DATA決済が、新しいR065契約を満たしません。

したがって、force-flatだけをOFFにする、pendingの遅延上限だけを長くする、fake barで事前signalを作る、旧fillを後から消してnullにする、という回避は提案しません。

## R032の解釈を更新

報告L73～L75によれば、表示された各群平均とbootstrap比較は単位・母集団・分母・重みが異なります。

確認／非確認の費用前合計をGc/Gn、取引数をNc/Nn、対象night数をTとする概念例では、

- 1取引平均差：Gc/Nc − Gn/Nn
- 同じ予定night軸の0円日を含む日次差：(Gc − Gn)/T

は異なる量です。従来の「表示平均の単純差と記載差が一致しない」という指摘は、まずestimandの表示・対応の問題として監査を続けます。式は説明用であり、元台帳の数値やCIを再計算・訂正したものではありません。R032の旧判定は維持します。

## 次の順序（提案）

1. TASK-R1-02：R065の依存分離、共通決定runnerへの合成結合、全処理経路の未来変更テスト。
2. R2：未完了であればデータ意味・来歴の証拠確認。R065別executorの完成を前提にしない。
3. R3-A：評価基盤・単位・nullの共通化。R032の台帳読取りは個別許可した限定監査として実施。
4. R065を再開候補に残す場合のみ、TASK-R65-EXEC-DESIGN-01から別executor設計へ進む。実装はさらに別依頼。
5. 品質・共通基盤・完全な仕様・校正を満たした候補だけを、別依頼によってDevelopmentへ進める。

これは必須ゲートを免除する変更ではなく、対象別に依存を分離する提案です。R1全体の残件を隠して完了とせず、PAUSED_METHOD_REPAIRとOOS/Final Holdoutロックは維持します。

## このパッケージの適用

原文`16_r1_logical_causality_audit.md`は変更していません。本パッケージは指示書の提案です。実行担当へ渡した後、実コード・既存schemaと照合し、実測結果を元の監査記録へ追記してください。
