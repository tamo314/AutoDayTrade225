# 19. R3-A R032保存台帳照合

監査ID: **AUDIT-R3-A-20260915T045000Z**  
規約: **RG-20260915-01**  
状態: **COMPLETE — R032の限定台帳照合**

## 結論

R032-Q001の訂正版`r032-q001-20260914-prior-day-night-gap-confirmation-02`について、表示した0tick grossの確認−非確認差とbootstrap差を、保存されたB0 event ledger、固定target-night軸、seed、block bootstrapから再現した。

`516.4271047 − 553.6842105 = −37.2571058` は、filled tradeを分母とする`JPY_PER_TRADE`の差である。一方、`−10.4072398`（95% CI `[−385.9954751, 412.2285068]`）は、無取引nightを0円で残す固定1,105 target nightの`JPY_PER_TARGET_NIGHT`日次平均との差である。二つは単位、population、分母、重みが異なるため、互いの算術的一致を要件にしてはならない。

## 実測範囲

- `B_all_gap_follow_0tick/events.parquet`から`trade_date`、confirmation status、fill status、`gross_pnl_jpy`だけを読んだ。価格列は読んでいない。
- confirmedは487 trade・gross 251,500円、nonconfirmedは475 trade・gross 263,000円であり、保存済み診断と一致した。
- 固定axis 1,105 night、seed `20260913`、20 trade-date block、10,000回、non-wrap/tail-truncate、linear percentileを再現し、保存bootstrapのestimateとCIに一致した。
- `…-01`の内容、raw/Silver/Gold bars、特徴量、OOS、Final Holdoutは読んでいない。旧`REJECT`は変更しない。

## 境界

この監査は、R032の経済性、仮説、既存決定、またはR3全体をPASSにするものではない。表示値が「同じ意味の差」として混同されていないことだけを実測で確認した。R2は`PASS_LIMITED`のままであり、OOS、Final Holdout、候補選定、執行主張は引き続き禁止する。R065の専用executor要件にも変更はない。
