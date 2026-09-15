# TASK-R082-Q002 result: US cash-open N225 night-session continuation

run_id: `r082-q002-20260915-us-open-continuation-01`  
状態: **REJECT**  
対象: Development trade_date 2021-01-01--2025-06-30 only

## PnL前の確認と凍結

Q001正式成果物 `r082-q001-20260915-us-open-continuation-02` の`decision.json`と
`COMPLETED.json`を評価前に監査した。いずれも`INCONCLUSIVE`、理由は
`R082_PNL_FREE_FEASIBILITY_GATE_FAILED`であり、profiles、bootstrap、orders/fills/trades
のPnL成果物は存在しなかった。従ってQ001のPnLは未取得である。

Q002はこの確認を先頭に記録し、件数下限だけを850から800へ改訂して事前登録した。
Q001 `primary_events.json`全体のSHA-256
`c67345cc1f3ae390fd216464af6128ad7030c83414719e1c4cf18b6e2bc111f0`、順序付き固定806
event IDのSHA-256 `746efde7d3a66107d4b5193bb5b6c17dc4aa70d752d29caa652c660c06347616`は一致した。
NYSE calendar、America/New_Yorkの09:30→JST変換（DST含む）、NYSE日とN225 `trade_date`の対応、
Sがnight sessionに入ること、UのS+29 endpoint、S+30 entry、S+180 exit、U方向/fade方向を全806件で
監査した。再抽出、ID追加、欠損救済、強度・時刻・保有期間による主選別は行っていない。

情報量gateは806≥800、U正/負=432/374、年別2021--2024=154/182/191/187（各≥140）、
2025H1=92≥60、説明不能除外0で全通過した。U/P共通日は747（P符号同一361、不一致386）である。

## 一回評価の結果

片道1 tick＋30円、1枚、20 trade_date非循環MBB 10,000回（seed 20260915）で、主continuationは
806取引、Net **-616,860円**、PF **0.862**、期待値 **-765.33円/取引**、最大DD **678,320円**だった。
95%CIは、予定軸日次平均`[-1,156.91, +81.29]`円、continuation−fade`[-802.85, +1,675.53]`円、
U/P共通日continuation−pre-open-sign control`[-1,410.98, +1,781.83]`円である。主ANDの5条件は全て不成立である。

固定感度Netはsignal 15/45分=-505,640/-350,460円、entry 1本遅延=-751,860円、
exit S+120/S+240=-859,860/-592,360円、片道2/3 tick=-1,422,860/-2,228,860円、
手数料2倍=-665,220円で、全て負だった。U正/負Net=-21,420/-595,440円、DST/標準時間=-279,280/-337,580円、
年別Net=2021 -8,740、2022 -233,920、2023 -192,960、2024 +19,280、2025H1 -200,520円である。
top-10勝ち取引除外後Netは-1,207,260円だった。|U|五分位は第5分位のみ+150,780円だが、記述値として保存するだけで
選別・救済には使わない。

全profileが固定806 IDの部分集合だけを用いたこと、1日1取引、continuation/fadeの同一entry/exit反対side、
U/P共通日のpre-open control同一entry/exit、Stop/Targetなし、`Net=Gross-fees`を監査してPASSした。

実行中、全取引台帳・日次軸・bootstrap indexを保存した後、監査コードのstring/date比較不一致で要約書込み前に停止した。
市場データまたはエンジンの再実行はせず、保存済み台帳・日次軸のSHA-256を固定したartifact-only finalizationで
集計・監査・判定を完成した。詳細は`execution_repair.json`に保存している。

主AND不成立のため **R082-Q002 = REJECT**。結果依存の救済、OOS、Walk Forward、2026年以降のFinal Holdoutは実施しない。

保存成果物: [run directory](/C:/Work/AutoDayTrade225/results/research/r082-q002-20260915-us-open-continuation-01), [decision](/C:/Work/AutoDayTrade225/results/research/r082-q002-20260915-us-open-continuation-01/decision.json), [preregistration](/C:/Work/AutoDayTrade225/results/research/r082-q002-20260915-us-open-continuation-01/preregistration.json), [profiles](/C:/Work/AutoDayTrade225/results/research/r082-q002-20260915-us-open-continuation-01/profiles.json), [execution repair](/C:/Work/AutoDayTrade225/results/research/r082-q002-20260915-us-open-continuation-01/execution_repair.json).
