# TASK-R094-Q002 結果: full cash-path efficiency to following-night continuation

Run ID: `task-r094-q002-cash-path-efficiency-night-gate-revision-20260915-01`  
Decision: **REJECT**

Immutable artifacts are in [task-r094-q002-cash-path-efficiency-night-gate-revision-20260915-01](../../results/research/task-r094-q002-cash-path-efficiency-night-gate-revision-20260915-01/). The frozen Q002 registration is [93_r094_q002_cash_path_efficiency_night_gate_revision.md](93_r094_q002_cash_path_efficiency_night_gate_revision.md); Q001's artifacts remain unchanged.

## PnL-free K audit and authorized revision

Q001 stopped before creating orders, fills, trades, fill prices, returns, PnL, PF, bootstrap, sensitivity-performance, execution-accounting, or S3 artifacts. Before Q002 accessed an economic result, its regenerated primary `qx50`/`qe75` ledger matched Q001's immutable PnL-free ledger value-for-value. The 1,099-date scheduled axis had 957 q-ready nonzero cash paths, H=222, K=243, no unexplained exclusion, and no filled unresolved exit. Exact current-excluded/non-backfilled prior-120 references, cash-to-following-night mapping, post-close submission, first/last normal-night opens, R004 isolation, and the absence of future-night-dependent H/K selection all passed.

K submitted 254 orders: 243 completed and 11 cancelled. The cancellations were exactly 10 `FIRST_NORMAL_NIGHT_ENTRY_OPEN_MISSING_OR_INELIGIBLE` and one `R004_FOLLOWING_NIGHT_SESSION_QUARANTINED`; no other missing-data or schedule reason occurred. Completed K by cash year was 2021/2022/2023/2024/2025H1 = 24/58/68/69/24; cash direction positive/negative = 111/132; old/new OSE regime = 212/31; rolling-x bands `[0.50,0.75)`/`[0.75,1.00]` = 185/58. The required A--M support remains H/M=45/185 and 177/58 in those two bands. Therefore only the pre-registered K minimum was changed once, from 250 to 240; every other gate remained unchanged.

## Fixed evaluation

At one adverse tick plus JPY30 per side, A continuation had 222 trades, Net **+123,180 JPY**, PF **1.054**, and expectancy **+554.86 JPY/trade**. The fixed common-index 20-`trade_date` non-circular MBB (10,000 repetitions, seed `20260915`) 95% lower bounds were A daily Net **−589.33 JPY**, A−B **−741.70 JPY**, A−C **−785.30 JPY**, and A−D **−361.24 JPY** per scheduled trade date. The standardized two-band A−M conditional Net-per-trade estimate was **+7,103.19 JPY**, CI **[+713.94, +13,562.04]**.

Although A Net, PF, and standardized A−M passed, all four registered daily/paired CI-lower conditions failed. The frozen primary AND therefore fails and the decision is **REJECT**. No result-dependent rescue is permitted.

Registered robustness also did not all pass: qe70 Net **−109,260**, qe80 **+92,580**, qx40 **+123,180**, qx60 **+106,360**, one-minute delay **+102,680**, 30-minute early exit **+108,180**, two-tick **−98,820**, double-fee **+109,860**, and mandatory three-tick diagnostic **−320,820 JPY**. Positive/negative cash-direction A Net was **+193,020/−69,840 JPY**; 2022--2024 Net was **−90,860/+353,580/−15,180**, 2025H1 **−141,060**, and removal of the ten largest A winners gave **−484,720 JPY**. Both OSE regimes were not positive.

Execution and accounting checks passed: A/B/C/D shared H and entry/exit timing, A/B sides were opposite, there was at most one position per cash day, no Stop/Target, `Net = Gross − fees`, and no double slippage deduction. Pre-execution validation also passed (pytest 15, Ruff, mypy, and `py_compile`, all exit code 0). OOS, Walk Forward, and Final Holdout were not accessed.
