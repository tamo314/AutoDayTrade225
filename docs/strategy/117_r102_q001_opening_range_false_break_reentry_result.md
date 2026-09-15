# TASK-R102-Q001 結果: 現物開始opening rangeの一方向突破後再侵入

run_id: `task-r102-q001-opening-range-false-break-reentry-20260916-01`  
状態: **REJECT**  
実行日: 2026-09-16 JST

PnL前にR001--R101を照合した。最も近いR054は09:00--09:29 range後の31--90分strict-close breakout/b+5/30分保有、R076は30分rangeを09:30以後にbreakし後刻のrange内closeを確認して14:30に決済、R096は60分range端受容と後場を対象とする。09:00--09:14 H/L、09:15--09:29 high/lowの一方向突破、09:29 closeによるQ/T/Z、09:30--10:30逆方向という同じfalse-break/re-entry検定は既済有効研究にないため、重複停止には該当しなかった。これは既知Developmentの反復利用であり、結論上限はINVESTIGATEのままである。

Development予定trade_date軸は1,131日、q-ready=729、一方向突破event=779、Q/T/Z=380/399/0、S完了=380だった。Qの突破方向はup/down=178/202、年別=81/93/85/77/44（2021--2025H1）、旧/新制度=326/54である。Q/T×x low/high×突破方向の8セルは36--149件で、全てのPnL前件数・因果性ゲートを通過した。R004混入、未来参照、当日後刻のevent/fill/exit可用性による選別、説明不能除外、既約定後未完了はすべて0件だった。

基本費用（片道1 tick+30円）のSは380取引、Net **−405,800円**、PF **0.782**、期待値−1,067.89円/取引だった。共通1,131日軸・20 trade_date非循環MBB 10,000回（seed=20260916）の95%CIは、S日次Net `[-720.39,+9.20]`円、paired S−B `[-717.97,+729.44]`円、SのQ当たりNet−Uの一方向突破event当たりNet `[-221.78,+1,379.49]`円、x帯×突破方向の等重みDelta `[-92.33,+3,437.22]`円だった。S Net>0、PF>1、及び4つのCI下限>0の全6主条件が不成立である。

事前固定感度のS Netは、2 tick=−220,600円、`max(1 tick,0.10W)`=−224,440円、opening 10/20分=−548,940/−630,120円、確認10/20分=−554,820/−568,080円、entry+1=−381,800円、exit10:00/11:00=−298,800/−540,300円、片道2/3 tick=−785,800/−1,165,800円、手数料2倍=−428,600円で、全て負だった。主AND不成立の救済には使用していない。top-10勝ち取引除外後も−674,200円である。

S/Bは同一380 event・同一entry/exit時刻の反対side、全routeは最大1取引/trade_date、全exitはSIGNAL、Stop/Targetなし、`Net=gross-fees`の不一致0件を取引台帳から再確認した。pytest 12件、Ruff、mypy、compileはPASSした。規則に従い **R102-Q001=REJECT** とし、追加探索、Walk Forward、2025H2 OOS、Final Holdoutには進まない。

不変成果物は`results/research/task-r102-q001-opening-range-false-break-reentry-20260916-01/`の`preregistration.json`、`duplicate_review_before_evaluation_pnl.json`、`pre_pnl_gate.json`、`causality_audit_before_pnl.json`、`primary_*`台帳、`bootstrap.json`、`sensitivities.json`、`decision.json`、`COMPLETED.json`である。
