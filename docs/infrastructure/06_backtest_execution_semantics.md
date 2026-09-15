# 06. Backtest and Execution Semantics

## 1. Event ordering per bar

For each eligible bar, use a deterministic ordering. Recommended V1:

1. Apply pending next-bar market orders at current bar open.
2. Update position with entry fill.
3. Evaluate existing protective stop/target against the bar range.
4. Resolve simultaneous stop/target using configured intrabar policy.
5. Process forced-exit rules if applicable.
6. Update indicators/features using information available through this bar.
7. Call strategy at bar close.
8. Convert new signal into pending order for the next eligible bar.
9. Persist events/diagnostics.

A newly generated close signal cannot fill at that same bar's open or close under the default V1 model.

## 2. Market order fill

Default:
- reference fill = next eligible bar open
- fill = reference price + adverse tick slippage

For `slippage_ticks = s`, tick size `T=5`:
- Buy fill = reference + `s*T`
- Sell fill = reference - `s*T`

This applies to both entries and exits in the adverse direction.

## 3. Stop and take-profit

Protective levels are specified in absolute price or ticks/points from actual entry fill.

For a long position:
- stop is touched if `bar.low <= stop_price`
- target is touched if `bar.high >= target_price`

For a short position:
- stop is touched if `bar.high >= stop_price`
- target is touched if `bar.low <= target_price`

## 4. Same-bar ambiguity

If both stop and target are touched inside the same minute and no tick path is known:

Default `intrabar_policy=conservative`:
- choose the economically adverse exit for the current position.

Other policies can exist for sensitivity analysis but must be explicit and labeled. Never silently choose profit-first.

## 5. Gap-through stop

For a long stop below market, if next bar opens below the stop, fill at the worse of:
- bar open adjusted for slippage, or
- policy-specific stop execution rule.

Recommended V1: stop-market behavior, so gap-through fills at bar open plus adverse slippage, not magically at the stop price.

Analogous rule for shorts.

## 6. Missing next bar

A pending next-bar order executes on the next **eligible observed bar**, not a synthesized minute. Record the delay/gap. Optionally reject if gap exceeds configured maximum minutes.

## 7. Session boundaries

Default modes:
- `day_only`
- `night_only`
- `full_session`

For a day-only strategy, pending orders must not carry into the night unless explicitly allowed.

Forced flat is configurable by minutes before session close rather than one hardcoded clock time, so it survives historical schedule changes.

Example:
- `new_entry_cutoff_minutes_before_close: 15`
- `force_flat_minutes_before_close: 5`

## 8. Position model

V1 states:
- `FLAT`
- `LONG 1`
- `SHORT 1`

No simultaneous long/short, no pyramiding.

If an opposite signal arrives while in a position, policy must be explicit:
- default: `close_then_reverse_next_bar=false`; close current position first, no same-bar instant reversal.

## 9. Fees

Fees are configured per contract per side or round trip. Store exact model in run manifest.

Default example config can be zero because broker fees vary, but production research should set realistic fees.

## 10. Slippage stress test

The reporting pipeline must support batch runs with at least:
- 0 ticks
- 1 tick
- 2 ticks
- 3 ticks

Metrics should show robustness to friction.

## 11. MAE/MFE

For each trade, calculate maximum adverse/favorable excursion from actual entry fill using observed OHLC during holding period. Document whether entry/exit bars are included; V1 includes them after the fill event while respecting event ordering.

## 12. Determinism

Same input dataset + same config + same code must yield same trade ledger ordering and PnL.

## 13. Causality of universe and scheduled orders — RG-20260915-01

Strategyのhistory制限だけでなく、loader、QC、U、E_exec、条件配線まで時点整合を要求する。将来のprice・quality・placebo・exit可用性を変更しても、変更時点より前の集合判定・特徴量・signal・orderは不変でなければならない。将来のfill、exit、最終PnLまで不変とは要求しない。

bar-startラベルの1分足は、その分のcloseで全OHLCが確定する。close通知でopenを見た後、同じopenへ新規注文を遡及させない。価格非依存の予定時刻注文を追加する場合は、発注作成・受付・約定の順序を別型で定義し、実装した証拠なしに既存on_bar APIが対応すると仮定しない。

## 14. New-entry cutoff, held EXIT, and missing outcomes

`new_entry_cutoff`は新規entryに対する制約で、保有建玉のEXIT callbackを抑止する境界ではない。pending EXIT、force-flat、新規拒否が同時刻に競合する場合、凍結したイベント順序で一度だけ決済する。旧runでこの順序に不備があるときは、影響監査・修正・新runを分離する。

一般のnext eligible observed barによる約定と、特定研究で指定されたstrict scheduled barを区別する。研究設定は、entry最大遅延、pendingの取消、exit遅延・強制決済、欠損時の不明損益を定める。entry取消とexit未約定を同じ処理にしない。建玉を消去する取消は許されない。

後刻のデータ欠落で、約定済みentryを初めから存在しなかった扱いにしない。解決できないexitはOPEN_POSITION/UNKNOWN_PNLとして残し、集計は不完全とする。架空exit、事後0円化、未来欠損を使う主entryのfilterは禁止する。

## 15. Holding-clock profiles

`exit_clock`を `scheduled_entry_anchor` と `actual_fill_anchor` に分ける。R003原案は実fill基準、後続の多くは予定entry基準の固定exitであり、同一の保有時間設定へまとめない。entry遅延でexitを延長しない条件はscheduled profileで検査する。旧設定の解釈を無断で変更しない。

`holding_policy=session_flat`を標準とする。R065のように取引session終了後の休場を跨ぐ研究は、`explicit_cross_session`の専用profile、実時間状態管理、リスク／期間末端テストを必要とする。セッション独立runの連結で持越しを偽装しない。未実装ならPnLを止める。

## 16. Cost and paired-control identities

同じevent・reference価格・entry/exitの反対sideでは費用前Gross和=0。対称な固定費用下ではNet和=−2×往復費用。この条件が成立する場合だけ恒等式を検査し、Stop等で経路が違う対照に機械的に当てない。

固定費用還元、実遅延、再約定stress、exit不利overlayを区別する。費用と経済約定を修正する場合は別versionと影響検証を必要とし、研究を勝たせるための変更を禁止する。
