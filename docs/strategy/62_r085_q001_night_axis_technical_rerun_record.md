# TASK-R085-Q001 NIGHT trade_date軸の技術的再実行記録

`r085-q001-20260915-day-night-reopen-gap-fade-02` はS2と会計監査を通過したが、scheduled axisを対応DAYの`trade_date`で保存していた。TASK-R085-Q001は各**night session**を対象にしており、OSEの公式`trade_date`はそのnight sessionに付く翌取引日である。これではDevelopment端点、年別の分母、およびMBB日次系列のラベルが仕様と一致しない。

`-02` は不変保存し採用しない。`-03`はscheduled axisをDevelopment内NIGHT公式`trade_date`へ訂正し、そこから`previous_trade_date`のDAYをDとして一対一対応を検査する。この変更以外の経済仮説、閾値、状態、entry/exit、費用、感度、seed、データ版は不変である。既にDevelopment PnLを読んだ事実を保持し、再実行を独立確認とは扱わない。
