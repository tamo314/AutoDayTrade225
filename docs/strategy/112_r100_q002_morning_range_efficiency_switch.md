# TASK-R100-Q002 事前登録: 午前総値幅×経路効率による後場固定切替

Q001はPnL、PF、bootstrap、Delta、感度を一切取得せず、評価可能1,011日、HE 319件、LE 11件でPnL前停止した。Q001では `r=|C-O|=e×(H-L)` を高rの条件に用いたため、高rが低e状態を構造的に希少化した。この事実はQ001のPnL-free成果物からだけ確認し、成績は参照しない。

Q002は一度限りの定義修復である。Development trade_date=2021-01-01--2025-06-30のみを使い、R004 DAY隔離、09:00--11:29の150本の正常・適格DAY bar、O/C/H/L、`e=|C-O|/(H-L)`、current-excludedの直前120予定日・有効100件、nearest-rank e q33/q67、11:29での選択、12:30以後最初の適格open entry、14:55 open exit、1枚・最大1建玉、片道1 tick+30円をQ001から変更しない。

唯一の変更は選別Eである。午前総値幅 `v=H-L` の同じstrict-prior参照集合からnearest-rank q50(v)を作り、`E: v>=q50(v)` とする。HE=`E and e>=q67(e)`、LE=`E and e<=q33(e)`、AはHEで`sign(C-O)`方向、LEで反対方向、それ以外無取引である。C/F/I/N、signal、entry、exit、費用、約定規則はQ001と同一である。r閾値成績との比較、結果を使う救済選択はしない。

PnL前ANDは、評価可能日700以上、HE/LE各120 event以上、A完了180以上、両脚70以上、午前正負各70以上、2021初期化部分期15以上、2022--2024各35以上、2025H1 15以上、旧/新制度150/15以上、HE/LE×v q50--q75/q75以上×午前方向の各セル8 event以上、future referenceなし、当日事後選別なし、日次軸一致、R004混入なし、説明不能除外0、未完了建玉0である。未達ならPnLを取得せずINCONCLUSIVEで停止する。

通過時だけ、共通1,131日trade_date軸の20日非循環MBBを10,000回、seed=20260916、末尾切詰め、linear percentileで行う。A日次Net、paired A-C/A-F/A-I、二つのv帯×午前正負を等重みとする `Delta=(HE continuation-reversal)-(LE continuation-reversal)` を評価する。主ANDはA Net>0、PF>1、各CI下限>0である。一つでも未達ならREJECT。固定感度はe q25/q75、e q40/q60、v q40、v q60、prior60/valid50、prior240/valid200、entry+1、exit14:30、片道2/3tick、手数料2倍である。主AND通過後でも指定の頑健性が未達ならINVESTIGATE、全達成でも上限INVESTIGATEとする。Walk Forward、2025H2 OOS、Final Holdoutには進まない。
