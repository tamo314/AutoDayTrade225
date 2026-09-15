# R093-Q001: TSE cash session extreme direction to following OSE night reversal

Task ID: `TASK-R093-Q001`  
Status: `FROZEN_BEFORE_PNL`  
Scope: Development only, `trade_date` 2021-01-01 through 2025-06-30.

## Hypothesis and historical linkage

The hypothesis is: an extreme causal direction across the complete official TSE cash session reverses over the corresponding following official OSE night session after costs. This is a one-time, Development-reuse experiment. Its decision ceiling is **INVESTIGATE**; it cannot become CANDIDATE.

Before PnL access, R001--R092 were checked for overlap. R058 is the closest prior closing-pressure-to-night reversal study; R070 is the unconditional night risk-premium control; R073 is the official-night-to-day reversal; R085 is the day-close-to-night-reopen gap fade; and R092 is a TSE-session extreme rolling-quantile study. Their recorded Development results are known and this is not an independent confirmation. This specification does not modify them. Inputs are limited to the normalized 225Labo-derived continuous Parquet series: no actual contract is invented and neither execution feasibility nor live tradability is claimed for that continuous series.

## Frozen causal construction

The scheduled axis `U` is the official TSE cash-business-day axis in Development. For cash day `d`, let `o` be the scheduled 09:00 bar open and let `c` be the close of the final scheduled one-minute bar immediately before the official cash end `T` (15:00 through 2024-11-01; 15:30 from 2024-11-05). Define `r=(c-o)/o` and `x=abs(r)`.

For each `d`, exactly the preceding 120 scheduled TSE days are inspected, current day excluded and with no backfill. Every valid `x`, including zero, is retained; at least 100 valid observations are required. `q75` is nearest rank `ceil(n*0.75)-1`, without interpolation, and equality is in the upper tail. `E` is `r != 0 and x >= q75`. R004 whole-session isolation invalidates a day observation and is never backfilled. Neither the future night mapping, night price availability, gap, weekday, cash-close-after information, confirmation, Stop, Target, re-entry, nor intraday updates may select `E_order`.

After the `c` close is known, the order is submitted at `T`, with side `-sign(r)` for A and `sign(r)` for B. The only mapping is the explicit one-to-one calendar link from cash day `d` to the official following OSE night whose `night_calendar_start_date=d`; no mapping means a reasoned pre-fill cancellation. A valid order fills one contract at that night session's first normal scheduled bar open and exits at the open of its last normal scheduled one-minute bar. The new OSE regime's closing-auction portion is not called a normal bar: its final normal open is one minute before `regular_end_next_day`; prior regimes use one minute before the official night close. Entry availability is assessed after submission and cannot be used to alter `E_order`; a filled entry with unavailable exit is unresolved with null PnL and stops the experiment.

A is cash-to-night reversal, B is same-event continuation, C is fixed long, D is fixed short, and N is no trade (JPY 0). A/B/C/D share E, submit, entry and exit. There is one contract and at most one position per cash day. The execution ledger uses the existing adverse-fill and PnL accounting primitives without changing the engine: `Net=Gross-fees`, with one adverse tick and JPY30 per side in the base case; slippage is not deducted twice.

## PnL-free gates

Any defect, leakage, schedule inconsistency, unexplained exclusion, or filled-but-unresolved exit produces **INCONCLUSIVE** before PnL. The following AND gate is fixed:

- unexplained exclusions and filled unresolved exits: 0;
- q-ready nonzero-`r` days: at least 800; E order share of them: 20--30%;
- A/B/C/D completed trades: at least 200 each; positive/negative `r`: at least 80 each;
- 2021 initialisation partial period: at least 15; 2022--2024: at least 35 each; 2025H1: at least 15;
- old OSE regime: at least 170; new OSE regime: at least 20.

## Fixed analysis after gate passage

The only sensitivity profiles are q70, q80, entry delayed one minute, exit 30 minutes earlier, 2 and 3 adverse ticks per side, and double fees. The common scheduled cash-day axis is bootstrapped by 20-`trade_date`, non-circular moving blocks, 10,000 repetitions, seed `20260915`, tail truncation and linear percentile. Common indices evaluate A daily Net and paired daily A-B, A-C and A-D.

The primary AND is A Net positive, PF greater than one, and strictly positive lower 95% bootstrap bounds for A daily Net and all three paired differences. Any failure is **REJECT**. If it passes, the decision remains **INVESTIGATE** unless q70, q80, delayed entry, early exit, two ticks and double fees all have positive Net; both `r` signs and both OSE regimes have positive Net; at least two of 2022--2024 and 2025H1 are positive; and Net excluding the ten largest winning trades is positive. Three ticks is a required saved diagnostic.

No result-dependent direction, threshold, entry/exit, night interval, Walk Forward, 2025H2 OOS, or Final Holdout work is allowed.
