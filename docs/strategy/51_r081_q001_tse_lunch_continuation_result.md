# TASK-R081-Q001 result

状態: **COMPLETE / REJECT**  
正式成果物: `results/research/r081-q001-20260915-tse-lunch-continuation-01/`

事前登録、実装、合成テスト、設定を価格performance取得前に凍結し、Development trade_date 2021-01-01--2025-06-30だけを一回実行した。これはR078--R080の時刻又は閾値救済ではなく、東京現物昼休み中の先物価格発見という別の制度的メカニズムの検査である。OOS、Walk Forward、Final Holdoutはいずれも`NOT_ACCESSED`である。

## PnL前ゲートと実行監査

主L=B(12:29 close)-A(11:30 open)は、予定軸1,131日のうち実行可能1,053件（必要900）、L正/負=550/503（必要各350）、年別=2021:217、2022:233、2023:242、2024:241、2025H1:120（必要150/150/150/150/70）、説明不能除外0件であり、全ゲートを通過した。zero Lは58日、R004 day-session隔離は20日だった。

morning-sign共通集合（主Lが実行可能かつM=11:29 close-09:00 openがnonzero）は1,039件で、L/M符号同一531、不一致508だった。Mは主戦略の選別・取消には使用していない。

execution/accounting/causality auditは、1日1取引、continuationとfadeの同event・同entry/exit・逆side、12:29選択後12:30 entry／14:30 exit、morning controlの同一L/M共通日・entry/exit、Stop/Target不使用、`net=gross-fees`をすべて確認した。

## 主結果

lunch-continuationは1,053取引、Net **-46,180円**、PF **0.9891**、期待値 **-43.86円/取引**、最大実現DD **446,540円**だった。20 trade-date非循環MBB（10,000回、seed=20260915）の予定日次平均Netは-40.83円、95%CI **[-739.14, +704.64]**円/trade_dateである。

同一日・同entry/exitのfadeはNet -2,186,180円、PF 0.5932だった。continuation--fade対応日次差は+1,892.13円、95%CI **[+503.98, +3,392.60]**円/trade_dateである。L/M共通日集合のmorning-sign controlは1,039取引、Net -1,379,340円、PF 0.7193で、continuation--morning-sign control対応日次差は+1,291.63円、95%CI **[+172.26, +2,490.88]**円/trade_dateだった。

Net>0、PF>1、主MBB下限>0は不成立である。一方、fade差とmorning-sign差のCI下限は正だったが、主ANDの全条件を満たさない。固定判定は **REJECT**。

## 固定感度・分解

固定感度のNetは、昼休み窓両端5分除外=-115,400円、entry追加1本=-264,180円、exit14:15=-188,180円、exit14:45=+147,820円、片道2 tick=-1,099,180円、3 tick=-2,152,180円、手数料2倍=-109,360円だった。従って全固定感度Net>0は不成立である。

L正/負Net=-188,500/+142,320円。年別Net=2021:+32,480円、2022:-157,980円、2023:-44,520円、2024:+47,540円、2025H1:+76,300円であり、2021--2024の正年は2年だけだった。L/M同符号・逆符号別Net=-169,860/+132,520円、|L|五分位Net=Q1:-195,600、Q2:-83,160、Q3:-12,600、Q4:+71,340、Q5:+173,840円である。これらは記述分解であり、選別・救済には使わない。top-10勝ち取引除外後Netは-664,080円だった。

対象pytest 14件、Ruff、mypy、runner構文検査はPASSした。主AND不成立のため、結果依存の窓・方向・強度探索、Walk Forward、OOS、Final Holdoutには進まない。
