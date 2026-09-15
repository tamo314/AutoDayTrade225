# TASK-R097-Q001 result: audited TSE cash-only strategy meta-selection

Final run: `task-r097-q001-tse-cash-meta-selection-20260916-03`  
Decision: **REJECT**

The run froze six non-economicly screened principal cash strategies before evaluation PnL: R078-A, R079-A, R080-A, R081-A, R084-A, and R086-A. Their immutable daily-Net/trade artifacts, SHA-256 hashes, family IDs, and the mechanically excluded R001--R096 IDs are in `constituent_freeze_before_evaluation_pnl.json`. The screen used no economic result or preceding REJECT label. All six had matching 1,131-date TSE axes, base one-tick-plus-JPY30 costs, PASS validation, and all-true execution/accounting audits.

The 252-date calibration period left 879 evaluation dates. The PnL-before-performance gate passed: 805 non-cash A selections, 445 completed A trades, annual completed trades of 145/75/146 in 2022/2023/2024 and 79 in 2025H1, and at least 20 selections for every included strategy. The immutable-ledger audit found no future reference, same-date availability switch, axis mismatch, unexplained exclusion, or unresolved filled position. Static S was R084-A.

The common-index 20-trade-date non-wrapping moving-block bootstrap (10,000 repetitions, seed 20260915, tail truncation, linear percentile) rejected the primary AND. A Net was **−687,700 JPY** and PF **0.783**. A daily-Net CI was **[−1,708.14, 302.66] JPY/date**; paired A−Q was **[−1,814.69, 183.37]**; paired A−S was **[−1,484.06, 515.09]**. Thus all five primary requirements failed: positive Net, PF>1, and positive lower bounds for A, A−Q, and A−S.

Per the frozen protocol, no result-dependent constituent change, sensitivity, OOS, or Final Holdout access occurred after this failure. Technical artifacts `...-01` and `...-02` stopped before performance reporting due to two corrected serialization/boolean-gate defects; `...-03` preserves the same frozen constituents and rules and is the only performance evaluation.

Immutable final artifacts: `results/research/task-r097-q001-tse-cash-meta-selection-20260916-03/`.
