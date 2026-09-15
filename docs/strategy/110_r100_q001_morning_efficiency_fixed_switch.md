# TASK-R100-Q001: 午前経路効率による後場固定切替

状態: **FROZEN_BEFORE_EVALUATION_PNL**  
登録日: 2026-09-16 JST  
family: `morning_path_efficiency_afternoon_fixed_switch` / study: `R100-Q001` / version: `v1`

## 仮説、重複照合、範囲

R078/R079/R099を閲覧した後の、Developmentを反復利用する一回限りの事後仮説である。夜間状態familyの救済ではなく、結論の上限は結果によらず **INVESTIGATE** とする。OOS (2025H2)、Walk Forward、Final Holdoutは読取・実行しない。

PnL、return、PF、bootstrap、感度を新規に取得する前にR001--R099の意味的重複を照合する。R088は09:00--09:59の経路効率による同方向継続のみ、R096は同時間帯の方向側レンジ端受容、R099は完全nightの効率による寄付き後のR078/R079既存eventの切替である。R081/R095は昼休み中の変位を扱う。いずれも、09:00--11:29全体の値幅・効率から、12:30--14:55でHEを同方向、LEを逆方向へ固定切替し、無条件継続・無条件反転・逆切替との共通日次比較及び二値幅帯の交互作用を検定していない。したがってmaterially equivalentな既済有効検定はない。関連する既知Development結果は本件の独立確認を意味しない。

## 固定状態と実行

各予定TSE営業日の09:00--11:29の150本の適格通常DAY一分足から、`O=最初のopen`、`C=最後のclose`、`H=max(high)`、`L=min(low)`、`r=abs(C-O)`、`e=abs(C-O)/(H-L)` を作る。H<=L、150本の窓不完全、R004 DAY隔離は理由付きunavailableとする。D=C-O=0は有効履歴値だがentry eventではない。

当日を除く直前120予定TSE営業日の有効午前だけを、古い有効観測で補充せず使用する。100件以上かつnearest-rank `q33(e)<q67(e)`のときのみ状態評価可能とする。同一strict-prior標本からq50(r)、q75(r)も作る。`E=r>=q50(r)`、`HE=E and e>=q67(e)`、`LE=E and e<=q33(e)`、その他は無取引である。`s=sign(C-O)`で、HEはs方向、LEは-s方向をAとする。Cは両方s、Fは両方-s、IはHE=-s/LE=s、Nは常時無取引である。

signalとrouteは11:29足close時に固定する。選択後の価格、event、fill、exit可用性によりrouteを乗り換えない。entryは12:30以降の最初の適格通常足open、exitは14:55の標準signal/fill規則である。Stop/Targetなし、1枚、最大1建玉、基本費用は片道1 tickと片道30円。entry前取消は予定日次軸の0円、既約定後のexit不能はnullで全PnL判定を停止する。

## PnL前ゲート

評価可能日700以上、HE/LE event各120以上、A完了180以上、HE継続脚・LE反転脚各70以上、午前方向正負各70以上、2021初期化部分15以上、2022--2024各35以上、2025H1 15以上、旧/新制度150/15以上を要求する。`r>=q50 and r<q75`と`r>=q75`の二帯について、HE/LE×r帯×午前方向正負の共通event各セル8以上も必須とする。

strict-prior未来参照なし、当日event/fill/exit可用性による状態選別なし、日次軸一致、R004混入なし、説明不能除外なし、未完了建玉0を全て要求する。いずれかが未達ならPnLを取得せず **INCONCLUSIVE** とする。

## 推論、判定、固定感度

ゲート通過後、共通予定日軸に20 trade_date、非循環・末尾切詰めmoving-block bootstrapを10,000回、seed `20260916`、linear percentileで実行する。A日次Net、paired A-C/A-F/A-Iを評価する。各標本で`G=Net(continuation)-Net(reversal)`を計算し、r二帯×午前方向正負の4層を等重みとする

`Delta=(1/4) sum_strata(mean(G | HE,stratum)-mean(G | LE,stratum))`。

主ANDはA Net>0、PF>1、A日次Net CI下限>0、A-C/A-F/A-I各CI下限>0、Delta CI下限>0である。ひとつでも未達なら **REJECT**。

主判定にかかわらず、e=q25/q75・q40/q60、r=q40・q60、参照60日（有効50）・240日（有効200）、entry追加1分、exit14:30、固定routeの片道2/3 tick、手数料2倍を実行・保存する。主AND成立後も、主要感度すべてでA Net>0かつDelta>0、HE継続脚とLE反転脚各Net>0、2022--2024の少なくとも2年と2025H1、旧新制度双方、top-10勝ち取引除外後が正でなければINVESTIGATEとする。全条件を満たしても上限はINVESTIGATEである。結果依存の境界、窓、片側削除、Stop/Target、entry/exit、weight、戦略、Walk Forward、OOS、Final Holdoutは行わない。
