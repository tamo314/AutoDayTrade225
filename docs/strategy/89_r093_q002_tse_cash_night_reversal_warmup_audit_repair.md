# R093-Q002: R093 cash-to-night reversal — warm-up audit repair

Task ID: `TASK-R093-Q002`  
Status: `FROZEN_BEFORE_PNL`  
Scope: Development only, `trade_date` 2021-01-01 through 2025-06-30.

## Purpose and access record

R093-Q001 stopped as **INCONCLUSIVE before PnL**. Its immutable output contains no trade, fill-price, return, PnL, PF, bootstrap, sensitivity, or execution-accounting artifact. This Q002 registration is made before any economic result is obtained. Q001's non-economic S2 ledger counts are known only as the required reproduction target below.

The hypothesis, causal inputs, `r`/`x` definition, q75 equality rule, directions, order time, entry/exit mapping, A/B/C/D/N controls, one-contract limit, cost model, profiles, bootstrap, primary AND, and robustness rules are exactly those frozen in R093-Q001. No threshold, direction, time, night interval, period, or analysis rule is changed.

## Sole repair

Only the causality audit's warm-up predicate is repaired. At scheduled-axis zero-based position `i`, its recorded reference dates must be exactly the ordered, unique list `axis[max(0, i-120):i]`: the immediately preceding `min(i,120)` scheduled TSE dates. It must contain neither the current date nor a future date. Thus positions 0, 1, 99, 100, 119, 120, and 121 are audited explicitly; warm-up windows of length 1--119 are valid prefix windows, and positions at or after 120 must always contain the immediately preceding 120 scheduled dates.

Quantiles use valid `x` values only from that reference window, without filling invalid observations from older dates. The frozen rule remains q-ready only after the complete 120 scheduled-date window is present and it has at least 100 valid `x` values. A missing reference observation cannot be backfilled.

## PnL-free reproduction gate

Before any economic computation, the new primary ledger must be byte-for-value equal to Q001's saved PnL-free primary ledger and its count summary must be exactly: scheduled axis 1,099; q-ready nonzero returns 957; E orders 253; completed paths 246; positive/negative cash returns 126/120; yearly completed paths 2021/2022/2023/2024/2025H1 = 27/62/69/65/23; old/new OSE regimes 215/31; unexplained exclusions 0; unresolved filled positions 0. The seven E-order cancellations must consist of one `R004_FOLLOWING_NIGHT_SESSION_QUARANTINED` and six `FIRST_NORMAL_NIGHT_ENTRY_OPEN_MISSING_OR_INELIGIBLE`.

Any ledger mismatch, causality-audit failure, unexplained exclusion, or unresolved filled position is **INCONCLUSIVE** and stops before PnL.

## Conditional fixed execution and decision

Only after that gate passes, run once the frozen A/B/C/D/N profiles at one adverse tick plus JPY30/side, q70/q80, one-minute entry delay, 30-minute early exit, two/three ticks, double fee, and the common-index 20-`trade_date` non-circular moving-block bootstrap (10,000 repetitions, seed `20260915`). Apply the unchanged primary AND, direction, regime, year, and top-ten-winner concentration conditions.

Failure of the primary AND is **REJECT**. If it passes but any registered robustness condition fails, the result is **INVESTIGATE**. Even if every condition passes, the decision ceiling is **INVESTIGATE**. Do not conduct result-dependent one-sided selection, threshold/time/night changes, Walk Forward, OOS, or Final Holdout work.
