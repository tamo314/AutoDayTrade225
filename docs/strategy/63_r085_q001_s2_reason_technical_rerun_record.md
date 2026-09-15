# TASK-R085-Q001 S2 reason分類の技術的再実行記録

`r085-q001-20260915-day-night-reopen-gap-fade-03`はPnL前S2で停止した。2021-01-04のnight公式`trade_date`はDevelopment内だが、対応DAYは2020-12-30でDevelopment外である。この境界は事前登録済みの、価格を読まない`DAY_PARTNER_OUTSIDE_DEVELOPMENT`である。

S2の「説明不能除外」一覧がこの明示reason prefixだけを許可集合へ登録し忘れ、誤って1件のunexplained exclusionと分類した。`-04`ではreason分類だけを訂正する。イベント母集団、価格読取り、取引規則、費用、閾値、感度、seedは変更しない。
