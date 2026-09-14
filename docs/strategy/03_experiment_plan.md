# R001: セッション初動の継続と反転（事前登録）

## R045-Q001: TSE現物昼休み変位の後場再開15分反転（事前登録）

正式IDは `r045-q001-20260914-tse-lunch-placebo-reversal-03`。`…-01` は全売買・入力・費用・seed・判定を固定して実行したが、20日block bootstrapの13反復で当該再標本に存在しない暦年dummyがゼロ列になり、結果保存前に停止した不変記録である。`…-02` はその再標本内でのみ不在暦年factor levelを除外してOLSを再計算する実装訂正だけを再登録して完走したが、要求済みの昼休み／placebo方向別の成績要約を独立成果物として保存しなかった不変記録である。`…-03` は売買・入力・費用・seed・判定を変えず、その集計成果物だけを追加して再登録する。R001--R044を価格統計・適格件数・PnL前に照合する。R020は同じ60分昼休み方向を120分結合観測に入れ、12:30--13:30に逆張りし、同長30分前placebo、共通E、15分保有、固定方向／追随対照、固定pooled OLSを持たない。R038はstrict極端値の30分追随、R029は5分確認追随である。よって同一登録はない。既知Development上の追加探索であり、独立確認ではない。

版管理したTSE予定表 `R045-LUNCH-1` から各TSE営業日の前場終了 `tS` と後場開始 `tR` を取得し、`Delta=tR-tS`、`tB=tS-30分` とする。`rL=close(tR-1)-open(tS)`、`rP=close(tB-1)-open(tB-Delta)` は各予定窓の連続適格1分足だけで得る。昼休み・placeboの全足、両方のentry/exit固定経路が連続適格で、R004隔離外、`rL!=0`、`rP!=0` の日だけを共通Eとする。固定1,111 trade_date軸では、休業、隔離、欠損、zero、取消、無取引を0円で残す。raw、出来高、現物・外部価格、OOS、Final Holdoutは読まない。

共通Eで A=`-sign(rL)`、B=`sign(rL)`、C=固定買い、D=固定売りをtR始値entry、tR+15分始値exit、G=`-sign(rP)`をtB始値entry、tB+15分始値exitで実行する。A_delayはtR+1 entryかつexit非延長、A2/A3はAの片道2/3 tick再約定である。片道費用は30円、標準slippageは片道1 tick。1枚、日次最大1取引・最大1ポジション、Stop/Target、re-entry、途中更新、早期exitは使わない。

境界診断は各E日に昼休み／placeboの2行を作り、方向調整済み0 tick・費用前の15分将来returnをy、昼休み指標をL、`z=ln(|r|/P)`、`U=1[r>0]` として、`y=alpha+beta L+gamma1(z-mean(z))+gamma2(z-mean(z))^2+eta U+暦年固定効果+epsilon` を固定する。mean(z)は両行全Eで一度だけ固定し、full rank不成立はBLOCKEDである。20 trade_date非循環moving-block bootstrap 10,000回、seed=20260925、共通index、末尾切詰め、linear percentileでA日次平均、A-B/A-C/A-D/A-Gの条件付き期待値差、betaを再計算する。

入力・予定表・合成・実行・会計・OLSのgate失敗はBLOCKED。E>=800、rL上／下各>=250、rP上／下各>=250が不足ならINCONCLUSIVE。充足後はA Net>0、PF>1、A日次平均、4比較、betaの全95% CI下限>0、A2/A3/A_delay期待値>0、2021--2024の正年>=3、正月>=27/54、top10利益取引除去後Net>0をすべて満たすときだけINVESTIGATE、他はREJECTとする。WFA、OOS、Final Holdout、時刻・窓・placebo・回帰形・filter・Stop/Targetの救済探索は実施しない。

## R044-Q001: TSE現物終了前25分／最終5分の競合後10分逆張り（事前登録）

正式IDは `r044-q001-20260914-tse-close-conflict-reversal-01`。R001--R043を価格統計・適格件数・PnL前に照合する。R030はTSE終了直前5分の全非zero日を逆張りし同型時刻placeboと比較するが、25分対5分の符号競合・一致日対照・all/固定/追随対照・連続変化幅調整OLSを持たないため同一ではない。既知Development上の追加探索であり独立確認ではない。

TSE通常営業日dについて、凍結済み版管理予定表の現物終了を`tC`、`tB=tC-60分`とする。`rP=close(tC-6)-open(tC-30)`はtC-30..tC-6開始の連続25本、`rL=close(tC-1)-open(tC-5)`は連続5本で求める。両nonzeroかつ完全・適格なら`rP*rL<0`を競合T=1、`>0`を一致T=0とし、tBにも同じrPB/rLBと競合TBを独立に作る。観測端の代用、値幅・gap・night・曜日・年・出来高filterは置かない。

A=競合日の`-sign(rL)`、B=全有効日の`-sign(rL)`、C=一致日の`-sign(rL)`、D/E=A event固定buy/sell、F=A eventの`sign(rL)`、G=TB競合日の`-sign(rLB)`である。A--FはtC始値entry、tC+10始値exit、GはtB始値entry、tB+10始値exit。A2/A3はAを片道2/3tick、A_delayはtC+1 entryでexitを延長しない。全条件片道30円、1枚、日次最大1取引・最大1ポジション、Stop/Target/reentry/途中更新/早期exitは禁止する。

入力はDevelopment選択済み正規化Parquet、版管理TSE/OSE予定表、R004固定隔離のみである。raw、出来高、現物・外部価格、OOS、Final Holdoutは読まない。45 session/27,345 bar除外、2,216 session/1,326,086 bar残存、固定1,111日軸、manifest/source hash、seed=20260924を保存する。合成・実行・会計・OLS rankが失敗すればBLOCKED。20 trade_date非循環moving-block bootstrap 10,000回、共通index、末尾切詰め、linear percentileでA、A-B/C/D/E/F/G、βのCIを固定する。情報量はB>=800、A/C>=250、A上下各80、終了境界4符号群各70、G>=250かつ上下各80。不足はINCONCLUSIVE、充足後の全固定経済条件未達はREJECT、全通過でもDevelopment一次INVESTIGATE止まりとする。WFA、OOS、Final Holdout、救済探索は行わない。

## R043-Q001: 08:45--08:59 と 09:00--09:04 の競合後60分追随（事前登録）

正式実行IDは `r043-q001-20260914-precash-cash-conflict-followthrough-02`。`…-01` は売買仕様・入力・評価を変更せず実行したが、OLSの固定中心およびdesign-rank拒否を明示する合成検証が不足していたため、判断に使わない不変記録として残す。`…-02` はその合成検証だけを価格統計・適格件数・PnL取得前に追加して再登録する。R001--R042を価格統計・適格件数・PnL取得前に照合した。R024は同じ二窓の同符号を09:05--09:30に追随する規則であり、本件の反対符号・09:05--10:05・一致日対照・固定比較・連続変化幅OLSと同一ではない。R031はfull night、R041はfull night対09:00--09:29、R042はnight終端状態であり、いずれも別の観測・市場引継ぎ機構である。既知Development上の追加探索であり、独立確認ではない。

TSE通常営業日dにのみ、連続かつ適格な08:45--08:59の15本から `rP=close_08:59-open_08:45`、09:00--09:04の5本から `rC=close_09:04-open_09:00` を得る。予定足の欠損、不適格、R004隔離、期間外、zeroは見送る。先頭・最終観測行への代用はしない。`rP*rC<0`を競合、`>0`を一致とし、値幅、gap、night、曜日、年、出来高その他はfilterに使わない。

A_conflict=競合日のsign(rC)、B_all_cash=全有効日のsign(rC)、C_agreement=一致日のsign(rC)、D_buy/E_sell=競合日の固定long/short、F_precash=競合日のsign(rP)（Aと必ず反対side）とする。09:04確定後の09:05予定足openでentry、10:05予定足openで固定exit。A_delayは09:06 entryだがexitを延長しない。A2/A3はAと同じevent/sideで片道2/3 tick、その他は片道1 tick、全て片道30円、1枚・日次最大1取引・最大1ポジション、Stop/Target/re-entry/更新/早期exitなしとする。

入力はDevelopment選択済み正規化Parquet、版管理OSE/TSE予定表、R004固定隔離だけである。raw、出来高、現物・外部価格、OOS、Final Holdoutは読まない。R004の45 session/27,345 bar除外、2,216 session/1,326,086 bar残存、固定1,111 trade_date軸、入力manifest/source hash/seed=20260923を必須にし、品質上限はPASS_LIMITEDとする。

全有効Bで `y=sign(rC)*(open_10:05-open_09:05)`、`zP=ln(abs(rP)/open_08:45)`、`zC=ln(abs(rC)/open_09:00)`、`U=1[rP>0]` とし、同一集合で一度だけ求めた各z平均を使い、`y=alpha+beta*T+gamma1(zP-mean(zP))+gamma2(zC-mean(zC))+gamma3(zC-mean(zC))^2+eta*U+calendar-year FE+epsilon` を固定する。full rankでなければBLOCKED。20 trade_date非循環moving-block bootstrapを10,000回、共通index、末尾切詰め、linear percentile、seed=20260923で行い、条件別sum/countとOLSを各反復で再計算する。

実データ前に制度・休日・calendar_date/trade_date・二窓境界/連続性・zero・競合/一致・欠損/隔離/期間外・09:04因果性・prefix不変性、T=1のA/B、T=0のB/Cの経路一致、A/F反対side、翌足entry・固定exit・遅延非延長・費用会計・Holdout拒否を合成検証する。入力/合成/実行/会計/OLS失敗はBLOCKED。B>=800、A/C>=250、A long/short各>=80、競合/一致×rC方向各>=70を満たさなければINCONCLUSIVE。充足後、A Net>0/PF>1、A・A-B/C/D/E/F・betaの全CI下限>0、A2/A3/A_delay期待値>0、2021--24の3正年、正月>=27/54、top10除去後Net>0の全通過でのみDevelopment一次INVESTIGATE、それ以外REJECTとする。WFA/OOS/Final Holdout、救済探索は行わない。

## R042-Q003: night終値方向側端部受容の08:46--09:46追随（事前登録）

正式実行IDは `r042-q003-20260914-night-terminal-continuous-range-03`。R042-Q001はCN時点のcross-session pending orderが不変の実行契約に反して価格統計前にBLOCKED、R042-Q002は直前60予定nightの有効RN/ONが最大48で50件要件を一度も満たせず全条件0取引のINCONCLUSIVEとして不変保存する。Q003は同じnight状態、端部等号、08:45 fresh signal、08:46 entry、09:46 exitを維持し、rolling QMおよび低・高レンジ分類だけを廃止する。Q003-01はGross時刻分解監査の符号誤り、Q003-02はbootstrap内のz再中心化によりBLOCKEDとして残し、Q003-03はそれらの監査・集計だけを価格読込み前に訂正した。R001--R042-Q002を照合し、同じ状態・時刻・連続RN/ON調整を持つ登録はなかった。

同一trade_dateの予定normal nightを一つだけ選び、予定全足の連続適格性を要求する。ON=最初の予定open、CN=最終予定close、HN/LN=high/low extrema、`rN=CN-ON`、`RN=HN-LN`とする。隔離、欠損、不適格、期間外、ON<=0、RN<=0、rN=0、day約定足不適格は見送り、auction、force-flat、観測端、古いnightへの遡及は使わない。rN>0で`4(CN-LN)>=3RN`、rN<0で`4(HN-CN)>=3RN`を端部確認T=1（等号含む）とし、それ以外T=0とする。rolling参照・分位点・値幅filterはない。

A=端部`sign(rN)`、B=全有効night`sign(rN)`、C=非端部`sign(rN)`、D/E=端部常時buy/sell、F=端部`-sign(rN)`である。A2/A3は片道2/3 tick、A_delayは08:47 entry、A_tseは08:59確認後09:00 entryで、全て09:46 exit、片道30円、1枚、最大1日1取引・1ポジション、Stop/Target/re-entry/途中更新/早期exitなしを固定する。08:45以降の価格はstate、side、選別、取消へ使わない。

全有効Bについて `y=sign(rN)*(open_09:46-open_08:46)`（0 tick・費用前）、`z=ln(RN/ON)`、`U=1[rN>0]` とし、全eligible Development集合から一度だけ得たmean(z)で `y=alpha+beta*T+gamma1(z-mean(z))+gamma2(z-mean(z))^2+eta*U+calendar-year FE+epsilon` を固定する。20 trade_date非循環moving-block bootstrapを10,000回、共通index、末尾切詰め、linear percentile、seed=20260922で行い、各反復でも固定mean(z)のOLSを再計算する。R004隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、固定1,111日次軸、Development正規化Parquet限定、品質上限PASS_LIMITEDを必須とし、OOS/Final Holdoutは読まない。

## R041-Q001: night方向と09:00--09:29方向の競合後追随（事前登録）

正式実行IDは `r041-q001-20260914-night-conflict-opening-followthrough-03`。`…-01` は条件A台帳保存直前の診断用時刻参照例外で停止、`…-02` は売買・費用・評価を変えずに完走したが、合成gateに「直前60日内で有効50件」と「09:30以降の改変不変性」の明示ケースが不足していたため、いずれも判断に用いない不変記録とする。`…-03` はその合成検証補強のみを価格統計前に再登録する。R031はnight全体と09:00--09:04の同符号確認を09:05--10:05に適用するため、09:00--09:29との反対符号・09:30--10:30追随とは同等でない。R001--R040に同一登録はない。

同一trade_dateの予定night通常連続取引全足から `rN=final-normal-close-first-normal-open`、TSE通常営業日の09:00--09:29連続30足から `rO=close_09:29-open_09:00` を作る。night/dayの欠損・不適格・隔離、zero、期間外は見送り、観測端・auction・force-flat・古いnightで代用しない。`rN*rO<0` が競合、`>0` が一致であり、08:45--08:59、gap、値幅、曜日、年、出来高その他はfilterに使わない。対象日を除く直前60予定TSE営業日の有効な`abs(rO)`が50件以上なら昇順`ceil(.5*n)`番目をQMとして、`abs(rO)<=QM`低幅／それ以外高幅を診断専用に記録する。無効参照を古い日で補充しない。

A_conflictは競合日に`sign(rO)`、B_all_openは全有効日に`sign(rO)`、C_agreementは一致日に`sign(rO)`、D/EはA eventの常時buy/sell、F_nightはA eventで`sign(rN)`を取る。A2/A3はAを片道2/3 tick、A_delayは1本遅延し、基本費用は片道1 tick+30円。09:29確定後に09:30 openでentry、10:30 openで固定exit（delayもexitを延長しない）とし、1枚、日次最大1取引、最大1ポジション、Stop/Target/re-entry/早期exitなしを固定する。

R004固定隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、hash `2974bec…3213` を必須とする。Development正規化Parquetと版管理予定表・凍結TSE休日証拠だけを読み、raw、現物・外部価格、出来高、OOS、Final Holdoutへアクセスしない。固定1,111日次軸には見送り・取消・無取引を0円で残す。20 trade_date非循環moving-block bootstrap 10,000回、seed=20260919、共通index、末尾切詰め、linear percentileでA、A-B/C条件付き期待値差、A-D/E/F日次差、低幅・高幅それぞれの0tick費用前sign(rO)調整将来60分returnの競合−一致差を単純平均したΔMを保存する。

B>=800、A/C>=300、A long/short各100、4符号群各75、低/高×競合/一致各80が情報量gateである。不足ならINCONCLUSIVE。充足後はA Net>0/PF>1、A・比較・ΔMの全CI下限>0、A2/A3/A_delay期待値>0、2021--24の3正年、正月27/54、top10除去後Net>0の全通過だけをINVESTIGATEとし、それ以外はREJECTとする。救済探索、WFA、OOS、Final Holdoutは行わない。

## R038-Q001: 現物昼休み極端変化の後場再開追随（事前登録）

登録日: 2026-09-14。正式IDは `r038-q001-20260914-tse-lunch-extreme-follow-04`。`…-01`は取引規則・入力・費用・seedを変更せず完走したが、非極端群の層別trade集計がA台帳だけを読んで0件になる報告上の不備を検出したため判断に使わない不変記録として残す。`…-02`はnonextreme群を固定対照C台帳から集計する修正を再登録したが、bootstrapと判定の保存前に実行枠で中断した未完了記録である。`…-03`も同じ固定仕様だが、静的gate後・Development price access前に実行枠で中断した未完了記録である。`…-04`は取引規則・入力・費用・seedを変えず、直前にPASSした同一pytest/Ruff/mypy gateを再実行しないことだけを成果物へ明記して、同じ修正済み集計を価格統計前に登録し完走する。R020は昼休み変化の反転・120分結合窓・60分保有、R029は12:30後5分確認を使う。R001--R037には、L/P別々の直前60予定TSE営業日、各75% nearest-rank、strict極端、30分固定保有、preclose placeboを同時に持つ仕様はない。既知Developmentでの追加探索であり、独立確認ではない。

仮説は、11:30--12:29の先物 `rL=close_12:29-open_11:30` が直前60予定TSE営業日の同窓で有効な非zero `|rL|` の昇順 `ceil(.75*n)` 番目 `QL` をstrictに上回るとき、方向追随の12:30--13:00費用後期待値が、非極端・逆方向・固定方向、および10:30--11:29の独立placebo `rP` の同じ規則を上回る、である。現物価格、注文フロー、ニュース、裁定を観測・識別したとは主張しない。

TSE通常営業日かつ対応OSE day sessionだけで、L/Pとも対象日以前の直前60予定TSE営業日を日付順に固定する。期間外、隔離、欠損、不適格、zeroは有効参照に含めず、古い日で補充しない。参照が60日未満または有効数50未満、対象return=0、窓欠損は当該L/Pを不成立とする。targetは参照に含めず、同値は極端に含めない。PはAのfilterや日選択に使わない。

A=極端L追随、B=全有効L追随、C=非極端L追随、D/E=A event常時buy/sell、F=A event逆張り、G=極端P追随。A--Fは12:29確定後の12:30 open entry、13:00 open exit、Gは11:29確定後の11:30 open entry、12:00 open exitである。A2/A3は片道2/3 tick、A_delayは一足遅延してexitを延長しない。他は片道1 tick+30円で、1枚・最大1ポジション・日最大1取引、Stop/Target/re-entry/早期exitは置かない。

実行前に入力manifest/hash、source snapshot/hash、seed=20260916、R004隔離hashと全gateを保存する。入力はDevelopmentの選択済み正規化Parquetだけで、raw、出来高、現物/外部価格、OOS、Final Holdoutを読まない。R004隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、hash `2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa` の一致を必須とし、品質上限はPASS_LIMITED。

共通day軸では不成立・取消・無取引を0円とする。極端/非極端×上/下L、G上/下P、0tick費用前の方向調整済み30分returnを保存する。20 trade_date非循環moving-block bootstrapを10,000回、seed=20260916、共通index、末尾切詰め、linear percentileでA日次、A-D/E/F、A-C・A-G条件付き期待値差（反復ごとsum/count再計算）とA-B報告に使う。合成・実行・会計gate、OOS/Final Holdout拒否を通過しない時はBLOCKED。B>=800、A>=300かつ上/下各>=100、C>=700、Lの4群各>=100、G>=300かつ上/下各>=100に満たなければINCONCLUSIVE。情報量充足後、指定のNet/PF/CI、A2/A3/delay、2021--24の3正年、正月27/54、top10除去後Netをすべて満たす時だけINVESTIGATE、それ以外はREJECT。救済探索、WFA、OOS、Final Holdoutは行わない。

## R037-Q001: day開始30分経路効率の直後60分追随（事前登録）

正式実行IDは `r037-q001-20260914-opening-path-efficiency-02`。`…-01` は全条件の約定と集計を完了したが、値幅層に必要eventがないときΔM bootstrapへ0を代入してしまう集計不備を監査で検出したため、判断に用いない不変成果物として残す。`…-02` は取引規則・入力・費用・seed・評価基準を変えず、ΔMを情報量不足時に未推定と表示する修正だけを、再度の価格統計取得前に登録する。R001--R036を価格統計・閾値・event件数・PnLの取得前に照合した。R033は開始30分レンジ圧縮と31--90分の最初のbreakoutを用いるため、開始30分の `|D|/V`、対象日を除く直前60予定dayの75% nearest-rank、30分直後から60分の固定追随とは異なる。登録済み同等仕様はなく、本件だけを実行する。これは既知Development上の追加探索で、独立確認ではない。

仮説は、day開始30分の純変化 `D=p30-p0` が総価格移動 `V=sum(|pi-p(i-1)|, i=1..30)` に対して、対象日を含まない直前60予定day sessionの有効 `eta=|D|/V` の75% nearest-rankより厳密に高いとき、`sign(D)` の次60分追随が費用後期待値を持ち、無条件追随、非高効率、同時刻常時buy/sell、反転を上回る、である。p0は第1足open、p1--p30は各足closeで、opening gapを含めない。D=0またはV=0はsignal外である。これは持続的情報フローの価格代理の検証に限り、注文フロー、出来高、参加者、ニュースを識別しない。

各公式TSE営業日の版管理済みOSE予定通常day開始から連続30適格1分足を要する。参照は対象より前の直前60予定dayだけを新しい順に固定し、期間外・欠損・不適格・R004隔離を古い日で補充しない。その中のD非zeroかつV>0の有効数が50以上のときだけ `ceil(.75*n)` 番目をQetaとする。同値は非高効率である。比較は `abs(D)*Qeta_V > abs(Qeta_D)*V` の整数交差積で行う。同じ参照日の|D|の25/50/75% nearest-rankで対象を4値幅層に記録するが、取引filter・事後選択には使わない。曜日、年、gap、range、方向、出来高その他をfilterしない。

A_effは高効率日のsign(D)、B_allは全有効日のsign(D)、C_noneffは非高効率日のsign(D)、D_buy/E_sellはAと同event時刻で常時buy/sell、F_reverseはAの-sign(D)を取引する。A2/A3はAを片道2/3 tick＋片道30円で再約定し、A_delayはAと同signalを1本遅延するがexitを延長しない。基本費用は片道1tick＋30円。30本目close確定後、次の予定適格足始値Eでentryし、E+60分の足始値Xでexitする。1枚、day最大1取引、最大1ポジション、Stop/Target/re-entry/途中更新/早期exitなし。

入力は `trade_date=2021-01-01..2025-06-30` の選択済み正規化Parquetだけで、raw・出来高・外部価格・OOS・Final Holdoutにアクセスしない。価格統計前に未使用ID、設定、seed=20260915、input manifest完全hash、source snapshot/hashを保存する。R004固定隔離hash、45 session/27,345 bar除外、2,216 session/1,326,086 bar残存が一致しなければBLOCKED、品質上限はPASS_LIMITEDである。共通day軸は予定表と隔離でPnL前に固定し、履歴不足、有効数不足、D/V zero、欠損、隔離、Aの非高効率見送り、取消、無取引を0円で残す。

合成gateは制度変更、calendar_date/trade_date、最初の30本、p0--p30、D/V、gap除外、zero、直前60予定日、50有効日、遡及補充禁止、nearest-rank、閾値同値、交差積、4値幅層、欠損/隔離/期間外拒否、因果性、prefix不変性を含む。実行gateは高効率でA/B、非高効率でB/Cのsignal・side・予定/実時刻・約定経路・1tick損益一致、A/Fの逆side、翌足entry、固定exit、遅延非延長、A/A2/A3/A_delayのevent/side一致、1ポジション、費用とslippage非二重控除、OOS/holdout拒否を要求する。共有engine逸脱なら停止する。

全条件のevent/order/fill/trade、見送り・取消理由、D/V/eta/閾値/値幅層、予定・実時刻、Gross/slippage/fees/Netと日次系列を保存する。高効率/非高効率×上/下、値幅4層を集計する。高効率−非高効率条件付きNet期待値差、B_allとの条件付き比較、各値幅層の0tick費用前sign(D)調整60分returnの高−非高平均と4層単純平均ΔMを保存する。20 trade_date非循環moving-block bootstrap 10,000回（seed=20260915、共通index、末尾切詰め、linear percentile）でA日次平均、A−D/E/F、高−非高条件付き差、ΔMの95%CIを求める。Aの年・月・long/short、正月数、上位5/10利益取引除去後Netも保存する。2025年は1--6月の部分集計である。

情報量gateはB_all>=800、A>=180かつlong/short各60、C_noneff>=500、高/非高×方向4群各60、各値幅層で高>=20・非高>=60であり、不足はINCONCLUSIVE。充足後はA Net>0/PF>1、A日次平均、A−D/E/F、高−非高条件付きNet差、ΔMの全CI下限>0、A2/A3/A_delay期待値>0、2021--2024の3年以上A Net>0、正月27/54以上、top10除外Net>0をすべて要する。一つでも未達はREJECT、全通過でもDevelopment一次INVESTIGATE止まりである。救済探索、WFA、OOS、Final Holdoutは実行しない。

## R034-Q001: day開始30分rangeの固定15分後rejection fade（事前登録）

正式実行IDは `r034-q001-20260914-opening-range-fixed15-rejection-05`。`…-01`は事前登録・source snapshot・input manifest・合成/静的検証後、Development入力を読む前に実行枠の30秒制限で中断した不変記録であり、価格統計・event・PnL・判定を含まない。`…-02`は同一仕様でpreflightと条件別台帳までは作成したが、集計・監査・bootstrap・判定前に実行枠で停止したため判断に用いない。`…-03`は同じ地点で監査の`date`/ISO文字列比較不備により停止し、売買規則・費用・入力・評価は変更していない。`…-04`は全結果まで完走したが、指定synthetic gateの制度時期・下方breakout・探索窓端・calendar/trade_date分離を明示する検証を追加するため、判断に用いない。R004は1 tick外側break後の最初の5分以内の再入を使い、Stop/Targetを持つ逆張りであり、R033は圧縮条件付きのbreakout追随である。R001--R033に、全dayの予定開始30分H/L、31--90本目の最初のstrict close breakout、必ずb+15のcloseだけでrejection/persistenceを判定し、E+60に固定決済する仕様はない。

予定通常day開始から連続30適格1分足でH/Lを固定する。31--90本目で最初にclose>Hなら上方、close<Lなら下方breakoutとし、同値・接触は除外する。breakout bからb+15まで15本は連続適格を要求する。上方は`close_k<=H`、下方は`close_k>=L`をrejection（反対側通過も含む）、厳密な外側をpersistenceとする。最初の再入時刻、range幅・break幅・gap・曜日・年・出来高等のフィルターは使わない。

Aはrejectionだけをbreakout逆方向へ、B_allは全分類eventを同方向へ、C_persistentはpersistenceだけを同方向へ取引する。D/EはAと同event時刻の常時buy/sell、F_continueはAのbreakout方向追随である。k確定後に次適格足始値Eへentryし、E+60分の足始値Xでexitする（exit signalはXの一つ前の足）。A_delayは1本遅らせてもexitを延長しない。各1枚・day最大1取引・最大1ポジション、Stop/Target/re-entry/途中更新/早期exitなし。基本は片道1 tick+30円、A2/A3は2/3 tick+30円である。

価格統計、event件数、PnL前に全Development Parquet manifest hash、source snapshot/hash、seed=20260914、R004隔離hashを保存する。入力は2021-01-01--2025-06-30の選択済み正規化Parquetだけで、raw・出来高・外部価格・OOS・Final Holdoutにはアクセスしない。R004隔離は45 session/27,345 bar除外、2,216 session/1,326,086 bar残存に一致しなければBLOCKED、品質上限はPASS_LIMITEDである。

固定1,111 day軸に無取引・欠損・取消を0円で残す。rejection/persistence×上/下の4群、breakout 15分bucket、20 trade_date非循環moving-block bootstrap 10,000回（共通index、末尾切詰め、linear percentile）を保存する。B_all>=600、A/C各>=200、A/C上下各>=60、4群各>=60が情報量gateであり、不足はINCONCLUSIVE。充足後はA Net>0/PF>1、A日次平均・A-B_all/D/E/F・A-C条件付き期待値差の全95%CI下限>0、A2/A3/A_delay期待値>0、2021--2024で3年正、正月27/54、top10利益除去後Net>0を全て要し、未達はREJECT、全通過もINVESTIGATEに留める。救済探索、Stop/Target、WFA、OOS、Final Holdoutは実施しない。

## R033-Q001: day開始30分レンジ圧縮後の最初のbreakout追随（事前登録）

正式実行IDは `r033-q001-20260914-opening-range-compression-breakout-06`。`…-01`は静的Ruff gate、`…-02`は実行環境の親プロセス制限、`…-03`/`…-04`は予定軸と隔離後day取引対象（1111日）のpreflight照合で停止し、いずれもevent・約定・PnLは未作成である。`…-05`は完走したが、15分bucket表記とA-C bootstrapの反復内sum/count再計算が固定仕様どおりでないことを台帳監査で検出したため判断に用いない。`…-06`はその集計／表示だけを訂正して、同一仮説・価格規則・時刻・費用・入力・seed・判定で再登録する。R008は同一sessionの3×30分局所レンジで開始後90分以降に30分保有する規則、R003は中央値比と複数保有時間の未実行設計である。直前20予定dayの開始30分レンジの5番目順序統計、開始31--90分の最初の終値breakout、固定60分保有を併せ持つR001--R032の同等仕様はない。既知Developmentの追加探索であり独立確認ではない。

対象Rは予定day開始から連続30適格1分足の `H-L`（R>0）。Tは対象を含めない直前20予定day sessionの同Rを昇順にした5番目で、20日の一つでもDevelopment外・欠損・不適格・隔離なら遡及補充せず見送る。`R<=T`を圧縮とし、開始31--90本目で最初に終値が厳密にH超/L未満となる方向だけを採る。同値・接触・breakoutなしは無取引。breakout確定後の次適格始値entryから固定60分後始値exitとし、遅延はexitを延長しない。

A=圧縮日の追随、B_all=全valid日の追随、C_noncompressed=非圧縮日の追随、D/E=同じA eventの常時buy/sell、F=同じA eventの反転、A2/A3=Aの片道2/3 tick、A_delay=A signalを1本遅延した固定exit診断とする。基本は片道1 tick+30円、各条件1枚、day最大1取引、Stop/Target/reentry/早期exitを置かない。R004隔離hashと45 session/27,345 bar除外、2,216 session/1,326,086 bar残存が一致しなければBLOCKED。入力manifest、source snapshot、seed=20260914、合成・実行・会計gateは価格統計、event、PnLより前に成果物へ固定する。OOSとFinal Holdoutにはアクセスしない。

共通予定day軸に無取引を0円で残し、20 trade_date非循環moving-block bootstrapを10,000回、seed=20260914、共通index、末尾切詰め、linear percentileで実施する。A日次平均、A-D/E/F、A-Cの条件付き1取引期待値差を保存する。A>=180・long/short各60、C>=400・long/short各140未満はINCONCLUSIVE。充足後は、A Net>0/PF>1、AおよびA-D/E/FのCI下限>0、A-C条件付き差CI下限>0、A2/A3/A_delay期待値>0、2021--2024の3年以上A Net>0、正月27/54以上、top10除去後A Net>0をすべて満たさなければREJECT。全通過してもINVESTIGATE止まりで、救済探索、WFA、OOS、Final Holdoutは実施しない。

登録日: 2026-09-13。以下は戦略PnLを計算する前に固定する。

## Hypothesis

H1 opening_momentum: セッション開始後の初動には、持ち越し情報に対する注文の分割執行が現れ、その方向が短時間継続する可能性がある。

H2 opening_reversal: セッション開始直後に一時的な需給偏りが発生し、初動の方向と逆に流動性が戻る可能性がある。

いずれも検証前の市場仮説。Day/Nightを同じルールで実行し、診断は分離する。勝った時間帯だけを事後的に選ばない。初動ゼロなら取引しない。

## Implementation / Parameters tested

セッションの予定開始からL分の連続した適格バーが存在することを確認する。初値とL本目終値の差の符号で方向を決め、L本目終値でシグナル、次の適格バー始値で約定する。初動に欠損があればそのセッションは不成立として数える。価格は補完しない。

lookback_minutes = [15,30,45]、holding_minutes = [30,60,90]。2仮説×9点=18条件。パラメータ代表点は事前に(30,60)に固定。1セッション1回、1枚、Stop/Targetなし、時間決済。最初の約定を観測した時点からH分後の始値を目指し、1分前の終値で決済指示。既存の強制決済・最大約定遅延・日付版別取引時間を維持する。

## Development result / 判定基準（実行前固定）

候補の必要条件: 代表点の1 tickと2 tick期待値>0、Development取引数>=200、9点中6点以上の基準コスト期待値>0、代表点の正の月比率>=50%、上位10利益取引除去後の総損益>0。Day/Night・Long/Shortの不一致は報告し、後付けフィルターを作らない。

12か月train/3か月test/3か月step。各trainで9点中6点以上と固定代表点の期待値が正のときのみ次のtestを取引する。trainの最大利益点を選ばない。全14 test窓のうち稼働>=半数、合算期待値>0、稼働窓の過半数が正を要求する。

## Robustness result / 事前に固定した追加検証

代表点2つに0/1/2/3 tick、片道手数料2倍、entry1分遅延、exit1分遅延をエンジンで再実行する。出口の遅延が改善した場合に備え、元のexit referenceと次の1分始値の不利な方を採る追加の台帳ストレスも表示する（再約定ではなく影響見積りと明記）。遅延/手数料2倍でも正を候補条件とする。3 tickは診断として記録。

seed=225、1,000回、取引10%欠落・順序shuffle・復元抽出bootstrap。正の総損益割合とDDの分布を示す。独立取引仮定の限界を明記し、時系列の頑健性はWalk Forwardで別途判断する。

## Validation result / OOSゲート

必要条件を通過した固定代表点のみ、仕様・Development結果を保存してからOOSを読む。OOS >=50取引、1/2 tick期待値>0、上位5利益除去後>0を要求。追加ストレスと期間偏りを確認して判断する。通過なしならOOSとFinal Holdoutは開かない。

## Decision / Next experiment

条件未達はREJECT、データや計測上の問題があればINVESTIGATE。CANDIDATE後も限月別データでの再検証が必要。メタ戦略は複数の根拠ある戦略ができてから、過去情報で算出する状態変数と選定窓を事前登録する。今回の損益から都合のよい局面を切り出さない。

## 実行前データ追記
初回読み込み時に年次ファイル間の完全同一バー重複を検出。PnL計算前に、Bar全列が一致する重複のみ1バー化し品質集計に記録する方針を追加。価格・品質等に相違する重複は拒否する。詳しくは05_data_overlap.md。

## R019-Q001: 過去同種セッション値幅状態による初動追随／反転（事前登録）

登録日: 2026-09-14。Developmentの価格統計・event・PnLにアクセスする前に固定する。R003の現在初動圧縮＋突破、R008のsession内圧縮、R009の現在経路一貫性、R013の直前同種session方向とは、参照窓・状態変数・方向規則・時刻・出口が異なる。既知Developmentでの追加探索であり、独立確認または研究全体の多重性補正済み検証とは扱わない。

day/nightを同一規則で扱う。版管理された予定開始をS、`t=S+29`、`E=S+30`、`X=S+90`とし、予定時刻で`E<=new-entry cutoff`かつ`X<=F`を確認する。現在より前に終了したcalendar上の直前20同種予定sessionを直近から`p1..p20`とする。観測バーから予定列を作らず、欠落・隔離・範囲外・不適格な`pi`を古いsessionで補充しない。各`pi`の`[P_i,P_i+59]`に連続60本の適格足を要求し、`Ri=max(high)-min(low)`とする。`Ri=0`は正当に観測した値として残す。`V=3*sum(R1..R5)-sum(R6..R20)`で、`V>0`を拡大、`V<0`を縮小、`V=0`を見送りとする。現在`[S,t]`の連続30本から`M=close_t-open_S`を計算し、`M=0`も見送る。現在価格をVに含めず、閾値、現在値幅、曜日・年・方向の追加選別はしない。

共通event上でAは拡大時`sign(M)`追随・縮小時`-sign(M)`反転、Bは常時買い、Cは常時売り、Dは常時`sign(M)`追随、Fは常時`-sign(M)`反転、A2はAを片道2 tick・手数料30円で既存engine再約定する。全て1枚、最大1ポジション、session当たり最大1event、Stop/Target/re-entry/状態変化early exitなしとする。t確定後に最短E始値でentryし、X-1確定後にEXITを出して最短X始値で決済する。遅延はXを延長せず、既存の翌適格バー・最大遅延取消・競合・強制決済契約を維持する。

R004固定隔離を45 session／27,345 bar除外、2,216 session／1,326,086 bar残存、一覧hash `2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa`で再現しなければ停止する。物理I/Oは`trade_date=2021-01-01..2025-06-30`の選択済み正規化Parquetだけとし、raw、OOS、Final Holdout、外部価格、出来高は用いない。両session隔離日だけを除いた固定1,120 `trade_date`にday/nightを合算し、見送り・無取引は0円とする。

合成検証はp1..p20の順序・同種限定・5/15非重複、現在session除外、60本高安、V符号／ゼロ、Ri=0、履歴不足・隔離・欠損・範囲外拒否、同じMで過去値幅だけを変えたA反転、過去価格水準の平行移動不変性、30本初動、翌足entry、固定exit、遅延時非延長、cutoff後EXIT、prefix不変性、最大1ポジション、費用会計を含める。実行後は全条件のevents/orders/fills/trades、予定／実際時刻、取消・見送り理由、日次系列、経路・会計照合を保存する。拡大ではA=D、縮小ではA=Fの注文方向・経路・損益を照合し、A-D差は縮小、A-F差は拡大からのみ生じることを確認する。

共通日次系列に20 trade_dateの非循環moving-block bootstrapを10,000回、seed=20260913、全条件共通index、末尾切詰め、linear percentileで行い、A平均、A-B/A-C/A-D/A-Fの95%区間を保存する。V正負×M正負の4群で事前event数、取引数、遅延、費用、Net、期待値を保存する。情報量はA/B/C/D/F各200取引、4群各50事前event。充足後、A Net>0、PF>1、全区間下限>0、A2期待値>0、正の月27/54以上、上位10利益取引除去後Net>0をすべて必要とする。入力・合成・実行・会計ゲート失敗はBLOCKED、情報量不足はINCONCLUSIVE、それ以外の未達はREJECT、全通過もDevelopment一次のINVESTIGATE止まりとする。WFA、追加費用・遅延、救済探索、OOS、Final Holdoutは実行しない。

## R020-Q001: 現物昼休み中の先物変化の後場再開反転（事前登録）

登録日: 2026-09-14。正式登録は `r020-q001-20260914-tse-lunch-reversal-02`。`…-01`は月末日カレンダー生成の実装不備で、登録・Development価格アクセス・event/orders/fills/trades/PnL/bootstrap前に停止し不変保持する。R001初動、R006ナイトgap、R007局所急変、R011開始120分後平均乖離とは窓・時刻・方向・出口が異なり、R001--R019に同等仕様はない。既知Development上の追加探索であり、独立確認・未使用標本・研究全体の多重性補正済み検証ではない。

公式JPX取引時間PDFと内閣府祝日CSVを取得元・SHA-256付きで保存し、TSE営業日をOSE日程から推定しない。TSE休業日は全条件見送り。日中のみ、TSE営業日に10:30--12:29 JSTの連続120適格足を要求し、`P=close_11:29-open_10:30`、`L=close_12:29-open_11:30`とする。P/L=0、窓欠損・不適格は共通見送りで、閾値・標準化・gap充足率・値幅・曜日/年/方向フィルター・現物価格・裁定・出来高・流動性・注文フローは使わない。

A=`-sign(L)`、B=常時買い、C=常時売り、D=`-sign(P)`、F=`sign(L)`を独立した1枚・最大1ポジションで、12:29確定後最短12:30始値entry、13:29確定後最短13:30始値exitとする。A2のみAを片道2 tick・手数料30円で既存engine再約定する。Stop/Target/reference-price exit/re-entryを置かず、遅延でXを延長しない。R004隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、hash `2974bec…3213`を再現する。物理I/OはDevelopment正規化Parquetのみ、R006/R012/R015の保存済み1,111日中trade_dateを日次軸として日中隔離を除外し、TSE休業・欠損・ゼロ・取消・無取引は0円とする。

合成でTSE/OSE日程、JST/trade_date、10:30/11:29/11:30/12:29、P/Lの符号・ゼロ・欠損、同じPまたはLの反実例、prefix、翌足entry、固定exit、遅延非延長、cutoff後EXIT、費用会計、Final Holdout拒否を確認する。A/A2事前event・方向・時刻を照合し、P/L同符号でA=Dのside・経路・PnL一致、差が異符号群のみで生じることを検査する。20日非循環moving-block bootstrap 10,000回、seed=20260913、共通index、末尾切詰め、linear percentileでA/A-B/A-C/A-D/A-Fを評価する。情報量は各A/B/C/D/F>=200、A long/short>=50、P符号×L符号4群各>=50事前event。全充足後、A Net>0、PF>1、全CI下限>0、A2期待値>0、正の月>=27/54、上位10除去後Net>0が必要である。失敗はBLOCKED、情報量不足はINCONCLUSIVE、それ以外の未達はREJECT。WFA、追加費用/遅延、救済探索、OOS、Final Holdoutは実行しない。

## R022-Q001: 直前予定セッションのレンジ端終了後の初動追随（事前登録）

正式IDは `r022-q001-20260914-prior-session-range-end-01`。価格統計・event・PnL前の完全な登録は同成果物の `preregistration.json`、入力Parquetの全hashは `input_manifest.json`、source snapshot/hash・seedは `campaign_manifest.json` に固定した。R014は同一セッション中の60分レンジ位置、R013は前回同種セッション初動、R005/R012/R015は参照時刻・方向・出口が異なるため、R001--R021に同等仕様はない。既知Developmentの追加探索であり、独立確認・研究全体の多重性補正済み検証とは扱わない。

版管理された予定表を実時間順に並べ、現在cのpは直前の予定day/nightセッション一つだけとする。pは観測バーから選ばず、休日・週末・trade_dateとnightのcalendar_date差を予定表で扱う。pがDevelopment外、隔離、欠損、不適格なら古いpへ遡らない。pの予定開始から最終通常1分足まで全予定分の連続適格足を要求し、別途定義された終値オークション／force-flat／観測最終行で代用しない。`H=max(high), L=min(low), O=最初のopen, C=最終通常close, J=C-open(最終60分先頭)`で、`4C>3H+L` は上位四分位買い、`4C<H+3L` は下位四分位売り。`H=L`、両四分位境界の等号、中間50%、`C-O=0`、`J=0`は共通見送りとする。

S足は適格性と注文発行だけに使い、S+1始値でentry、S+61始値で固定exitする。A=四分位方向、B=常時買い、C_short=常時売り、D=`sign(C-O)`、F_late=`sign(J)`を1 tick/片道・30円/片道、A2だけ2 tick/片道・30円で既存engine再約定する。R004隔離hash `2974bec…3213`、45 session/27,345 bar除外、2,216 session/1,326,086 bar残存を再現しなければBLOCKED。日次軸は両セッション隔離日だけを除く固定1,120 trade_dateで、片方のみ隔離、参照不足、欠損、見送り、取消、無取引は0円として残す。

合成はday→night／night→day、週末・休日・制度変更、calendar_date/trade_date、遡及禁止、予定全足の連続性、最終通常足、H/L/O/C/J、境界・ゼロ・中間、S価格独立性、prefix不変性、高安だけを変えたA反転、翌足entry、固定X、遅延非延長、cutoff後exit、最大1ポジション、費用会計、Final Holdout拒否を対象とする。共通日次系列に20 trade_dateの非循環moving-block bootstrap 10,000回（seed=20260913、共通index、末尾切詰め、linear percentile）でA、A-B、A-C_short、A-D、A-F_lateの95%区間を保存する。情報量はA/B/C/D/F各200取引、A long/short各50、A day/night各100、A-D/A-F不一致各50。情報量充足後もA Net>0、PF>1、全CI下限>0、A2期待値>0、正の月>=27/54、上位10除去後Net>0をすべて要求する。失敗はBLOCKED、情報量不足はINCONCLUSIVE、その他の未達はREJECT、全通過もINVESTIGATE止まりである。WFA、救済探索、追加費用・遅延、OOS、Final Holdoutは実行しない。
# R031-Q001: 完了night全体方向の09:00--09:04確認後追随（事前登録）

正式IDは `r031-q001-20260914-night-total-cash-open-confirmation-01`。完全な固定仕様、重複照合、input manifest/hash、source snapshot/hash、seed=20260913、凍結済みTSE営業日証拠は実験成果物の `preregistration.json` に、R031固有の価格統計・event・PnLより前に保存する。R024は08:45--08:59方向、R026/R027はnight極値、R006はnight終値から08:45までのgapであり、本仕様の「完了予定night全体open-to-final-normal-closeのS」と09:00--09:04のQの一致確認には等価でない。

公式TSE営業日の日中だけを対象にし、同一trade_dateの予定nightをscheduleから一つだけ選ぶ。nightがDevelopment外、隔離、欠損、不適格なら古いnightへ遡らない。予定通常開始から予定最終通常1分足まで全予定分の連続適格足を要求し、`S=close_final-open_first`とする。09:00--09:04の連続5適格day足から`Q=close_09:04-open_09:00`を得る。S/Q非zeroが基礎event、同符号が確認、異符号が非確認である。値幅、標準化、gap、night range、曜日、年、出来高、現物/外部価格、basis、注文フロー、流動性、裁定、参加者属性は使わない。

A=確認eventの`sign(S)`、B_night=全基礎eventの`sign(S)`、C_cash=全基礎eventの`sign(Q)`、D_buy/E_sell/F_reverse=確認eventの常時買い／常時売り／`-sign(S)`、A2=Aの2tick/片道再約定である。標準は片道1tick+30円/片道。09:04確定後に最短09:05始値entry、10:04確定後にEXITを発行し最短10:05始値exitとする。entry遅延でexitを延長せず、1枚・最大1ポジション・日次1取引、Stop/Target/re-entry/途中更新/早期exitなしを固定する。

R004隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、hash `2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa`を再現しなければBLOCKED。物理I/OはDevelopment選択済み正規化Parquetだけであり、raw、出来高、現物/外部価格、OOS、Final Holdoutを読まない。固定1,111日中trade_date軸ではday隔離日だけを除外し、休業、参照不足、欠損、zero、A非確認、取消、無取引を0円で残す。

実データ前にnight→day対応、休日／週末、calendar_date/trade_date、制度変更、night全予定足・最終通常足、S/Q正負・zero・4符号群、確認、欠損・隔離・期間外、09:04確定、prefix不変性を検証する。同じSでQだけ、同じQでSだけを変える例を含める。確認eventではA/B_night/C_cashのsignal/side/予定時刻/約定経路/1tick PnL一致、非確認eventではA無注文、B_night/C_cashが反対sideで取引、A−B/A−C日次差の非確認control損益符号反転、翌足entry、10:05固定exit、遅延非延長、A/A2 event・方向一致、費用会計、slippage非二重控除、Holdout拒否を要求する。

20 trade_date非循環moving-block bootstrap 10,000回（seed=20260913、共通index、末尾切詰め、linear percentile）でAとA−B_night/A−C_cash/A−D_buy/A−E_sell/A−F_reverseを評価する。S符号×Q符号の4群、年・月・long/short、正の月、上位5/10利益取引除去後Netを保存する。情報量はB_night/C_cash各>=700、A/D/E/F各>=300、A long/short各>=100、非確認>=250、4符号群各>=100。充足後はA Net>0、PF>1、Aと5差の全CI下限>0、A2期待値>0、正の月>=27/54、上位10除去後Net>0、非確認B_night/C_cash双方の0tick Gross期待値<0を全て必要とする。不足はINCONCLUSIVE、その他の未達はREJECT、全通過でもINVESTIGATE止まり。救済探索、追加費用・遅延、WFA、OOS、Final Holdoutは実施しない。

# R030-Q001: 現物大引け直前5分の先物方向に対する大引け後10分反転（事前登録）

登録日: 2026-09-14。正式IDは `r030-q001-20260914-tse-cash-close-reversal-02`。`…-01` はDevelopment読込後、condition選択辞書がplacebo専用`G_direction`をA処理中にもeager評価して `KeyError` になったため停止した不変成果物である。condition event/orders/fills/trades/PnL/bootstrap/decisionは保存していない。`…-02` はこの分岐実装だけを修正して固定規則を最初から再登録する。この節、実験成果物の `preregistration.json`、入力manifest/hash、source snapshot/hash、seed、実装snapshotをR030固有の価格統計・event/orders/fills/trades/PnL/bootstrap前に固定する。R007は一般的なsession内の局所1分急変後反転であり、版管理TSE大引け相対のT-5..T-1方向、T..T+10保有、時刻placeboを持たない。R021は09:00--09:29方向をC-30からCまで追随する規則（およびpreclose追随対照）で、T-5..T-1を反転してC後10分保有しない。R001--R029に等価な登録はない。この一固定仕様だけを実行し、代替仕様を作らない。既知Developmentでの追加探索であり、独立確認、未使用検証、多重性補正済み検証ではない。

公式TSE営業日だけを対象とし、TSE休業日はOSE day足があっても全条件見送りとする。TSE営業日と大引けTは凍結済みR020/R021の内閣府休日・JPX証跡および版管理scheduleで定め、観測先物バーから推定しない。T=2024-11-01までは15:00、2024-11-05から15:30である。T-5..T-1の連続5適格day足から `P=close_(T-1)-open_(T-5)` を得て、P=0、欠損、不適格、day隔離、期間外はA/B/C/Dを共通見送りとする。A=`-sign(P)`、B_buy=常時買い、C_sell=常時売り、D_follow=`sign(P)`で、T-1確定後に最短T始値entry、T+9確定後にEXITを発行し最短T+10始値exitとする。片道1tick＋30円、1枚、最大1ポジション、1日1取引、Stop/Target/re-entry/途中更新/早期exitなし、遅延時もexitを延長しない。A2はAだけを片道2tick＋30円で既存engine再約定し、Aの0tick Grossは診断専用とする。

G_placeboは独立してT-35..T-31の連続5適格day足から `P0=close_(T-31)-open_(T-35)` を求め、P0非zero時のみ`-sign(P0)`をT-30始値entry、T-20始値exitする。P/P0の一方のzero・欠損は他方のeventを救済も取消もしない。全条件の物理I/OはDevelopment `trade_date=2021-01-01..2025-06-30`の選択済み正規化Parquetのみ。raw、出来高、現物・外部価格、OOS、Final Holdoutへアクセスしない。R004隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、hash `2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa`を再現しなければBLOCKED。固定1,111日を共通日次軸にし、休業・欠損・zero・取消・無取引を0円とする。

合成では旧新T、休日、JST/trade_date、P/P0正負・zero・欠損・隔離・期間外、A/P0独立性、T-1時点の確定とprefix不変性を検証する。さらに翌足entry、固定exit、遅延非延長、cutoff/force-flatとの順序、A/A2同一event、最大1ポジション、slippage非二重控除、OOS/Final Holdout拒否を監査する。共有engineとの不整合はBLOCKEDとする。

20 trade_dateの非循環moving-block bootstrapを10,000回、seed=20260913、共通index、末尾切詰め、linear percentileでA平均とA-B_buy/A-C_sell/A-D_follow/A-G_placebo日次平均差の95%区間を保存する。P符号別、年・月別、T=15:00/15:30別、上位5/10利益除去後Netを保存する。情報量はA/B/C/D各700、G>=700、A long/short各250、Aの各T制度>=100。未達はINCONCLUSIVEで仕様を変えない。充足後はA Net>0、PF>1、Aと4差の全CI下限>0、A2期待値>0、両T制度A Net>0、正の月>=27/54、上位10利益除去後Net>0の全通過が必要で、未達はREJECT、全通過もDevelopment一次のINVESTIGATE止まりとする。WFA、追加費用・遅延、救済探索、OOS、Final Holdoutは実行しない。

# R023-Q001: 08:45--08:59先物変化の現物寄付き後反転（事前登録）

登録日: 2026-09-14。正式IDは `r023-q001-20260914-cash-open-reversal-01`。この節と同成果物の `preregistration.json`、入力manifest、source snapshot、実装hashを、R023固有の価格統計・event・orders/fills/trades・PnL・bootstrapにアクセスする前に固定する。R001はセッション初動の直後に入る一般的な初動規則、R006はnight終了から08:45までのgapを08:46から1時間保有する規則、R020は昼休み境界、R021は09:00--09:29方向を現物引け前30分に適用する規則である。いずれも08:45--08:59の先物変化を、09:00--09:30だけ逆張りし、P追随・G反転を同一event対照にする仕様ではない。等価仕様はないため、この一固定仕様だけを進め、救済仕様へ変更しない。

日中かつ公式TSE営業日だけを対象にする。TSE休業日はOSE先物日中sessionが観測されても全条件を見送る。TSE営業日根拠はR020/R021で凍結したJPX／内閣府証拠をその成果物hash付きで参照し、OSEの観測sessionから推定しない。直前の予定night sessionの最終通常1分足closeを `N` とし、時刻は版管理済みscheduleから導出する。観測最終行、closing auction、古いnightへの遡及で代用しない。当日08:45足openを `O`、08:59足closeを `C`、`P=C-O`、`G=O-N` とする。08:45--08:59の連続15本、N、双方の適格性を必要とし、`P=0` または `G=0`、nightの隔離・欠損・Development外、参照時刻不一致は全条件共通見送りとする。閾値、標準化、曜日、年、値幅、出来高、現物・外部価格、注文フロー、裁定、流動性は使わない。

共通eventでAは `-sign(P)`、Bは常時買い、C_shortは常時売り、Dは `sign(P)`、F_gapは `-sign(G)` とする。A/B/C_short/D/F_gapは片道1 tick＋片道30円、A2はAを片道2 tick＋30円で、同一共有engineにより独立に再約定する。08:59足確定後に発注し最短09:00始値でentry、09:29足確定後にEXITを発行して最短09:30始値で決済する。entry遅延はexit時刻を延長しない。1枚・最大1ポジション・日次最大1取引、Stop/Target/re-entry/early exitなしを維持する。

物理I/Oはtrade_date 2021-01-01--2025-06-30の選択済み正規化Parquetのみとし、raw、出来高、現物・外部価格、OOS、Final Holdoutにはアクセスしない。R004のwhole-session隔離を、45 session/27,345 bar除外、2,216 session/1,326,086 bar残存、一覧hash `2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa` で再現できなければBLOCKEDとする。R006/R012/R015保存済みの固定1,111日中trade_date軸を用い、day隔離日は対象外、TSE休業日、参照不足、欠損、zero、取消、無取引は0円のまま残す。

実データ前に、schedule由来night→day参照、休日/trade_date、最終通常night足、15本連続、P/G正負・zero、隔離・欠損・範囲外拒否、09:00前のsignal確定、prefix不変性を合成検証する。同じGでPだけを変える例、同じPでGだけを変える例を含める。翌足entry、固定09:30 exit、遅延時の非延長、共通event、A/A2信号一致、1ポジション、費用会計、Final Holdout拒否を確認する。共有engineの逸脱はBLOCKEDとし、修正と実験を同時に進めない。

全conditionsのevents/orders/fills/trades、予定・実際時刻、遅延、取消、決済reason、Gross/fees/Netを保存する。slippage attributionは情報表示のみで二重控除しない。20 trade_dateの非循環moving-block bootstrapを10,000回、seed=20260913、共通index、末尾切詰め、linear percentileでA平均、A-B、A-C_short、A-D、A-F_gapの95%区間を保存する。P/G符号別、年・月、long/short、正の月数、上位5/10利益取引除去後Netを保存する。

入力・合成・実行・会計gate失敗はBLOCKED。各1 tick conditionが500取引以上、A long/short各100以上、A/F_gap方向不一致100 event以上を満たさなければINCONCLUSIVE。充足後はA Net>0、PF>1、A平均と4比較の全CI下限>0、A2期待値>0、正の月27/54以上、上位10除去後Net>0を全て必要とし、未達はREJECT、全通過でもDevelopment一次のINVESTIGATEに留める。WFA、追加費用・遅延、OOS、Final Holdout、窓・方向・zero救済探索は実施しない。

## R024-Q001: 08:45--08:59方向の09:00--09:04確認後追随（事前登録）

正式IDは `r024-q001-20260914-cash-open-confirmation-02`。`…-01` は同一固定実行後に、合成テストが4符号群の `P<0,Q<0` を明示していないことを発見したため不変保持した監査補完前の成果物である。価格仕様を変えず、全4群の合成検証を加えた `…-02` を価格統計・event・PnL前に改めて登録し、正式成果物とする。R023は08:45--08:59の `-sign(P)` を09:00から無条件に取引するため、09:00--09:04の `Q` による一致確認後の09:05--09:30追随とは同等でない。R001--R023に同等仕様はない。既知Development上の追加探索であり、独立確認・研究全体の多重性補正済み検証ではない。

日中かつ公式TSE営業日だけを対象にする。TSE休業日はOSE日中足があっても全条件を見送る。先物だけの連続適格15本から `P=close_08:59-open_08:45`、連続適格5本から `Q=close_09:04-open_09:00` を求め、P/Q=0と欠損・不適格・day隔離は共通見送りとする。`sign(P)=sign(Q)`を確認event、異符号を非確認eventとし、閾値、振幅、range、gap、標準化、曜日、年、出来高、現物/外部価格、注文フロー、裁定、流動性を使わない。Aは確認eventだけ`sign(Q)`、B_allは全基礎eventで`sign(Q)`、C_buy/D_sell/F_reverseは確認eventだけ常時買い/常時売り/`-sign(Q)`、A2はAを片道2 tickで再約定する。基本は片道1 tick＋30円である。

09:04確定後に発注し最短09:05始値でentry、09:29確定後のEXITを最短09:30始値で決済する。entry遅延はexitを延長しない。1枚・最大1ポジション・1日1取引、Stop/Target/re-entry/途中更新/早期exitなしを固定する。R004隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、hash `2974bec…3213`を再現しなければBLOCKEDとする。物理I/OはDevelopment選択済み正規化Parquetだけであり、raw、OOS、Final Holdout、現物/外部価格、出来高は読まない。固定1,111日中trade_date軸ではday隔離日を除外し、閉場・欠損・zero・非確認によるA見送り・取消・無取引を0円で残す。

合成でTSE/OSE区別、JST/trade_date、15/5本、P/Qの正負/zero/全4群、同じPまたはQだけを変える反実例、同符号振幅不変性、欠損/隔離/範囲外、09:05前のsignal確定、prefix不変性、翌足entry、固定09:30 exit、遅延非延長、最大1ポジション、費用会計、Holdout拒否を検証する。確認eventではA/B_allのsignal/side/予定/実際時刻/1 tick損益を全件一致、非確認eventではA無注文/B_all取引、A−B_all日次差がB_all非確認損益の符号反転、A/A2事前event・方向一致を要求する。20 trade_date moving-block bootstrapを10,000回、seed=20260913、共通index、非循環・末尾切詰め・linear percentileで評価する。B_all非確認部分の0 tickは、負のgross回避か費用節約かを分ける診断だけである。

情報量はB_all>=700、A/C_buy/D_sell/F_reverse各>=300、A long/short各>=100、非確認>=200、P符号×Q符号の各群>=100事前eventとする。充足後はA Net>0、PF>1、A平均とA−B_all/A−C_buy/A−D_sell/A−F_reverseの全CI下限>0、A2期待値>0、正の月>=27/54、上位10利益取引除去後Net>0、かつB_all非確認の0 tick Gross期待値<0をすべて必要とする。ゲート失敗はBLOCKED、情報量不足はINCONCLUSIVE、その他はREJECT、全通過でもDevelopment一次のINVESTIGATE止まりである。WFA、OOS、Final Holdout、救済探索、追加費用・遅延は実施しない。

## R025-Q001: 直前TSE営業日日中レンジ外開始の08:59定着後追随（事前登録）

登録日: 2026-09-14。`…-01`/`…-02` は事前登録・合成検証後、Development価格読込み前に前景実行枠終了で停止した不変成果物である。正式IDは同一固定仕様を価格統計、event、orders/fills/trades、PnL、bootstrap前に再登録する `r025-q001-20260914-prior-tse-day-range-acceptance-03` とする。この節、成果物の `preregistration.json`、入力manifest/hash、source snapshot/hash、seedを固定する。R002は同一session初動レンジの事後突破、R004は初回突破失敗、R006は前night終値から08:45までのgap反転、R023/R024は08:45後の短期方向であり、直前TSE営業日の先物day全レンジに対する08:45位置と08:59の同側定着を使わない。R022の直前予定session終了位置も異なる。R001--R024に同等仕様はない。既知Development上の追加探索であり、独立確認・多重性補正済み検証ではない。

TSE営業日cのpは、凍結した版管理TSE営業日根拠で定める直前営業日一つだけとし、観測バーで推定せず古い日へ遡らない。pがDevelopment外、day隔離、欠損、不適格なら全条件を見送る。pの予定先物day通常開始から予定最終通常1分足まで全予定分の連続適格足を要求し、`H=max(high), L=min(low)`とする。終了オークション、force-flat、観測最終行では代用しない。当日08:45--08:59の連続15適格day足から`O=open_08:45, K=close_08:59`を得る。基礎eventは`O>H or O<L`、方向dは上側long・下側short、確認は上側`K>H`／下側`K<L`であり、K境界・range内・反対側は非確認、`H=L`は共通見送りである。距離・標準化・gap率・曜日・年・値幅・出来高・現物/外部価格・注文フロー・裁定は使わない。

Aは確認eventのみd、B_allは全基礎eventでd、C_buy/D_sell/F_reverseは確認eventのみ常時買い/常時売り/-d、A2だけAを2 tick/片道で再約定する。標準は1 tick/片道＋30円/片道。08:59確定後に発注し最短09:00始値entry、09:59確定後にEXITを発行し最短10:00始値exitとする。entry遅延はexitを延長しない。1枚、最大1ポジション、日次1取引、Stop/Target/re-entry/途中更新/早期exitなしを維持する。

R004隔離を45 session/27,345 bar除外、2,216 session/1,326,086 bar残存、hash `2974bec…3213`で再現できなければBLOCKED。物理I/OはDevelopment選択済み正規化Parquetだけであり、raw、出来高、現物/外部価格、OOS、Final Holdoutにはアクセスしない。固定1,111日中trade_date軸（day隔離除外）では、TSE閉場、参照不足、欠損、非基礎、Aの非確認、取消、無取引を0円で残す。

実データ前にTSE/OSE区別、休日/週末、calendar_date/trade_date、制度変更、p非遡及、pの予定全足・最終通常足、H/L、上下レンジ外O、K確認/境界/range内/反対側、H=L、欠損/隔離/期間外、08:59確定、prefix不変性を合成検証する。同じO/KでH/Lだけを変える例、同じH/L/OでKだけを変える例を含む。確認ではA/B_allのsignal/side/予定時刻/約定経路/1 tick PnLが一致、非確認ではA無注文・B_allだけ取引、A−B_all日次差がB_all非確認PnLの符号反転、A/A2事前event・方向一致を検査する。翌足entry、固定exit、遅延非延長、最大1ポジション、費用会計、slippage非二重控除、Final Holdout拒否を確認する。

全条件のevents/orders/fills/trades、見送り/取消理由、予定/実際時刻、遅延、決済reason、Gross/fees/Netを保存する。upper/lower×confirmed/nonconfirmedの4群について件数、取引数、Gross、slippage帰属、fees、Net、期待値を保存する。B_all非確認の0 tick Gross期待値は、負raw回避か費用節約かを区別する診断のみである。共通日次系列に20 trade_date非循環moving-block bootstrap 10,000回、seed=20260913、共通index、末尾切詰め、linear percentileでA、A-B_all、A-C_buy、A-D_sell、A-F_reverseを評価する。Aの年/月/long/short、正の月数、上位5/10利益取引除去後Netを保存し、2025年は1--6月の部分集計である。

入力・制度・合成・実行・会計gate失敗はBLOCKED。B_all>=300、A/C_buy/D_sell/F_reverse各>=200、A long/short各>=50、非確認>=100、upper/lower基礎event各>=100を満たさなければINCONCLUSIVE。充足後はA Net>0、PF>1、A平均と4対照差の全95%CI下限>0、A2期待値>0、正の月>=27/54、上位10利益除去後Net>0、B_all非確認0 tick Gross期待値<0をすべて必要とする。いずれか未達はREJECT、全通過でもDevelopment一次のINVESTIGATE止まり。距離閾値救済、追加費用/遅延、WFA、OOS、Final Holdoutは実行しない。

## R027-Q001: 直前nightレンジの09:14外側定着後の外向き追随（事前登録）

正式IDは `r027-q001-20260914-prior-night-range-acceptance-03`。R026は同じ基礎eventで09:14終値がnightレンジ内へ厳密に戻った場合の内向き反転であり、今回の外側定着・外向き追随とは競合する別説明である。R001--R025にも、完了済み直前予定night全レンジ、09:00--09:14の片側だけの極値越え、厳密な外側09:14終値、09:15--10:15外向き保有を組み合わせた同等仕様はない。これはR026後の既知Development上の競合説明検証であり、独立確認・多重性補正済み検証ではない。

公式TSE営業日の日中だけを対象にし、同一trade_dateの予定nightを一つだけ参照する。nightがDevelopment外、隔離、欠損、不適格なら古いnightへ遡らず見送る。nightの予定通常時間の全連続適格足から `H=max(high), L=min(low)` を得る。当日09:00--09:14の連続15適格足で `U=max(high), D=min(low), K=close_09:14` を得る。上側基礎eventは `U>H and D>=L`、方向dはlong、下側は `D<L and U<=H`、dはshortである。両側・越境なし・`H=L`は見送る。上側`K>H`／下側`K<L`だけを受容eventとし、等号・内側・反対側終値は非受容とする。距離、越境時刻、滞在本数、gap、値幅、曜日、年、標準化、出来高、現物・外部価格、注文フロー、裁定、流動性は使わない。

Aは受容eventだけdへ外向き、B_allは全基礎eventでdへ外向き、C_buy/D_sell/F_inwardは受容eventだけ常時買い／常時売り／`-d`、A2はAを片道2 tickで再約定する。基本費用は片道1 tickと片道30円。09:14確定後に最短09:15始値でentryし、10:14確定後に最短10:15始値で固定exit、遅延でexitを延長しない。1枚・最大1ポジション・日次最大1取引、Stop/Target/re-entry/途中更新/早期exitなしを固定する。

物理I/OはDevelopmentの選択済み正規化Parquetだけで、raw、出来高、現物・外部価格、OOS、Final Holdoutを読まない。R004固定隔離（45 session/27,345 bar除外、2,216 session/1,326,086 bar残存、hash `2974bec…3213`）と固定1,111日中trade_date軸を要求する。合成ではnight→day対応、休日・週末、calendar_date/trade_date、制度変更、参照遡及禁止、night予定全足、H/L、片側／両側／非越境、Kの外側・境界・内側・反対側、H=L、欠損・隔離・期間外、09:14確定、prefix不変性を確認する。同一H/L/U/DでKだけを変える反実例を含める。A/B_allの受容event経路一致、非受容時A無注文/B_all取引、日次差恒等式、翌足entry、固定exit、遅延非延長、A/A2 event・方向一致、費用会計・非二重slippage、Holdout拒否を検証する。

20 trade_date非循環moving-block bootstrapを10,000回（seed=20260913、共通index、末尾切詰め、linear percentile）でA、A-B_all、A-C_buy、A-D_sell、A-F_inwardの95%区間を保存する。upper/lower×受容/非受容の4群、年・月・long/short、正の月、上位5/10利益取引除去後Net、B_all非受容0 tick Grossを保存する。B_all>=300、A/C_buy/D_sell/F_inward各>=150、A long/short各>=40、非受容>=150、upper/lower基礎event各>=100を情報量gateとする。充足後はA Net>0、PF>1、全5区間下限>0、A2期待値>0、正の月>=27/54、上位10除去後Net>0、B_all非受容0 tick Gross期待値<0を全て必要とする。不足はINCONCLUSIVE、それ以外の未達はREJECT、全通過でもINVESTIGATE止まり。救済探索、追加費用・遅延、WFA、OOS、Final Holdoutは実行しない。

## R029-Q001: 現物昼休み方向の12:30後5分確認追随（事前登録）

登録日: 2026-09-14。正式IDは `r029-q001-20260914-tse-lunch-confirmation-01`。Developmentの価格統計、event件数、PnL前に入力manifest/hash、source snapshot/hash、固定設定、seed=20260913を保存する。R020は10:30--11:29と11:30--12:29の方向を用いる12:30--13:30反転、R024は08:45/09:00確認、R028は08:59→09:00の単一バー境界であり、同等仕様ではない。既知Development上の追加仮説であり、独立確認・多重性補正済み検証ではない。

公式TSE営業日のday sessionで、凍結済みJPX TSE前後場時刻と内閣府休日CSVを用い、OSE観測から現物昼休みやTSE営業日を推定しない。11:30--12:34の連続65適格1分足を要求し、`P=close_12:29-open_11:30`、`Q=close_12:34-open_12:30`を整数価格で求める。P/Q非zeroを基礎event、同符号をconfirmed、異符号をnonconfirmedとする。値幅・絶対値・標準化・gap・曜日・年・午前方向・night情報・現物/外部価格・basis・出来高・注文フロー・裁定・流動性のフィルターは加えない。

Aはconfirmedだけ`sign(P)`、B_allは全基礎eventで`sign(P)`、C_buy/D_sell/F_reverseはconfirmedだけ常時買い／常時売り／`-sign(P)`とする。片道1tick＋30円、A2だけ2tick＋30円で既存engineを再約定する。12:34確定後に最短12:35始値entry、12:59確定後に最短13:00始値exitとし、entry遅延でexitを延長しない。1枚・最大1ポジション・日次1取引、13:00後の新規entry、Stop/Target/re-entry/途中更新/早期exitなしを固定する。

物理I/OはDevelopmentの選択済み正規化Parquetだけとし、raw、出来高、現物・外部価格、OOS、Final Holdoutを読まない。R004固定隔離（45 session/27,345 bar除外、2,216 session/1,326,086 bar残存、hash `2974bec…3213`）と固定1,111日中trade_date軸を要求する。TSE休業、欠損、zero、A非確認見送り、取消、無取引は0円で残し、品質上限はPASS_LIMITEDを継承する。

実価格前にTSE/OSE日程、休日、JST/calendar_date/trade_date、65本、P/Q正負・zero・4符号群、確認、欠損・隔離・期間外拒否、12:34確定、prefix不変性を合成検証する。同じPでQだけ、同じQでPだけ、同符号で振幅だけを変える反実例を含める。confirmed上のA/B_all同一経路、nonconfirmedのA無注文/B_all取引、翌足entry、固定exit、遅延非延長、A/A2同一event・方向、費用会計・非二重slippage、Holdout拒否を確認する。20日非循環moving-block bootstrap 10,000回（seed=20260913、共通index、末尾切詰め、linear percentile）でAと4差の区間、4符号群、年/月/long/short、正月、上位5/10除去後Net、B_all nonconfirmed 0tick Grossを保存する。

情報量はB_all>=700、A/C_buy/D_sell/F_reverse各>=300、A long/short各>=100、nonconfirmed>=200、4符号群各>=100基礎event。不足はINCONCLUSIVE、入力・制度・合成・実行・会計失敗はBLOCKED。充足後はA Net>0、PF>1、Aと4対照差の全CI下限>0、A2期待値>0、正月>=27/54、上位10除去後Net>0、B_all nonconfirmed 0tick Gross期待値<0の全通過だけをINVESTIGATEとし、他はREJECTとする。窓・方向・exit・閾値救済、追加費用/遅延、WFA、OOS、Final Holdoutは実施しない。

## R032-Q001: 直前day終値からnight開始gapの5分確認追随（事前登録）

正式実行IDは `r032-q001-20260914-prior-day-night-gap-confirmation-02`。`…-01` は C_nonconfirm が確認eventにも発注するランナー分岐不備を台帳照合で検出した無効記録であり、数値を判断に用いない。`…-02` は C を非確認eventだけに限定するという本節の元仕様へ訂正しただけで、仮説、価格定義、時刻、費用、入力、判定、seedを変更せず、Developmentの価格統計・event・PnL前に再登録する。R005、R006、R012/R031、R024は参照境界または取引sessionが異なり、R001--R031に同等仕様はない。既知Development上の追加探索であり独立確認ではない。

版管理OSE予定表を実時間順に使い、対象night `n` の直前予定sessionを一つだけ選び、それがday `d` でなければ見送る。`d`/`n` の欠損・不適格・隔離・Development外に対して代替day/nightへ遡らない。`C_D` は `d` の予定最終通常1分足終値であり、終了オークション、force-flat足、観測最終行では代用しない。night最初の連続5適格足から `O_N=first open`、`C_5=fifth close`、`G=O_N-C_D`、`Q=C_5-O_N` を整数価格で計算する。G/Q非zeroを基礎event、同符号を確認、異符号を非確認とし、追加フィルターは加えない。

A=確認eventの`sign(G)`、B_all=全基礎eventの`sign(G)`、C_nonconfirm=非確認eventの`sign(G)`、D/E/F=確認eventの常時買い／常時売り／`-sign(G)`、A2=Aの片道2 tick再約定とする。基本は片道1 tick+30円。5本目確定後の6本目始値entryから固定60分後始値exitで、entry遅延はexitを延長しない。1枚・最大1ポジション・nightごと条件別最大1取引、Stop/Target/reentry/途中更新/早期exitなしである。

R004固定隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、hash `2974bec…3213` の再現を必須とする。物理I/OはDevelopment選択済み正規化Parquetのみ。対象night隔離は日次軸から除外し、参照day隔離・欠損、zero、非確認、取消、無取引は固定対象night軸で0円とする。20日非循環moving-block bootstrapを10,000回、seed=20260913、共通index、末尾切詰め、linear percentileでA、A-B_all、A-D/E/F、および確認−非確認の`sign(G)`方向0tick Gross平均差に適用する。情報量・判定はPlanner指定どおりとし、WFA、追加費用/遅延、救済探索、OOS、Final Holdoutは行わない。

## R040-Q001: 直前dayレンジ圧縮後の前日境界accepted breakout追随（事前登録）

正式実行IDは `r040-q001-20260914-prior-day-range-compression-acceptance-01`。R001--R039を価格統計・閾値・event件数・PnLの前に照合した。R033は当日開始30分レンジと直前20日、R036は前日境界の5分後不受容を逆張りする。直前通常dayの全レンジ、直前60予定dayの下位四分位、翌日開始120分内の最初のstrict close breakout、5分後の外側受容、60分順張りを同時に持つ登録済み仕様はない。既知Development上の追加探索であり、独立確認ではない。

仮説は、直前予定day `p` の予定通常連続取引全適格足から得る `W=PDH-PDL>0` が、`p` より前の直前60予定dayの有効Wを昇順にした `ceil(.25*n)` 番目 `QW` をstrictに下回るとき、翌day `d` 開始後120予定足内の最初のstrict close breakoutが、`b+5` の終値でも同じ外側にあるなら、breakout方向への60分追随は費用後期待値を持ち、全accepted、非圧縮accepted、常時buy/sell、逆方向を上回る、である。これは圧縮後の外側受容を価格発見の可能な代理として検査するだけであり、注文フロー、出来高、ニュース、オプション需給を識別しない。

`d` の直前予定day `p` 一つだけを用い、欠損・不適格・隔離・Development外なら古い日を代用しない。終了オークション、force-flat、観測最終行でPDH/PDL/Wを代用しない。参照60日は期間外・隔離・欠損・不適格・W<=0を無効として残し、古い日で補充しない。50有効幅未満は見送る。`W<QW` のみ圧縮で同値は非圧縮である。`d` の第1足openが `[PDL,PDH]` 外ならgapとして無条件見送り、high/low接触・close同値はbreakoutにしない。bからk=b+5まで連続適格を要し、unacceptedなら同日の後続breakoutを使わない。

A_compressedは圧縮acceptedをbreakout方向、B_allは全acceptedを同方向、C_noncompressedは非圧縮acceptedを同方向、D_buy/E_sellはA eventの固定long/short、F_reverseはA eventの反対方向とする。A2/A3はAを片道2/3 tick+30円、A_delayはA entryだけ一足遅らせ、A--Fは片道1tick+30円である。k確定後に次の予定適格足openでentryし、そのentry予定時刻から60分後の予定足openでexitする。1枚、day最大1取引、最大1ポジション、Stop/Target/re-entry/途中更新/早期exitなしである。

実価格前にinput manifest/hash、source snapshot/hash、seed=20260918、全gateを保存する。入力はDevelopment選択済み正規化Parquetと版管理OSE予定表だけで、raw、出来高、外部価格、OOS、Final Holdoutを読まない。R004固定隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、hash `2974bec152bf385b6d006ff4c8a29e51e4e883abcd513d08920504f2ab3213fa` の一致が必須で、品質上限はPASS_LIMITED。合成gateは制度変更、calendar_date/trade_date、p一意選択、全通常足、QW、gap、upper/lower/equality、120足端、最初のb、b+5、欠損・隔離・期間外・prefix、翌足entry、60分exit、遅延非延長、共通event/path、会計、OOS/holdout拒否を含む。

予定表と隔離で固定した共通day軸へ全見送り・取消・無取引を0円で残す。圧縮/非圧縮×上/下の4群の適格日数、breakout、accepted、取引、Gross、fees、Net、期待値を保存する。20 trade_date非循環moving-block bootstrapを10,000回、seed=20260918、共通index、末尾切詰め、linear percentileでA日次平均、A-B/A-C条件付きNet期待値差（各反復sum/count再計算）、A-D/E/Fを算出する。年・月・方向、正月数、上位5/10利益除去後Netを保存する。B>=400、A>=100かつ上下各35、C>=250、4群各35未満はINCONCLUSIVE。充足後にA Net>0、PF>1、指定全CI下限>0、A2/A3/delay期待値>0、2021--24の3正年、正月>=27/54、top10除去後Net>0を全通過したときだけINVESTIGATE、その他はREJECTとする。救済探索、WFA、OOS、Final Holdoutは実施しない。
## R046-Q001: OSE day開始30分方向効率と通常午前placeboの60分追随（事前登録）

登録日: 2026-09-15。正式IDは `r046-q001-20260914-opening-path-efficiency-placebo-01`。R001--R045を価格統計、効率、閾値、event件数、PnLの取得前に照合した。R037は同じday開始30分の経路効率を用いるが、閾値同値を非高効率とするstrict規則で、09:45--10:14の通常午前placeboとの共通E、q70/q80、固定pooled OLSを持たないため同等登録ではない。既知Development上の追加探索であり、独立確認・多重性補正済み検証とは扱わない。

単一仮説は、08:45--09:14の先物価格が少ない往復で一方向へ進んだ高方向効率日では、09:15--10:15の同方向追随が費用後期待値を持ち、全有効日、非高効率、固定方向、逆方向、および通常午前の同型高効率追随を上回る、である。これはday開始と現物開始をまたぐ一貫した価格改定が情報吸収の代理かもしれないという価格仮説だけであり、注文フロー、ニュース、参加者属性、現物価格を観測・識別したとは主張しない。

入力は `trade_date=2021-01-01..2025-06-30` の選択済み正規化Parquet、版管理済みOSE/TSE予定表、R004固定隔離のみとする。raw、出来高、現物・外部価格、OOS、Final Holdoutを読まない。R004隔離45 session/27,345 bar、残存2,216 session/1,326,086 bar、固定1,111 trade_date軸を照合し、不一致はBLOCKED、品質上限はPASS_LIMITEDである。input manifest完全hash、source snapshot/hash、設定、seed=20260926を価格統計前に保存する。

W0=08:45--09:14、WP=09:45--10:14をそれぞれ連続30本とする。各窓でp0は第1足open、piは第i足close、`r=p30-p0`、`L=Σ|pi-p(i-1)|`、`e=|r|/L`である。L>0かつr≠0を要する。各窓は対象日前の直前60予定TSE営業日だけから同じ有効eを集め、50件以上でnearest-rank q70/q75/q80を求める。当日を含めず、60日より前へ補充しない。`e>=q`を高効率（等号含む）とし、placeboは自身の過去分布を使う。両窓、両因果的閾値、09:15/10:15/11:15の必要約定経路が適格な日だけを共通Eとし、欠損、隔離、期間外、zeroを代用しない。

A_open_highはW0 q75高効率日のsign(r0)、B_open_allは全E日のsign(r0)、C_open_nonhighはW0非高効率日のsign(r0)、D_buy/E_sellはAと同event・時刻の固定buy/sell、F_reverseはAと同eventの−sign(r0)、G_placebo_highはWP q75高効率日のsign(rP)である。A--Fは09:15始値entry、10:15始値exit、Gは10:15始値entry、11:15始値exit。A_delayは09:16 entry・10:15 exit、A2/A3はAを片道2/3 tickで再約定する。基本費用は片道1 tick+30円。各1枚、日次最大1取引・最大1ポジション、Stop/Target/re-entry/途中更新/早期exitなしとする。

休日、制度変更、calendar_date/trade_date、窓端、p0--p30、r/L/e、zero、60予定日、最低50、当日除外、非遡及、nearest-rank、q70/q75/q80、等号、欠損・隔離・範囲外、因果性・prefix不変性を合成検証する。開始窓高効率ではA/B、非高効率ではB/Cのevent・side・時刻・約定経路・1tick損益、A/Fの反対side、A/D/Eの同event、A/Gの共通E時刻変換、翌足entry、固定exit、遅延非延長、A/A2/A3/A_delayのevent・side、費用会計、最大1ポジション、OOS/holdout拒否を実行gateとする。

全event/order/fill/trade、見送り理由、各pi/r/L/e/閾値/分類、予定・実時刻、Gross/slippage/fees/Net、固定日次系列、高効率/非高効率×方向4群を保存する。A Grossは09:15--09:30と09:30--10:15に固定分解する。方向調整済み0tick費用前将来Gross円をyとして、全E日2行・全行一回の平均で、`y=α+θO+λH+δ(O×H)+γ1(zR-平均zR)+γ2(zR-平均zR)^2+γ3(zL-平均zL)+ηU+暦年固定効果+ε` を固定する（O=開始窓、H=q75高効率、zR=ln(|r|/p0)、zL=ln(L/p0)、U=1[r>0]）。full rank以外はBLOCKEDである。

20 trade_date非循環moving-block bootstrapを10,000回、seed=20260926、共通日index、末尾切詰め、linear percentileで実施する。rolling分類は再推定せず、A日次平均、A−B/C/D/E/F/G条件付き期待値差、δの95% CIを保存する。Aの年・月・方向、正月数、上位5/10利益取引除去後Netを報告し、2025年は1--6月部分集計と明記する。

E>=800、B>=800、A>=180、C>=550、A long/short各>=60、開始窓高/非高×方向4群各>=60、G>=180かつlong/short各>=60、A80>=140が情報量gateであり、未達はINCONCLUSIVE（仕様変更しない）。情報量充足後はA Net>0、PF>1、A日次平均・A−B/C/D/E/F/G・δの全CI下限>0、A2/A3/A_delay期待値>0、A70/A80ともNet>0・PF>1、2021--2024の少なくとも3年A Net>0、正月27/54以上、top10除外Net>0を全て要する。一つでも未達はREJECT、全通過してもDevelopment一次INVESTIGATE止まりとする。救済探索、追加閾値、時刻/方向/年/値幅filter、Stop/Target、WFA、OOS、Final Holdoutは実施しない。
## R048-Q001: OSE night終盤30分レンジへのday再開失敗breakout逆張り（事前登録）

登録日: 2026-09-15。正式IDは `r048-q001-20260915-night-terminal-failed-breakout-03`。`…-01` はA実行後にBが復帰なし/ambiguousの初回breakoutを扱う際、復帰時だけ生成される方向fieldを参照して `KeyError` で停止した不変の技術記録である。`…-02` はBを訂正して完走したが、placebo SPにnightのH0/L0を再利用しており、WP自身の30分rangeを用いる指定と違うため無効記録である。`…-03` は主売買規則・入力・費用・seed・判定を変えず、placeboをWPのH/L/Pから分類する実装訂正だけを、価格統計・event件数・PnL集計前に再登録して実行する。R001--R047を価格統計・event件数・PnL前に照合する。R042は同一trade_date night全体のterminal状態から08:46--09:46を追随する仕様であり、night最終30分のH/L、day開始後の最初のstrict close breakout、5分内のレンジ内close復帰、30分逆張り、09:45--10:44の同型placeboを持たない。R036はprior-dayレンジである。よって同一登録はない。既知Developmentでの探索であり、独立確認ではない。

W0は同一trade_dateの予定normal OSE night最終30分、`H0=max(high)`、`L0=min(low)`、`P0=第1足open`とする。S0はday予定開始から30分で、最初の`close>H0`/`close<L0`だけを上方/下方breakoutとする。接触・wick・等号はbreakoutでない。breakoutの次の5予定足だけを順に見て、最初の`L0<=close<=H0`を復帰確認とする。復帰前のstrict反対境界外closeはambiguousで取引しない。期限内非復帰はeventでなく、後続breakoutへ振り替えない。WP=09:45--10:14、SP=10:15--10:44には同じ規則を用いる。両reference/search/最大確認/最遅entry/各30分exit経路が連続適格で、R004隔離外、期間内の共通Eのみを使う。欠損を観測上の別行で補わない。

Aは主5分失敗eventを上方ならshort、下方ならlongで復帰確認後の次予定足openにentryし、そのentryから30予定分後の足openでexitする。BはEの主初回breakout全てをbreakout次足openから同じ逆方向で30分保有し、復帰を参照しない。C/DはAと同event・時刻の固定buy/sell、FはAのbreakout方向、Gはplacebo失敗eventの同型fadeである。A2/A3はAを片道2/3 tick+30円で再約定し、A_delayはentryだけ1予定分遅らせ元のexitを延長しない。復帰期限感度は独立再分類したA_h3/A_h7だけである。各条件1枚、trade_date最大1取引・最大1ポジション、Stop/Target/reentry/途中更新/早期exitなしとする。

主/placeboの失敗eventを各1行にして、fade方向調整済み0tick・費用前30分Gross円を`y`、主指標を`O`、`zR=ln((H-L)/P)`、`zB=ln((breakout excess+1tick)/P)`、復帰分数`k`、上方指標`U`として、`y=alpha+delta O+gamma1(zR-mean zR)+gamma2(zR-mean zR)^2+gamma3(zB-mean zB)+gamma4(k-mean k)+eta U+暦年固定効果+epsilon`を固定する。meanは全回帰行で一度だけ、full rank不成立はBLOCKEDである。20 trade_date非循環moving-block bootstrapを10,000回、seed=20260928、共通index・末尾切詰め・linear percentileでA日次平均、A-B/C/D/F/G条件付き期待値差、deltaを再計算する（eventは再推定しない）。

入力・予定表・合成・実行・会計・OLS gateの失敗はBLOCKED。E>=800、B>=400、A/G各>=120、A/Gの上/下各>=40、A_h3>=80を満たさなければINCONCLUSIVE。充足後はA Net>0、PF>1、A日次平均・5比較・deltaの全CI下限>0、A2/A3/A_delay期待値>0、A_h3/A_h7のNet>0/PF>1、2021--2024の正年>=3、正月>=27/54、top10除外Net>0を全て満たすときだけDevelopment一次INVESTIGATE、他はREJECTとする。raw、volume、現物/外部価格、OOS、Final Holdout、指定外の時刻・窓・horizon・保有・方向・回帰・filter、Stop/Target、WFA、救済探索を実施しない。
