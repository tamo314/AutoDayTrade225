# TASK-R080-Q001 result

状態: **COMPLETE / REJECT**  
正式成果物: `results/research/r080-q001-20260915-cash-first-hour-all-continuation-01/`

事前登録、実装、合成テスト、設定を価格performance取得前に凍結した。これはR079の極端状態continuationの正の点推定とE--M差不成立を見た後の、同一Developmentに対するfollow-upであり、独立確認ではない。OOS、Walk Forward、Final Holdoutはいずれも`NOT_ACCESSED`である。

## PnL前ゲート

R078正式event ledgerのSHA-256は凍結値と一致し、全1,131 event ID（`trade_date`）は予定軸と同じ順序で一意に一致した。極端度選別を外したR080のR≠0かつ実行可能eventは1,090件（必要900）、R正/負=552/538（必要各350）、年別=2021:220、2022:242、2023:252、2024:252、2025H1:124（必要150/150/150/150/70）、説明不能除外0件だった。したがってPnL前停止条件は発動しなかった。

execution/accounting/causality auditは、1日1取引、continuationとfadeの同event・同entry/exit・逆side、09:59選択後10:00 entry／14:30 exit、Stop/Target不使用、`net=gross-fees`を全て確認した。

## 主結果

continuationは1,090取引、Net **-426,900円**、PF **0.9433**、期待値 **-391.65円/取引**、最大実現DD **684,320円**だった。20 trade-date非循環MBB（10,000回、seed=20260915）の予定日軸平均Netは-377.45円、95%CI **[-1,492.25, +896.47]**円/trade_dateである。

同一日・同entry/exitのfadeはNet -1,883,900円、PF 0.7726だった。continuation--fade対応日次差は+1,288.24円、95%CI **[-947.02, +3,834.70]**円/trade_dateである。直前適格trade_dateのR符号だけを使うlagged-sign placeboは1,089取引、Net -1,004,840円、PF 0.8717で、continuation--placebo対応日次差は+511.00円、95%CI **[-786.94, +1,836.43]**円/trade_dateだった。

したがってNet>0、PF>1、主MBB下限>0、fade差下限>0、lagged-sign placebo差下限>0の主ANDはすべて不成立である。固定判定は **REJECT**。

## 固定感度・分解

全固定感度もNet<0だった。09:44=-764,400円、10:29=-635,380円、entry追加1本=-436,400円、exit14:15=-540,900円、exit14:45=-556,400円、`|R|<=1 tick`除外=-474,960円、片道2 tick=-1,516,900円、3 tick=-2,606,900円、手数料2倍=-492,300円。

R正/負Net=-408,620/-18,280円、年別Net=2021:-255,700円、2022:+72,980円、2023:+23,880円、2024:+70,880円、2025H1:-338,940円だった。|R|五分位NetはQ1:-413,080円、Q2:-569,080円、Q3:-17,080円、Q4:+119,920円、Q5:+452,420円で、記述分解であり選別・救済には使わない。top-10勝ち取引除外後Netは-1,253,800円である。

対象pytest 15件、Ruff、mypy、runner構文検査はPASSした。主AND不成立のため、追加の閾値・時刻探索、Walk Forward、OOS、Final Holdoutには進まない。
