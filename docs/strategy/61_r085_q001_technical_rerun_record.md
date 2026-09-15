# TASK-R085-Q001 技術的再実行記録

`r085-q001-20260915-day-night-reopen-gap-fade-01` は、事前登録、入力監査、S2、および全固定profileのtrade/daily-axis保存後、結果判定前の`one_trade_per_day_event`監査でFAILした。

原因は経済規則・価格・約定・費用・集計ではない。runnerがeventごとに独立したengineを起動するため、各engine内の最初のtrade IDがいずれも`trade-000001`となる。一方、監査は全event横断でこのengine局所IDの一意性を誤って要求した。このID重複は同日重複注文、複数建玉、または重複PnLを表さない。

初回出力は不変保存する。`-02` は、`trade_id`でなく対応night `trade_date`／eventの一意性を検査する技術訂正だけを行う。同じsource/config/規則・期間・感度・seedを用い、閾値、保有時間、方向、制度版、費用または対象集合を変更しない。初回にDevelopment PnLが既に生成された事実は`prior_information_seen=true`として保持し、この技術訂正を独立検証とは扱わない。
