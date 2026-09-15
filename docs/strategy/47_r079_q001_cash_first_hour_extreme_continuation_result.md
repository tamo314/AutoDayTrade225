# TASK-R079-Q001 result

状態: **COMPLETE / REJECT**  
正式成果物: `results/research/r079-q001-20260915-cash-first-hour-extreme-continuation-01/`

`46_r079_q001_cash_first_hour_extreme_continuation.md`、runner、再利用するR078 event helper/strategy、tests、configを価格performance取得前に保存した。これはR078 Development結果に依存するfollow-upであり、独立確認ではない。OOS、Walk Forward、Final Holdoutはいずれも`NOT_ACCESSED`である。

## PnL前 event再利用ゲート

R078正式event ledgerのSHA-256は凍結値と一致し、全1,131 event ID（`trade_date`）は予定軸と同じ順序で一意に一致した。E executable=230、R正/負=111/119、年別E=2021:36、2022:48、2023:65、2024:57、2025H1:24、説明不能除外=0を全て確認した。従って監査停止条件は発動しなかった。

execution/accounting auditは、1日1取引、continuationとfadeの同event・同entry/exit・逆side、E/M排他、09:59選択後10:00 entry/14:30 exit、Stop/Target不使用、`net=gross-fees`をすべて確認した。

## 主結果

E-continuationは230取引、Net **+529,200円**、PF **1.3665**、期待値 **+2,300.87円/取引**、最大実現DD **155,400円**だった。20 trade-date非循環MBB（10,000回、seed=20260915）の予定日軸平均Netは+467.90円、95%CI **[-200.84, +1,271.41]**円/trade_dateである。

同一E日・同entry/exitのfadeはNet -1,016,800円、PF 0.5482で、E-continuation minus fadeの日次差は+1,366.93円、95%CI **[+17.64, +3,012.47]**円/trade_dateだった。M-continuationは589取引、Net -358,340円、PF 0.9096で、E-continuation minus M-continuationの取引当たり差は+2,909.26円、block-bootstrap 95%CI **[-913.32, +6,780.41]**円だった（空condition再標本0回）。

主ANDのNet>0、PF>1、fade差下限>0は成立したが、主MBB下限>0およびM対照差下限>0は不成立である。固定判定は **REJECT**。

## 固定感度・分解

全固定感度のNetは正だった。q70=+297,400円、q90=+220,520円、参照40日=+535,580円、参照80日=+495,420円、signal終点09:44=+371,500円、10:29=+254,880円、entry追加1分=+488,200円、exit14:15=+439,700円、exit14:45=+492,700円、2 tick=+299,200円、3 tick=+69,200円、手数料2倍=+515,400円。

R正Netは+81,840円、R負Netは+447,360円。年別Netは2021=+15,840円、2022=+21,620円、2023=+1,600円、2024=+528,080円、2025H1=-37,940円だった。top-10勝ち取引除外後Netは-209,200円である。したがって、主ANDとは独立に、2025H1および利益集中度の条件も満たしていない。

対象pytestは14 passed、Ruff、mypy、runner構文検査はPASSした。主AND不成立のため、結果を使った追加探索、Walk Forward、OOS、Final Holdoutには進まない。
