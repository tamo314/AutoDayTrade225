# TASK-R082-Q002: US cash-open N225 night-session continuation

family_id: `us_cash_open_n225_night_price_discovery` / study_id: `R082-Q002` / spec_version: `v2`  
protocol_revision: `R082-Q002-count-gate-only-20260915`  
状態: **PnL取得前に凍結**

## Q001 の確認と本改訂の範囲

Q001 の正式完走成果物 `r082-q001-20260915-us-open-continuation-02` の
`COMPLETED.json` と `decision.json` は `INCONCLUSIVE`、理由
`R082_PNL_FREE_FEASIBILITY_GATE_FAILED` を記録している。PnL、PF、bootstrap、
orders/fills/trades、profile 成果物はいずれも存在しない。したがって Q001 の
PnL は未取得である。この確認は Q002 の価格・PnL読込み前に行う。

Q002 は Q001 のPnL-free監査済み `primary_events.json` を唯一の選択入力とし、
そこで `status=EXECUTABLE` の固定806 event ID（trade_date）を一度だけ評価する。
固定ファイル全体のSHA-256は
`c67345cc1f3ae390fd216464af6128ad7030c83414719e1c4cf18b6e2bc111f0`、順序付き806
ID（LF区切り・末尾改行なし）のSHA-256は
`746efde7d3a66107d4b5193bb5b6c17dc4aa70d752d29caa652c660c06347616` である。

唯一の変更は、情報量ゲートの実行可能件数を850から**800以上**に改訂することだけである。
U正負各300、2021--2024各140、2025H1 60、説明不能除外0は不変である。
この文書作成、固定入力ハッシュ・カレンダー・対応監査、および事前実装検証の後にのみ
追加PnLを取得する。再抽出、event ID追加、欠損日の救済、時刻・保有期間・強度による選別はしない。

## 固定定義・入力監査

NYSE regular day は凍結済み
`config/calendars/nyse_regular_trading_days_2021_2025h1_v1.yaml`（SHA-256
`ca4f74d7866af3f970be7e610979460e3935546f76adcf8503adc3a95d54b520`）に限る。
`America/New_York` の09:30をzoneinfoでJSTへ変換したものをSとする（DSTを固定JST時刻で近似しない）。
固定event内のNYSE日、S_ET、S_JST、N225 night open/close、`trade_date`対応、およびSがnight
session内であることはQ002で全806件について再検証する。

主仕様はQ001と完全に同じである。U=`close(S+29)-open(S)`、U≠0の固定eventをU方向に、
S+29 closeを観測後、S+30 openで1枚entryし、S+179 closeを観測後S+180 openでexitする。
Stop/Target、再entry、早期exitはない。P=`close(S-1)-open(S-30)`で、U≠0かつP≠0の同日だけを
対応集合として、同一entry/exitのP符号方向controlを計算する。fadeは主と同event、同entry/exit、反対sideである。
Q001の固定除外規則、R004隔離、予定軸、1日最大1ポジション、翌適格バー約定、Net会計を変更しない。

不一致、説明不能除外、実行可能800未満、U正負各300未満、2021--2024各年140未満、2025H1 60未満では
**INCONCLUSIVE**としてPnL計算を開始しない。

## 評価・判定

ゲート通過時のみ、片道1 tick＋片道30円、20 trade_date非循環MBB、10,000回、seed 20260915、
tail truncation、linear percentileで評価する。主ANDは continuation の Net>0、PF>1、日次平均Net
95%CI下限>0、continuation−fade 対応日次差CI下限>0、continuation−pre-open-sign control（U/P共通日）
対応日次差CI下限>0である。

固定感度はQ001どおり、signal 15/45分、entry追加1本遅延、exit S+120/S+240分、片道2/3 tick、手数料2倍のみである。
これらのsignal/entry/exit感度も固定806 IDの部分集合だけで実行し、新しいIDを加えない。保存項目は年別、U正負別、
DST/標準時間別、Pとの符号関係別、|U|五分位別、最大DD、利益集中度、top-10勝ち取引除外後Netである。

主AND不成立は `REJECT`。成立しても、全感度Net>0、U正負ともNet>0、DST/標準時間ともNet>0、
2021--2024の少なくとも3年および2025H1が正、top-10除外後Net>0の全てを満たさなければ `INVESTIGATE`。
全てを満たしてもDevelopment反復利用のため判定上限は `INVESTIGATE`。結果依存の救済、OOS、Walk Forward、
2026年以降Final Holdoutには進まない。
