# R091-Q001: 公式TSE現物取引時間の固定short — REJECT

`TASK-R091-Q001` の不変成果物は [task-r091-q001-tse-cash-hours-fixed-short-20260915-02](../../results/research/task-r091-q001-tse-cash-hours-fixed-short-20260915-02/) に保存した。`…-01` は価格・PnL前の相対Parquet path直列化エラーで停止した技術記録であり、`…-02` はそのmanifest path表現だけを修正して再実行した。

R001--R090との重複照合、R070の既知ナイト固定買いREJECT、Developmentの反復利用をPnL前に記録した。R066の09:00--14:30固定longは関連する同じ無条件方向familyだが、今回の予定T（旧15:00、新15:30）とexitが異なる。従って独立検証とは主張しない。OOSとFinal Holdoutはアクセスしていない。

公式TSE営業日だけの予定軸は1,099 trade_dateであり、R004 day whole-session隔離20日を説明付き無注文として残した。E_execは08:59のsubmit/decisionと09:00 open fillの可用性だけで決め、後刻exitを用いてentryを取消していない。A/Bの完了pathは各1,079件、年別は2021--2025H1で225/244/246/245/119、旧/新TSE終了制度は920/159、説明不能除外0、既約定の未完了exit 0だったため、全PnL前gateを通過した。

予定Tは観測barから推測せず、2024-11-01まで15:00、2024-11-05以降15:30とした。submit/decision/fill、trade_date、R004、A/Bの同event・同entry/exit・反対side、1日1取引、signal exit、Stop/Targetなし、`Net=Gross-fees`、slippage非二重控除の実行・会計監査は全てPASSした。pytest 16件、Ruff、mypyはいずれもexit code 0だった。

| 条件 | 取引 | Net (円) | PF |
|---|---:|---:|---:|
| A fixed short | 1,079 | -723,240 | 0.932 |
| B fixed long | 1,079 | -1,564,240 | 0.859 |
| A 1分entry遅延 | 1,079 | -631,740 | 0.939 |
| A 09:05 entry | 1,079 | -670,740 | 0.934 |
| A T-5 exit | 1,079 | -715,240 | 0.932 |
| A 2 tick | 1,079 | -1,802,240 | 0.839 |
| A 3 tick（必須診断） | 1,079 | -2,881,240 | 0.756 |
| A 手数料2倍 | 1,079 | -787,980 | 0.926 |

共通予定日軸の20 trade_date非循環moving-block bootstrap 10,000回（seed `20260915`、共通index、末尾切詰め、linear percentile）の95%区間は、A日次Net `[-1,718.34, +553.16]` 円、paired A-B差 `[-1,343.99, +3,185.67]` 円だった。AのNet、PF、A日次CI下限、A-B差CI下限の主ANDは全て不成立である。よって **R091-Q001=REJECT**。

頑健性も全て救済にはならなかった。旧制度Netは-798,700円、新制度は+75,460円、年別は2021 +259,500円、2022 -268,640円、2023 -441,260円、2024 -287,700円、2025H1 +14,860円であり、2021--2024の正年は1年だけだった。top-10勝ち取引除外後Netは-1,737,640円である。結果依存の時間・方向・曜日変更、Walk Forward、2025H2 OOS、Final Holdoutは実行しない。
