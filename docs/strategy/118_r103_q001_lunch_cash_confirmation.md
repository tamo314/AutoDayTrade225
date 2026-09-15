# TASK-R103-Q001 事前登録: 現物昼休み変位と後場開始確認の二段階継続

状態: **FROZEN_BEFORE_EVALUATION_PNL**  
登録日: 2026-09-16 JST  
family: `tse_lunch_cash_reopen_two_stage_confirmation` / study: `R103-Q001` / version: `v1`

現物市場の昼休み中に先物だけで生じた通常以上の方向変位が、12:30の現物後場開始後にも通常以上の同方向変位で確認された場合、参加者間で方向が受容されたため12:45以降も持続する、をDevelopmentで一度だけ検証する。R102の救済ではなく、R101の二段階確認を現物昼休みという制度的に異なる参加者交代で再検証する時間帯レプリケーションである。Development反復利用のため、結論の上限は **INVESTIGATE** とする。Walk Forward、2025H2 OOS、Final Holdoutには進まない。

PnLアクセス前にR001--R102を意味照合した。R038/R081/R095は昼休み変位のみで12:30から追随し、R090は12:30--12:39の否定に対するfadeである。いずれも、昼休み強変位と12:30--12:44の強い同方向確認の双方をcurrent-excluded個別q50で選別し、12:45--14:30を同方向へ追随するK/D比較ではない。従って同一の仮説が既に有効検定済みではなく、重複停止には該当しない。

各予定TSE営業日で、完全な適格通常足から昼休み先物区間11:30--12:29の `p=Cp-Op`、現物後場確認区間12:30--12:44の `c=Cc-Oc` を得る。各窓が不完全、p又はcが0、R004該当なら理由付きunavailableとする。直前120予定TSE営業日のcurrent-excludedな有効日を補充なしで用い、有効100件以上で `|p|` と `|c|` のnearest-rank q50を別々に求める。`E=(|p|>=qp and |c|>=qc)`、`K=E and sign(p)=sign(c)`、`D=E and sign(p)!=sign(c)` とする。`w=min(|p|/qp, |c|/qc)` は固定境界1.5でLOW/HIGHに分ける。

`A` はKだけsign(c)方向、`U` は全Eでsign(c)方向、`R` はKだけ-sign(c)方向、`D_control` はDだけsign(c)方向、`N` は常時無取引とする。signalと分類は12:44足確定時に固定し、entryは12:45以降最初の適格通常足、exitは14:30以降最初の適格通常足とする。選択後の価格、fill、exit可用性での再分類・乗換えは禁止する。Stop/Targetなし、1枚・最大1ポジション、基本費用は片道1 tick+片道30円である。

PnL前ANDは、q-ready>=700、E>=220、K/D各>=80、A完了>=80、Aの方向正負各>=25、2021初期化部分>=8、2022--2024各>=15、2025H1>=8、旧/新制度>=65/8、K/D×w帯×sign(c)の8共通eventセル各>=5、未来参照なし、当日事後選別なし、日次軸不一致なし、R004混入なし、説明不能除外なし、未完了建玉0とする。未達ならPnLを取得せずINCONCLUSIVEで停止する。

通過時は、共通予定日軸の20 trade_date非循環moving-block bootstrapを10,000回、seed=20260916、全比較共通index、末尾切詰め、linear percentileでA日次Net、paired A-R、AのK当たりNet-UのE当たりNetを評価する。各標本でw二帯×sign(c)の4層を等重みとして `Delta=(1/4)Σ層{mean(Net sign(c)|K,層)-mean(Net sign(c)|D,層)}` を再計算する。主ANDはA Net>0、PF>1、A日次Net CI下限>0、A-R CI下限>0、A/K当たりNet-U/E当たりNet CI下限>0、Delta CI下限>0。ひとつでも未達ならREJECTする。

主判定にかかわらず、q40/q60、確認窓12:30--12:39/12:30--12:49、参照60日（有効50）/240日（有効200）、entry追加1分、exit14:00/14:55、選択経路固定の片道2/3 tick・手数料2倍を感度として保存する。主AND成立後にも、実行可能な全固定感度でA Net>0かつDelta>0、2022--2024の少なくとも2年と2025H1、方向正負、旧新制度双方、top-10勝ち取引除外後が正でなければINVESTIGATEとする。全条件を満たしても上限はINVESTIGATEである。結果依存の窓・閾値・片側削除・方向反転・Stop/Target・entry/exit・追加状態は禁止する。
