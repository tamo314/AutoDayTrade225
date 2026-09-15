# TASK-R078-Q001 result

状態: **COMPLETE / REJECT**  
正式成果物: `results/research/r078-q001-20260915-cash-first-hour-extreme-fade-01/`

`44_r078_q001_cash_first_hour_extreme_fade.md`、実装、config、Development入力partition hash、bootstrap seedを、価格performance取得前に保存した。対象はDevelopment trade_date 2021-01-01--2025-06-30のみである。OOS、Walk Forward、Final Holdoutは`NOT_ACCESSED`である。

## PnL前ゲート

E実行可能は230件（R正111、R負119）。年別Eは2021=36、2022=48、2023=65、2024=57、2025H1=24であり、各件数最低条件を通過した。説明不能な除外は0件である。予定日軸1,131日に対し状態はE=230、M=589、NONE=312で、既約定後のunknown exitは0件だった。

R004 day session隔離=20、R=0=21、直前有効R履歴不足=60、状態境界外=211を理由つきで保存した。day-close→09:00 gapとRの診断は、SAME=110取引・Net -492,100円・PF 0.543、OPPOSITE=116取引・Net -503,460円・PF 0.562、ZERO_GAP=1取引・Net -15,560円だった。これは事前指定どおり診断のみで、選別・救済・主ゲートには使っていない。

## 主結果

E-fadeは230取引、Net **-1,016,800円**、PF **0.5482**、期待値 **-4,420.87円/取引**、最大実現DD **1,034,800円**だった。20 trade-date非循環MBB（10,000回、seed=20260915）の予定日次平均Netは-899.03円、95%CI **[-1,740.42, -212.52]**円/trade_dateである。

同一E日・同entry/exitのcontinuationはNet +529,200円、PF 1.3665で、E-fade minus continuationの日次差は-1,366.93円、95%CI **[-3,012.47, -17.64]**円/trade_dateだった。M-fadeは589取引、Net -890,340円、PF 0.7901で、E-fade minus M-fadeの取引当たり差は-2,909.26円、block-bootstrap 95%CI **[-6,780.41, +913.32]**円だった（空condition再標本0回）。

主ANDのNet>0、PF>1、主MBB下限>0、continuation差下限>0、M差下限>0は全て不成立である。したがって固定判定は **REJECT**。

## 固定診断・感度

R正Netは-317,160円、R負Netは-699,640円で、両方向とも負だった。年別Netは2021=-92,160円、2022=-123,380円、2023=-139,400円、2024=-648,920円、2025H1=-12,940円である。top-10勝ち取引除外後Netは-1,380,200円だった。

固定感度はq70=-1,007,600円、q90=-502,480円、参照40日=-1,027,420円、参照80日=-957,580円、signal終点09:44=-848,500円、10:29=-736,120円、entry 1分遅延=-975,800円、exit 14:15=-927,300円、exit 14:45=-980,300円、2 tick=-1,246,800円、3 tick=-1,476,800円、手数料2倍=-1,030,600円で、すべてNet<0だった。

execution/accounting auditでは、1日1取引、E-fadeとcontinuationの同event・同entry/exitかつ逆side、E/Mの排他、09:59 close選択後10:00 entry／14:30 exit、Stop/Target不使用、`net=gross-fees`を全て確認した。対象pytest（13 passed）、Ruff、mypyはPASSした。

初回runnerはgap×Rを状態件数として保存したが、要求された同符号／逆符号の**成績**別集計を出力していない成果物欠落を結果確認時に発見した。`gap_r_sign_diagnostic_performance.json`は、保存済み`primary_events.json`と`extreme_fade_trades.json`だけから導出した補足であり、市場データ読込、event選択、約定、PnL再計算、bootstrap、ゲート、決定を変更していない。修復scriptと静的検査結果も`source_snapshot/`に保存した。

主AND不成立のため、結果を使った閾値・時刻・方向・費用の救済、Walk Forward、OOS、Final Holdoutには進まない。Development反復利用により、仮に成立しても上限はINVESTIGATEのままである。
