# TASK-R077-Q001 result

状態: **COMPLETE / REJECT**  
正式成果物: `results/research/r077-q001-20260915-day-close-cash-open-gap-fade-01/`

`42_r077_q001_day_close_cash_open_gap_fade.md`、実装、config、Development入力partition hash、bootstrap seedを、価格performance取得前に保存した。対象はDevelopment trade_date 2021-01-01--2025-06-30のみである。OOS、Walk Forward、Final Holdoutは`NOT_ACCESSED`である。

## PnL前ゲート

E実行可能は230件（G正113、G負117）。年別Eは2021=31、2022=52、2023=57、2024=60、2025H1=30であり、各件数最低条件を通過した。説明不能な除外は0件である。予定日軸1,131日に対し状態はE=230、M=595、NONE=306で、既約定後のunknown exitは0件だった。

## 主結果

E-fadeは230取引、Net **-274,800円**、PF **0.8362**、期待値 **-1,194.78円/取引**、最大実現DD **553,960円**だった。20 trade-date非循環MBB（10,000回、seed=20260915）の予定日次平均Netは-242.97円、95%CI **[-715.07, +248.06]**円/trade_dateである。

同一E日・同entry/exitのcontinuationはNet -212,800円、PF 0.8730で、E-fade minus continuationの日次差は-54.82円、95%CI **[-968.17, +923.10]**円/trade_dateだった。M-fadeは595取引、Net -100,200円、PF 0.9683で、E-fade minus M-fadeの取引当たり差は-1,026.38円、block-bootstrap 95%CI **[-3,496.42, +1,717.23]**円だった（空condition再標本0回）。

主ANDのNet>0、PF>1、主MBB下限>0、continuation差下限>0、M差下限>0は全て不成立である。したがって固定判定は **REJECT**。

## 固定診断・感度

G正Netは-217,560円、G負Netは-71,040円で、両方向とも負だった。年別Netは2021=+16,780円、2022=-182,240円、2023=+27,160円、2024=-155,700円、2025H1=+5,400円である。top-10勝ち取引除外後Netは-735,400円だった。

固定感度はq70=-130,680円、q90=-121,480円、参照40日=-260,720円、参照80日=-200,820円、exit 09:45=-241,300円、exit 10:30=-189,300円、entry 1分遅延=-372,300円、2 tick=-504,800円、3 tick=-734,800円、手数料2倍=-288,600円で、すべてNet<0だった。

execution/accounting auditでは、1日1取引、E-fadeとcontinuationの同event・同entry/exitかつ逆side、E/Mの排他、09:00選択後09:01 entry／10:00 exit、Stop/Target不使用、`net=gross-fees`を全て確認した。

主AND不成立のため、結果を使った閾値・時刻・方向・費用の救済、Walk Forward、OOS、Final Holdoutには進まない。Development反復利用により、仮に成立しても上限はINVESTIGATEのままである。

## 検証環境

実験ランナー内の対象pytest（13 passed）、Ruff、mypyはPASSした。追加の全件pytestは、ローカルにPython 3.10しかなく、既存無関係test collectionが`datetime.UTC`および`enum.StrEnum`のimportで失敗したため完走不能だった。プロジェクト要件はPython 3.12--3.13であり、この環境制約はR077成果物の`pre_execution_validation.json`には影響しないが、全件suiteのPASSを主張しない。
