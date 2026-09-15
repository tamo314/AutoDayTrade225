# R072-Q001: ナイト方向の日中反転 — INCONCLUSIVE（S2停止）

タスクID: `TASK-R072-Q001`。正式成果物は
`results/research/r072-q001-20260915-night-direction-day-reversal-01/`である。事前登録は
[24_r072_q001_night_direction_day_reversal.md](24_r072_q001_night_direction_day_reversal.md)に不変保存した。

Development trade_date `2021-01-01..2025-06-30`の正規化済みParquet、版管理calendar/session、適格性だけをS2で読み取った。OOSとFinal Holdoutはともに`NOT_ACCESSED`である。

S2の予定軸は1,131日、entry/exit実行可能な非zero night-direction signalは749件だった。年別は2021年186、2022年191、2023年202、2024年170、2025H1 0件、反転execution sideはbuy 344、sell 405件である。したがってbuy/sell各300件は満たすが、総数`>=900`と2024年`>=180`を満たさない。予定上のcross-session windowなしは375日、night directionが等値の見送りは7日だった。

事前固定S2ゲートの未達により、**R072-Q001 = INCONCLUSIVE** として停止した。PnL、取引・fill、主／momentum対照の成績、bootstrap、費用・時刻感度、Candidate判定は実行していない。閾値、時刻、方向、対象期間を救済的に変更せず、OOS・Final Holdoutも開かない。

合成pytest 17件、Ruff、mypyはPASSした。合成経路では、前calendar_dateのnight、05:30固定の方向、day寄付き後の値がsideを変えないこと、等値見送り、05:00/09:01/exit時刻感度、休場schedule、filled後exit欠損、day-only entry/exit、Net会計、固定seed MBBを検証した。
