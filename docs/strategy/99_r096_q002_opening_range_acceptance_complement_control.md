# TASK-R096-Q002: 現物開始60分の方向側レンジ端受容と day 継続 — K*補集合対照

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_PNL**  
対象: Developmentのみ（trade_date 2021-01-01--2025-06-30）

Q001の不変成果物について、PnL、PF、orders/fills/trades、bootstrap、感度成績を一切取得しなかったことを先に記録する。本Q002は同じPnL-free primary ledgerから、予定軸1,131日、q-ready nonzero 990、E=197、旧K=49、EのD上/下=100/97、およびQ001の年別・制度別・二つのrolling-x帯件数を値ごとに再現する。不一致ならPnLへ進まずINCONCLUSIVEとする。

## 唯一の変更と監査

Q001の60本09:00--09:59 OHLC、O/C/H/L/D/x/z、strict current-excluded prior-120 scheduled TSE day参照、nearest-rank qx50/qz25/qz75、E、A/B/C/D、entry、exit、費用、実行規約、bootstrap seed/index、主AND、方向・制度・年・集中度判定は完全に凍結する。旧Kだけは実行前の母集団対照として **K*=x>=qx50かつz<qz75** に置換する。`z==qz75`はEに属する。従って高x日のE/K*は互いに排反で全体を尽くす。同一変位条件を満たす非高受容群との補集合比較であり、旧`z<=qz25`群の経済成績は取得しない。

PnL前に、x>=qx50日についてstrict-prior qz25/qz50/qz75によるz四分位群、D方向、年、旧新制度、`[qx50,qx75)`/`[qx75,+inf)`の交差件数を保存する。旧K不足は、欠測・将来entry/exit可用性での選別・予定表不整合・R004混入・約定処理不良ではなく、xとzの条件付き分布と凍結済みの旧下側四分位定義だけで説明できることを必須監査とする。監査不合格又は説明不能な不一致はPnL前INCONCLUSIVEで停止する。

## PnL前ゲート

説明不能除外0、entry後exit未完了0、q-ready nonzero>=800、E完了>=100、K*完了>=200、E D正/負各>=35、K* D正/負各>=50、E/K*とも二つのcurrent-day rolling-x帯各>=20を要求する。EはQ001の2021初期化>=8、2022--2024各>=18、2025H1>=8、旧/新>=85/8を維持する。K*は2021初期化>=10、2022--2024各>=35、2025H1>=10、旧/新>=170/15を要求する。未達ならPnL前停止する。

## 条件付きPnL評価

通過時のみ新IDで一度実行する。基本費用は片道1 tick+30円、next eligible open entry、T-5 exit、固定方向/反転A/B/C/D、20 trade_date non-wrapping moving-block bootstrap 10,000回、seed=20260915、共通indexである。標準化A-M差は二つのx帯を**等重み**でA/K*取引当たりNet差として評価する。

z=q70/q80感度は各々E=z>=閾値、K*=z<閾値の補集合、他はx=q40/q60、観測30/90分、entry追加1分、exit T-20、exit14:55、片道2 tick、手数料2倍で固定する。片道3 tickは診断として保存する。主ANDの一つでも未達ならREJECT、主AND成立後に固定頑健性、方向・制度・年・集中度のいずれかが未達ならINVESTIGATE、全て成立しても上限はINVESTIGATEである。結果依存変更、片側方向、時刻、追加探索、Walk Forward、2025H2 OOS、Final Holdoutは行わない。
