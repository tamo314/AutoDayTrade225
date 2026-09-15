# TASK-R102-Q001 事前登録: 現物開始opening rangeの一方向突破後再侵入

状態: **FROZEN_BEFORE_EVALUATION_PNL**  
登録日: 2026-09-16 JST  
family: `opening_range_false_break_reentry` / study: `R102-Q001` / version: `v1`

R100/R101閲覧後のDevelopment反復利用による一回限りの事後仮説である。方向効率・二段階方向確認familyの救済ではなく、09:00開始rangeの境界突破とrange内再侵入という経路トポロジーを対象とする。結論の上限は結果によらず **INVESTIGATE**。Walk Forward、2025H2 OOS、Final Holdoutを読取・実行しない。

PnL、return、PF、bootstrap、感度にアクセスする前にR001--R101を意味照合する。R054は09:00--09:29 range後の31--90分最初のstrict-close突破、b+5確認、30分保有である。R076は09:00--09:29 range、09:30--10:30内のstrict-close突破とその後のrange内close復帰であり、entryは復帰確認後、exitは14:30である。R096は09:00--09:59の方向側range端受容から後場を扱う。したがって、09:00--09:14 range、09:15--09:29のhigh/low一方向突破、09:29 close再侵入、09:30--10:30逆方向という同じopening-range false-break仮説の有効済み検定はない。既知Development結果は独立確認を意味しない。

各予定TSE営業日で、完全な09:00--09:14の適格通常DAY一分足から `H0=max(high)`、`L0=min(low)`、`W=H0-L0`を確定する。`W<=0`、窓不完全、R004 DAY隔離は理由付きunavailableである。完全な09:15--09:29足から `eu=max(high-H0)`、`ed=max(L0-low)`を得る。凍結tickで`eu>=1 tick and ed<1 tick`を`b=+1`、`ed>=1 tick and eu<1 tick`を`b=-1`とする。両側突破・無突破は非event。09:29 closeが`[L0,H0]`内はQ、b方向の境界外はT、それ以外はZであり、Q/T/Zとsignalは09:29 closeで固定する。後続価格、fill、exit可用性による分類/route変更を禁止する。

SはQだけ`-b`、BはQだけ`b`、Uは全一方向突破eventで`-b`、T対照はTだけ`-b`、Nは常時無取引である。entryはsignal後の最初の適格通常足（09:30以降）open、exitは10:29 signal後の最初の適格通常足（10:30以降）open。Stop/Targetなし、1枚・最大1建玉、片道1 tick+30円を基本費用とする。

各日に、current-excluded直前120予定TSE営業日の一方向突破eventだけを参照し、有効50以上なら`x=max(eu,ed)/W`のnearest-rank q50を作る。x low/highは層別評価だけに用い、当日routeを変えない。PnL前ANDはq-ready>=700、unidirectional event>=250、Q/T各>=90、S完了>=90、Qのb正負各>=30、2021初期化部分>=8、2022--2024各>=18、2025H1>=8、旧/新制度>=75/8、Q/T×x帯×b正負8セル各>=6、future参照なし、当日事後選別なし、日次軸一致、R004混入なし、説明不能除外0、未完了建玉0である。未達はPnLを取得せずINCONCLUSIVE。

通過時、共通予定日軸に20 trade_date非循環・末尾切詰めMBBを10,000回、seed=20260916、linear percentileでS日次Net、paired S-B、SのQ当たりNet-Uの全一方向突破event当たりNetを評価する。各標本でx帯×b正負を等重みとして `Delta=(1/4)sum(mean(Net(-b)|Q,stratum)-mean(Net(-b)|T,stratum))` を再計算する。主ANDはS Net>0、PF>1、S日次Net/S-B/S-Q当たり-U-event当たり/Deltaの各CI下限>0。ひとつでも未達はREJECT。

主判定と独立に、突破閾値2 tick、`max(1 tick,0.10W)`、opening range 09:00--09:09/09:00--09:19、確認窓10/20分、entry+1、exit10:00/11:00、同一選択経路の片道2/3tick、手数料2倍を保存する。主AND後にも主要感度S Net/Delta正、2022--2024の二年以上と2025H1、b正負、旧新制度双方、top-10勝ち除外後正を要し、未達はINVESTIGATE。全達成でも上限INVESTIGATE。結果依存の窓・閾値・片側除外・Stop/Target・entry/exit・方向反転・追加状態は禁止する。
