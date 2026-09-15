# R071-Q001: ナイトopening range突破継続 — REJECT

タスクID: `TASK-R071-Q001`。正本成果物は
`results/research/r071-q001-20260915-night-opening-range-continuation-02/`である。
Development trade_date `2021-01-01..2025-06-30`のみを使用し、OOSとFinal Holdoutは
いずれも`NOT_ACCESSED`である。

事前登録は[22_r071_q001_night_opening_range_continuation.md](22_r071_q001_night_opening_range_continuation.md)
に保存した。16:30--16:59の30本OHLCでrangeを固定し、17:00--23:30に最初にstrict closeで
rangeを突破した方向へnext eligible openで入り、同一trade_dateの05:30 openで出た。休場を
跨ぐcalendar組合せと、2024-11-05以降の17:00開始sessionは事前既知の無取引0円である。

## S2

予定軸は1,131 trade_date、実行可能signal日は754（2021: 187、2022: 193、2023: 203、
2024: 171、2025H1: 0）であった。long 392、short 362であり、`>=400`、各2021--2024年
`>=60`、方向別`>=120`のS2を通過した。signal日はすべてentry/exit可観測である。

## S3と判断

主戦略は754取引、Net **-982,740円**、PF **0.864**、scheduled-axis日次平均Net
**-868.91円**だった。20 trade-date non-wrapping MBB（10,000回、seed `20260915`）の
95% CIは **[-2,161.45, +476.23]円**である。方向だけを反転した機構対照との差の平均は
**-324.49円**、同CIは **[-2,883.36, +2,383.80]円**だった。

よってNet、PF、主CI下限、主−偽ブレイク差CI下限の全主ゲートが未達であり、事前固定規則で
**REJECT**とする。固定感度もopening 20/45分、探索期限22:30/00:30、1分遅延、2/3tick、
手数料2倍のscheduled-axis日次平均Netが全て負だった。OOS、Final Holdout、救済的な追加探索は
実行しない。

`-01`は休場を跨ぐpositionを検出するevent/engine一致監査で停止し、不変保存した。`-02`は
この休場跨ぎ禁止を正しく実装する技術訂正のみを加え、同じ事前登録の範囲で再実行した。
