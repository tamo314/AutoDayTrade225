# TASK-R101-Q001 事前登録: 先物開始と現物開始の二段階確認

状態: **FROZEN_BEFORE_EVALUATION_PNL**  
登録日: 2026-09-16 JST  
family: `precash_cash_open_two_stage_confirmation` / study: `R101-Q001` / version: `v1`

これはR100閲覧後のDevelopment反復利用による一回限りの事後仮説である。R100の窓・閾値・routeを救済・変更するものではなく、08:45の先物立会開始と09:00の現物開始における参加者交代を対象にする別familyである。結果によらず結論の上限は **INVESTIGATE** とする。OOS、Walk Forward、Final Holdoutを読まない。

PnL、return、PF、bootstrap、感度へのアクセス前にR001--R100を意味的に照合する。R024は08:45--08:59と09:00--09:04の無閾値符号確認を09:05--09:30へ適用した関連研究であるが、二つの独立15分区間の各current-excluded通常変位q50、09:14固定、09:15--10:30、K/D比較を持たない。R031/R042はnightを含む別の開始確認、R057/R096はgap又は60分range受容、R100は09:00--11:29経路状態である。従って同一の二段階・二変位閾値確認の有効済み検定はない。既知Developmentの関連結果は独立確認を意味しない。

Developmentの各予定TSE営業日について、DAY通常適格一分足08:45--08:59の15本から `p=C1-O1`、09:00--09:14の15本から `c=C2-O2` を測る。いずれかが15本未満、`p=0`、`c=0`、R004 DAY隔離なら理由付きunavailableである。直前120予定TSE営業日のcurrent-excluded有効測定を補充なしで使い、有効100以上で各`abs(p)`、`abs(c)`のnearest-rank q50を作る。`E=(|p|>=qp and |c|>=qc)`、`K=E and sign(p)=sign(c)`、`D=E and sign(p)!=sign(c)`である。

`A`はKのみ`sign(c)`、`U`は全Eで`sign(c)`、`R`はKのみ`-sign(c)`、`D_control`はDのみ`sign(c)`、`N`は常時無取引である。signalとrouteは09:14 close時に固定する。entryは09:14 signal後の最初の適格通常DAY bar open（09:15以降）、exitは10:29 signal後の最初の適格通常DAY bar open（10:30以降）。選択後の価格、fill、exit可用性でevent又はrouteを変更しない。Stop/Targetなし、1枚、最大1建玉、主費用は片道1 tick+30円である。

PnL前ANDはq-ready>=700、E>=220、K/D各>=90、A完了>=90、Aのc正負各>=30、2021初期化部分>=8、2022--2024各>=18、2025H1>=8、旧/新制度>=75/8、未完了建玉0、future参照なし、当日事後選別なし、日次軸不一致なし、R004混入なし、説明不能除外なしである。さらに`w=min(|p|/qp,|c|/qc)`を同じstrict-prior U内のE観測へ適用し、その中央値でlow/high二帯とし、K/D×w帯×sign(c)の8共通eventセル各>=6を要する。未達ならPnLを取得せずINCONCLUSIVEで停止する。

通過時、共通予定日軸の20 trade_date非循環moving-block bootstrapを10,000回、seed=20260916、共通index、末尾切詰め、linear percentileでA日次Net、paired A-R、Aの取引当たりNet-UのE当たりNetを評価する。各標本でw帯×sign(c)の4層を等重みとする `Delta=(1/4) sum[mean(Net sign(c)|K,stratum)-mean(Net sign(c)|D,stratum)]` を再計算する。主ANDはA Net>0、PF>1、A日次Net/A-R/A取引当たり-U E当たり/Deltaの各CI下限>0であり、一つでも未達はREJECT。

主判定と無関係にq40/q60、第2区間09:00--09:09と09:00--09:19、prior60/valid50とprior240/valid200、entry+1、exit10:00/11:00、同じ選択経路の2/3 tick及び手数料2倍を保存する。主AND後にも、主要感度A Net/Delta正、2022--2024二年以上と2025H1、c正負、旧新制度、top-10勝ち除外後が正でなければINVESTIGATE。結果依存の窓・閾値・方向・除外・Stop/Target・entry/exit・追加状態は禁止する。
