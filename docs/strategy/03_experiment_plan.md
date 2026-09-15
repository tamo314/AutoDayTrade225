# R001: セッション初動の継続と反転（事前登録）

## R067-Q001: OSEナイト情報のTSE開始後120予定分継続（事前登録）

登録IDは最終的に`r067-q001-20260915-night-to-day-information-continuation-03`。価格統計前にR001--R066を照合した。R055は全ナイトreturnをTSE開始で追随するが30分保有で経路効率を持たず、R062は開始15分の拒否と30分保有を使う。同じ全ナイトreturn・経路効率・次TSE開始entry・120予定分exitの組合せは既評価ではない。`…-01`は未適格行の台帳side参照で注文前に停止、`…-02`は空の感度event集合をBLOCKEDとした監査表現で統計前に停止した。`…-03`は仮説、入力、閾値、費用、感度、seedを変えず、これら技術表現だけを訂正した不変実行である。

Developmentの正規化Parquet、版管理OSE/TSE予定表、R004固定隔離のみを使う。各trade_dateの直前OSEナイトについて、全予定足の`n0` openと`nc` closeから`r=nc/n0-1`、`a=|r|`、`s=sign(r)`、`e=|nc-n0|/(|c0-n0|+Σ|ci-c(i-1)|)`を作る。rolling Uはnight観測値だけの直前120予定trade_dateであり、当日を除外して有効100件以上の場合だけaのq50/q65/q70/q75、eのq40/q50/q60をnearest-rankで作る。古い観測への補充、後続TSEの取引適格性によるU選択はしない。

A=`a>=q70,e>=e_q50`、C=`q50<=a<q70,e>=e_q50`、D=`a>=q70,e<e_q50`、M=`q50<=a<q70,e<e_q50`、B=`a>=q50`の方向eventは+sで、A_fadeは−s、A_buy/A_sellは固定sideである。entryはTSE最初予定足open、exitは120予定分後open。0 tick診断、2/3 tick、手数料2倍、1/5予定足delay（exit非延長）、60/180分保有、q65/q75、e_q40/e_q60のみを固定感度とする。固定1,111日軸、20日非循環MBB 10,000回（seed=20261010）、block 10/40、nuisance-only MP-FWLと20日block-wild score bootstrap（seed=20261011）を指定どおり行い、情報不足はINCONCLUSIVE、その他の経済gate未達はREJECTとする。OOS、Final Holdout、WFA、救済探索はしない。

## R066-Q001: TSE全体下側分位後のOSEナイト固定買い（事前登録）

登録日: 2026-09-15。価格統計、event数、PnLを取得する前にR001--R065を照合した。R059はTSE終値から後続OSEナイト開始までのgap取引、R065は直前OSE night開始から当日TSE開始までの固定買い、R055/R062はnight情報からTSE開始後を取引する。因果的なTSE全体returnの下側分位、後続night開始entry、翌TSE開始exit、固定買いとA/C/D/U/A_short対照を同時に持つ既評価登録はない。本件は反復利用済みDevelopment上の探索である。

入力はtrade_date 2021-01-01--2025-06-30の正規化Parquet、版管理済みTSE/OSE予定表、R004固定隔離のみである。各TSE日tの最初予定足openをd0、最終予定足closeをdc、r=dc/d0-1とする。rolling Uはd0/dcのみでrを計算できる直前120予定TSE営業日で、night・将来の取引適格性・当日を使わず、古い日で補充しない。100件以上でnearest-rank q20/q25/q30/q50/q70/q75/q80を算出し、下側等号は外側に含める。共通EはTSE d0/dc、後続night n0/n0+1/n0+5、次TSE d0/d0+1が一意対応の予定表連鎖に属し、正価格、適格、非隔離の日だけである。

A=r<=q25、C=q25<r<=q50、D=r>=q75、U=rolling有効な全Eで、いずれもn0固定買い・翌TSE d0固定exit、A_shortはAと同一event/timeの固定売りとする。費用は片道1tick+30円、診断0tick、2/3tick、手数料2倍、n0 entryの1/5予定足遅延（exit非延長）、exitの1予定足遅延、独立再分類q20/q30（上側q80/q70）だけを固定感度とする。固定1,111日軸、20日非循環MBB 10,000回（seed=20261008）、block10/40、A/D eventの0tick Grossへのnuisance-only MP-FWLと20日block-wild 10,000回（seed=20261009）を使う。依頼記載の因果性・実行・会計・情報量・経済gateを必須とし、WFA/OOS/Final Holdout/救済探索は行わない。

## R065-Q001: OSEナイト開始からTSE開始までの固定買い（事前登録）

登録日: 2026-09-15。価格、event数、PnL、既存の時刻別・方向別成績を取得する前にR001--R064を照合した。R055/R062はナイト情報を用いるがTSE開始後の取引であり、R059はTSE終値から後続OSEナイト開始へのギャップ取引である。`N(t)`開始から`TSE(t)`開始への固定買い、および同じ`TSE(t)`開始から次の`N`開始への固定買いを、同一の版管理境界・固定entry/exitで比較する登録済み仕様はない。本件は反復利用済みDevelopment上の探索であり、独立再現ではない。

入力はtrade_date 2021-01-01--2025-06-30の正規化Parquet、版管理済みOSE/TSE予定表、およびR004固定隔離のみとし、OOSと2026年以降を読まない。各固定1,111 trade_date軸のTSE日`t`について、予定表から一意に対応する直前OSEナイト`N(t)`の最初の予定足openを`n0`、TSE(t)最初の予定足openを`d0`、予定表でその後に一意対応するOSEナイトの最初の予定足openを`n1`とする。calendar_dateから対応先を推測せず、`previous_trade_date`/`next_trade_date`を含む版管理済み連鎖だけを使う。

共通Eには、三境界が一意で、正価格・適格・R004非隔離、予定表連鎖が連続し、主条件と全感度に必要な足が存在する日だけを入れる。休日跨ぎ、制度変更、欠損、曖昧なsession対応、隔離、非正価格は監査台帳に理由付きで残し、代替境界への遡及・補完をしない。主条件Aは`n0`で固定買いを約定し`d0`で決済、対照Dは同じ`t`で`d0`買い・`n1`決済、A_shortはAと同一event・時刻の固定売りとする。価格からsignalやsideを選ばず、Stop/Target/re-entry/早期exitなし、各条件1枚・最大1ポジションである。予定開始への固定市場注文は、価格観測後のsignalではなく事前に置かれた時刻注文として指定し、全entry/exitのopenに片道slippageを一度だけ適用する。

主費用は片道1 tick+片道30円、診断用は0 tick、固定感度は片道2/3 tick、手数料2倍、Aのnight entryのみを1/5予定足遅延し`d0` exitを延長しない条件、Aの`d0` exitを1予定足遅延する条件だけである。他の時刻・曜日・月・方向filterは計算・表示しない。20 trade_date非循環moving-block bootstrapを10,000回、固定seed=20261007、共通index、末尾切詰め、linear percentileで実施し、A日次平均、paired A-D、A-A_shortの95%CIを保存する。block長10/40を推論感度とする。

E>=800かつAの1/5足night-entry遅延感度各>=750を情報量gateとする。充足後はA Net>0、PF>1、三つの主CI下限>0、2/3 tick・手数料2倍・全遅延感度のNet>0かつPF>1、block 10/40でもA平均とA-DのCI下限>0、2021--2024の少なくとも3年でA Net>0、正の月27/54以上、上位10利益取引除去後Net>0をすべて必要とする。未達一つでREJECT、全通過でもDevelopment反復利用上のINVESTIGATEとする。旧新取引時間制度、曜日、月、休日までの間隔は報告専用である。WFA、OOS、Final Holdout、結果依存の境界・方向・期間救済は行わない。

`r065-q001-20260915-overnight-risk-premium-01` は、事前登録、static/synthetic gate、R004隔離、Development event台帳の後、遅延条件の予定timestampが台帳に未展開である実装不備により、注文・fill・trade・PnL・bootstrap・判断の生成前に停止した不変技術記録である。`…-02` は`n0+1`、`n0+5`、`d0+1`という既登録済み予定時刻を台帳へ明示するだけで、仮説・境界・価格・費用・感度・seed・判定を変えずに再登録する。

## R064-Q001: TSE開始後のtrend-pullback continuation（事前登録）

登録日: 2026-09-15。価格統計、方向別・時刻別損益、event数、将来returnを取得する前にR001--R063を照合した。R035/R049/R063は短時間shock又は拒否であり、R033/R046/R051は開始局面の即時継続、breakout又は圧縮である。TSE最初の60予定足の方向変動、続く15足の20--50%かつ起点非破壊の部分押し、次足entry、30予定分hold、rolling q50/q70/q75/q80の四セル比較を同時に持つ登録済み仕様はない。本件は反復利用済みDevelopment探索であり、独立再現ではない。

入力はtrade_date 2021-01-01--2025-06-30の正規化Parquet、版管理TSE予定表、R004固定隔離だけとし、OOSおよび2026年以降を読まない。最初の60予定足の最初openをo、60本目closeをc59、d=c59-o、m=|d|/o、s=sign(d)、e=|d|/(|c0-o|+Σ|ci-c(i-1)|)とする。d=0はEに残すが方向eventから除外する。各観測長45/60/75について、mを計算可能な日だけから直前120予定TSE営業日を参照し、100有効値以上でnearest-rank q50/q70/q75/q80（当日除外、補充なし、等号上側）を得る。U採否はresponse、entry、exit、rollingの成立又は将来情報を要求しない。

続く15予定足の最終closeをc74とし、p=-s(c74-c59)/|d|とする。Rは0.20<=p<=0.50かつresponse全closeでs(close-o)>0、Fは-0.10<=p<0.10である。X=m>=q75、中程度=q50<=m<q75、A=X/R、C=中程度/R、D=X/F、M=中程度/Fは排他的である。A/C/D/MとB=m>=q50の全方向eventは+s方向、A_fadeは-s、A_buy/A_sellは同一A event・時刻の固定方向である。signal=response最終close、entry=次予定足open、exit=entryから30予定分後openとし、観測・responseの値動きはPnLに含めない。1枚・最大1ポジション、Stop/Target/reentry/early exitなし、片道1tick+30円を固定する。

共通Eは主仕様と全感度のrolling、観測、response、entry、1足遅延entry、15/30/45分exitに必要な全足が同一TSE segmentで適格な日だけとする。感度は片道2/3tick、delay非延長、q70/q80、押し帯0.15--0.45/0.25--0.55、観測45/75（m/s/e/rolling/response/entryから独立再構成）、同一A eventの15/45分holdだけである。指定外の窓、閾値、押し帯、entry、exitは計算・表示しない。

A/C/D/Mの全eventに+s方向の0tick費用前30分Grossをy、X、R、Q=X×R、ln(m/q50)、p、p²、e、response range/|d|、最初15分s調整return bps、s調整gap bps、upward指標、暦年固定効果をnuisanceとする。deltaはnuisance-only Moore--Penrose FWL（rcond=1e-12、残差Q SS tolerance=1e-12）で推定し、固定design 20 trade_date block-wild score bootstrap 10,000回、固定seedでCIを得る。その他は固定1,111日軸、20 trade_date非循環MBB 10,000回、別固定seed、共通index、末尾切詰め、linear percentileで評価する。全gate、情報量、経済判定、OOS/Final Holdout lockは依頼本文の固定規則に従う。

`r064-q001-20260915-tse-trend-pullback-continuation-01` と `…-02` は、事前登録、合成/static gate、R004隔離、U・event台帳を保存した後に、外部executorの30秒同期枠で終了した不変技術記録である。注文、fill、PnL、bootstrap、結果判定は生成されていない。`…-03` は仮説、入力、U、event、費用、seed、統計、gateを変えず、`…-02`で実測済みのpytest/Ruff/mypy成功記録を研究・strategy・test hash一致で検証して引き継ぎ、PnL計算を別の同期実行として再登録する。

## R063-Q001: TSE日中の時刻調整済み流動性shock拒否後20分継続（事前登録）

登録日: 2026-09-15。価格統計、方向別／時刻別PnL、event数、将来returnを取得する前にR001--R062を照合した。R035はTSE内の非重複5分blockと同時計rolling shockの最初のeventを用いるが、直前60日・strict q90・直後拒否なし・15分保有である。R049は120日同時計分位と5分shockを用いるが、固定6 anchor、全anchor共通E、直後拒否なし、15分保有である。従って、第31--150予定足の完全分割、位置別120日rolling q70/q85/q90/q95、最初のq70 event、同長response拒否、次open entry、20分holdを同時に持つ既登録仕様はない。本件は反復利用済みDevelopment探索であり、独立再現ではない。

入力はtrade_date 2021-01-01--2025-06-30の正規化Parquet、版管理予定表、R004固定隔離のみとし、OOS/2026以降を読まない。主仕様は第31--150予定足を長さ5の完全非重複blockに分割する。各位置blockの最初openと最後closeからm=abs(c-o), s=sign(c-o)を得る。各位置のUは、そのblockだけが適格・非隔離・正価格でmを計算できる直前120予定TSE営業日（当日除外、古い日への補充なし）であり、response・entry・exitやrolling成立をU採否に用いない。100有効U以上でnearest-rank q70/q85/q90/q95（等号は上側）を得る。m=0は方向candidateから除外する。

各E日に時系列順で最初のm>=q70 blockだけをeventとする。X=m>=q90、中程度=q70<=m<q90。同長の直後response最終close crについてz=s(cr-c)を計算し、z<=-0.25mをR、z>=+0.25mをFとする。A=X/R、C=中程度/R、D=X/F、M=中程度/Fは排他的で、A/C/D/M/Bは全て-s方向、Bは全first-q70 eventである。signal=response close、entry=次予定足open、exit=entryから20予定分後open。A_follow=s、A_buy/A_sellは同一A event/timeの固定sideである。1枚、最大1ポジション、Stop/Target/reentry/early exitなし、片道1tick+30円を固定する。

Eは主仕様と全感度の位置別rolling閾値、TSE第1--191予定足、response、base/delay entry、10/20/30分exitが同一TSE segment内で適格な日に限定する。感度はA2/A3、A_delay（exit非延長）、極端q85/q95、拒否率0.20/1/3、block長3/10の独立因果再構成、同一A event holding10/30だけである。A/C/D/Mの全eventに0tick・費用前20分の-s grossをy、Q=X*R、指定されたnuisanceとyear/block-position FEのnuisance-only Moore--Penrose FWL（rcond/tolerance=1e-12）を用いる。deltaは固定1,111日軸、20 trade_date block-wild score bootstrap 10,000回、他は同軸20日非循環MBB 10,000回で評価する。情報量・経済gate、監査、OOS/Final Holdout lockは依頼本文の固定規則に従う。

`r063-q001-20260915-tse-liquidity-shock-rejection-01` は、価格統計・event・PnL・Parquet collectの前に、input manifestで相対Parquet pathへ`relative_to(ROOT)`を適用した際のPath基準不一致で停止した不変技術記録である。`…-02` は入力範囲・hash対象・仮説・価格規則・費用・seed・判定を変えず、manifest中のpath表記だけをabsolute resolved pathへ訂正して再登録する。

`…-02` はrolling Uとbase eventの監査台帳を書いた後、感度eventごとに全position Uを重複保持する実装により作業setが約8GBへ増加したため、event分類・注文・fill・PnL・bootstrap・判定の前にexecutorが停止した不変技術記録である。`…-03` は独立の`rolling_u_ledger.json`を正本とし、event台帳には選択blockのrolling閾値・positionとledger参照だけを保持する。この表現上の重複除去はU、E、event、価格、費用、seed、統計、判定を変更しない。

## R062-Q001: OSEナイト在庫のTSE開始15分拒否後30分継続（事前登録）

登録日: 2026-09-15。価格統計、event数、PnL取得前にR001--R061を照合した。R055は全OSE night returnをq分位で選びTSE始値から追随するが、開始15分の拒否/確認を条件にせず始値entryである。R031/R012は5分確認、異なるentry・保有である。全night return、次TSE開始15分の拒否、翌予定足entry、30分固定holdを同時に持つ既登録はない。本件は反復利用済みDevelopment探索であり独立再現ではない。

版管理予定表で直前TSE通常、後続OSE night、次TSE通常を一意対応する。nightの最初予定足openをoN、最終予定足closeをcN、rN=(cN-oN)/oN、x=|rN|、s=sign(rN)とする。各targetより前の直前120予定三つ組のみから有効xが100以上ならnearest-rank q50/q70/q75/q80（等号は上側）を得る。TSE最初openをoT、第15予定足closeをc14としてh=s(c14-oT)を計算し、h<=-1 tickを拒否、h>=+1 tickを確認とする。A=x>=q75かつ拒否、C=q50<=x<q75かつ拒否、D=x>=q75かつ確認、M=q50<=x<q75かつ確認で排他的とする。signal=c14 close、entry=第16予定足open、exit=entryから30予定分後open。Eは完全night、TSE最初66予定足、全rolling/10・15・20分反応/15・30・45分exit pathを要求する。

主費用は片道1 tick+30円、A2/A3、A_delay（exit非延長）。q70/q80、拒否2 tick、10/20分反応、15/45分holdのみ感度とし、反応感度は因果的に再構成する。A/C/D/Mの全方向eventでは-s方向0tick Grossをy、Q=1[x>=q75]×1[拒否]、指定nuisanceと暦年FEによるnuisance-only Moore--Penrose FWL（rcond=1e-12、残差Q SS tolerance=1e-12）を使う。deltaは固定1,111日軸20 trade_date block-wild score bootstrap 10,000回、他は同軸20日非循環MBB 10,000回、固定seed=20261006で評価する。Q未識別はBLOCKED。情報量・経済gate、禁止事項、OOS/Final Holdout lockは依頼本文の固定規則に従う。

## R062-Q002: OSEナイト在庫のTSE開始15分拒否後30分継続 — 固定U再検証（事前登録）

Q001からはE=0、night無効237、R004隔離14、rolling有効x不足860、損益・注文・fill・回帰が未生成という事実だけを引き継ぎ、Q001の価格・方向・event候補・将来returnは利用しない。唯一の売買前変更は、rolling参照集合Uを当日のEから独立に固定することにある。Uは予定表で一意なTSE→OSEナイト→次TSE三つ組で、ナイト最初予定足open `oN` と最終予定足close `cN` が適格・非隔離・正価格で `x=|(cN-oN)/oN|` を計算できるものだけである。次TSE足、rolling閾値、entry/exit、感度足、将来情報はU採否に使わない。

各対象三つ組は当日を含めず、trade_date順の直前120個までのUだけを参照し、100個以上ならnearest-rank q50/q70/q75/q80（等号上側）を得る。Eは完全night、TSE最初66予定足、10/15/20分反応、entry/遅延entry、15/30/45分exitと有効な過去U閾値を要求する。E=0、予定表三つ組不能、rolling因果性違反ならPnLを作らずBLOCKEDとする。合成ではU membershipの当日TSE／未来非依存、100個目の過去U到達、prefix不変性をgate化する。売買、費用、A/C/D/M、A=-s、signal=第15足close、翌予定足entry、30予定分後open exit、A_follow・固定buy/sell・1枚/最大1・Stop/TargetなしはQ001と同一である。

主費用は片道1tick+30円、2/3tick、1足遅延、q70/q80、拒否2tick、反応10/20分、hold15/45分だけを評価する。E>=700、A/C/D/M各>=70、A long/short各>=25、q80 A>=45、反応10/20分A各>=50以外の情報量・経済gateはQ001を維持する。不足はINCONCLUSIVE、充足後の一つでも未達はREJECT、全通過でもDevelopment探索上のINVESTIGATEに留める。WFA/OOS/Final Holdout、方向反転、rolling長・閾値・時刻・保有時間の救済探索を行わない。

## R061-Q001: TSE開始後61--90予定分の局所compression後breakout追随（事前登録）

正式実行IDは `r061-q001-20260915-tse-local-compression-breakout-03`。R001--R060を価格統計前に照合した。R008は同一session内の局所30分rangeを使うが、過去120予定TSE営業日のrolling range分位を持たない。R033は開始1--30分、直前20日order statistic、31--90分探索、60分保有である。61--90分の圧縮、直前120日nearest-rank、91--120分の最初の1 tick終値breakout、次open entry、30分holdを同時に持つ既登録仕様はない。これは反復利用されたDevelopment上の探索であり、独立再現ではない。

各日TSE第61--90予定足の最初openをa、block high/lowをH/L、`w=10000*(H-L)/a` bpsとする。a<=0又はH<=Lは方向eventから除外する。当日を含めない直前120予定TSE営業日だけの有効wを使い、100件以上でq30/q35/q40/q65 nearest-rankを得る。古い日への補充は行わず、等号は低range側でありA/Dを排他的にする。91--120足の最初の`close>=H+1tick`又は`close<=L-1tick`だけをsignalとし、次予定足open entry、entry+30予定分open exitとする。A=`w<=q35`でs追随、D=`w>=q65`でs追随、B=A∪D、A_fade=-s、A_buy/A_sellは同一A event/timeの固定sideである。1枚・最大1ポジション、Stop/Target/reentry/early exitなし、片道1tick+30円、A2/A3、A_delay（exit非延長）を固定する。

共通Eは必要な120日履歴、TSE第1--166予定足、20/30/40分compression、30分search、delay、15/30/45分exitを同一TSE segmentで満たす日だけとする。q30/q40、20/40分compressionの独立再構成、15/45分hold以外は計算・表示しない。Bの全eventで`s`調整0tick費用前30分Grossをy、`Q=1[w<=q35]`、w、search経過、signal overshoot ticks、compression return、最初60分range、TSE open gap、upper指標、year FEを固定し、R053-Q002と同じnuisance-only FWL（rcond/tolerance=1e-12）を用いる。20 trade_date非循環MBB 10,000回、seed=20261006、固定1,111日軸、共通index、末尾切詰め、linear percentileを使う。E>=800、B>=350、A/D>=90、A上下各25、q30 A>=70、20/40分A各>=60を情報量gateとし、後続の全Net/PF/CI/感度/年/月/top10 gate未達はREJECTである。OOS、Final Holdout、WFA、救済探索は行わない。

`…-01`はA_fadeのside配線auditで停止、`…-02`はその修正後にFWLがpost-execution statusを誤参照してB行0件となりBLOCKEDである。いずれも判断に用いない不変技術記録として保存した。`…-03`は元event status保持だけを追加し、仮説・価格・event・費用・seed・判定を変えずに再登録した。

## R060-Q001: 前回TSE通常レンジの開始30分failed auction逆張り（事前登録）

正式実行IDは `r060-q001-20260915-prior-tse-range-opening-failed-auction-01`。価格統計・event数・PnLの取得前にR001--R059を照合した。最も近いR036は直前TSE通常レンジに対する最初のstrict close突破を開始60分内で選び、b+5分類・60分保有を行う。本件は当日始値が前回レンジ内、開始30予定足のhigh/lowによる1tick一方向突破、30本目終値の±1tick状態、次足entry・30分保有を同時に固定するため同一登録ではない。R054等の結果を閾値・方向選択には使わない。本件も既知Development上の反復探索であり、独立再現とは扱わない。

入力はtrade_date 2021-01-01--2025-06-30の選択済み正規化Parquet、版管理TSE予定表およびR004固定隔離だけである。直前TSE通常session全予定足からH/Lと最終close pを得る。当日最初の予定足openがL<=o<=H、前回幅H-L>0で、前回session、当日最初40足、20/30/40分観測、各entry、entry基準15/30/45分exitに必要な足がすべて適格な日をEとする。開始N予定足（主N=30）のhigh>=H+1tickだけをupper、low<=L-1tickだけをlowerとし、両方・どちらもなしはE台帳に残して方向eventから除外する。upperだけはs=+1、lowerだけはs=-1、突破深度dは境界からの最大tick到達とする。N本目closeがupperではH-1tick以下、lowerではL+1tick以上ならA（range内回帰）、upperではH+1tick以上、lowerではL-1tick以下ならD（外側維持）、中立帯はA/Dから除外する。B=A∪Dである。

signalはN本目close後、entryは次予定足open、exitはentryから固定30予定分後のopenとする。Aは-s、D/Bは各eventの-s、A_continueは同一A eventのs、A_buy/A_sellは同一A event・時刻の固定買い/売りである。1枚・最大1ポジション、Stop/Target/reentry/早期exitなし。片道1tick+30円、A2/A3は片道2/3tick、A_delayはentryのみ1予定分遅延し元exitを延長しない。感度は突破幅2tick、range内回帰幅2tick、観測窓20/40、同一A eventの保有15/45だけとし、観測窓は最初から独立に因果的再構成する。

B全eventで-s方向の0tick費用前30分Grossをy、Q=1[A]、d、最初30分high-low bps、最初30分s方向adjusted return bps、(o-p)/pのs方向adjusted gap bps、前回TSE通常sessionのs方向adjusted return bps、前回range bps、upper指標、暦年FEを固定する。R053-Q002と同じnuisance-only Moore--Penrose FWL（rcond/tolerance=1e-12）でdeltaを推定し、全標本又は10,000回bootstrapのいずれかで残差化Qが非識別ならBLOCKEDとする。20 trade_date非循環moving-block bootstrapを10,000回、固定seed=20261005、共通1,111日index、末尾切詰め、linear percentileでA日次平均、A-D/B/A_continue/A_buy/A_sell、deltaのCIを保存する。E/event/execution全台帳、年/月/方向別、正月数、top5/top10除去後Netを保存する。

E>=800、B>=160、A/D各>=60、Aのupper/lower各>=20、2tick突破A>=35、20/40分観測窓A各>=40未達はINCONCLUSIVE。充足後はA Net>0、PF>1、A平均/A-D/A-B/deltaのCI下限>0、同一eventのcontinue/fixed buy/fixed sell超過、A2/A3/A_delayおよび全突破・回帰・観測窓・保有時間感度のNet>0かつPF>1、2021--2024の正年3以上、正月27/54以上、top10除去後Net>0を全て必要とする。未達一つでREJECT、全通過でもDevelopment探索上のINVESTIGATEに留める。WFA、OOS、Final Holdout、指定外の救済探索は実行しない。

## R054-Q001: TSE開始30分rangeの5分failed-breakout逆張り（事前登録）

正式実行IDは`r054-q001-20260915-tse-opening-range-failed-breakout-03`。`…-01`はDevelopment価格・event・PnL・統計を読む前に自身のRuff import-order gateで停止した不変技術記録である。`…-02`はevent・約定まで完走したが、R004 day隔離日を含む残存night barをoverall metricsに渡し1,120日軸となったため判断に使用しない。`…-03`はevent・価格・費用・seed・判定を変えず、metricsを固定1,111 trade_date軸へ訂正し、固定OLS台帳を追加する。R001--R053を価格統計前に照合した。R034は同じ30分H/Lと31--90分の最初のstrict close breakoutを用いるが、b+15確認・60分保有であり、本件のb+5確認・30分保有と、20/40分range、3/10分確認、15/45分保有の固定感度を同時には持たない。したがって同一登録はない。

trade_date 2021-01-01--2025-06-30の選択済み正規化Parquet、版管理TSE予定表、R004固定隔離のみを用いる。各mSから30予定足のH/Lを固定し、mS+30--mS+89の最初のclose>H/close<Lだけをeventとする。b+5のcloseが上方ならH以下、下方ならL以上（等号を含む）をfailed、strict外側維持をacceptedとし、確認後の次予定openでentry、entryから30予定分後のopenでexitする。Eにはopening、探索、確認、entry、15/30/45分exitまで同一TSEセグメントで連続適格な日だけを含める。A=failed fade、D=accepted fade、B=全event fade、A_continue/A_buy/A_sellは同一A eventの固定対照である。片道1tick+30円、A2/A3、1予定足delay（exit非延長）、20/40分range、3/10分確認、15/45分holdingだけを実行する。

全方向eventに0tick費用前のbreakout逆方向30分Grossをy、Q=failed、overshoot bps、range幅bps、予定breakout分、上方指標、暦年FEを用いる。R053-Q002のnuisance-only Moore--Penrose FWLでdeltaを推定し、full sample又は10,000回の20 trade_date非循環MBBで残差化Q二乗和<=1e-12ならBLOCKEDとする。E>=850、breakout>=500、A/D各>=150、A buy/sell各>=50、A20/A40各>=100を情報量gateとし、充足後の固定経済条件未達はREJECT、全通過のみDevelopment一次INVESTIGATEである。WFA、OOS、Final Holdout、救済探索を実行しない。

## R051-Q001: TSE前場圧縮後・後場15分確認breakout追随（事前登録）

正式実行IDは `r051-q001-20260915-tse-morning-compression-breakout-02`。`…-01` は売買・入力・費用・seed・統計を変えずに完走したが、A/D/BのうちD/Bの述語一致を実行gateへ明示していなかったため、判断には用いない不変記録とする。`…-02` はそのgateだけを追加し、価格統計・event・PnL前に再登録した完了記録である。R001--R050を価格統計前に照合した。R033はday開始30分の圧縮とその後の最初のbreakout、R029/R038/R045は昼休み変位・確認・極端値であり、全TSE前場range、120予定日q30/q40/q50/q60、aS+15確定close、同一60分保有の組合せは持たない。

版管理予定表 `R051-TSE-MORNING-AFTERNOON-1` の `mS=09:00`、`mE=11:30`（exclusive）、`aS=12:30` を使う。前場150予定1分足の`(high-low)/open(mS)*10,000`をrange_bpsとし、対象日を含めない直前120予定TSE営業日の同指標から有効100件以上のときだけnearest-rank q30/q40/q50/q60を得る。古い日への補充、時計時刻の推測、raw/出来高/現物・外部価格/OOS/Final Holdoutの読取りは禁止する。前場、aSから15本、entry、aS+45/+75/+105の各予定openが連続適格かつR004隔離外の日をEとし、aS+14のcloseが前場highをstrictに上回ればlong、lowをstrictに下回ればshortとする。entry=aS+15 open、exit=aS+75 openである。

A=q40以下、D=q60以上、B=全breakoutの追随、A_fade=反対方向、A_buy/A_sell=固定方向を同一A eventで評価する。A2/A3は片道2/3tick、A_delayは1予定足遅延でexitを延長しない。A30/A50と30/90分hold以外の感度は計算・表示しない。片道30円、標準slippage片道1tick、1枚・最大1ポジション、Stop/Target/reentry/早期exitなしを固定する。全breakoutの0tick費用前60分方向調整Grossをyとして、`Q=1[range<=q40]`、`z=ln(range/q40)`、overshoot_bps、方向調整gap_bps、上方指標、暦年FEのOLSを固定し、full-rank不成立はBLOCKEDとする。20 trade_date非循環moving-block bootstrapを10,000回、seed=20260930、共通index・末尾切詰め・linear percentileでA、A-D/B/fade/buy/sell、deltaのCIを保存する。

E>=850、A>=120、D>=150、Aのbuy/sell各>=40、A30>=80の情報量不足はINCONCLUSIVE。充足後の固定経済・CI・費用/遅延/感度・年/月・top10除去のいずれか未達はREJECT、全通過でもINVESTIGATEに留める。WFA、OOS、Final Holdout、救済探索は行わない。

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
# R049-Q001: TSE通常時間の同時刻極端5分変化後15分逆張り（配線訂正の同一事前登録）

`r049-q001-20260915-same-clock-extreme-5m-fade-02` は、2026-09-15の台帳監査でBのq75条件が実行呼出しでは既定q90のまま配線されていたことが確認されたため、判断保留の不変記録とする。A⊆Bは残ったが、C⊆Bが破れた（C=189日に対しB=131日）。`…-03` はB配線を直したが、追加した台帳監査が選択後に上書きされたstatusを参照してBLOCKEDとなった不変の技術記録であり、結果は判断に用いない。売買仮説、anchor、120日窓、100件要件、分位点、5分観測、15分保有、費用、seed、入力、情報量・経済判定は一切変更しない。`…-04` はBへq75を渡す条件配線と、全event台帳の包含・先頭選択・述語・共通E監査を追加するだけのDevelopment再実行であるが、後続の集計軸監査で`daily_net_pnl_aligned`／bootstrapは固定1,111日なのに各条件のoverall metricsだけが1,120日バー軸を使ったことが判明したため、上書きせず判断保留とする。`…-05` はanchor、event、side、価格、費用、seed、判定基準を固定し、metrics集計だけを1,111日に訂正するDevelopment再集計である。OOS及びFinal Holdoutはアクセスしない。

# R049-Q001: TSE通常時間の同時刻極端5分変化後15分逆張り（事前登録）

登録日: 2026-09-15。正式IDは `r049-q001-20260915-same-clock-extreme-5m-fade-02`。`…-01`はDevelopment価格統計・event数・PnLの前に、R045凍結証拠の旧ファイル名を参照して停止した不変の技術記録である。`…-02`は売買・入力・費用・seed・判定を変えず、実在する凍結証拠ファイル名だけを訂正して再登録する。R001--R048を価格統計・分位点・event数・PnL取得前に照合した。R035は同時刻5分shockを扱うが、固定6 TSE相対anchor、各anchorの直前120予定TSE営業日からのnearest-rank q75/q85/q90/q95、全6 anchor共通E、q75--q90対照、q90 event固定の買い・売り・追随対照、指定OLSを持たない。よって同一登録はない。既知Development上の追加探索であり独立確認ではない。

版管理 `R049-TSE-NORMAL-1` 予定表のmS=前場開始、aS=後場開始から、anchorをmS+30/+60/+90、aS+15/+45/+75分とする。各anchor直前5本の先頭openをp0、最終closeをp5、r=p5-p0、x=abs(r)/p0とする。対象日を含めない直前120予定TSE営業日の同anchor有効xだけを参照し、100件以上でnearest-rank q75/q85/q90/q95を作る。r=0、欠損、隔離、期間外、120日以外の履歴、参照100未満は無効であり、古い日への補充をしない。全6当日観測、entryから20分exit、全rolling閾値が有効な日だけがEである。

Aは最初のx>=q90を-rの方向に15分、Bは最初のx>=q75を独立に逆張り、Cは最初のq75<=x<q90を独立に逆張り、D/E/FはAの同event・entry・exitで固定buy/fixed sell/sign(r)追随とする。A2/A3は同Aを片道2/3tick、A_delayはentryだけ1予定分遅らせexit非延長、A85/A95は独立に最初のx>=q85/q95、A_h10/A_h20はAと同event/side/entryで10/20分exitである。signalの5本目close後、anchor予定足open entry、固定exit足open、片道1tick+30円、1枚・日次最大1取引・最大1ポジション、Stop/Target/reentry/update/early exitなしを固定する。

全E anchorで、逆張り方向調整済み0tick・費用前15分Gross円をy、Q=1[x>=q90]、z=ln(x/q90)、U=1[r>0]として、`y=alpha+delta Q+gamma1 z+gamma2 z^2+eta U+anchor固定効果+calendar-year固定効果+epsilon`を固定する。full rank不成立はBLOCKED。20 trade_date非循環moving-block bootstrapを10,000回、seed=20260929、共通日index、末尾切詰め、linear percentileでA日次平均、A-B/C/D/E/F、deltaのCIを再計算するが、rolling閾値とeventを再推定しない。

入力・予定表・合成・実行・会計・OLSが失敗すればBLOCKED。E>=750、A>=250、B>=450、C>=250、A buy/sell各>=80、各6 anchorのA>=25、A95>=120が不足ならINCONCLUSIVE。充足後、A Net>0/PF>1、A日次平均、A-B/C/D/E/F、deltaのCI下限>0、A2/A3/A_delay期待値>0、A85/A95およびA_h10/A_h20のNet>0/PF>1、2021--24の正年3以上、正月27/54以上、top10除去後Net>0の全通過でのみDevelopment一次INVESTIGATE、それ以外はREJECT。raw、出来高、現物・外部価格、OOS、Final Holdout、指定外anchor/quantile/window/holding/direction/regression、WFA、救済探索を行わない。

## R052-Q001: TSE開始90分アンカーVWAP極端乖離後30分逆張り（事前登録）

登録日: 2026-09-15。正式IDは `r052-q001-20260915-tse-morning-vwap-extreme-fade-01`。価格・出来高統計より前にR001--R051を照合した。R049はmS+90を含むが、6 anchorの直前5分価格変化、15分保有であり、90予定足のtypical-price/出来高アンカーVWAP、同観測120日分位点、30分固定逆張りではない。よって同一の時点・VWAP乖離・閾値・保有時間を併せ持つ登録はない。既知Development上の追加探索であり独立確認ではない。

ただしvolumeを使う前提なので、価格または出来高値、統計、正規化Parquetを読む前に、225Laboの正確な`出来高`列が同一尺度の各1分確定約定数量で、非累積・非建玉・非売買代金・非件数であり、単位、時刻境界、bar確定後の利用可能性、missing/zero/負値/制度・訂正の扱いが明文化されていることを監査する。満たせなければ実装・注文・PnLへ進まずBLOCKEDとする。

PASSした場合のみ、版管理TSE/OSE予定表のTSE前場開始mSから`[mS,mS+90)`の予定90本を使う。`typical=(high+low+close)/3`、`VWAP=sum(typical*volume)/sum(volume)`、`p=close(mS+89)`、`d=(p-VWAP)/VWAP`、`x=abs(d)`とする。90本の価格又は出来高が不完全、出来高null/負値、総出来高非正、隔離、期間外なら無効とし、時計時刻を推測しない。各当日より前の直前120予定TSE営業日の同観測だけから100件以上でnearest-rank q75/q85/q90/q95を計算し、当日を含めず古い日へ補充しない。等号は上側に含める。Eは観測、全閾値、entry、15/30/45分exitが適格な共通日である。

A=`x>=q90`で`-sign(d)`、B=`x>=q75`で独立逆張り、C=`q75<=x<q90`で独立逆張り、A_continueはA eventの`sign(d)`、A_buy/A_sellは同eventの固定long/shortとする。`r=0`又は`d=0`は方向条件から除外する。窓確定後の最初の予定足openでentry、30予定分後のopenで固定exit、1枚・最大1ポジション・Stop/Target/re-entry/途中更新/早期exitなしである。片道1tick+30円を主費用とし、A2/A3は2/3tick、A_delayは1予定足遅延しexitは延長しない。A85/A95と同eventの15/45分holdだけを感度とする。

全方向有効Eで、逆張り方向調整済み0tick・費用前30分Grossをy、`Q=1[x>=q90]`、`z=ln(x/q90)`、mS--signal絶対return bps、signal直前5分の方向調整return bps、上方VWAP指標、暦年FEの固定OLSを推定し、Q係数deltaを保存する。full rankでなければBLOCKEDである。20 trade_date非循環moving-block bootstrapを10,000回、seed=20261001、共通固定1,111日index、末尾切詰め、linear percentileでA費用後日次平均、A-B/C/A_continue/A_buy/A_sell、deltaの95%CIを保存する。event/thresholdは再推定しない。全観測・event台帳、年/月/方向別、正月数、top5/top10除去後Netを保存する。

E>=850、A>=90、B>=220、C>=100、A buy/sell各>=30、A95>=40を満たさなければINCONCLUSIVE。充足後、A Net>0/PF>1、A平均/A-B/A-C/deltaのCI下限>0、同eventのcontinue/fixed buy/fixed sell超過、A2/A3/A_delay/A85/A95/15分/45分すべてNet>0/PF>1、2021--2024の3正年、正月>=27/54、top10除去後Net>0を全て満たさなければREJECT、全通過でもDevelopment一次INVESTIGATEに留める。WFA、OOS、Final Holdout、救済探索は行わない。

## R053-Q001: TSE後場始値の極端な昼休みギャップ後30分反転（事前登録）

正式完了試行IDは`r053-q001-20260915-tse-afternoon-open-gap-fade-06`。R001--R052を価格統計前に照合した。R038/R045は昼休み中の連続先物変化、R051は後場15分確認後のbreakoutであり、前場最終予定closeから後場最初予定openの離散ギャップ、直前120予定TSE営業日のq75/q85/q90/q95、次予定足entry・30分逆張りの組合せはない。

版管理`R053-TSE-MORNING-CLOSE-AFTERNOON-OPEN-1`からcM=前場最後の予定1分足close、oA=後場最初の予定1分足openを得て、`g=(oA-cM)/cM`、`x=abs(g)`とする。各対象日前の直前120予定TSE営業日のxだけを使い、有効100件以上のnearest-rank q75/q85/q90/q95を得る。当日を含めず、古い日への補充をしない。共通Eは前場始値/cM/oA、閾値、entry、15/30/45分exitが全て適格な日とし、欠損・隔離・g=0は軸/Eに残すが方向条件から除外する。g確定後の次予定足openでentryし、entryから30予定分後openでexitする。A=q90以上の`-sign(g)`、B=q75以上、C=q75以上q90未満の独立逆張り、A_continue=`sign(g)`、A_buy/A_sellは同一A eventである。費用、A2/A3/A_delay、A85/A95、15/45分保持、OLS、20日非循環MBB 10,000回seed=20261001、情報量・経済判定と禁止事項は成果物`preregistration.json`に固定した。

## R053-Q002: R053-Q001 の FWL rank-deficiency 処理固定再実行（事前登録）

登録日: 2026-09-15。正式IDは`r053-q002-20260915-tse-afternoon-open-gap-fade-01`。R053-Q001の入力、E、gap定義、120日rolling quantile、A/B/C・対照、side、entry/exit、費用、感度、1,111日軸、20 trade_date非循環MBB 10,000回、seed=20261001、共通日index、情報量・判定基準を変更しない。Q001の最初の失敗はreplicate 711で、2021年（基準年）が抽出されず、interceptと残った2022--2025年dummyが従属になった。rank/特異値・抽出index・年/Q/gap方向別件数は`…-07-bootstrap-rank-audit/bootstrap_rank_audit.json`に不変保存した。

deltaだけを固定変更する。各標本で固定済みnuisance `Z`（intercept、z、morning reverse-adjusted return、morning range、gap-up、calendar-year FE）へのMoore--Penrose射影のみを使うFWL、`delta=(M_Z Q)'(M_Z y)/(M_Z Q)'(M_Z Q)`とし、`pinv_rcond=1e-12`、残差化Q二乗和tolerance=`1e-12`を事前固定する。Qを含む全係数の最小ノルム解は使わず、欠落year dummy・nuisance内従属を削除しない。観測標本又は各replicateで残差二乗和がtolerance以下なら、破棄・引き直しなしでBLOCKEDとする。観測標本のQ残差二乗和=63.49182593682709（`…-08-q-identification-audit/`）で識別可能だったためにのみ、この実行へ進む。full-rank OLSとの一致、欠落dummyの継続、Q非識別停止の合成検証を含める。OOS、WFA、Final Holdout、救済探索を禁止する。

## R055-Q001: 極端なOSEナイト全体リターンのTSE開始後30分追随（事前登録）

正式完了試行IDは`r055-q001-20260915-ose-night-extreme-tse-open-follow-04`。R001--R054を価格統計前に照合した。R012は全ナイト方向を09:00--09:04確認後09:05から60分追随するだけであり、120予定trade_dateのq75/q85/q90/q95絶対リターン、TSE最初の予定open、30分固定保持、指定FWL増分を持たない。したがって同一登録ではない。`…-01`は実行環境時間枠でFWL gate中に中断、`…-02`/`…-03`は週末・祝日をまたぐnight終了予定日の検証でPnL前に停止した不変技術記録である。`…-04`は売買仮説、入力、費用、閾値、予定窓、統計、判定を変更せず、観測標本のQ非識別を明示的にBLOCKEDとして保存する。

各target trade_dateにExchangeCalendarが一意に割当てたOSE nightだけを使い、最初の予定通常足openを`oN`、最後の予定通常足closeを`cN`、`rN=(cN-oN)/oN`、`x=|rN|`とする。night開始calendar_dateから翌日の通常終了を導き、weekend/holidayを跨いでもcalendar_dateからtrade_dateを推測しない。全予定通常night足、q75/q85/q90/q95、TSE最初の予定open entry、entry+15/30/45予定分open exitが適格な日だけをEに含める。当日を除く直前120予定trade_dateの同じ有効xから100件以上の場合だけnearest-rankを作り、等号は上側へ含める。rN=0はEに残すが方向条件から除外する。

Aはq90以上の`sign(rN)`追随、Bはq75以上の独立追随、Cはq75以上q90未満の独立追随、A_fadeは同一eventの`-sign(rN)`、A_buy/A_sellは同一eventの固定方向である。A2/A3は片道2/3 tick、A_delayはentryだけ1予定分遅らせexitを延長しない。主費用は片道1 tick+30円。signalはナイト最終通常足確定、entryはTSE最初の予定open、exitは固定openであり、夜間終了からTSEまでのgapをPnLに含めない。クロスセッション注文は共有engineの`allow_cross_session_pending_order=true`と20,160分の上限だけを使い、実行入力を最後の通常night足と厳密なTSE経路に限定するので欠損entryを代用しない。

全方向有効Eについて、0tick・費用前30分Grossを`y`、`Q=1[x>=q90]`、`z=ln(x/q90)`、cNからentryまでの方向調整gap_bps、直前通常TSE時間の方向調整return_bps、night_range_bps、night上昇指標、calendar-year FEを固定する。deltaはR053-Q002と同じnuisance-only FWL、`pinv_rcond=1e-12`、残差Q二乗和tolerance=`1e-12`で求め、観測全体又は10,000回・20 trade_date非循環MBBのいずれかで非識別なら、破棄・引直し・列削除をせずBLOCKEDとする。E>=850、A>=80、B>=200、C>=100、A買い/売り各25、A95>=35の情報量、ならびに指定のNet/PF/CI/対照/感度/年・月・集中度の全判定、OOS・Final Holdout・WFA・救済探索の禁止を成果物`preregistration.json`に固定した。

## R057-Q001: 前回TSE通常終値から当日TSE始値への大幅gapの15分acceptance追随（事前登録）

登録日: 2026-09-15。R001--R056を価格統計前に照合した。R056は当日09:00からの30分変位・経路効率、R006/R015/R032/R055はOSEナイト境界、R053は昼休みの離散gapである。前回の版管理TSE通常session最終close、当日TSE始値、15分確認、次予定足entry、30分固定保持の全組合せは既登録ではない。

版管理`R057-TSE-PREVIOUS-CLOSE-OPENING-GAP-1`で、直前TSE通常sessionを予定表から一意に対応させ、その最終予定1分足closeを`p`、当日最初の予定足openを`o`、最初の15予定足の最終closeを`c14`とする。`g=(o-p)/p`、`x=|g|`、`s=sign(g)`、`r15=(c14-o)/o`である。calendar_dateから前回sessionを推測せず、`g=0`又は`r15=0`を方向条件から除外する。当日を含めない直前120予定TSE営業日の有効`x`のみで、100件以上ならnearest-rank q70/q75/q80を得る。古い日で補充せず、等号は上側に含める。

Eは前回最終足、当日最初の20予定足、rolling閾値、各仕様のentry、15/30/45分exitが同じTSE segmentで適格な日だけとする。A=`x>=q75`かつ`sign(r15)=s`で`s`方向、D=`x>=q75`かつ`sign(r15)=-s`で同じ`s`方向、B=A∪D、A_fadeはAと同event/timeの`-s`、A_buy/A_sellは同event/timeの固定方向である。15本目確定後、次予定足openでentry、entryから30予定分後openで固定exitし、gap/確認中の動きはPnLへ含めない。1枚・最大1ポジション、Stop/Target、re-entry、早期exitは禁止する。

主費用は片道1tick+30円、A2/A3は2/3tick、A_delayはentryだけ1予定分遅らせexitを延長しない。感度はq70/q80、因果的に独立再構成する確認10/20分、同A eventのholding15/45分だけである。Bの全eventを、s方向調整済み0tick費用前30分Gross`y`、`Q=1[sign(r15)=s]`、`ln(x/q75)`、`|r15|` bps、最初の15分range bps、前回TSE通常sessionのs方向調整return bps、gap-up、暦年FEで固定回帰する。R053-Q002と同じnuisance-only FWL（rcond/tolerance=1e-12）を使い、観測又はいずれかのbootstrap標本でQ非識別なら破棄・引直し・回帰変更なしでBLOCKEDとする。

20 trade_date非循環moving-block bootstrapを10,000回（共通1,111日index、seed=20261004、末尾切詰め、linear percentile）で、A費用後日次平均、A-D、A-B、A-A_fade、A-A_buy、A-A_sell、deltaの95% CIを保存する。年/月/方向別、正月、上位5/10利益除去後Net、全候補・閾値・確認状態・event台帳を保存する。E>=850、B>=220、A/D>=80、A buy/sell>=25、q80及び20分確認A>=35を満たさなければINCONCLUSIVE。充足後は指定のA Net/PF、全CI・対照、A2/A3/delay、全閾値・確認窓・holding感度、2021--2024の3正年、正月27/54、top10除去後Netを全て満たせなければREJECT、全通過でもDevelopment一次INVESTIGATEとする。WFA、OOS、Final Holdout、結果依存の反転又は救済探索は実施しない。

## R059-Q001: TSE終値から対応OSEナイト始値への極端な再開gap後30分反転（事前登録）

正式成果物は `r059-q001-20260915-session-reopen-gap-reversal-03`。R001--R058を価格統計前に照合した。R053はTSE昼休み内の離散gap、R039/R058はTSE引け30分の連続値動きを選別する。TSE最終予定close、対応OSE night最初の予定open、120対応日rolling極端度、第2予定足entry、30分保有の組合せは既登録にない。`…-01`はRuff gateで価格アクセス前に停止、`…-02`は未登録の`ln(x/q90)`を回帰nuisanceに加えたため不変保存し判断から除外する。`…-03`はその列だけを除去して、価格統計・event・PnL前に再登録した。

版管理ExchangeCalendarの`target_night.previous_trade_date`だけでTSE通常sessionと後続OSE nightを一意対応させる。p=TSE最後の適格予定足close、oN=OSE最初の予定足open、`g=(oN-p)/p`、`x=|g|`、`s=sign(g)`である。各target前の直前120対応日だけの有効xから、100件以上でnearest-rank q75/q85/q90/q95を算出する。当日を含めず、古い日への補充なし、等号は上側である。Eは両端点、OSE最初の2予定足、rolling閾値、entryと15/30/45分exitが同一night segmentで適格な日だけとし、g=0はEに残し方向条件から除外する。signalはoN観測後、entry=oN+1予定足open、exit=entry+15/30/45予定分openであり、gapと第1 OSE足をPnLに含めない。

A=`x>=q90`の`-s`、B=`x>=q75`の`-s`、C=`q75<=x<q90`の`-s`、A_continue=`s`、A_buy/A_sellは同一A event/timeである。A2/A3、A_delay（exit非延長）、q85/q95、15/45分だけを感度とし、1枚・最大1ポジション、Stop/Target/reentry/early exitなし、片道1 tick+30円を固定する。Bの全方向有効eventについて0 tick・費用前30分grossをy、Q、絶対gap bps、TSE最終30分のs調整return/range、TSE通常session全体のs調整return、OSE第1足のs調整return、gap-up、暦年FEをR053-Q002と同じnuisance-only FWL（rcond/tolerance=1e-12）で推定する。観測又はいずれかの20 trade_date非循環MBB 10,000回でQが非識別ならBLOCKEDである。E>=800、B>=190、A>=70、C>=100、A buy/sell各25、A95>=30を満たさなければINCONCLUSIVE、充足後の全経済・CI・対照・感度・年/月・集中度条件のいずれか未達はREJECT、全通過でもDevelopment探索上のINVESTIGATEとする。OOS、Final Holdout、WFA、救済探索を行わない。
