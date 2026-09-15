# TASK-R098-Q001 result: R075 state-conditioned cash meta-selection

Final performance run: `task-r098-q001-state-conditioned-cash-meta-selection-20260916-02`  
Decision: **REJECT**

R098 reused exactly the R097-frozen R078-A/R079-A/R080-A/R081-A/R084-A/R086-A base-cost daily-Net and complete-trade artifacts. Every constituent and daily/trade SHA-256 hash matched R097 before ledger parsing. The R075 official-night state was calculated only from complete NIGHT data through 05:29 and was recorded as available by 08:45; strict-prior reference and score audits passed. R004 isolation and every state-unavailable date are recorded in the immutable state ledger.

The PnL-before-performance gate passed. The common 1,131-date Development axis had 252 calibration dates and 694 known-state evaluation dates (L/M/H=185/299/210). There were 509 A non-cash selections (L/M/H=110/231/168), 276 completed selected trades, and 2022/2023/2024/2025H1 completed-trade counts of 65/69/68/74. The six-constituent/hash, common-axis, strict-prior, state-unavailable-reason, state-specific selection-count, no-future-reference, no same-day availability selection, no unexplained exclusion, and no-unresolved-position checks all passed. 120 known-state dates with fewer than 60 matching-state observations remained in the evaluation axis with A/Q routed to cash.

With common 20-trade-date non-wrapping moving-block bootstrap indices (10,000 repetitions, seed 20260916, tail truncation, linear percentile), A produced **−755,060 JPY** Net and PF **0.665**. A daily Net CI was **[−2,115.03, −197.47] JPY/date**. Paired CIs were A−U **[−1,185.21, +808.53]**, A−Q **[−2,422.42, −276.37]**, and A−S **[−1,563.97, +950.68] JPY/date**. State Net was L/M/H=**−164,200/−471,340/−119,520 JPY**. Thus positive A Net, PF>1, and all four positive lower-CI requirements failed.

Per the frozen rule, no sensitivity was run after the failed primary AND; 3-tick diagnostics, OOS, Walk Forward, and Final Holdout were not accessed. Technical run `...-01` is retained as a pre-performance gate-only repair record: it had incorrectly excluded known-state, score-not-ready dates. Run `...-02` corrected that cash-routing implementation without changing the state, constituents, score thresholds, costs, comparators, or inference plan.
