# R070-Q001: ナイト固定買いの無条件リスクプレミアム — REJECT

タスクID: `TASK-R070-Q001`。正式な成果物は
`results/research/r070-q001-20260915-night-unconditional-long-03/` である。
Development のみを使用し、OOS は `NOT_ACCESSED`、Final Holdout は
`NOT_ACCESSED` である。

## 凍結仕様

仮説は一つだけである。ナイトsessionに正の無条件リスクプレミアムがあり、
16:30 bar確定後の次の適格1分足open（通常16:31、calendar_dateは前日になり得る）で
1枚を買い、同一trade_dateの05:30適格openで決済した費用後期待値が正である。
Stop、Target、価格条件、day/prior-session情報、休場跨ぎ保有は使用しない。

予定軸は版管理されたDevelopment trade_date 1,131日である。固定時刻がその版のnight
sessionに存在しない場合は、decision時点で既知の無取引0円として軸に残した。約定後の
後刻exit欠損は0円へ置換せずnullとする。基準費用は片道1 tick + 30円で、fixed sellは
同時刻の方向対照、無取引0円は主対照である。推論は20 trade-date、non-wrapping MBB、
10,000回、seed `20260915`、linear percentileで凍結した。

## S2と結果

entry/exit可能日は965日（2021--2024: 244/248/257/216）で、S2の
`>=900`かつ各年`>=180`を通過した。主固定買いは965取引、Net **+202,600円**、
PF **1.020**、予定軸日次平均 **+179.13円**だったが、MBB 95% CIは
**[-1,072.01, +1,394.61]円**で下限は正でなかった。

固定売りはNet -2,248,400円、PF 0.780だった。事前固定感度は17:00 entry、05:00 exit、
05:45 exitがいずれも日次平均正、1分遅延はNet +231,100円だった。一方で2 tickは
Net **-762,400円**、3 tickは -1,727,400円であった。さらに2025H1は固定16:30時刻が
版管理night sessionに存在しないためNet 0円となり、年別条件も満たさない。

主Net/PFの点推定は正でも、主CI下限と2 tick条件が未達である。従って事前固定規則により
**R070-Q001 = REJECT**。救済的な時刻、方向、費用、フィルター、WFA、OOS、Final Holdoutは
実行しない。

## 実装訂正の記録

`…-01` は価格読取り前のmypy失敗で停止した不変のpre-execution記録である。`…-02` は
1分遅延時に、通常entry時刻と等しいdelay signalを予定window外と誤分類し、遅延取引を
全件0にした。これは経済仕様でなくschedule-boundaryの実装バグである。

`…-03` はこの述語だけを訂正し、遅延entryが16:32 next eligible openへ到達する合成回帰
テストを追加して、全事前登録profileを再実行した。遅延以外の全8 profileのNetは`…-02`と
完全一致した。`…-02`は削除・上書きしていない。
