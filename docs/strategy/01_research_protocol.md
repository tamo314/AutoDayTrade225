# 戦略研究規約

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

採用は頑健性、OOS期待値、DD、パラメータ安定性、コスト耐性、利益分布、最後に総利益の順で判断する。十分な根拠がないものはINVESTIGATE、仮説・事前基準を満たさないものはREJECT。CANDIDATEは実売買の承認を意味しない。

## 実験記録

一意ID、UTC時刻、git commit、作業ツリーのコードhashとスナップショット、データhash、戦略version、パラメータ、全設定、期間、seedを記録する。hypothesis.md、config.yaml、metrics.json、trades.parquet、summary.mdを各実験に保存。既存ディレクトリは拒否し、未完了実行は完了と扱わない。ローカルの価格・取引明細はGit除外。

各研究報告に Hypothesis / Implementation / Parameters tested / Development result / Validation result / Robustness result / Problems discovered / Decision / Next experiment を含める。

## 文書の記述方針

[OpenAIの指定記事](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)を参考に、AGENTS.mdは目的・固有制約・必要時の参照先に絞る。詳細を分割し、毎回の全仕様読了や過剰な手順固定を要求しない。研究の完了条件と許可されたローカル実行範囲を明確にする。
