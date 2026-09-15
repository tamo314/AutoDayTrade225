# R094-Q002: full cash-path efficiency to following-night continuation — K gate revision

Task ID: `TASK-R094-Q002`  
Status: `FROZEN_BEFORE_PNL`  
Scope: Development only, `trade_date` 2021-01-01 through 2025-06-30. The decision ceiling remains **INVESTIGATE**.

## Parent-run access record and one permitted revision

R094-Q001 stopped **INCONCLUSIVE before PnL**. Its immutable artifact contains no orders, fills, trades, fill prices, returns, PnL, PF, bootstrap, sensitivity-performance, execution-accounting, or S3 result artifact. Q002 is registered before accessing any of those outcomes. The Q001 PnL-free event ledger records only the known reproduction target that K completed paths were 243, against the frozen 250-path gate.

Q002 retains Q001's hypothesis, cash-path `r/x/V/e` features, official TSE and OSE schedules, current-excluded non-backfilled prior-120 reference set, nearest-rank `qx50`/`qe75`, equality assignment, H/K definitions, direction, cash-close submission, following-night first-normal-open entry, final-normal-open exit, A/B/C/D/M/N controls, one-contract maximum, costs, common-index moving-block bootstrap, all sensitivity profiles, primary AND, and all robustness rules.

The sole possible revision is the pre-PnL K-completion lower bound, from 250 to **240**, and it is authorized once only if the audit below passes. No other gate is relaxed: in particular both rolling-x bands of the A--M comparison retain **H and M at least 20 completed paths each**.

## Mandatory PnL-free K audit

Before creating any trade, fill, return, PnL, PF, bootstrap, or sensitivity-performance artifact, rebuild the frozen primary `qx50`/`qe75` ledger from the Development normalized Parquet input and compare it value-for-value with Q001's immutable PnL-free ledger. Audit K's submitted orders, 243 completed paths, entry-cancellation count and reason, and completed-K counts by cash-date year, cash-return direction, old/new OSE regime, and rolling-x band.

The audit may pass only when the exact current-excluded H/K split reproduces Q001, K has 254 submitted orders, 243 completed paths, and 11 entry cancellations consisting exactly of 10 `FIRST_NORMAL_NIGHT_ENTRY_OPEN_MISSING_OR_INELIGIBLE` and one `R004_FOLLOWING_NIGHT_SESSION_QUARANTINED`. It must also show zero unexplained exclusions, zero filled unresolved exits, exact scheduled-reference causality, explicit cash-to-following-night mapping, post-cash-close submission, first/last normal-night opens, and that H/K selection is made before and independently of future-night availability. Missing data, schedule mismatch, R004 leakage, any different cancellation reason, a future-night-dependent selection, or an entry followed by unknown exit fails the audit.

If this audit fails, completed K is below 240, or either A--M rolling-x band lacks H or M 20 completed paths, stop **INCONCLUSIVE** without PnL. Only after all conditions pass may this one Q002 run replace the Q001 K `>=250` gate by `>=240` and execute the unchanged frozen evaluation.

## Fixed evaluation and decision

After the gate only, run once the Q001 base profiles and all fixed sensitivities: `qe70`, `qe80`, `qx40`, `qx60`, one-minute entry delay, 30-minute early exit, two and three adverse ticks, and double fees. Use the unchanged non-circular 20-`trade_date` moving-block bootstrap, 10,000 repetitions, seed `20260915`, tail truncation, linear percentiles, and common indices. Evaluate A daily Net, paired A-B/A-C/A-D daily Net, and the equal-weight two-band standardized conditional A-M Net-per-trade difference.

The original primary AND is unchanged: A Net > 0; PF > 1; A daily-Net CI lower > 0; every paired CI lower > 0; and standardized A-M CI lower > 0. Any primary-AND failure is **REJECT**. A primary pass with any failed frozen robustness condition is **INVESTIGATE**; even a full pass is capped at **INVESTIGATE**. Do not make result-dependent threshold, side, entry, exit, or night-interval changes; do not add searches, Walk Forward, 2025H2 OOS, or Final Holdout work.
