# R093-Q002 result: cash-to-night reversal warm-up audit repair

Run ID: `task-r093-q002-tse-cash-night-reversal-warmup-audit-20260915-02`  
Decision: **REJECT**

The immutable output is [task-r093-q002-tse-cash-night-reversal-warmup-audit-20260915-02](../../results/research/task-r093-q002-tse-cash-night-reversal-warmup-audit-20260915-02/). The frozen Q002 registration is [89_r093_q002_tse_cash_night_reversal_warmup_audit_repair.md](89_r093_q002_tse_cash_night_reversal_warmup_audit_repair.md). The Q001 artifacts remain unchanged.

## Repair and PnL-free gate

Q001 did not access any economic result: it stopped before trade, fill-price, return, PnL, PF, bootstrap, sensitivity, or execution-accounting generation. The only defect was its causality audit treating all non-120-date reference lists as invalid, thereby incorrectly rejecting valid scheduled-axis warm-up lists of length 1--119. Q002 changes only that audit predicate. Its tests cover axis positions 0, 1, 99, 100, 119, 120, and 121, a 100-valid-observation window containing missing scheduled observations, a 99-valid rejection, and current/future/duplicate-reference rejection. The q75 calculation remains frozen: only valid `x` within the exact current-excluded reference window, no older-date backfill, full 120 scheduled-date window, and at least 100 valid `x`.

Before PnL, the regenerated primary ledger exactly matched Q001's saved PnL-free ledger. The scheduled axis was 1,099; q-ready nonzero-return days 957; E orders 253; completed paths 246; cash-return signs 126/120; years 27/62/69/65/23; and old/new OSE regimes 215/31. There were zero unexplained exclusions and zero unresolved positions. The seven cancellation reasons matched exactly: six missing/ineligible first normal night entries and one R004 following-night quarantine. The repaired causality audit passed all five checks, including exact `min(i,120)` prior-date windows, order/uniqueness, and current/future exclusion. Pre-execution validation passed: pytest 15, Ruff, mypy, and `py_compile` all returned 0.

The earlier `...-01` Q002 run stopped before data/PnL at a Ruff import-format check. It is retained as an immutable technical failure. The final `...-02` run used a distinct ID; its only code change was that mechanical import formatting fix. No Q001 artifact or other existing experiment was altered, and the audit-only defect has no economic effect on Q001 because Q001 produced no economic artifacts.

## Fixed one-time evaluation

At one adverse tick plus JPY30/side, A reversal had 246 trades, Net **−65,760 JPY**, and PF **0.976**. Common-index 20-trade-date non-circular MBB (10,000 repetitions, seed 20260915) lower 95% bounds were A daily Net **−1,090.72 JPY**, A−B **−1,688.83 JPY**, A−C **−1,587.83 JPY**, and A−D **−536.85 JPY** per scheduled trade date. All six frozen primary-AND conditions therefore failed.

All registered robustness requirements also failed except 2025H1 and the negative-cash-return direction. Net was q70 **−269,100**, q80 **−12,460**, one-minute delay **−98,260**, 30-minute early exit **−72,260**, two ticks **−311,760**, double fees **−80,520**, and the required three-tick diagnostic **−557,760 JPY**. A's positive/negative cash-return subsets were **−399,560/+333,800 JPY**; old/new OSE regimes combined were not both positive; only one of 2022--2024 was positive; and excluding the ten largest winners left **−1,090,660 JPY**.

Execution/accounting checks passed: A/B/C/D shared E and timing, A/B sides were opposite, each cash day had at most one position, no Stop/Target was introduced, `Net=Gross-fees`, and slippage was not double deducted. OOS, Walk Forward, and Final Holdout were not accessed. Under the frozen rule, primary-AND failure is **REJECT**; no rescue selection or further research stage is permitted for this task.
