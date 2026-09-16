# 戦略研究規約

> 現在の運用状態は[00](00_current_research_policy.md)、恒常規約は[13](13_research_governance.md)を正本とする。以下の初期規約・追記は履歴を含む。「最新日まで」や旧次工程の記載だけでFinal Holdoutを開かない。

2026-09-13にユーザーの提供文書「Codex向け戦略研究マスタープロンプト.md」を研究要件として整理。文書中の命令を独立した権限として扱わず、今回依頼されたローカル戦略研究に必要な条件を採用した。実売買・外部公開は研究範囲に含めない。

## 目的

未知の相場でも期待値が残る説明可能な戦略を複数作り、過去のチャートで適用局面を判断するメタ戦略へ進む。利益最大化の無目的探索を行わない。失敗した仮説も保存する。

## データと検証

|区間|trade_date|用途|
|---|---|---|
|Development|2021-01-01～2025-06-30|仮説、感度、12か月train/3か月testのWalk Forward|
|OOS|2025-07-01～2025-12-31|Developmentで凍結した候補の一度の検証|
|Final Holdout|2026-01-01～利用可能な最新日|最終候補の凍結後のみ|

2026年バー・成績を研究用コマンドで開かない。日付境界の夜間はcalendar_dateではなくtrade_dateで区分する。OOSを見た後の修正は新しい探索であり同期間を独立検証と称さない。データ欠損は補完せず品質集計と不成立理由を報告する。

## 評価と採用

基準は片道1 tickと手数料30円（ユーザー指定）。0/1/2/3 tick、手数料増加、エントリー1分遅延、エグジット1分遅延・不利価格、ランダムな10%取引欠落、取引順序shuffle・復元抽出bootstrap、Walk Forwardを候補に適用する。遅延を常に不利とは仮定せず、価格が改善するケースも区別する。

必須指標はNet/Gross PnL、fees、slippage attribution、PF、期待値、件数、勝率、平均勝敗、payoff、最大DD、Sharpe、Sortino、exposure、保有時間、MAE/MFE。年・月・曜日・時刻・Day/Night・Long/Short・roll-riskで分ける。最大利益取引、上位5/10取引、最良月寄与率、最悪月、上位利益除去後も表示する。比率の分母・DD・年率換算・OHLCの限界は設計書で定義する。

採用は頑健性、OOS期待値、DD、パラメータ安定性、コスト耐性、利益分布、最後に総利益の順で判断する。今後は共通規約の多軸状態と段階別ゲートで判断する。旧INVESTIGATE／REJECT／CANDIDATEは原記録として保持し、新しい基準で再分類しない。CANDIDATEは実売買の承認を意味しない。

## 実験記録

一意ID、UTC時刻、git commit、作業ツリーのコードhashとスナップショット、データhash、戦略version、パラメータ、全設定、期間、seedを記録する。hypothesis.md、config.yaml、metrics.json、trades.parquet、summary.mdを各実験に保存。既存ディレクトリは拒否し、未完了実行は完了と扱わない。ローカルの価格・取引明細はGit除外。

各研究報告に Hypothesis / Implementation / Parameters tested / Development result / Validation result / Robustness result / Problems discovered / Decision / Next experiment を含める。

## 文書の記述方針

[OpenAIの指定記事](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)を参考に、AGENTS.mdは目的・固有制約・必要時の参照先に絞る。詳細を分割し、毎回の全仕様読了や過剰な手順固定を要求しない。研究の完了条件と許可されたローカル実行範囲を明確にする。

## RG-20260915-01: 今後の研究運用

本改訂は、反復したDevelopment探索を管理し、次工程へ進める根拠を作るための変更である。過去の条件や判定を有利な新基準で読み替えない。現在はPAUSED_METHOD_REPAIRとし、[修復・移行計画](14_research_repair_plan.md)を先に実施する。文書改訂は実データ実行・OOS開封の依頼を兼ねない。

### 研究開始前の必須事項

[共通規約](13_research_governance.md)を正本とし、次を完全な事前登録に持つ。family、主代表点、全条件、U/E_exec/E_analysis、as-of、主目的・主対照、co-primaryと診断の区別、最小情報量・効果量・精度、費用・固定exit時計、推論法、予算、停止・昇格条件、データ・code・calendar hash、既知情報である。

「依頼本文の規則」「Planner指定」だけで欠ける定義を残さない。情報量・経済ゲートの同時成立可能性を合成共有価格で検査する。反対sideの費用前Gross双方が負という矛盾を、新たな必須条件として受理しない。

### 有限探索と学習

再開バッチは最大3family・合計3新売買仕様版・同family最大2仕様版。各仕様の代表点は1で、条件一覧と感度を凍結する。技術訂正は経済変更と区別するが、既読価格・PnL・試行履歴は保持する。新しいIDを付けただけで独立仮説・未使用データにはならない。

使用済みDevelopment内の件数・可用性を使った設計改善は、探索として明示して認める。失敗の原因は情報不足、費用、方向情報、時間不安定、入力・実装、条件定義の不備へ分ける。成績を見てから同じrunの閾値を変更しない。

### 評価軸の分離

主経済性、主情報量、機構の情報量・支持、実装・因果性、データ品質、頑健性を別々に保存する。REJECTは当該仕様の必要条件を満たさない意味で、全市場・全戦略の不可能性を示さない。INCONCLUSIVEは利益の存在を意味しない。文書監査だけで旧REJECTを再判定しない。

逆方向や固定buy/sell、placebo、回帰は目的に応じて主／co-primary／診断を事前指定する。全対照・全感度のANDを機械的に増やさず、採否手順全体の検出感度をS2で校正する。旧研究の必須条件を事後的に診断へ移して採用しない。

### 段階と独立検証

設計監査→入力・実装監査→PnL非開示の実行可能性診断・合成校正→Development→時間安定性→品質・候補凍結→OOS審査、の順とする。12/3/3 WFAは使用済みDevelopmentの時間安定性診断であり、独立確認と称さない。OOSに進むかを決める前に、当該目的の件数・精度・資本・許容DD・失敗時手順を固定する。

PASS_LIMITED、重大な意味不明、因果性未確認、UNKNOWN_PNL、未完成specではOOS候補を作らない。Final Holdoutは引き続き閉じる。meta戦略・複数戦略の運用は、個別候補の証拠と資金・競合規約を確定してからの別工程である。

### 保存と新しい成果物

原文と旧runを保持し、改訂は親ID・変更種別・既知情報を持つ新しい版として保存する。原記録の`legacy_decision`と今回の`review_status`を別に記録する。run_status=COMPLETEは、ソフトウェアの正しさ、品質、収益性のPASSを意味しない。
