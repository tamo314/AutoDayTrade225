# R001: セッション初動の継続と反転（事前登録）

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
