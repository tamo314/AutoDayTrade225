# TASK-R084-Q001: 午前圧縮レンジの昼休み後突破継続

登録日: **2026-09-15 JST**  
状態: **FROZEN_BEFORE_ADDITIONAL_PNL**  
対象: Developmentのみ（trade_date 2021-01-01～2025-06-30）

## 仮説と範囲

午前の値幅が異常に圧縮された日は注文・情報不均衡が未解消であり、現物昼休み後に午前レンジを終値で突破すると14:30まで同方向の価格発見が続く。これは既知時刻までの変位方向を取引したR078～R083の救済ではない。事前のvolatility compressionと、その後に確認した午前境界突破を組み合わせる別機序の検査である。

既知Developmentを反復利用しているため、いかなる結果でも判定上限はINVESTIGATEである。OOS、Walk Forward、2026-01-01以降のFinal Holdoutは読取も実行もしない。

## 固定した因果的定義

- 版管理した取引時間とtrade_date台帳の各trade_dateについて、09:00～11:29の全150本の適格DAY barから高値 **H**、安値 **L**、09:00 bar open **A** を取る。いずれかのbar、A、又は正価格が不適格・欠損ならWは無効であり、先行bar補完はしない。既存R004 whole-session隔離DAYは無効である。
- `W = 10,000 × (H − L) / A`。直前60有効trade_dateのWをcurrent-excluded、oldest-to-newest dequeで保持し、nearest-rank `ceil(n×p/100)-1`（補間なし）でq30/q70を計算する。60未満、又はq30>=q70は状態なし。圧縮 **C** は`W<=q30`、通常値幅 **M** は`q30<W<q70`であり、それ以外は状態なし。無効Wは状態を作らず、過去有効観測を古い値で補充しない。
- C又はM日に、12:30～13:29の各barを時刻順に観測する。最初の`close>H`はlong、最初の`close<L`はshortのbreakout eventである。同一barが双方を満たすことはH>=Lのため起こらない。信号bar closeの後の最初の適格bar openでentryし、14:30 openでexitする。breakoutなし又はentry前取消は無取引・日次PnL 0、既約定後の固定exit不明はnullでありentryを遡及取消しない。
- 1日最大1取引・最大1ポジション、Stop・Target・再entryなし。overnight gap、night情報、午前方向、レンジ内経路、出来高、13:30以後の追加シグナル情報を使わない。13:29 breakoutのentryは13:30以降の最初の適格openであっても、突破close以外の情報を使わない。

## PnL前S2ゲート

PnL、return、勝敗、PF、順位、bootstrapを計算する前に、trade_date台帳と入力SHA-256、H/L/A、current-excluded rolling分位、breakout時刻、翌適格bar約定、欠損・R004隔離を監査する。次をすべて要求する。

- 説明不能除外0件
- C状態の適格日280件以上
- C breakout実行取引80件以上
- C long/short各25件以上
- C breakout取引が2021～2024各年10件以上、2025H1 5件以上

不足又は監査不合格なら **INCONCLUSIVE** としてPnL取得前に停止する。

## 固定した会計、対照、推論

- 基本費用は片道1 tick＋片道30円。1枚、既存engineの翌適格bar約定を使用し、slippageを二重控除しない。
- 主対照は、(1) 共通予定trade_date軸の無取引0円、(2) 同一C breakout event・同一entry/exitの逆方向fade、(3) M日に完全に同じbreakout規則を適用するmagnitude-controlである。
- C対Mは、全適格日を共通trade_date系列に配置し、各群の無取引日を0円として比較する。MBBは20 trade_dateのnon-wrapping block、tail truncation、10,000反復、seed 20260915、linear percentile 95% CI、共通block indexとする。推定量はC-breakout予定日次平均Net、C-breakout−C-fade対応日次平均Net、C−M群別予定日次平均Net差である。
- 主ANDは、C-breakout Net>0、PF>1、C-breakout MBB 95%CI下限>0、C-breakout−C-fade対応日次差CI下限>0、C−M平均日次Net差CI下限>0。

## 固定感度・記述保存・判定

- 感度は圧縮cutoff q20/q40（q70は不変）、参照40/80日、entry追加1本遅延、exit 14:15/14:45、片道2/3 tick、手数料2倍だけに限定する。
- C-breakoutについて年別、long/short別、breakout時刻帯（12:30–12:49、12:50–13:09、13:10–13:29）、C-breakout内W順位五分位別、取引発生率、最大DD、利益集中度、top-10勝ち取引除外後Netを保存する。部分群は救済選択に使わない。
- 主AND不成立は **REJECT**。成立しても、全固定感度Net>0、long/shortともNet>0、2021～2024の少なくとも3年と2025H1が正、top-10除外後Net>0を満たさなければ **INVESTIGATE**。全条件を満たしてもDevelopment反復利用のため **INVESTIGATE**。

結果依存のレンジ閾値・確認時刻・exit探索、Walk Forward、OOS、Final Holdoutには進まない。
