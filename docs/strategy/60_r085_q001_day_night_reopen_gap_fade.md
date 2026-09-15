# TASK-R085-Q001: Day終了からNight再開への短期ギャップfade

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Developmentのみ（NIGHT sessionの公式`trade_date` 2021-01-01～2025-06-30）

## 仮説と範囲

日中セッション終了から対応するnightセッション開始までの短い休止中に生じた極端な価格ギャップは、night開始直後の薄い流動性と在庫不均衡を含み、night開始後60分以内に反転する。本研究はR083の前日day closeから翌cash openまでの長時間gap fadeの救済ではない。同一営業サイクル内の短い休止とnight再開時流動性を検査する別機序である。

既知Developmentの反復利用であるため、いかなる結果でも判定上限はINVESTIGATEである。Walk Forward、OOS、2026-01-01以降のFinal Holdoutは読取りも実行もしない。

## 固定した因果的定義

- 予定軸はDevelopment内の各NIGHT sessionの公式`trade_date`。各NIGHT日について、台帳の`previous_trade_date`を対応DAYの`trade_date`とし、逆向きの`next_trade_date`と`night_calendar_start_date == D日`がともに一致する一対一対応だけを予定上のday partnerとする。DAY partnerがDevelopment外のNIGHT日は、価格を読まず`DAY_PARTNER_OUTSIDE_DEVELOPMENT`として軸に残す。日中の制度版はD日の版、nightの正式openと版はNIGHTの`night_calendar_start_date`で選ぶ。
- **D** はD日の正式DAY session close時刻の最終適格bar close、**N** は対応NIGHTの正式session open時刻の最初の適格bar open、**J=N−D** とする。いずれも正価格・同一の予定対応・session・trade_dateでなければJ無効であり、観測最終行、時計時刻固定、補完、古いpartnerへの置換を使わない。
- `|J|` が有効かつJ≠0の過去観測だけを、Development開始以後からcurrent-excludedで時刻順に保持する。直前60適格sessionの`|J|`にnearest-rank `ceil(n×p/100)-1`（補間なし）を適用しq20/q80を得る。60未満又はq20>=q80は状態なし。**E** は`|J|>=q80`、**M** は`q20<|J|<q80`。J=0、無効Jは状態を作らず、currentを参照集合へ入れない。
- J≠0のE/M日にNを観測した後、次の適格NIGHT bar openで、Jと逆方向に1枚入る。exitは正式night openから60分後の最初の適格bar openである。entry前取消は0円、既約定後exit不明はnullでありentryを遡及取消しない。1 session最大1取引・最大1ポジション、Stop・Target・re-entryなし。
- day方向、休止中の外部市場、night open後の方向、出来高、`|J|`以外の特徴量を使わない。

## PnL前S2ゲート

PnL、return、勝敗、PF、順位、bootstrapの前に、取引時間版、D/N一対一対応、calendar_date/trade_date境界、休場・短縮取引、rolling分位の過去情報限定、翌適格bar約定を監査する。次をすべて要求する。

- 説明不能除外0件
- E実行取引180件以上
- EのJ正/負各60件以上
- E取引が2021～2024各年25件以上、2025H1 12件以上

不足又は監査不合格なら**INCONCLUSIVE**としてPnL取得前に停止する。

## 会計、対照、推論

- 基本費用は片道1 tick＋片道30円、1枚。既存engineの翌適格bar約定を使い、slippageを二重控除しない。
- 主対照は(1) 共通予定trade_date軸の無取引0円、(2) 同一E日・同entry/exitのJ方向continuation、(3) M日の同一fade規則によるmagnitude-control。
- E/Mは全予定NIGHT `trade_date`の共通系列に置き、非該当日を0円とする。20 trade_date、non-wrapping、tail truncationのMBBを10,000回、seed=20260915、linear percentile 95% CI、共通indexで行う。推定量はE-fade平均日次Net、E-fade−E-continuation対応日次Net、E−M群別平均日次Net差である。
- 主ANDはE-fade Net>0、PF>1、主MBB下限>0、E-fade−E-continuation差CI下限>0、E−M平均日次Net差CI下限>0。

## 感度、保存、判定

- 感度はcutoff q70/q90、参照40/80 session、entryをnight open+5分以後の最初の適格bar openへ遅延、exit open+30分/open+90分、片道2/3 tick、手数料2倍のみ。
- E-fadeについて年別、J正負別、night取引時間制度版別、E内`|J|`の事前固定五分位別、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。部分群は救済選択に使わない。
- 主AND不成立は**REJECT**。成立しても、全固定感度Net>0、J正負ともNet>0、主要night制度版で符号整合、2021～2024の少なくとも3年と2025H1が正、top-10除外後Net>0を満たさなければ**INVESTIGATE**。全条件を満たしてもDevelopment反復利用のため**INVESTIGATE**。

結果依存の閾値・保有時間・制度版選択、Walk Forward、OOS、Final Holdoutへは進まない。
